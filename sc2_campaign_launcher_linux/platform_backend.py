# platform_backend.py
import sys
from pathlib import Path
from PyQt6.QtCore import QProcess, QProcessEnvironment


class LinuxBackend:
    name = 'linux'

    # ---- directories ----
    def data_dir(self) -> Path:       # installed assets / app data
        return Path.home() / '.local' / 'share' / 'SC2CampaignLauncher'

    def cache_dir(self) -> Path:      # asset cache
        return Path.home() / '.cache' / 'SC2CampaignLauncher'

    # ---- SC2 discovery ----
    def sc2_quick_roots(self) -> list[Path]:
        home = Path.home()
        return [
            home / '.local' / 'share' / 'Steam' / 'steamapps' / 'common',
            home / '.steam' / 'steam' / 'steamapps' / 'common',
            home / '.local' / 'share' / 'umu',
            home / '.wine',
            home / 'Games',
        ]

    # ---- launching ----
    def launch_sc2(self, settings, map_path: Path, parent) -> QProcess:
        """Start SC2Switcher via umu-run. Returns the QProcess."""
        proc = QProcess(parent)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert('WINEPREFIX', settings.wine_prefix())
        env.insert('PROTONPATH', _strip_trailing_proton(settings.proton_path()))
        env.insert('PROTON_VERB', 'run')
        env.insert('GAMEID', 'umu-default')
        proc.setProcessEnvironment(env)
        proc.start('/usr/bin/umu-run', [str(settings.sc2_switcher_path()),
                                        '-run', f'Z:{str(map_path).replace("/", chr(92))}'])
        return proc

    # ---- feature flags for UI ----
    needs_runner_selection = True     # show Wine/Proton combo + wizard page 2
    needs_prefix_settings = True

class WindowsBackend:
    name = 'win32'
    needs_runner_selection = False
    needs_prefix_settings = False

    def data_dir(self) -> Path:
        # QStandardPaths handles registry-backed roaming dirs properly
        from PyQt6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation)
        return Path(base)

    def cache_dir(self) -> Path:
        return self.data_dir() / 'cache'

    def sc2_quick_roots(self) -> list[Path]:
        return [
            Path(r'C:\Program Files (x86)\StarCraft II'),
            Path(r'C:\Program Files (x86)\Steam') / 'steamapps' / 'common',
            Path(os.environ.get('PROGRAMFILES(X86)', r'C:\Program Files (x86)'))
                / 'Blizzard' / 'StarCraft II',
        ]

    def launch_sc2(self, settings, map_path: Path, parent) -> QProcess:
        proc = QProcess(parent)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)
        proc.start(str(settings.sc2_switcher_path()), ['-run', str(map_path)])
        return proc

_BACKENDS = {'linux': LinuxBackend, 'win32': WindowsBackend}

def get_backend():
    return _BACKENDS[detect_platform()]()
