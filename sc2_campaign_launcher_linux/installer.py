"""Install application files and remove only unchanged files recorded at installation."""

import argparse
import base64
import json
import os
import re
import shutil
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


def desktop_entry(name, args, icon, terminal=False, uninstall=None, hidden=False):
    text = ('[Desktop Entry]\nType=Application\n'
            f'Name={name}\nExec={" ".join(desktop_argument(str(arg)) for arg in args)}\n'
            f'Icon={icon}\nTerminal={str(terminal).lower()}\nCategories=Game;\n'
            'Keywords=StarCraft;SC2;Campaign;Launcher;\n')
    if hidden:
        text += 'NoDisplay=true\n'
    if uninstall:
        text += ('Actions=uninstall;\n\n[Desktop Action uninstall]\nName=Uninstall\n'
                 f'Exec={" ".join(desktop_argument(str(arg)) for arg in uninstall)}\n'
                 'Icon=edit-delete-remove\n')
    return text.encode('utf-8')


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
        python = Path(python or sys.executable).expanduser().absolute()
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
                ['env', f'SC2CL_DESKTOP_FILE={desktop_name}', python, '-B', entrypoint], icon,
                uninstall=[python, '-B', uninstall, '--uninstall', scope, '--gui']),
            f'applications/{desktop_name}-uninstall.desktop': desktop_entry(
                f'Uninstall SC2 Campaign Launcher ({scope})',
                [python, '-B', uninstall, '--uninstall', scope], 'system-software-install', True, hidden=True),
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
        # Both desktop entries can invoke uninstall; keep them until the icon and app files are removed.
        entries = sorted(record['external'].items(),
                         key=lambda item: (item[0].endswith('.desktop'),
                                           not item[0].endswith('-uninstall.desktop')))
        for relative, entry in entries:
            if failures and relative.endswith('.desktop'):
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


def choose_action(installer, args):
    installed = installer.installations()
    if args.uninstall == 'auto':
        scopes = list(installed)
        if not scopes:
            raise ValueError('No installation is recorded.')
        if len(scopes) == 1:
            args.uninstall = scopes[0]
        else:
            args.uninstall = choose('Remove which installation?', [('Local', 'local'), ('Custom', 'custom'),
                                                                  ('Both', 'both')])
        return
    if args.install or args.uninstall:
        return
    print('SC2 Campaign Launcher')
    for scope, record in installed.items():
        print(f'{scope.title()} installation found at {record["directory"]}')
    if not installed:
        legacy = installer.legacy_paths()
        for path in legacy:
            print(f'Older installation found at {path}. Its files will be kept when upgrading.')
        if len(legacy) == 1:
            local = installer.locations.home / '.local/share/SC2CampaignLauncher'
            args.install = 'local' if legacy[0] == local else 'custom'
            if args.install == 'custom':
                args.directory = legacy[0]
            return
        args.install = choose('Where would you like to install?', [('Local', 'local'), ('Custom', 'custom')])
        return
    options = [(f'Update {scope} installation', ('install', scope)) for scope in installed]
    options += [(f'Uninstall {scope} installation', ('uninstall', scope)) for scope in installed]
    if len(installed) == 2:
        options.append(('Uninstall both', ('uninstall', 'both')))
    else:
        other = 'custom' if 'local' in installed else 'local'
        options.append((f'Also install {other}', ('install', other)))
    action, scope = choose('Choose an action:', options)
    setattr(args, action, scope)


def choose(prompt, options):
    print(prompt)
    for index, (label, _) in enumerate(options, 1):
        print(f'{index}. {label}')
    value = input(f'Choose [1-{len(options)}]: ').strip()
    if not value.isdecimal() or not 1 <= int(value) <= len(options):
        raise ValueError('Invalid selection.')
    return options[int(value) - 1][1]


def qt_available():
    check = subprocess.run([sys.executable, '-c',
                            'from PyQt6.QtWidgets import QApplication; '
                            'from PyQt6.QtCore import PYQT_VERSION; assert PYQT_VERSION >= 0x060203'],
                           capture_output=True, check=False)
    return check.returncode == 0


def dependency_command():
    if sys.prefix != sys.base_prefix:
        return []
    try:
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                       if '=' in line)
    except OSError:
        return []
    distro = release.get('ID', '').strip('"\'')
    if distro in ('arch', 'cachyos', 'endeavouros', 'manjaro', 'garuda'):
        command = ['pacman', '-S', '--needed', 'python-pyqt6']
    elif distro in ('debian', 'ubuntu', 'linuxmint', 'pop'):
        command = ['apt-get', 'install', 'python3-pyqt6']
    elif distro in ('fedora', 'nobara'):
        command = ['dnf', 'install', 'python3-qt6']
    else:
        return []
    return ['sudo', *command] if shutil.which(command[0]) and shutil.which('sudo') else []


def ensure_dependencies():
    if qt_available():
        return
    command = dependency_command()
    if command:
        answer = input(f'Install {command[-1]} using the package manager? [y/N]: ').strip().lower()
        if answer == 'y':
            subprocess.run(command, check=True)
    if not qt_available():
        raise ValueError('PyQt6 6.2.3 or newer could not be loaded in the selected Python environment. '
                         'Install PyQt6 there, then run the installer again. '
                         'See the virtual environment instructions in README.md.')


def gui_uninstall(installer, scope):
    from PyQt6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    if QMessageBox.question(None, 'Uninstall SC2 Campaign Launcher',
                            f'Remove the {scope} installation?\nCampaigns and settings will be kept.') != QMessageBox.StandardButton.Yes:
        return
    try:
        kept = installer.uninstall(scope)
    except (OSError, ValueError) as error:
        QMessageBox.critical(None, 'Uninstall failed', str(error))
        return
    text = 'SC2 Campaign Launcher was uninstalled. Campaigns and settings were kept.'
    if kept:
        text += '\n\nKept changed or pre-existing files:\n' + '\n'.join(kept)
    QMessageBox.information(None, 'Uninstall complete', text)
    app.processEvents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--install', choices=('local', 'custom'))
    parser.add_argument('--directory', type=Path, help='Parent directory for a custom installation')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent.parent)
    actions.add_argument('--uninstall', nargs='?', const='auto', choices=('auto', 'local', 'custom', 'both'))
    parser.add_argument('--yes', action='store_true', help='Skip the uninstall confirmation')
    parser.add_argument('--gui', action='store_true', help='Show desktop uninstall dialogs')
    args = parser.parse_args()
    try:
        installer = Installer()
        if args.gui:
            if args.uninstall not in ('local', 'custom'):
                raise ValueError('A desktop uninstall must name the local or custom installation.')
            gui_uninstall(installer, args.uninstall)
            return
        choose_action(installer, args)
        if args.install:
            ensure_dependencies()
            if args.install == 'custom' and args.directory is None:
                existing = installer.installations().get('custom')
                args.directory = (Path(existing['directory']).parent if existing else
                                  Path(input(f'Parent directory (the app will use a {APP} subdirectory): ').strip()))
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
    except (OSError, ValueError, EOFError, subprocess.CalledProcessError) as error:
        print(f'Error: {error}', file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == '__main__':
    main()
