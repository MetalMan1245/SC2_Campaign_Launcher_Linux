"""Campaign verification and ownership records for one SC2 installation."""

import hashlib
import os
from pathlib import Path
from threading import Event, RLock

from .catalog import file_key, parse_campaign, raw_campaign
from .files import atomic_json, check_cancel, contained_path, file_hash, prune_empty, read_json, signature
from .network import HttpClient


def is_sc2_root(path: Path) -> bool:
    return (path / 'Support64' / 'SC2Switcher_x64.exe').is_file()


class Library:
    def __init__(self, root: Path, state_dir: Path, client: HttpClient | None = None):
        self.root = root.expanduser().resolve()
        key = hashlib.sha256(os.path.normcase(str(self.root)).encode()).hexdigest()
        self.state_path = state_dir / 'libraries' / f'{key}.json'
        self.client = client or HttpClient()
        self.lock = RLock()
        self.problem = ''
        self.data = {'schema': 1, 'root': str(self.root), 'campaigns': {}, 'files': {}, 'verified': {}}
        try:
            saved = read_json(self.state_path)
            if saved is not None:
                self._validate_state(saved)
                self.data = saved
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.problem = f'Could not read installation records at {self.state_path}: {error}'

    def _validate_state(self, data):
        if (not isinstance(data, dict) or data.get('schema') != 1
                or data.get('root') != str(self.root)
                or any(not isinstance(data.get(k), dict) for k in ('campaigns', 'files', 'verified'))):
            raise ValueError('Invalid installation records.')
        for key, entry in data['files'].items():
            contained_path(self.root, key)
            if (not isinstance(entry, dict) or not isinstance(entry.get('sha256'), str)
                    or not isinstance(entry.get('owned'), bool)
                    or not isinstance(entry.get('campaigns'), list)
                    or any(not isinstance(s, str) for s in entry['campaigns'])):
                raise ValueError('Invalid file ownership record.')
        for slug, campaign in data['campaigns'].items():
            if parse_campaign(campaign)['slug'] != slug:
                raise ValueError('Invalid campaign record.')

    def _save(self):
        if self.problem:
            raise ValueError(self.problem)
        atomic_json(self.state_path, self.data)

    def _require_installation(self):
        if not is_sc2_root(self.root):
            raise ValueError(f'StarCraft II was not found at {self.root}. Check Settings.')
        if self.problem:
            raise ValueError(self.problem)

    def destination(self, campaign: dict, file: dict) -> Path:
        return contained_path(self.root, file_key(campaign, file))

    def _current(self, path: Path, expected: str, cancel: Event, force=False) -> bool:
        if not path.is_file():
            return False
        key = str(path.relative_to(self.root))
        before = signature(path)
        cached = self.data['verified'].get(key)
        if (not force and isinstance(cached, dict) and cached.get('signature') == before
                and cached.get('sha256') == expected):
            return True
        actual = file_hash(path, cancel)
        if before != signature(path):
            return False
        self.data['verified'][key] = {'signature': before, 'sha256': actual}
        return actual == expected

    def recorded_campaigns(self) -> list[dict]:
        with self.lock:
            return [parse_campaign(c) for c in self.data['campaigns'].values()]

    def statuses(self, campaigns: list[dict], cancel: Event, force=False) -> list[dict]:
        with self.lock:
            result = []
            for campaign in campaigns:
                check_cancel(cancel)
                status, error, present = 'installed', '', False
                try:
                    files = campaign['maps'] + campaign['mods']
                    if not campaign['maps']:
                        raise ValueError('No launcher map is available.')
                    for file in files:
                        check_cancel(cancel)
                        path = self.destination(campaign, file)
                        if file in campaign['maps'] and path.is_file():
                            present = True
                        if not path.is_file():
                            status = 'not_installed'
                        elif not self._current(path, file['sha256'], cancel, force):
                            if status != 'not_installed':
                                status = 'update_available'
                except (OSError, ValueError) as failure:
                    status, error = 'error', str(failure)
                managed = any(campaign['slug'] in f['campaigns']
                              for f in self.data['files'].values())
                result.append({**campaign, 'status': status, 'error': error, 'managed': managed,
                               'removable': managed or present})
            if not self.problem:
                try:
                    self._save()
                except OSError:
                    pass  # Verification still reflects disk state when the cache cannot be saved.
            return result

    def install(self, campaign: dict, cancel: Event, progress=lambda _: None) -> str:
        with self.lock:
            self._require_installation()
            # Validate again before accepting a saved or programmatically supplied campaign.
            campaign = parse_campaign(raw_campaign(campaign))
            files = campaign['maps'] + campaign['mods']
            downloaded = 0
            for index, file in enumerate(files, 1):
                check_cancel(cancel)
                destination = self.destination(campaign, file)
                key = file_key(campaign, file)
                existed = destination.exists()
                previous = self.data['files'].get(key, {})
                current = self._current(destination, file['sha256'], cancel, force=True)
                if not current:
                    if existed and not destination.is_file():
                        raise ValueError(f'Expected a file at {destination}')
                    self.client.download(
                        file['url'], destination, file['sha256'], cancel,
                        lambda received, total, file=file, index=index: progress({
                            'name': file['name'], 'index': index, 'count': len(files),
                            'received': received, 'total': total,
                        }),
                        lambda file=file: self.destination(campaign, file),
                    )
                    downloaded += 1
                else:
                    progress({'name': file['name'], 'index': index, 'count': len(files),
                              'received': destination.stat().st_size, 'total': destination.stat().st_size})
                owners = set(previous.get('campaigns', [])) | {campaign['slug']}
                self.data['files'][key] = {
                    'sha256': file['sha256'], 'owned': bool(previous.get('owned')) or not existed,
                    'campaigns': sorted(owners),
                }
                self.data['campaigns'][campaign['slug']] = raw_campaign(campaign)
                self.data['verified'][str(destination.relative_to(self.root))] = {
                    'sha256': file['sha256'], 'signature': signature(destination),
                }
                self._save()
            return f'{campaign["name"]}: {downloaded} file(s) downloaded; all files verified.'

    def remove(self, campaign: dict, catalog: list[dict], cancel: Event) -> str:
        with self.lock:
            self._require_installation()
            campaign = parse_campaign(raw_campaign(campaign))
            slug = campaign['slug']
            candidates = {file_key(campaign, file): {file['sha256']}
                          for file in campaign['maps'] + campaign['mods']}
            for key, record in self.data['files'].items():
                if slug in record['campaigns']:
                    candidates.setdefault(key, set()).add(record['sha256'])
            removed, kept, shared_count = 0, 0, 0
            for key, hashes in candidates.items():
                check_cancel(cancel)
                record = self.data['files'].get(key)
                remaining = [s for s in record['campaigns'] if s != slug] if record else []
                path = contained_path(self.root, key)
                shared = bool(remaining)
                if key.startswith('Mods/') and not shared:
                    shared = self._used_elsewhere(key, slug, catalog)
                if not shared and path.exists():
                    if path.is_file() and file_hash(path, cancel) in hashes:
                        path.unlink()
                        self.data['verified'].pop(str(path.relative_to(self.root)), None)
                        prune_empty(self.root, path.parent)
                        removed += 1
                    else:
                        kept += 1
                elif shared and path.exists():
                    shared_count += 1
                if record and (remaining or shared):
                    record['campaigns'] = remaining
                elif record:
                    del self.data['files'][key]
                self._save()
            self.data['campaigns'].pop(slug, None)
            self._save()
            message = f'Removed {removed} file(s) from {campaign["name"]}.'
            if kept:
                message += f' Kept {kept} modified or unrecognized file(s).'
            if shared_count:
                message += f' Kept {shared_count} shared file(s) used by other campaigns.'
            return message

    def _used_elsewhere(self, key: str, slug: str, catalog: list[dict]) -> bool:
        others = {c['slug']: c for c in self.recorded_campaigns() + catalog}
        for other in others.values():
            if other['slug'] == slug or not any(file_key(other, f) == key for f in other['mods']):
                continue
            try:
                # Partial installations may still need the shared mod when resumed.
                if any(self.destination(other, f).is_file() for f in other['maps']):
                    return True
            except (OSError, ValueError):
                return True
        return False
