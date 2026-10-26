"""CCM package import and recoverable campaign activation."""

import copy
import hashlib
import logging
import os
import re
import shutil
import stat
import tempfile
import uuid
import zipfile
from pathlib import Path
from threading import RLock

from .catalog import timestamp
from .bank_scanner import scan_package
from .files import atomic_json, check_cancel, contained_path, file_hash, read_json, relative_name, prune_empty, signature
from .library import is_sc2_root
from .game_state import game_running
from .progress_metadata import PROGRESS_FILE, campaign_owner, load_progress, validate_progress

SLOTS = {
    'wol': ('Wings of Liberty', 'Maps/Campaign'),
    'hots': ('Heart of the Swarm', 'Maps/Campaign/swarm'),
    'lotv': ('Legacy of the Void', 'Maps/Campaign/void'),
    'nco': ('Nova Covert Ops', 'Maps/Campaign/nova'),
}
MAX_FILES = 100000
MAX_BYTES = 64 * 1024**3


def metadata(path):
    if path.stat().st_size > 64 * 1024:
        raise ValueError('metadata.txt is too large.')
    text = path.read_text(encoding='utf-8-sig')
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.partition('=')
        if separator:
            fields[key.strip().casefold()] = value.strip()
    value = fields.get('campaign', '').casefold()
    aliases = {'wol': ('wings', 'liberty', 'wol'), 'hots': ('heart', 'swarm', 'hots'),
               'lotv': ('legacy', 'void', 'lotv'), 'nco': ('nova', 'covert', 'ops', 'nco')}
    slot = next((k for k, names in aliases.items() if any(n in value for n in names)), None)
    if not slot or not fields.get('title'):
        raise ValueError('metadata.txt must specify a title and a supported campaign.')
    updated = fields.get('updated_at', '')
    if updated and timestamp(updated) is None:
        raise ValueError('metadata.txt: updated_at must be an ISO 8601 date or timezone-aware timestamp.')
    return {'name': fields['title'][:200], 'slot': slot, 'author': fields.get('author', 'Unknown')[:200],
            'version': fields.get('version', 'Unknown')[:80], 'description': fields.get('desc', '')[:20000],
            'updated_at': updated}


def copy_file(source, target, cancel=None, *, prefix='.ccm-', expected_sha=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=prefix, dir=target.parent)
    try:
        with source.open('rb') as incoming, os.fdopen(fd, 'wb') as outgoing:
            digest = hashlib.sha256()
            while chunk := incoming.read(1024 * 1024):
                check_cancel(cancel)
                outgoing.write(chunk)
                digest.update(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if expected_sha is not None and digest.hexdigest() != expected_sha:
            raise ValueError('A campaign source or backup is damaged or changed during copying.')
        check_cancel(cancel)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def _walk_error(error):
    raise error


def walk_files(root):
    count = 0
    for directory, folders, files in os.walk(root, followlinks=False, onerror=_walk_error):
        for name in folders + files:
            path = Path(directory) / name
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError(f'Links are not supported in campaign packages: {path.name}')
            contained_path(root, path.relative_to(root).as_posix())
        for name in files:
            count += 1
            if count > MAX_FILES:
                raise ValueError('The package contains too many files.')
            yield Path(directory) / name


def extract(source, destination, cancel):
    if source.is_symlink() or (hasattr(source, 'is_junction') and source.is_junction()):
        raise ValueError('Choose a regular ZIP file or folder.')
    size = 0
    seen = set()
    if source.is_dir():
        for path in walk_files(source):
            check_cancel(cancel)
            size += path.stat().st_size
            if size > MAX_BYTES:
                raise ValueError('The unpacked campaign exceeds 64 GiB.')
            relative = path.relative_to(source).as_posix()
            if relative.casefold() in seen:
                raise ValueError(f'Duplicate filename: {relative}')
            seen.add(relative.casefold())
            copy_file(path, contained_path(destination, relative), cancel)
        return
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_FILES:
            raise ValueError('The ZIP contains too many entries.')
        for item in entries:
            check_cancel(cancel)
            name = relative_name(item.filename.replace('\\', '/').rstrip('/'))
            mode = item.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError(f'Unsupported ZIP entry: {name}')
            if name.casefold() in seen:
                raise ValueError(f'Duplicate ZIP entry: {name}')
            seen.add(name.casefold())
            path = contained_path(destination, name)
            if item.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            size += item.file_size
            if size > MAX_BYTES or item.flag_bits & 1:
                raise ValueError('The ZIP is encrypted or exceeds the unpacked size limit.')
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as incoming, path.open('xb') as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    check_cancel(cancel)
                    outgoing.write(chunk)


def package_files(root, meta, slot, cancel):
    files = []
    destinations = set()
    for path in walk_files(root):
        check_cancel(cancel)
        if path.parent == meta.parent and path.name.casefold() == PROGRESS_FILE:
            continue
        parts = path.relative_to(root).parts
        mod = next((i for i, p in enumerate(parts) if p.lower().endswith('.sc2mod')), None)
        if mod is not None:
            dest = 'Mods/' + '/'.join(parts[mod:])
        elif path.is_relative_to(meta.parent):
            relative = path.relative_to(meta.parent)
            if relative.parts[0].casefold() in ('lotvprologue', 'voidprologue'):
                if slot != 'lotv':
                    raise ValueError('Only a LotV package can replace the prologue.')
                dest = 'Maps/Campaign/voidprologue/' + '/'.join(relative.parts[1:])
            else:
                dest = SLOTS[slot][1] + '/' + relative.as_posix()
                if slot == 'wol' and relative.parts[0].casefold() in ('swarm', 'void', 'nova'):
                    raise ValueError('A WoL package cannot replace another campaign slot.')
        else:
            if path.suffix.casefold() == '.sc2map':
                raise ValueError('Found maps outside the metadata folder. Split this package before importing.')
            continue
        relative_name(dest)
        if dest.casefold() in destinations:
            raise ValueError(f'Two package files target {dest}.')
        destinations.add(dest.casefold())
        files.append({'source': path.relative_to(root).as_posix(), 'destination': dest,
                      'sha256': file_hash(path, cancel)})
    if not any('.sc2map' in f['destination'].lower() for f in files):
        raise ValueError('No campaign maps were found beside metadata.txt.')
    return files



class OperationLock:
    def __init__(self, path):
        self.path = path
        self.thread_lock = RLock()
        self.depth = 0
        self.stream = None

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            if not self.depth:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.stream = self.path.open('a+b')
                if not self.path.stat().st_size:
                    self.stream.write(b'0')
                    self.stream.flush()
                self.stream.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    self.stream.close()
                    self.stream = None
                    raise ValueError('Another launcher is working on this campaign library. Wait for it to finish.') from error
            self.depth += 1
            return self
        except BaseException:
            self.thread_lock.release()
            raise

    def __exit__(self, *args):
        try:
            self.depth -= 1
            if not self.depth:
                self.stream.close()
                self.stream = None
        finally:
            self.thread_lock.release()


class CampaignManager:
    def __init__(self, root, state_dir):
        self.root = root.expanduser().resolve()
        key = hashlib.sha256(os.path.normcase(str(self.root)).encode()).hexdigest()
        self.directory = state_dir / 'ccm' / key
        self.state_path = self.directory / 'state.json'
        self.journal = self.directory / 'pending.json'
        self.packages = self.game_path('Maps/CustomCampaigns')
        self.lock = OperationLock(self.directory / 'operation.lock')

    def game_path(self, name):
        relative_name(name)
        path = self.root
        for part in name.split('/'):
            if path.is_dir():
                matches = [p for p in path.iterdir() if p.name.casefold() == part.casefold()]
                if len(matches) > 1:
                    raise ValueError(f'Ambiguous filename in {path}: {part}')
                path = matches[0] if matches else path / part
            else:
                path /= part
            if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
                raise ValueError(f'Cannot modify a linked campaign path: {path}')
        return contained_path(self.root, path.relative_to(self.root).as_posix())

    def _state(self):
        state = read_json(self.state_path, {'active': {}, 'originals': {}, 'files': {}, 'paths': {}})
        if not isinstance(state, dict) or any(not isinstance(state.get(k), dict)
                                             for k in ('active', 'originals', 'files')):
            raise ValueError('The CCM installation record is invalid.')
        if any(slot not in SLOTS for slot in state['active']):
            raise ValueError('The CCM record contains an unknown campaign slot.')
        state.setdefault('paths', {})
        state.setdefault('original_hashes', {})
        if not isinstance(state['paths'], dict) or not isinstance(state['original_hashes'], dict):
            raise ValueError('The CCM path record is invalid.')
        return state

    def _require_root(self):
        if not is_sc2_root(self.root):
            raise ValueError('Choose your StarCraft II installation in Settings first.')
        self.game_path('Maps/CustomCampaigns')

    def _recovery_target(self, name, journal):
        target = self.game_path(journal['paths'][name])
        if target.is_file():
            if file_hash(target) not in {journal['before_hashes'][name], journal['after_hashes'][name]}:
                raise ValueError(f'{name} changed after the interrupted switch. Preserve it before recovering.')
        elif target.is_dir():
            # Packed/unpacked map transitions can leave a directory at a file's path.
            if not any(key.startswith(name + '/') for key in journal['before']):
                raise ValueError(f'{name} changed after the interrupted switch. Preserve it before recovering.')
            for child in walk_files(target):
                key = child.relative_to(self.root).as_posix().casefold()
                if key not in journal['before']:
                    raise ValueError(f'{key} changed after the interrupted switch. Preserve it before recovering.')
        elif target.exists():
            raise ValueError(f'Unsupported recovery target: {name}')
        # A missing target is valid after an interrupted removal or rollback.
        return target

    def _verify_recovery(self, journal):
        if (not isinstance(journal, dict) or journal.get('format') != 2
                or any(not isinstance(journal.get(key), dict)
                       for key in ('before', 'state', 'paths', 'before_hashes', 'after_hashes'))
                or any(set(journal[key]) != set(journal['before'])
                       for key in ('paths', 'before_hashes', 'after_hashes'))):
            raise ValueError('The recovery record lacks valid integrity data. Keep it and the backups for manual review.')
        state = journal['state']
        if any(not isinstance(state.get(key), dict) for key in ('active', 'originals', 'files')):
            raise ValueError('The recovery state is damaged. Keep the recovery record and backups intact.')
        prefix = journal.get('temporary_prefix', '')
        if not isinstance(prefix, str) or not re.fullmatch(r'\.ccm-[0-9a-f]{32}-', prefix):
            raise ValueError('The recovery temporary-file record is invalid.')
        for name, backup in journal['before'].items():
            relative_name(name)
            if (not name.startswith('mods/') and self._slot(name) is None
                    or not isinstance(journal['paths'][name], str)
                    or journal['paths'][name].casefold() != name):
                raise ValueError('The recovery path record is invalid.')
            for key in ('before_hashes', 'after_hashes'):
                digest = journal[key][name]
                if digest is not None and (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
                    raise ValueError('The recovery integrity record is invalid.')
            digest = journal['before_hashes'][name]
            if (backup is None) != (digest is None):
                raise ValueError('The recovery backup record is damaged.')
            if backup is not None:
                if not isinstance(backup, str) or not re.fullmatch(r'transactions/[0-9a-f]{32}/[0-9]+', backup):
                    raise ValueError('The recovery backup path is invalid.')
                path = contained_path(self.directory, backup)
                if not path.is_file() or file_hash(path) != digest:
                    raise ValueError('A recovery backup is missing or damaged. Keep the files and recovery record intact.')
            self._recovery_target(name, journal)

    def recover(self):
        with self.lock:
            journal = read_json(self.journal)
            if journal is None:
                return
            if game_running():
                raise ValueError('Close StarCraft II before recovering campaign files.')
            self._verify_recovery(journal)
            paths = journal['paths']
            prefix = journal['temporary_prefix']
            if prefix:
                parents = {self.game_path(paths.get(name, name)).parent for name in journal['before']}
                for parent in parents:
                    if parent.is_dir():
                        for temporary in parent.glob(prefix + '*'):
                            if temporary.is_file() and not temporary.is_symlink():
                                temporary.unlink()
            for name in sorted(journal['before'], key=lambda n: -n.count('/')):
                target = self._recovery_target(name, journal)
                if target.is_file():
                    target.unlink()
                    prune_empty(self.root, target.parent)
            for name, backup in journal['before'].items():
                if backup:
                    target = self.game_path(paths.get(name, name))
                    if target.is_dir():
                        target.rmdir()
                    copy_file(contained_path(self.directory, backup), target, prefix=prefix,
                              expected_sha=journal['before_hashes'][name])
            atomic_json(self.state_path, journal['state'])
            self.journal.unlink()
            self._clean_transactions(journal['state'])

    def _clean_transactions(self, state):
        keep = {name.split('/')[1] for name in state['originals'].values() if name}
        for path in (self.directory / 'transactions').glob('*'):
            if path.name not in keep and path.is_dir() and not path.is_symlink():
                try:
                    shutil.rmtree(path)
                except OSError:
                    logging.getLogger(__name__).warning('Could not remove unused backup %s', path)

    def list_packages(self):
        with self.lock:
            state = self._state()
            result = []
            for path in sorted(self.packages.glob('*/package.json')):
                if len(path.parent.name) != 24 or any(c not in '0123456789abcdef' for c in path.parent.name):
                    continue
                item = self._package(path.parent.name)
                item['active'] = state['active'].get(item['slot']) == item['id']
                result.append(item)
            return result

    def import_package(self, source, cancel=None, *, base_build=None):
        with self.lock:
            self._require_root()
            self.recover()
            self.packages.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.import-', dir=self.packages) as name:
                stage = Path(name)
                payload = stage / 'payload'
                payload.mkdir()
                extract(Path(source), payload, cancel)
                metas = [p for p in walk_files(payload) if p.name.casefold() == 'metadata.txt'
                         and not any(x.lower().endswith(('.sc2map', '.sc2mod'))
                                     for x in p.relative_to(payload).parts[:-1])]
                if len(metas) != 1:
                    raise ValueError('Import one campaign at a time; exactly one metadata.txt is required.')
                item = metadata(metas[0])
                if progress := load_progress(metas[0].parent):
                    item['progress'] = progress
                if base_build:
                    item.update(base_campaign=True, base_build=base_build['key'], version=base_build['version'])
                identity = '\n'.join(item[k].casefold() for k in ('slot', 'author', 'name', 'version'))
                if base_build:
                    identity += '\n' + base_build['key']
                item['id'] = hashlib.sha256(identity.encode()).hexdigest()[:24]
                item['files'] = package_files(payload, metas[0], item['slot'], cancel)
                item['map_count'] = len({f['destination'].lower().split('.sc2map')[0]
                                         for f in item['files'] if '.sc2map' in f['destination'].lower()})
                destination = self.packages / item['id']
                if destination.exists():
                    raise ValueError('This campaign version is already imported.')
                atomic_json(stage / 'package.json', item)
                atomic_json(stage / 'bank-scan.json', scan_package(payload, item, cancel))
                check_cancel(cancel)
                stage.rename(destination)
            return item

    def scan_banks(self, identity, cancel=None, *, only_if_missing=False):
        with self.lock:
            check_cancel(cancel)
            item = self._package(identity)
            package = contained_path(self.packages, identity)
            report_path = contained_path(package, 'bank-scan.json')
            if only_if_missing and report_path.is_file():
                return None
            report = scan_package(contained_path(package, 'payload'), item, cancel)
            check_cancel(cancel)
            atomic_json(report_path, report)
            return report

    def _package(self, identity):
        if not isinstance(identity, str) or len(identity) != 24 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Invalid campaign identifier.')
        item = read_json(self.packages / identity / 'package.json')
        if not item or item.get('id') != identity or item.get('slot') not in SLOTS:
            raise ValueError('The imported campaign is missing or damaged.')
        if 'progress' in item:
            item['progress'] = validate_progress(item['progress'])
        for file in item.get('files', []):
            dest = relative_name(file['destination']).casefold()
            if not dest.startswith('mods/') and self._slot(dest) != item['slot']:
                raise ValueError('The package contains a destination outside its campaign slot.')
        return item

    def bank_conflicts(self, progress, owner):
        paths = {name.casefold() for name in validate_progress(progress)['banks']}
        conflicts = []
        for item in self.list_packages():
            if campaign_owner(item) == owner or not item.get('progress'):
                continue
            shared = paths.intersection(name.casefold() for name in item['progress']['banks'])
            if shared:
                conflicts.append(f"{item['name']}: {', '.join(sorted(shared))}")
        return conflicts

    def base_statuses(self):
        from .casc import installed_build
        with self.lock:
            build = installed_build(self.root)
            packages = self.list_packages()
            active = self._state()['active']
            result = {}
            for slot in SLOTS:
                versions = [p for p in packages if p['slot'] == slot and p.get('base_campaign')]
                current = next((p for p in versions if p.get('base_build') == build['key']), None)
                loaded = next((p for p in packages if p['id'] == active.get(slot)), None)
                result[slot] = {'base_state': 'current' if current else 'stale' if versions else 'absent',
                                'version': build['version'], 'base_id': current['id'] if current else None,
                                'active': bool(loaded and loaded.get('base_campaign')),
                                'loaded_name': loaded['name'] if loaded else ''}
            return result

    def base_id(self, slot):
        return self.base_statuses()[slot]['base_id']

    def install_base(self, slot, cancel=None, progress=lambda _: None):
        from .casc import extract_campaign, installed_build
        with self.lock:
            self._require_root()
            self.recover()
            if game_running():
                raise ValueError('Close StarCraft II before installing campaign maps.')
            build = installed_build(self.root)
            if identity := self.base_id(slot):
                self.activate(identity, cancel)
                return f'{SLOTS[slot][0]} maps loaded.'
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='base-', dir=self.directory) as name:
                extract_campaign(self.root, slot, Path(name), cancel, progress)
                if installed_build(self.root) != build:
                    raise ValueError('StarCraft II was updated during extraction. Try again when the update finishes.')
                item = self.import_package(Path(name), cancel, base_build=build)
            self.activate(item['id'], cancel)
            return f'{SLOTS[slot][0]} maps installed from this game installation.'

    def _targets(self, active):
        targets = {}
        for slot, identity in active.items():
            item = self._package(identity)
            if item['slot'] != slot:
                raise ValueError('Campaign slot mismatch.')
            for file in item['files']:
                name = relative_name(file['destination']).casefold()
                if name in targets and targets[name][1] != file['sha256']:
                    raise ValueError(f'The active campaigns require different versions of {name}. Unload one first.')
                targets[name] = (contained_path(self.packages / identity / 'payload', file['source']),
                                 file['sha256'], file['destination'])
        for name in targets:
            if any('/'.join(name.split('/')[:i]) in targets for i in range(1, len(name.split('/')))):
                raise ValueError(f'Active campaigns have a file/folder conflict at {name}.')
        return targets

    @staticmethod
    def _slot(name):
        parts = name.casefold().split('/')
        if parts[:2] != ['maps', 'campaign'] or len(parts) < 3:
            return None
        return {'swarm': 'hots', 'void': 'lotv', 'voidprologue': 'lotv', 'nova': 'nco'}.get(parts[2], 'wol')

    def activate(self, identity, cancel=None):
        with self.lock:
            item = self._package(identity)
            self._change(item['slot'], identity, cancel)
            return f'{item["name"]} loaded.'

    def unload(self, slot, cancel=None, progress=lambda _: None):
        with self.lock:
            status = self.base_statuses()[slot]
            if status['base_state'] == 'stale':
                return self.install_base(slot, cancel, progress)
            self._change(slot, status['base_id'], cancel)
            return f'{SLOTS[slot][0]} restored.'

    def _change(self, slot, identity, cancel):
        self._require_root()
        if game_running():
            raise ValueError('Close StarCraft II before changing campaign files.')
        if slot not in SLOTS:
            raise ValueError('Unknown campaign slot.')
        self.recover()
        state = self._state()
        marker = self.game_path(SLOTS[slot][1] + '/metadata.txt')
        if slot not in state['active'] and marker.exists():
            raise ValueError('This slot was loaded outside this launcher. Unload it in the original CCM first.')
        updated = copy.deepcopy(state)
        # Older completed installations have no original hashes. Adopt their current
        # backup bytes once; new installations retain the hash from capture time.
        for name, backup in state['originals'].items():
            if backup:
                path = contained_path(self.directory, backup)
                digest = file_hash(path, cancel) if path.is_file() else None
                expected = state['original_hashes'].get(name, digest)
                if digest is None or digest != expected:
                    raise ValueError('An original campaign backup is missing or damaged. Keep the files and records intact.')
                updated['original_hashes'][name] = digest
        if identity:
            updated['active'][slot] = identity
        else:
            updated['active'].pop(slot, None)
        targets = self._targets(updated['active'])
        for source, sha, _ in targets.values():
            if file_hash(source, cancel) != sha:
                raise ValueError('An imported campaign file changed. Revert to base and remove its stored copy, then import it again.')
        for name, expected in state['files'].items():
            path = self.game_path(name)
            if (path.is_file() and expected is None) or (expected is not None and
                    (not path.is_file() or file_hash(path, cancel) != expected)):
                raise ValueError(f'{name} changed outside the launcher. Restore it before switching campaigns.')
        folder = self.game_path('Maps/Campaign')
        if slot in state['active'] and folder.is_dir():
            for path in walk_files(folder):
                name = path.relative_to(self.root).as_posix().casefold()
                if self._slot(name) == slot and name not in state['files']:
                    raise ValueError(f'{name} was added outside the launcher. Move it aside before switching.')
        original_paths = {}
        names = set(state['originals']) | set(targets)
        if identity and slot not in state['active']:
            # Save the complete loose-map slot so leftover maps cannot leak into the replacement.
            folder = self.game_path('Maps/Campaign')
            if folder.is_dir():
                for path in walk_files(folder):
                    name = path.relative_to(self.root).as_posix().casefold()
                    if self._slot(name) == slot:
                        names.add(name)
                        original_paths[name] = path.relative_to(self.root).as_posix()
        self.directory.mkdir(parents=True, exist_ok=True)
        transaction = 'transactions/' + uuid.uuid4().hex
        before = {}
        before_hashes = {}
        after_hashes = {}
        desired = {}
        paths = {name: state['paths'].get(name, targets[name][2] if name in targets else original_paths.get(name, name)) for name in names}
        for index, name in enumerate(sorted(names)):
            check_cancel(cancel)
            path = self.game_path(paths[name])
            paths[name] = path.relative_to(self.root).as_posix()
            updated['paths'][name] = paths[name]
            if path.exists() and not (path.is_file() or path.is_dir()):
                raise ValueError(f'Unsupported campaign path: {name}')
            backup = f'{transaction}/{index}' if path.is_file() else None
            before_hashes[name] = file_hash(path, cancel) if backup else None
            if backup:
                captured = signature(path)
                copy_file(path, contained_path(self.directory, backup), cancel, expected_sha=before_hashes[name])
                if signature(path) != captured or file_hash(path, cancel) != before_hashes[name]:
                    raise ValueError(f'{name} changed while preparing the switch. No campaign files were changed.')
            before[name] = backup
            if name not in updated['originals']:
                updated['originals'][name] = backup
                if backup:
                    updated['original_hashes'][name] = before_hashes[name]
            if name in targets:
                desired[name] = targets[name][0]
                after_hashes[name] = targets[name][1]
                updated['files'][name] = targets[name][1]
            elif self._slot(name) in updated['active']:
                desired[name] = None
                after_hashes[name] = None
                updated['files'][name] = None
            else:
                original = updated['originals'].pop(name)
                desired[name] = contained_path(self.directory, original) if original else None
                after_hashes[name] = updated['original_hashes'].pop(name, None)
                updated['files'].pop(name, None)
                updated['paths'].pop(name, None)
        for name, source in desired.items():
            path = self.game_path(name)
            if source is not None and path.is_dir():
                for child in walk_files(path):
                    child_name = child.relative_to(self.root).as_posix().casefold()
                    if child_name not in desired or desired[child_name] is not None:
                        raise ValueError(f'A file/folder conflict prevents switching: {name}')
        check_cancel(cancel)
        for name, digest in before_hashes.items():
            path = self.game_path(paths[name])
            if (file_hash(path, cancel) if path.is_file() else None) != digest:
                raise ValueError(f'{name} changed while preparing the switch. No campaign files were changed.')
            if name in state['files'] and digest != state['files'][name]:
                raise ValueError(f'{name} changed outside the launcher. Restore it before switching campaigns.')
        temporary_prefix = '.ccm-' + transaction.split('/')[1] + '-'
        atomic_json(self.journal, {'format': 2, 'before': before, 'state': state, 'paths': paths,
                                  'before_hashes': before_hashes, 'after_hashes': after_hashes,
                                  'temporary_prefix': temporary_prefix})
        try:
            for name in sorted(desired, key=lambda n: -n.count('/')):
                check_cancel(cancel)
                target = self.game_path(paths[name])
                if desired[name] is None and target.is_file():
                    target.unlink()
                    prune_empty(self.root, target.parent)
            for name, source in desired.items():
                if source is not None:
                    target = self.game_path(paths[name])
                    if target.is_dir():
                        target.rmdir()
                    copy_file(source, target, cancel, prefix=temporary_prefix, expected_sha=after_hashes[name])
            atomic_json(self.state_path, updated)
            self.journal.unlink()
        except BaseException:
            self.recover()
            raise
        self._clean_transactions(updated)

    def remove(self, identity):
        with self.lock:
            self.recover()
            item = self._package(identity)
            if identity in self._state()['active'].values():
                raise ValueError('Revert to base before removing this campaign.')
            path = contained_path(self.packages, identity)
            shutil.rmtree(path)
            return f'{item["name"]} removed from the library.'

    def check_campaign_files(self, paths):
        with self.lock:
            if self.journal.exists():
                raise ValueError('Finish CCM recovery before using other campaign files.')
            managed = self._state()['files']
            for path in paths:
                name = path.relative_to(self.root).as_posix().casefold()
                if any(name == key or name.startswith(key + '/') or key.startswith(name + '/') for key in managed):
                    raise ValueError(f'{name} is managed by a loaded CCM campaign. Revert that campaign to base first.')

    def cards(self):
        cards = []
        for item in self.list_packages():
            if item.get('base_campaign'):
                continue
            cards.append({**item, 'slug': 'ccm-' + item['id'], 'type': 'custom', 'source': 'Local',
                          'status': 'installed', 'removable': not item['active'], 'maps': [], 'mods': [],
                          'has_launcher': False, 'updated_at': item.get('updated_at', '')})
        return cards
