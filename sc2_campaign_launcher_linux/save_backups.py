"""Verified account backups and recoverable restores for SC2 saves and banks."""

import hashlib
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .ccm import OperationLock, copy_file, walk_files
from .files import atomic_json, check_cancel, contained_path, file_hash, read_json, signature
from .game_state import game_running
from .progress_metadata import validate_scope

MAX_BYTES = 16 * 1024**3


class SaveBackups:
    def __init__(self, profile, state_dir):
        profile = Path(profile).expanduser().absolute()
        if profile.name.casefold() != 'saves' or not profile.parent.is_dir():
            raise ValueError('Choose an account’s Saves folder inside StarCraft II/Accounts.')
        self.root = profile.parent.resolve()
        self.profile = contained_path(self.root, profile.name)
        key = hashlib.sha256(os.path.normcase(str(self.profile)).encode()).hexdigest()
        self.directory = Path(state_dir) / 'save-backups' / key
        if self.directory.resolve().is_relative_to(self.root):
            raise ValueError('Store backups outside the account’s game folders.')
        self.journal = self.directory / 'pending.json'
        self.lock = OperationLock(self.directory / 'operation.lock')

    def _idle(self):
        if game_running():
            raise ValueError('Close StarCraft II before backing up or restoring progress.')

    def _files(self, scope=None):
        if scope is not None:
            result = {}
            total = 0
            for name in validate_scope(scope)['banks']:
                path = self.root
                for part in ('Banks/' + name).split('/'):
                    matches = [p for p in path.iterdir() if p.name.casefold() == part.casefold()] if path.is_dir() else []
                    if len(matches) > 1:
                        raise ValueError(f'Ambiguous bank path: {name}')
                    path = contained_path(self.root, ((matches[0] if matches else path / part)
                                                     .relative_to(self.root).as_posix()))
                if path.is_file():
                    total += path.stat().st_size
                    if total > MAX_BYTES:
                        raise ValueError('The declared banks exceed the 16 GiB backup limit.')
                    relative = path.relative_to(self.root).as_posix()
                    result[relative.casefold()] = (relative, path)
                elif path.exists():
                    raise ValueError(f'A declared bank path is a folder: {name}')
            return result
        result = {}
        total = 0
        for name in ('Saves', 'Banks'):
            matches = [p for p in self.root.iterdir() if p.name.casefold() == name.casefold()]
            if len(matches) > 1:
                raise ValueError(f'This account has ambiguous {name} folders.')
            folder = contained_path(self.root, matches[0].name if matches else name)
            if not folder.exists():
                continue
            if not folder.is_dir():
                raise ValueError(f'{folder.name} must be a folder.')
            for path in walk_files(folder):
                relative = path.relative_to(self.root).as_posix()
                total += path.stat().st_size
                if total > MAX_BYTES:
                    raise ValueError('The account’s saves and banks exceed the 16 GiB backup limit.')
                key = relative.casefold()
                if key in result:
                    raise ValueError(f'Ambiguous save or bank filename: {relative}')
                result[key] = (relative, path)
                if len(result) > 10000:
                    raise ValueError('The account exceeds the 10,000-file backup limit.')
        return result

    def _manifest(self, identity):
        if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
            raise ValueError('Invalid backup identifier.')
        folder = contained_path(self.directory, identity)
        value = read_json(contained_path(folder, 'backup.json'))
        if (not isinstance(value, dict) or value.get('id') != identity
                or value.get('profile') != str(self.profile) or not isinstance(value.get('files'), dict)
                or not isinstance(value.get('label'), str) or not isinstance(value.get('created'), str)):
            raise ValueError('The saved-progress backup record is missing or damaged.')
        if len(value['files']) > 10000:
            raise ValueError('The backup contains too many files.')
        names = set()
        scope = validate_scope(value['bank_scope']) if 'bank_scope' in value else None
        owned = {('Banks/' + name).casefold() for name in scope['banks']} if scope else None
        for name, digest in value['files'].items():
            contained_path(self.root, name)
            if name.split('/')[0].casefold() not in ('saves', 'banks') or '/' not in name:
                raise ValueError('A backup contains files outside Saves and Banks.')
            if name.casefold() in names or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise ValueError('A backup contains an invalid file record.')
            names.add(name.casefold())
            if owned is not None and name.casefold() not in owned:
                raise ValueError('A campaign bank backup contains files outside its declared bank list.')
        return value

    def list_backups(self):
        with self.lock:
            return sorted((self._manifest(p.name) for p in self.directory.iterdir()
                           if re.fullmatch('[0-9a-f]{32}', p.name) and p.is_dir()),
                          key=lambda item: item['created'], reverse=True)

    def snapshot(self, label, cancel=None, *, context=None, progress=lambda _: None, scope=None):
        with self.lock:
            self._idle()
            if self.journal.exists():
                raise ValueError('An interrupted save restore needs recovery. Open Saved progress first.')
            scope = validate_scope(scope) if scope is not None else None
            files = self._files(scope)
            self.directory.mkdir(parents=True, exist_ok=True)
            identity = uuid.uuid4().hex
            record = {'id': identity, 'profile': str(self.profile), 'label': label[:200],
                      'created': datetime.now(timezone.utc).isoformat(), 'files': {}, 'context': context or {}}
            if scope is not None:
                record['bank_scope'] = scope
            with tempfile.TemporaryDirectory(prefix='.backup-', dir=self.directory) as temporary:
                stage = Path(temporary)
                for index, (name, path) in enumerate(files.values(), 1):
                    check_cancel(cancel)
                    before = signature(path)
                    target = contained_path(stage, 'payload/' + name)
                    copy_file(path, target, cancel)
                    digest = file_hash(target, cancel)
                    if signature(path) != before or file_hash(path, cancel) != digest:
                        raise ValueError('A save or bank changed while being copied. Close SC2 and try again.')
                    record['files'][name] = digest
                    progress({'operation': 'Backing up', 'index': index, 'count': len(files), 'name': name})
                if self._files(scope) != files:
                    raise ValueError('The account’s files changed during backup. Try again with SC2 closed.')
                for name, path in files.values():
                    if file_hash(path, cancel) != record['files'][name]:
                        raise ValueError('A save or bank changed during backup. Try again with SC2 closed.')
                self._idle()
                check_cancel(cancel)
                atomic_json(stage / 'backup.json', record)
                stage.rename(self.directory / identity)
            return record

    def _verify(self, record, cancel=None):
        for name, digest in record['files'].items():
            path = contained_path(self.directory, record['id'] + '/payload/' + name)
            if not path.is_file() or file_hash(path, cancel) != digest:
                raise ValueError(f'The backup file is missing or damaged: {name}. No restore was started.')

    def restore(self, identity, cancel=None, progress=lambda _: None):
        with self.lock:
            self._idle()
            if self.journal.exists():
                raise ValueError('Recover the interrupted save restore before starting another.')
            record = self._manifest(identity)
            self._verify(record, cancel)
            before = self.snapshot('Before restoring ' + record['label'], cancel, progress=progress,
                                   scope=record.get('bank_scope'))
            current = self._files(record.get('bank_scope'))
            targets = {name: current.get(name.casefold(), (name, None))[0] for name in record['files']}
            prefix = '.save-restore-' + uuid.uuid4().hex + '-'
            check_cancel(cancel)
            self._idle()
            atomic_json(self.journal, {'backup': identity, 'before': before['id'], 'targets': targets,
                                       'temporary_prefix': prefix})
            try:
                for index, (name, target) in enumerate(targets.items(), 1):
                    self._idle()
                    old = before['files'].get(target)
                    path = contained_path(self.root, target)
                    if (file_hash(path) if path.is_file() else None) != old:
                        raise ValueError(f'{target} changed during restore. Keep the backups and recovery record.')
                    self._restore_file(contained_path(self.directory, identity + '/payload/' + name),
                                       path, record['files'][name], cancel, prefix, {old})
                    progress({'operation': 'Restoring', 'index': index, 'count': len(targets), 'name': name})
                self.journal.unlink()
            except BaseException:
                self.recover()
                raise
            return before

    def _restore_file(self, source, target, digest, cancel, prefix, allowed):
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=prefix, dir=target.parent)
        os.close(descriptor)
        temporary = Path(name)
        try:
            copy_file(source, temporary, cancel, prefix=prefix)
            if file_hash(temporary, cancel) != digest:
                raise ValueError('A backup changed during restore. The previous progress will be recovered.')
            self._idle()
            check_cancel(cancel)
            if (file_hash(target) if target.is_file() else None) not in allowed:
                raise ValueError(f'{target.name} changed during restore. Keep the recovery record and backups.')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def recover(self):
        with self.lock:
            self._idle()
            journal = read_json(self.journal)
            if journal is None:
                return
            if not isinstance(journal, dict) or not isinstance(journal.get('targets'), dict):
                raise ValueError('The save recovery record is damaged. Keep it and the backups intact.')
            prefix = journal.get('temporary_prefix', '')
            if not isinstance(prefix, str) or not re.fullmatch(r'\.save-restore-[0-9a-f]{32}-', prefix):
                raise ValueError('The save recovery record has an invalid temporary filename.')
            before = self._manifest(journal['before'])
            after = self._manifest(journal['backup'])
            self._verify(before)
            previous = {name.casefold(): (name, digest) for name, digest in before['files'].items()}
            targets = journal['targets']
            if set(targets) != set(after['files']):
                raise ValueError('The save recovery file list is damaged.')
            # Check every target before restoring any, so external edits remain intact.
            for name, target in targets.items():
                if not isinstance(target, str) or name.casefold() != target.casefold():
                    raise ValueError('The save recovery target is invalid.')
                path = contained_path(self.root, target)
                old = previous.get(name.casefold())
                allowed = {after['files'][name], old[1] if old else None}
                digest = file_hash(path) if path.is_file() else None
                if digest not in allowed:
                    raise ValueError(f'{target} changed after the interrupted restore. Preserve it before recovering.')
            for name, target in targets.items():
                self._idle()
                path = contained_path(self.root, target)
                for temporary in path.parent.glob(prefix + '*'):
                    if temporary.is_file() and not temporary.is_symlink():
                        contained_path(self.root, temporary.relative_to(self.root).as_posix()).unlink()
                if old := previous.get(name.casefold()):
                    self._restore_file(contained_path(self.directory, before['id'] + '/payload/' + old[0]),
                                       path, old[1], None, prefix, {old[1], after['files'][name]})
                elif path.is_file():
                    path.unlink()
            self.journal.unlink()
