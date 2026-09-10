import os
import sys
import time
from pathlib import Path
from threading import Event

import pytest
from PyQt6.QtCore import QSettings

from sc2_campaign_launcher_linux import sc2_campaign_launcher_linux as ui
from sc2_campaign_launcher_linux.catalog import CatalogResult
from sc2_campaign_launcher_linux.files import check_cancel
from sc2_campaign_launcher_linux.jobs import JobPool
from sc2_campaign_launcher_linux.launching import LaunchManager, external_environment
from sc2_campaign_launcher_linux.platform_backend import (
    LaunchOptions, LinuxBackend, MANAGED_PROTON, WindowsBackend, prefix_from_root,
)
from sc2_campaign_launcher_linux.settings import AppSettings
from conftest import campaign, write


def spin(qapp, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return
        time.sleep(.005)
    assert condition(), 'Timed out waiting for the Qt operation.'


class FakeCatalog:
    def __init__(self):
        self.calls = 0
        self.error = False
        self.media_threads = []

    def load(self, cancel):
        self.calls += 1
        cancel.wait(.025)
        check_cancel(cancel)
        return CatalogResult([] if self.error else [campaign(mods=[])], 'offline' if self.error else '')

    def details(self, campaign, cancel):
        from PyQt6.QtCore import QThread
        self.media_threads.append(QThread.currentThread())
        return {}

    def artwork(self, campaign, cancel):
        return b''


@pytest.fixture
def settings(tmp_path, sc2_root, monkeypatch):
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    value = AppSettings(QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    value.settings.setValue('sc2_root', str(sc2_root))
    monkeypatch.setattr(value.backend, 'data_dir', lambda: tmp_path / 'state')
    monkeypatch.setattr(value.backend, 'cache_dir', lambda: tmp_path / 'cache')
    return value


@pytest.fixture
def windows(qapp, settings):
    made = []

    def make(**kwargs):
        window = ui.MainWindow(settings, catalog=kwargs.pop('catalog', FakeCatalog()), **kwargs)
        made.append(window)
        return window

    yield make
    for window in made:
        for process, _ in window.launcher.processes.values():
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        window.close()
        spin(qapp, lambda window=window: not window.jobs.busy())


def test_refreshes_are_coalesced_without_destroying_workers(qapp, windows):
    catalog = FakeCatalog()
    window = windows(catalog=catalog)
    for _ in range(50):
        window.load_campaigns()
    spin(qapp, lambda: not window.jobs.busy() and window.fetcher is None)
    assert catalog.calls == 2
    assert len(window.cards) == 1
    assert all(thread != qapp.thread() for thread in catalog.media_threads)


def test_failed_refresh_keeps_installed_cards(qapp, windows):
    catalog = FakeCatalog()
    window = windows(catalog=catalog)
    spin(qapp, lambda: not window.jobs.busy())
    card = window.cards['Test']
    catalog.error = True
    window.load_campaigns()
    spin(qapp, lambda: not window.jobs.busy())
    assert window.cards['Test'] is card
    assert window.notice.text() == 'offline'


def test_window_close_cancels_jobs_before_destruction(qapp, windows):
    window = windows()
    window.show()
    window.close()
    assert window.closing
    spin(qapp, lambda: not window.jobs.busy() and not window.isVisible())


def test_pool_waits_for_thread_finish_before_callback(qapp):
    pool = JobPool(limit=1)
    calls = []
    gate = Event()
    first = pool.submit(lambda cancel, notify: gate.wait(.2), lambda result, error: calls.append('first'))
    second = pool.submit(lambda cancel, notify: 2, lambda result, error: calls.append(result))
    assert first in pool.active
    assert second in pool.queued
    gate.set()
    spin(qapp, lambda: not pool.busy())
    assert calls == ['first', 2]


class PythonBackend:
    def __init__(self, script):
        self.script = script

    def command(self, options, map_path):
        return [sys.executable, '-c', self.script], dict(os.environ)


def test_refresh_and_window_close_leave_game_process_running(qapp, windows, settings, tmp_path):
    stop = tmp_path / 'stop'
    script = ('import time\nfrom pathlib import Path\n'
              f'deadline = time.monotonic() + 10\n'
              f'while not Path({str(stop)!r}).exists() and time.monotonic() < deadline: time.sleep(.02)')
    launcher = LaunchManager(PythonBackend(script), tmp_path / 'logs')
    window = windows(launcher=launcher)
    spin(qapp, lambda: not window.jobs.busy())
    c = window.cards['Test'].campaign
    path = write(window.library.destination(c, c['maps'][0]), b'current map')
    launcher.launch('Test', LaunchOptions(settings.sc2_root()), path)
    process = launcher.processes['Test'][0]
    try:
        window.load_campaigns()
        spin(qapp, lambda: not window.jobs.busy())
        assert process.poll() is None
        window.close()
        qapp.processEvents()
        assert process.poll() is None
    finally:
        stop.touch()
        process.wait(timeout=5)


def test_early_runner_failure_is_reported(qapp, windows, settings, tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(ui, 'show_details', lambda parent, title, text, **kwargs: errors.append(text))
    launcher = LaunchManager(PythonBackend('print("runner error"); raise SystemExit(7)'), tmp_path / 'logs')
    window = windows(launcher=launcher)
    spin(qapp, lambda: not window.jobs.busy())
    c = window.cards['Test'].campaign
    path = write(window.library.destination(c, c['maps'][0]), b'current map')
    launcher.launch('Test', LaunchOptions(settings.sc2_root()), path)
    spin(qapp, lambda: bool(errors))
    assert 'code 7' in errors[0]
    assert 'runner error' in errors[0]
    assert window.cards['Test'].play.isEnabled()


def test_root_validation_applies_to_setup_and_settings(qapp, settings):
    for first_run in (False, True):
        pool = JobPool()
        dialog = ui.SettingsDialog(settings, pool, first_run=first_run)
        dialog.sc2_in.clear()
        dialog._save()
        assert 'Choose' in dialog.status.text()
        assert dialog.result() != ui.QDialog.DialogCode.Accepted
        dialog.reject()


def test_portable_assets_resolve_from_checkout(settings):
    assert (settings.asset_dir() / 'logo.png').is_file()


def test_prefix_detection_uses_a_whole_component():
    root = Path('/games/not_drive_c_folder/prefix/drive_c/StarCraft II')
    assert prefix_from_root(root) == str(Path('/games/not_drive_c_folder/prefix'))
    assert prefix_from_root(Path('/games/not_drive_c_folder/StarCraft II')) == ''


@pytest.mark.skipif(os.name == 'nt', reason='POSIX executable and prefix paths')
def test_plain_wine_uses_wine_and_umu_is_resolved_on_path(sc2_root, tmp_path, monkeypatch):
    backend = LinuxBackend()
    prefix = next(p.parent for p in sc2_root.parents if p.name == 'drive_c')
    wine = write(tmp_path / 'wine', b'#!/bin/sh\nexit 0\n')
    wine.chmod(0o755)
    umu = write(tmp_path / 'user-bin/umu-run', b'#!/bin/sh\nexit 0\n')
    umu.chmod(0o755)
    map_path = write(sc2_root / 'Maps/Test.SC2Map', b'test')
    options = LaunchOptions(sc2_root, str(wine), str(prefix))
    command, env = backend.command(options, map_path)
    assert command[0] == str(wine)
    assert command[-1].startswith('C:\\')
    assert 'PROTONPATH' not in env
    monkeypatch.setattr('sc2_campaign_launcher_linux.platform_backend.shutil.which', lambda _: str(umu))
    command, env = backend.command(LaunchOptions(sc2_root, MANAGED_PROTON, str(prefix)), map_path)
    assert command[0] == str(umu)
    assert 'PROTONPATH' not in env


def test_windows_arguments_keep_paths_with_spaces(sc2_root):
    path = sc2_root / 'Maps/Some Campaign/Launcher.SC2Map'
    command, _ = WindowsBackend().command(LaunchOptions(sc2_root), path)
    assert command == [str(sc2_root / 'Support64/SC2Switcher_x64.exe'), '-run', str(path.resolve())]


def test_packaged_libraries_are_not_passed_to_the_game(tmp_path, monkeypatch):
    bundle, system = tmp_path / 'bundle', tmp_path / 'system'
    monkeypatch.setattr(sys, '_MEIPASS', str(bundle), raising=False)
    original = {'PATH': os.pathsep.join([str(bundle), str(bundle / 'Qt/bin'), str(system)]),
                'LD_LIBRARY_PATH': str(bundle), 'LD_LIBRARY_PATH_ORIG': str(system),
                'QT_PLUGIN_PATH': str(bundle / 'Qt/plugins'), 'WINEPREFIX': '/games/prefix'}
    env = external_environment(original)
    assert env['PATH'] == str(system)
    assert env['LD_LIBRARY_PATH'] == str(system)
    assert 'QT_PLUGIN_PATH' not in env
    assert env['WINEPREFIX'] == '/games/prefix'
    assert 'QT_PLUGIN_PATH' in original


def test_card_columns_follow_the_actual_viewport(qapp, windows):
    window = windows(autoload=False)
    window._render([campaign(str(number)) for number in range(8)], window.generation)
    window.resize(400, 800)
    window.show()
    spin(qapp, lambda: not window.jobs.busy())
    assert window.grid.getItemPosition(1)[:2] == (1, 0)
    window.resize(1240, 800)
    spin(qapp, lambda: window.grid.getItemPosition(3)[:2] == (0, 3))
    assert window.scroll.horizontalScrollBar().maximum() == 0


def test_mutations_are_serial_and_queued_work_can_be_cancelled(qapp, windows, monkeypatch):
    window = windows(autoload=False)
    window._render([campaign('A'), campaign('B'), campaign('C')], window.generation)
    gate, entered = Event(), Event()
    calls = []

    def install(value, cancel, notify):
        calls.append(value['slug'])
        entered.set()
        assert gate.wait(5)
        return 'Done'

    monkeypatch.setattr(window.library, 'install', install)
    window._request('A', 'install')
    try:
        spin(qapp, entered.is_set)
        assert window.cards['B'].play.isEnabled()
        window._request('B', 'install')
        window._request('C', 'install')
        assert calls == ['A']
        window._request('B', 'cancel')
        assert [value['slug'] for value, _ in window.queue] == ['C']
    finally:
        gate.set()
    spin(qapp, lambda: window.mutation is None and not window.jobs.busy())
    assert calls == ['A', 'C']



def test_original_card_controls_and_summary_tooltip(qapp, windows):
    window = windows(autoload=False)
    c = {**campaign(), 'status': 'installed', 'removable': True}
    window._render([c], window.generation)
    card = window.cards['Test']
    card.set_media({'description': '<b>Campaign summary</b>', 'patch notes': 'Private patch notes'}, b'')
    assert card.size().width() == 280 and card.size().height() == 320
    assert card.remove.parent() is card.cover and card.remove.pos().x() == 4
    assert card.info.parent() is card.cover and card.info.pos().x() == 224
    assert card.remove.isEnabled()
    assert 'Campaign summary' in card.info.toolTip()
    assert 'Private patch notes' not in card.info.toolTip()
    buttons = [button.text() for button in card.findChildren(ui.QPushButton) if button.text()]
    assert buttons == ['Play']
    assert not hasattr(window, 'refresh_btn') and not hasattr(window, 'verify_btn')


def test_settings_contains_refresh_and_verification(qapp, settings):
    dialog = ui.SettingsDialog(settings, JobPool())
    actions = []
    dialog.refresh_requested.connect(actions.append)
    dialog.refresh_btn.click()
    dialog.verify_btn.click()
    assert actions == [False, True]
    dialog.reject()


def test_legacy_remove_button_works_and_keeps_the_result_visible(qapp, windows, monkeypatch):
    from threading import Event
    window = windows(autoload=False)
    c = campaign(mods=[])
    map_path = write(window.library.destination(c, c['maps'][0]), b'current map')
    window._render(window.library.statuses([c], Event()), window.generation)
    monkeypatch.setattr(ui.QMessageBox, 'question', lambda *a: ui.QMessageBox.StandardButton.Yes)
    window.cards['Test'].remove.click()
    spin(qapp, lambda: not window.jobs.busy() and window.mutation is None)
    assert not map_path.exists()
    assert not window.cards['Test'].remove.isEnabled()
    assert window.cards['Test'].play.text() == 'Install'
    assert 'Removed 1' in window.notice.text()


def test_repair_is_offered_after_a_failed_launch(qapp, windows, monkeypatch):
    window = windows(autoload=False)
    window._render([campaign(mods=[])], window.generation)
    prompts, requests = [], []
    monkeypatch.setattr(ui, 'show_details', lambda *a, **kw: prompts.append(kw) or True)
    monkeypatch.setattr(window, '_request', lambda *a: requests.append(a))
    window._launch_changed('Test', 'failed', 'Missing launcher map')
    assert prompts == [{'repair': True}]
    assert requests == [('Test', 'install')]


def test_saved_symlink_path_keeps_its_detected_prefix(qapp, settings, tmp_path, monkeypatch):
    root = tmp_path / 'external/StarCraft II'
    write(root / 'Support64/SC2Switcher_x64.exe', b'switcher')
    prefix = tmp_path / 'wine'
    alias = prefix / 'drive_c/StarCraft II'
    alias.parent.mkdir(parents=True)
    try:
        alias.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip('Symbolic links require privileges on this system')
    settings.backend = LinuxBackend()
    monkeypatch.setattr(ui, 'discover_runners', lambda _: [{'name': 'UMU', 'path': MANAGED_PROTON, 'type': 'proton'}])
    pool = JobPool()
    dialog = ui.SettingsDialog(settings, pool)
    spin(qapp, lambda: not pool.busy())
    dialog.auto_prefix.setChecked(True)
    dialog.sc2_in.setText(str(alias))
    dialog._save()
    assert dialog.result() == ui.QDialog.DialogCode.Accepted
    assert settings.sc2_root() == alias
    assert settings.wine_prefix() == str(prefix)
    if os.name != 'nt':
        wine = write(tmp_path / 'wine-runner/wine', b'#!/bin/sh\nexit 0\n')
        wine.chmod(0o755)
        map_path = write(root / 'Maps/Test/Launcher.SC2Map', b'current map')
        command, env = settings.backend.command(LaunchOptions(alias, str(wine), str(prefix)), map_path)
        assert command[-1] == 'C:\\StarCraft II\\Maps\\Test\\Launcher.SC2Map'
        assert env['WINEPREFIX'] == str(prefix)


def test_game_logs_and_file_protection_survive_launcher_exit(qapp, windows, settings, tmp_path):
    import subprocess
    from sc2_campaign_launcher_linux.game_process import MAX_LOG
    stop, ready = tmp_path / 'stop', tmp_path / 'ready'
    child = ('import sys, time\nfrom pathlib import Path\n'
             'sys.stdout.buffer.write(b"x" * (3 * 1024 * 1024)); sys.stdout.flush()\n'
             f'Path({str(ready)!r}).touch()\n'
             'deadline = time.monotonic() + 15\n'
             f'while not Path({str(stop)!r}).exists() and time.monotonic() < deadline: time.sleep(.02)')
    root, logs = settings.sc2_root(), tmp_path / 'logs'
    map_path = write(root / 'Maps/Test/Launcher.SC2Map', b'current map')
    parent = ('import os, sys\nfrom pathlib import Path\n'
              'from PyQt6.QtCore import QCoreApplication\n'
              'from sc2_campaign_launcher_linux.launching import LaunchManager\n'
              'from sc2_campaign_launcher_linux.platform_backend import LaunchOptions\n'
              'class Backend:\n'
              f'    def command(self, options, path): return [sys.executable, "-c", {child!r}], dict(os.environ)\n'
              'app = QCoreApplication([])\n'
              f'manager = LaunchManager(Backend(), Path({str(logs)!r}))\n'
              f'manager.launch("Test", LaunchOptions(Path({str(root)!r})), Path({str(map_path)!r}))\n')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    try:
        subprocess.run([sys.executable, '-c', parent], env=env, check=True, capture_output=True, timeout=10)
        spin(qapp, ready.exists)
        log = next(logs.glob('launch-*.log'))
        assert 0 < log.stat().st_size <= MAX_LOG
        manager = LaunchManager(PythonBackend(''), logs)
        assert 'Test' in manager.processes
        window = windows(launcher=manager)
        spin(qapp, lambda: not window.jobs.busy())
        assert not window.cards['Test'].play.isEnabled()
        assert not window.cards['Test'].remove.isEnabled()
        window._request('Test', 'install')
        assert not window.queue and window.mutation is None
        assert 'Close the game' in window.notice.text()
    finally:
        stop.touch()
    spin(qapp, lambda: not manager.processes)
    assert window.cards['Test'].play.isEnabled()


def test_reused_process_id_does_not_block_a_new_launcher(qapp, tmp_path):
    from sc2_campaign_launcher_linux.files import atomic_json
    atomic_json(tmp_path / 'launch-old.process', {'id': 'old', 'slug': 'Test', 'pid': os.getpid(),
                                                 'identity': 'a previous process'})
    launcher = LaunchManager(PythonBackend(''), tmp_path)
    assert not launcher.processes
