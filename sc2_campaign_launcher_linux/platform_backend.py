"""Platform directories, runner discovery, and launch arguments."""

import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .library import is_sc2_root

MANAGED_PROTON = '__umu__'


def xdg_directory(variable: str, fallback: Path) -> Path:
    value = Path(os.environ.get(variable, ''))
    return value if value.is_absolute() else fallback


def steam_roots() -> list[Path]:
    home = Path.home()
    return [home / '.local/share/Steam', home / '.steam/steam', home / '.steam/root',
            home / '.var/app/com.valvesoftware.Steam/.local/share/Steam']


def steam_libraries() -> list[Path]:
    libraries = list(steam_roots())
    for steam in steam_roots():
        manifest = steam / 'steamapps/libraryfolders.vdf'
        try:
            text = manifest.read_text(encoding='utf-8')
            for value in re.findall(r'"path"\s+"((?:\\.|[^"\\])*)"', text):
                path = Path(value.replace('\\\\', '\\'))
                if path.is_absolute():
                    libraries.append(path)
        except (OSError, UnicodeError):
            continue
    return list(dict.fromkeys(libraries))


def fingerprint_runner(path: Path) -> tuple[str, Path] | None:
    if path.is_file():
        if path.name == 'proton':
            return fingerprint_runner(path.parent)
        if path.name in ('wine', 'wine64') and os.access(path, os.X_OK):
            return 'wine', path
        return None
    if (path / 'toolmanifest.vdf').is_file() and (path / 'proton').is_file():
        return 'proton', path
    for binary in ('bin/wine', 'bin/wine64'):
        if (path / binary).is_file() and os.access(path / binary, os.X_OK):
            return 'wine', path / binary
    return None


def discover_runners(custom_paths=()) -> list[dict]:
    home = Path.home()
    found = {MANAGED_PROTON: {'name': 'UMU managed Proton', 'path': MANAGED_PROTON, 'type': 'proton'}}
    roots = [
        xdg_directory('XDG_DATA_HOME', home / '.local/share') / 'umu',
        home / '.local/share/Steam/compatibilitytools.d',
        home / '.local/share/lutris/runners/wine',
        home / '.var/app/net.lutris.Lutris/data/lutris/runners/wine',
        Path('/usr/share/steam/compatibilitytools.d'),
    ]
    for base in (home / '.config/heroic', home / '.var/app/com.heroicgameslauncher.hgl/config/heroic'):
        roots.extend(base / p for p in ('tools/wine', 'tools/proton', 'proton/proton'))
    for steam in steam_roots():
        roots.append(steam / 'compatibilitytools.d')
    roots.extend(path / 'steamapps/common' for path in steam_libraries())

    def consider(path):
        try:
            entry = fingerprint_runner(path)
            if entry:
                kind, runner = entry
                key = str(runner.resolve())
                name = runner.parent.parent.name if kind == 'wine' and runner.parent.name == 'bin' else path.name
                found[key] = {'name': name, 'path': str(runner), 'type': kind}
        except OSError:
            pass

    for root in dict.fromkeys(roots):
        try:
            for child in root.iterdir():
                consider(child)
        except OSError:
            continue
    if binary := shutil.which('wine'):
        consider(Path(binary))
    for path in custom_paths:
        consider(Path(path).expanduser())
    versions = sorted((v for k, v in found.items() if k != MANAGED_PROTON),
                      key=lambda v: (v['type'] != 'proton', v['name'].casefold()))
    return [found[MANAGED_PROTON], *versions]


def prefix_from_root(root: Path) -> str:
    for parent in (root, *root.parents):
        if parent.name == 'drive_c':
            return str(parent.parent)
    return ''


def validate_root(value: str) -> Path:
    if not value.strip():
        raise ValueError('Choose the StarCraft II installation directory.')
    path = Path(value).expanduser()
    if not path.is_absolute() or not is_sc2_root(path):
        raise ValueError('The directory must contain Support64/SC2Switcher_x64.exe.')
    return path.resolve()


def validate_prefix(value: str) -> Path:
    if not value.strip():
        raise ValueError('Choose the Wine prefix containing this StarCraft II installation.')
    path = Path(value).expanduser()
    if not path.is_absolute() or not (path / 'drive_c').is_dir():
        raise ValueError('The Wine prefix must be an absolute directory containing drive_c.')
    return path.resolve()


def windows_map_path(path: Path, prefix: Path) -> str:
    path = path.resolve()
    drive_c = (prefix / 'drive_c').resolve()
    if path.is_relative_to(drive_c):
        return 'C:\\' + str(path.relative_to(drive_c)).replace('/', '\\')
    try:
        drives = sorted((prefix / 'dosdevices').iterdir())
    except OSError:
        drives = []
    for drive in drives:
        if re.fullmatch('[a-zA-Z]:', drive.name) and drive.is_symlink():
            target = drive.resolve()
            if target.is_dir() and path.is_relative_to(target):
                return drive.name.upper() + '\\' + str(path.relative_to(target)).replace('/', '\\')
    raise ValueError('The map is outside the prefix and has no Wine drive mapping. '
                     'Add a drive mapping in winecfg or choose the matching prefix.')


@dataclass(frozen=True)
class LaunchOptions:
    root: Path
    runner: str = MANAGED_PROTON
    prefix: str = ''
    umu: str = ''


class LinuxBackend:
    name = 'linux'
    needs_runner_selection = True
    needs_prefix_settings = True

    def data_dir(self) -> Path:
        return xdg_directory('XDG_DATA_HOME', Path.home() / '.local/share') / 'SC2CampaignLauncher'

    def cache_dir(self) -> Path:
        return xdg_directory('XDG_CACHE_HOME', Path.home() / '.cache') / 'SC2CampaignLauncher'

    def sc2_quick_roots(self) -> list[Path]:
        home = Path.home()
        roots = [home / 'Games', home / '.wine', self.data_dir().parent / 'umu',
                 home / '.var/app/com.usebottles.bottles/data/bottles/bottles',
                 home / '.local/share/bottles/bottles']
        for library in steam_libraries():
            roots.extend((library / 'steamapps/common', library / 'steamapps/compatdata'))
        return list(dict.fromkeys(roots))

    def command(self, options: LaunchOptions, map_path: Path) -> tuple[list[str], dict]:
        root = validate_root(str(options.root))
        prefix = validate_prefix(options.prefix)
        env = dict(os.environ)
        env['WINEPREFIX'] = str(prefix)
        switcher = str(root / 'Support64/SC2Switcher_x64.exe')
        map_arg = windows_map_path(map_path, prefix)
        if options.runner == MANAGED_PROTON:
            runner = None
        else:
            runner = fingerprint_runner(Path(options.runner).expanduser())
            if runner is None:
                raise ValueError('The selected Wine or Proton version is missing or incomplete.')
        if runner and runner[0] == 'wine':
            for name in ('PROTONPATH', 'PROTON_VERB', 'STEAM_COMPAT_DATA_PATH'):
                env.pop(name, None)
            return [str(runner[1]), switcher, '-run', map_arg], env
        umu = options.umu or shutil.which('umu-run')
        if not umu:
            raise ValueError('umu-run was not found. Install UMU or set its executable in Settings.')
        umu_path = Path(umu).expanduser()
        if not umu_path.is_file() or not os.access(umu_path, os.X_OK):
            raise ValueError(f'UMU is not executable: {umu_path}')
        if runner:
            env['PROTONPATH'] = str(runner[1].resolve())
        else:
            env.pop('PROTONPATH', None)
        env.update(PROTON_VERB='run', GAMEID='umu-default')
        return [str(umu_path), switcher, '-run', map_arg], env


class WindowsBackend:
    name = 'win32'
    needs_runner_selection = False
    needs_prefix_settings = False

    def data_dir(self) -> Path:
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))
        return base / 'SC2CampaignLauncher'

    def cache_dir(self) -> Path:
        return self.data_dir() / 'cache'

    def sc2_quick_roots(self) -> list[Path]:
        roots = [Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)')),
                 Path(os.environ.get('PROGRAMFILES', r'C:\Program Files'))]
        return [path / 'StarCraft II' for path in roots]

    def command(self, options: LaunchOptions, map_path: Path) -> tuple[list[str], dict]:
        root = validate_root(str(options.root))
        return [str(root / 'Support64/SC2Switcher_x64.exe'), '-run', str(map_path.resolve())], dict(os.environ)


def get_backend():
    platform = os.environ.get('SC2CL_PLATFORM', sys.platform)
    return WindowsBackend() if platform == 'win32' else LinuxBackend()
