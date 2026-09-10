"""Install application files and remove only unchanged files recorded at installation."""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'sc2_campaign_launcher_linux'

from .files import atomic_bytes, atomic_json, contained_path, file_hash, prune_empty, read_json

APP = 'SC2CampaignLauncher'


def env_dir(name, fallback):
    value = Path(os.environ.get(name, ''))
    return value if value.is_absolute() else fallback


@dataclass(frozen=True)
class Locations:
    home: Path
    data: Path
    config: Path

    @classmethod
    def current(cls):
        home = Path.home()
        return cls(home, env_dir('XDG_DATA_HOME', home / '.local/share'),
                   env_dir('XDG_CONFIG_HOME', home / '.config'))

    @property
    def state(self):
        return self.config / APP / 'installations.json'


def desktop_argument(value):
    if '\n' in value or '\r' in value or '\x00' in value:
        raise ValueError('Desktop arguments cannot contain line breaks or NUL bytes.')
    escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
    return '"' + escaped.replace('\\', '\\\\').replace('%', '%%') + '"'


def desktop_entry(name, args, icon, terminal=False):
    return ('[Desktop Entry]\nType=Application\n'
            f'Name={name}\nExec={" ".join(desktop_argument(str(arg)) for arg in args)}\n'
            f'Icon={icon}\nTerminal={str(terminal).lower()}\nCategories=Game;\n'
            'Keywords=StarCraft;SC2;Campaign;Launcher;\n').encode('utf-8')


class Installer:
    def __init__(self, locations=None):
        self.locations = locations or Locations.current()
        self.records = read_json(self.locations.state, {'schema': 1, 'installations': {}})
        if (not isinstance(self.records, dict) or self.records.get('schema') != 1
                or not isinstance(self.records.get('installations'), dict)):
            raise ValueError(f'Invalid installation records: {self.locations.state}')
        for scope, record in self.installations().items():
            if (scope not in ('local', 'custom') or not isinstance(record, dict)
                    or not isinstance(record.get('directory'), str)
                    or not Path(record['directory']).is_absolute()
                    or any(not isinstance(record.get(key), dict) for key in ('files', 'external'))):
                raise ValueError(f'Invalid {scope} installation record.')
            for group in ('files', 'external'):
                root = Path(record['directory']) if group == 'files' else self.locations.data
                for relative, entry in record[group].items():
                    contained_path(root, relative)
                    if (not isinstance(entry, dict) or not isinstance(entry.get('sha256'), str)
                            or not re.fullmatch('[0-9a-f]{64}', entry['sha256'])):
                        raise ValueError(f'Invalid file record for {relative}.')
                    if group == 'external' and entry.get('previous') is not None:
                        previous = entry['previous']
                        if not isinstance(previous, str):
                            raise ValueError(f'Invalid backup for {relative}.')
                        base64.b64decode(previous, validate=True)

    def installations(self):
        return self.records['installations']

    def _save(self):
        atomic_json(self.locations.state, self.records)

    def _target(self, scope, directory):
        if scope == 'local':
            target = self.locations.data / APP / 'app'
        elif scope == 'custom' and directory is not None:
            parent = Path(directory).expanduser()
            if not parent.is_absolute():
                raise ValueError('The custom parent directory must be an absolute path.')
            target = parent / APP
        else:
            raise ValueError('Choose local or provide a custom parent directory.')
        if target.is_symlink():
            raise ValueError('The application directory cannot be a symbolic link.')
        target = target.resolve()
        forbidden = {Path(target.anchor), self.locations.home.resolve(), self.locations.data.resolve(),
                     self.locations.config.resolve()}
        if target in forbidden:
            raise ValueError('Choose a dedicated application directory.')
        existing = self.installations().get(scope)
        if existing and Path(existing['directory']) != target:
            raise ValueError('Remove the previous installation in this scope before choosing another directory.')
        for other_scope, record in self.installations().items():
            other = Path(record['directory'])
            if other_scope != scope and (target == other or target.is_relative_to(other) or other.is_relative_to(target)):
                raise ValueError('Application installations cannot overlap.')
        return target

    def install(self, source: Path, scope='local', directory=None, python=None):
        source = source.resolve()
        target = self._target(scope, directory)
        package = source / 'sc2_campaign_launcher_linux'
        assets = source / 'assets'
        if not (package / 'sc2_campaign_launcher_linux.py').is_file() or not (assets / 'logo.png').is_file():
            raise ValueError('The source checkout is incomplete.')
        python = Path(python or sys.executable).resolve()
        if not python.is_file():
            raise ValueError('The selected Python executable does not exist.')
        original = self.installations().get(scope, {'directory': str(target), 'files': {}, 'external': {}})
        record = json.loads(json.dumps(original))
        marker = {'application': APP, 'scope': scope, 'schema': 1}
        payload = {'.installation.json': (json.dumps(marker) + '\n').encode()}
        for file in sorted(package.glob('*.py')):
            payload[f'sc2_campaign_launcher_linux/{file.name}'] = file.read_bytes()
        for file in sorted(assets.iterdir()):
            if file.is_file() and file.suffix.lower() in ('.png', '.ico'):
                payload[f'assets/{file.name}'] = file.read_bytes()
        for name in ('LICENSE', 'README.md'):
            if (source / name).is_file():
                payload[name] = (source / name).read_bytes()
        if target.exists() and not original['files']:
            if any(target.iterdir()):
                raise ValueError(f'{target} is not empty. Choose another parent directory.')
        for relative in payload:
            path = contained_path(target, relative)
            old = original['files'].get(relative)
            if path.exists() and (not old or not path.is_file() or file_hash(path) != old['sha256']):
                raise ValueError(f'An installed file has been changed: {path}. Keep a copy before reinstalling.')
        target.mkdir(parents=True, exist_ok=True)
        self.installations()[scope] = record
        for relative, data in payload.items():
            path = contained_path(target, relative)
            new_parent = not path.parent.exists()
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                atomic_bytes(path, data)
            except OSError:
                if new_parent:
                    prune_empty(target, path.parent)
                raise
            record['files'][relative] = {'sha256': file_hash(path)}
            self._save()
        desktop_name = 'sc2-campaign-launcher' + ('-custom' if scope == 'custom' else '')
        entrypoint = target / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py'
        uninstall = target / 'sc2_campaign_launcher_linux/installer.py'
        icon = f'sc2-campaign-launcher-{scope}'
        entries = {
            f'applications/{desktop_name}.desktop': desktop_entry(
                'SC2 Campaign Launcher' + (' (custom)' if scope == 'custom' else ''),
                ['env', f'SC2CL_DESKTOP_FILE={desktop_name}', python, '-B', entrypoint], icon),
            f'applications/{desktop_name}-uninstall.desktop': desktop_entry(
                f'Uninstall SC2 Campaign Launcher ({scope})',
                [python, '-B', uninstall, '--uninstall', scope], 'system-software-install', True),
            f'icons/hicolor/48x48/apps/{icon}.png': (assets / 'logo.png').read_bytes(),
        }
        for relative, data in entries.items():
            path = contained_path(self.locations.data, relative)
            old = record['external'].get(relative)
            if path.exists() and not path.is_file():
                raise ValueError(f'Expected a file at {path}')
            if path.exists() and (not old or file_hash(path) != old['sha256']):
                previous = base64.b64encode(path.read_bytes()).decode('ascii')
            else:
                previous = old.get('previous') if old else None
            atomic_bytes(path, data)
            record['external'][relative] = {'sha256': file_hash(path), 'previous': previous}
            self._save()
        self._replace_legacy_helper()
        return target

    def _replace_legacy_helper(self):
        helper = contained_path(self.locations.home, '.local/bin/install-uninstall-SC2CLL.sh')
        if not helper.is_file():
            return
        wrapper = (
            '#!/usr/bin/env python3\n'
            'import json\nimport os\nimport sys\nfrom pathlib import Path\n'
            f'state = Path({str(self.locations.state)!r})\n'
            "try:\n    records = json.loads(state.read_text())['installations']\n"
            "except (OSError, ValueError, KeyError):\n    records = {}\n"
            "for record in records.values():\n"
            "    script = Path(record['directory']) / 'sc2_campaign_launcher_linux/installer.py'\n"
            "    if script.is_file():\n        os.execv(sys.executable, [sys.executable, '-B', str(script), *sys.argv[1:]])\n"
            "print('No current installation is recorded. Run the installer from a current source checkout.')\n"
        ).encode()
        if helper.read_bytes() == wrapper:
            return
        backup = self.locations.config / APP / f'legacy-uninstaller-{file_hash(helper)[:16]}.txt'
        atomic_bytes(backup, helper.read_bytes())
        atomic_bytes(helper, wrapper)
        helper.chmod(0o755)

    def uninstall(self, scope):
        record = self.installations().get(scope)
        if record is None:
            raise ValueError(f'No recorded {scope} installation exists.')
        root = Path(record['directory'])
        if not root.is_absolute() or root.is_symlink():
            raise ValueError('The installation directory has changed.')
        marker_path = contained_path(root, '.installation.json')
        marker = read_json(marker_path)
        if marker != {'application': APP, 'scope': scope, 'schema': 1}:
            raise ValueError('The installation marker is missing or invalid. No files were removed.')
        kept, failures = [], []
        # Leave the uninstaller available when another file cannot be removed.
        support = {'sc2_campaign_launcher_linux/' + name for name in
                   ('installer.py', 'files.py', '__init__.py')}

        def remove_files(relatives):
            for relative in relatives:
                entry = record['files'][relative]
                try:
                    path = contained_path(root, relative)
                    if path.exists():
                        if path.is_file() and file_hash(path) == entry['sha256']:
                            path.unlink()
                            prune_empty(root, path.parent)
                        else:
                            kept.append(str(path))
                    del record['files'][relative]
                    self._save()
                except (OSError, ValueError) as error:
                    failures.append(str(error))

        remove_files([key for key in record['files'] if key != '.installation.json' and key not in support])
        for relative, entry in list(record['external'].items()):
            if failures and relative.endswith('-uninstall.desktop'):
                continue
            try:
                path = contained_path(self.locations.data, relative)
                if path.is_file() and file_hash(path) == entry['sha256']:
                    if entry.get('previous') is not None:
                        atomic_bytes(path, base64.b64decode(entry['previous'], validate=True))
                        kept.append(str(path))
                    else:
                        path.unlink()
                elif path.exists():
                    kept.append(str(path))
                del record['external'][relative]
                self._save()
            except (OSError, ValueError) as error:
                failures.append(str(error))
        if failures:
            raise OSError('Some files could not be removed. Run uninstall again.\n' + '\n'.join(failures))
        remove_files([key for key in record['files'] if key in support])
        if failures:
            raise OSError('Uninstaller files could not be removed. Retry from a current source checkout.\n'
                          + '\n'.join(failures))
        marker_path.unlink()
        del self.installations()[scope]
        self._save()
        prune_empty(root.parent, root)
        return kept

    def legacy_paths(self):
        paths = []
        local = self.locations.home / '.local/share/SC2CampaignLauncher'
        if (local / 'sc2_campaign_launcher_linux.py').is_file():
            paths.append(local)
        legacy_state = self.locations.home / '.config/SC2CampaignLauncher/install_path'
        try:
            saved = Path(legacy_state.read_text().splitlines()[0])
            if saved.is_absolute() and (saved / 'sc2_campaign_launcher_linux.py').is_file():
                paths.append(saved)
        except (OSError, IndexError):
            pass
        return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', choices=('local', 'custom'))
    parser.add_argument('--directory', type=Path, help='Parent directory for a custom installation')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--uninstall', choices=('local', 'custom', 'both'))
    parser.add_argument('--yes', action='store_true', help='Skip the uninstall confirmation')
    args = parser.parse_args()
    try:
        installer = Installer()
        if not args.install and not args.uninstall:
            print('SC2 Campaign Launcher')
            for scope, record in installer.installations().items():
                print(f'{scope}: {record["directory"]}')
            for legacy in installer.legacy_paths():
                print(f'Older installation found at {legacy}. Its files will be kept.')
            print('1. Install or update local\n2. Install or update custom\n3. Uninstall')
            choice = input('Choose [1-3]: ').strip()
            if choice == '1':
                args.install = 'local'
            elif choice == '2':
                args.install = 'custom'
            elif choice == '3':
                args.uninstall = input('Scope to remove [local/custom/both]: ').strip()
            else:
                return
        if args.install:
            check = subprocess.run([sys.executable, '-c',
                                    'from PyQt6.QtWidgets import QApplication; '
                                    'from PyQt6.QtCore import PYQT_VERSION; assert PYQT_VERSION >= 0x060203'],
                                   capture_output=True, check=False)
            if check.returncode:
                raise ValueError('PyQt6 6.2.3 or newer could not be loaded. Run install-uninstall-SC2CLL.sh --install local '
                                 'or install PyQt6 in your Python environment first.')
            if args.install == 'custom' and args.directory is None:
                args.directory = Path(input(f'Parent directory (the app will use a {APP} subdirectory): ').strip())
            target = installer.install(args.source, args.install, args.directory)
            print(f'Installed at {target}')
        elif args.uninstall:
            if not args.yes and input(f'Remove the {args.uninstall} installation? [y/N]: ').strip().lower() != 'y':
                return
            scopes = list(installer.installations()) if args.uninstall == 'both' else [args.uninstall]
            for scope in scopes:
                kept = installer.uninstall(scope)
                print(f'Removed {scope} installation.')
                if kept:
                    print('Kept pre-existing or changed files:\n' + '\n'.join(kept))
            print('Campaigns, settings, and download records were kept.')
    except (OSError, ValueError, EOFError) as error:
        print(f'Error: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
