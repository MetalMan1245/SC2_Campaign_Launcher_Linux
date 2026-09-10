"""User preferences. Workers receive snapshots rather than QSettings objects."""

import sys
from pathlib import Path

from PyQt6.QtCore import QSettings

from .platform_backend import LaunchOptions, MANAGED_PROTON, get_backend, prefix_from_root


class AppSettings:
    def __init__(self, settings: QSettings | None = None):
        self.settings = settings if settings is not None else QSettings('SC2CampaignLauncher', 'App')
        self.backend = get_backend()

    def sc2_root(self) -> Path:
        return Path(self.settings.value('sc2_root', str(Path.home() / 'Games'), type=str)).expanduser()

    def runner(self) -> str:
        return self.settings.value('wine_binary', MANAGED_PROTON, type=str)

    def auto_prefix(self) -> bool:
        return self.settings.value('use_auto_prefix', True, type=bool)

    def wine_prefix(self) -> str:
        if self.auto_prefix():
            return prefix_from_root(self.sc2_root())
        return self.settings.value('wine_prefix_override', '', type=str)

    def custom_runners(self) -> list[str]:
        return self.settings.value('custom_wine_paths', [], type=list) or []

    def umu(self) -> str:
        return self.settings.value('umu_executable', '', type=str)

    def launch_options(self) -> LaunchOptions:
        return LaunchOptions(self.sc2_root(), self.runner(), self.wine_prefix(), self.umu())

    def save(self, root: Path, runner: str, auto_prefix: bool, prefix: str,
             umu: str, custom_runners: list[str]):
        for key, value in {
            'sc2_root': str(root), 'wine_binary': runner, 'use_auto_prefix': auto_prefix,
            'wine_prefix_override': prefix, 'umu_executable': umu,
            'custom_wine_paths': custom_runners, 'first_run_done': True,
        }.items():
            self.settings.setValue(key, value)
        self.settings.sync()
        if self.settings.status() != QSettings.Status.NoError:
            raise OSError('The settings could not be saved.')

    def is_first_run(self) -> bool:
        return not self.settings.value('first_run_done', False, type=bool)

    def asset_dir(self) -> Path:
        module = Path(__file__).resolve().parent
        candidates = [module / 'assets', module.parent / 'assets']
        if hasattr(sys, '_MEIPASS'):
            candidates.insert(0, Path(sys._MEIPASS) / 'assets')
        custom = self.settings.value('custom_asset_dir', '', type=str)
        if custom:
            candidates.append(Path(custom))
        candidates.extend((self.backend.data_dir() / 'assets',
                           Path(sys.prefix) / 'share/SC2CampaignLauncher/assets'))
        return next((path for path in candidates if (path / 'logo.png').is_file()), candidates[0])
