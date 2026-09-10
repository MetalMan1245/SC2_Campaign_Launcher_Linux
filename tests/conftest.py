import hashlib
import os
from pathlib import Path

import pytest

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from sc2_campaign_launcher_linux.catalog import parse_campaign
from sc2_campaign_launcher_linux.library import Library


@pytest.fixture
def sc2_root(tmp_path):
    root = tmp_path / 'prefix/drive_c/Program Files (x86)/StarCraft II'
    (root / 'Support64').mkdir(parents=True)
    (root / 'Support64/SC2Switcher_x64.exe').write_bytes(b'test switcher')
    return root


@pytest.fixture
def library(sc2_root, tmp_path):
    return Library(sc2_root, tmp_path / 'state')


def campaign(folder='Test', maps=None, mods=None):
    maps = maps if maps is not None else [('Launcher.SC2Map', b'current map')]
    mods = mods if mods is not None else [('Shared.SC2Mod', b'current mod')]
    entries = [{'name': name, 'sha256': hashlib.sha256(data).hexdigest(),
                'url': f'https://raw.githubusercontent.com/test/campaigns/main/{name}'}
               for name, data in maps + mods]
    return parse_campaign({'title': folder, 'folder': folder, 'maps': entries})


def write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture(scope='session')
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app
