import os
import subprocess
import sys
from unittest.mock import Mock

import pytest

from sc2_campaign_launcher_linux import game_process, game_state, platform_backend
from sc2_campaign_launcher_linux.files import atomic_json, read_json
from sc2_campaign_launcher_linux.launching import LaunchManager, RecordedProcess
from sc2_campaign_launcher_linux.platform_backend import LaunchOptions, WindowsBackend
from conftest import write


@pytest.mark.parametrize('quoted', [True, False])
def test_menu_launch_finds_registered_battlenet_with_spaces(sc2_root, tmp_path, monkeypatch, quoted):
    path = write(tmp_path / 'Games folder/Battle.net/Battle.net.exe', b'client')
    command = f'"{path}" --uri="%1"' if quoted else f'{path} --uri="%1"'
    monkeypatch.setattr(platform_backend, 'battlenet_registration', lambda: command)
    args, _ = WindowsBackend().command(LaunchOptions(sc2_root), None)
    assert args == [str(path), '--exec=launch S2']


def test_missing_registration_uses_default_install(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_backend, 'battlenet_registration', lambda: '"missing.exe" --uri="%1"')
    monkeypatch.setenv('PROGRAMFILES(X86)', str(tmp_path))
    path = write(tmp_path / 'Battle.net/Battle.net.exe', b'client')
    assert platform_backend.battlenet_executable() == path


def test_missing_battlenet_explains_manual_launch(sc2_root, tmp_path, monkeypatch):
    monkeypatch.setattr(platform_backend, 'battlenet_registration', lambda: '')
    for name in ('PROGRAMFILES(X86)', 'PROGRAMFILES'):
        monkeypatch.setenv(name, str(tmp_path / 'absent'))
    with pytest.raises(ValueError, match='use Load'):
        WindowsBackend().command(LaunchOptions(sc2_root), None)
    path = sc2_root / 'Maps/Launcher.SC2Map'
    args, _ = WindowsBackend().command(LaunchOptions(sc2_root), path)
    assert args[-2:] == ['-run', str(path.resolve())]


@pytest.mark.parametrize('client_code', [None, 0])
def test_monitor_waits_for_game_and_ignores_battlenet_lifetime(tmp_path, monkeypatch, client_code):
    polls = Mock(side_effect=[set(), set(), {41}, {41}, set()])
    monkeypatch.setattr(game_process, 'game_pids', polls)
    monkeypatch.setattr(game_process, 'windows_process_path', lambda _: tmp_path / 'Versions/SC2_x64.exe')
    monkeypatch.setattr(game_process.time, 'sleep', lambda _: None)
    client = Mock()
    client.poll.return_value = client_code
    assert game_process.monitor_battlenet(client, tmp_path) == 0
    assert polls.call_count == 5
    client.wait.assert_not_called()
    client.terminate.assert_not_called()


def test_monitor_rejects_another_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(game_process, 'game_pids', lambda: {41})
    monkeypatch.setattr(game_process, 'windows_process_path', lambda _: tmp_path / 'Other/SC2_x64.exe')
    with pytest.raises(OSError, match='different StarCraft II installation'):
        game_process.monitor_battlenet(Mock(), tmp_path / 'Selected')


def test_monitor_does_not_treat_exited_process_as_started(tmp_path, monkeypatch):
    monkeypatch.setattr(game_process, 'game_pids', lambda: {41})
    monkeypatch.setattr(game_process, 'windows_process_path', lambda _: None)
    with pytest.raises(OSError, match='did not start'):
        game_process.monitor_battlenet(Mock(poll=lambda: 0), tmp_path, timeout=0)


def test_monitor_times_out_or_reports_failed_client(tmp_path, monkeypatch):
    monkeypatch.setattr(game_process, 'game_pids', lambda: set())
    with pytest.raises(OSError, match='did not start'):
        game_process.monitor_battlenet(Mock(poll=lambda: 0), tmp_path, timeout=0)
    with pytest.raises(OSError, match='code 7'):
        game_process.monitor_battlenet(Mock(poll=lambda: 7), tmp_path)


def test_worker_records_handoff_failure_without_stopping_battlenet(tmp_path, monkeypatch):
    request = tmp_path / 'launch.request'
    atomic_json(request, {'id': 'test', 'command': ['client'], 'root': str(tmp_path), 'battlenet': True})
    atomic_json(request.with_suffix('.process'), {'id': 'test'})
    monkeypatch.setattr(game_process, 'game_pids', lambda: set())
    client = Mock()
    spawn = Mock(return_value=client)
    monkeypatch.setattr(game_process.subprocess, 'Popen', spawn)
    monkeypatch.setattr(game_process, 'monitor_battlenet', Mock(side_effect=OSError('Sign in to Battle.net')))
    assert game_process.run(request) == 1
    assert read_json(request.with_suffix('.result'))['error'] == 'Sign in to Battle.net'
    assert spawn.call_args.kwargs['stdout'] == subprocess.DEVNULL
    client.terminate.assert_not_called()


def test_battlenet_launch_records_monitor_and_survives_reopening(qapp, sc2_root, tmp_path, monkeypatch):
    monkeypatch.setattr(platform_backend, 'battlenet_executable', lambda: tmp_path / 'Battle.net.exe')
    monkeypatch.setattr('sc2_campaign_launcher_linux.launching.game_running', lambda: False)
    worker = Mock(pid=os.getpid())
    worker.poll.return_value = None
    spawn = Mock(return_value=worker)
    monkeypatch.setattr('sc2_campaign_launcher_linux.launching.subprocess.Popen', spawn)
    logs = tmp_path / 'logs'
    manager = LaunchManager(WindowsBackend(), logs)
    manager.launch_target('ccm:test', LaunchOptions(sc2_root), None)
    request = read_json(next(logs.glob('*.request')))
    assert request['battlenet'] is True
    assert request['command'][-1] == '--exec=launch S2'
    reopened = LaunchManager(WindowsBackend(), logs)
    assert isinstance(reopened.processes['ccm:test'][0], RecordedProcess)
    with pytest.raises(ValueError, match='Close the running game'):
        reopened.launch_target('ccm:other', LaunchOptions(sc2_root), None)
    monkeypatch.setattr('sc2_campaign_launcher_linux.launching.process_identity', lambda _: None)
    atomic_json(next(logs.glob('*.process')).with_suffix('.result'), {'id': request['id'], 'code': 0})
    reopened.poll()
    assert not reopened.processes
    manager.timer.stop()


def test_launch_refuses_game_started_outside_manager(qapp, sc2_root, tmp_path, monkeypatch):
    monkeypatch.setattr('sc2_campaign_launcher_linux.launching.game_running', lambda: True)
    backend = Mock()
    manager = LaunchManager(backend, tmp_path)
    with pytest.raises(ValueError, match='Close the running game'):
        manager.launch_target('ccm:test', LaunchOptions(sc2_root), None)
    backend.command.assert_not_called()


@pytest.mark.skipif(os.name != 'nt', reason='Windows process discovery')
def test_tasklist_matches_only_game_executables(monkeypatch):
    result = Mock(returncode=0, stdout='"SC2Switcher_x64.exe","1"\n"SC2_x64.exe","42"\n'
                                     '"SC2.exe","43"\n"Battle.net.exe","44"\n')
    monkeypatch.setattr(game_state.subprocess, 'run', lambda *a, **kw: result)
    assert game_state.game_pids() == {42, 43}
    result.returncode = 1
    with pytest.raises(OSError, match='Could not check'):
        game_state.game_pids()


@pytest.mark.skipif(os.name != 'nt', reason='Windows process discovery')
def test_windows_process_path_reads_executable_without_arguments():
    from pathlib import Path
    assert game_state.windows_process_path(os.getpid()).resolve() == Path(getattr(sys, '_base_executable', sys.executable)).resolve()
