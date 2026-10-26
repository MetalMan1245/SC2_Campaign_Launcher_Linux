import os
import signal
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest

from sc2_campaign_launcher_linux import game_process, launching
from sc2_campaign_launcher_linux.files import atomic_json, read_json
from sc2_campaign_launcher_linux.launching import LaunchManager
from sc2_campaign_launcher_linux.platform_backend import LaunchOptions
from test_runtime import windows, settings, spin  # noqa: F401


def test_linux_startup_deadline_with_silent_runner(monkeypatch):
    monkeypatch.setattr(game_process, 'game_pids', lambda: set())
    monkeypatch.setattr(game_process.time, 'monotonic', Mock(side_effect=[0, 60, 121]))
    drain = Mock()
    runner = Mock(poll=lambda: None)
    with pytest.raises(OSError, match='did not start within two minutes'):
        game_process.monitor_linux(runner, drain)
    assert drain.call_count == 1
    runner.terminate.assert_not_called()


@pytest.mark.parametrize('runner_code', [None, 0, 7])
def test_game_lifetime_wins_over_runner_and_startup_deadline(monkeypatch, runner_code):
    monkeypatch.setattr(game_process, 'game_pids', Mock(side_effect=[{41}, {41}, set()]))
    clock = Mock(return_value=0)
    monkeypatch.setattr(game_process.time, 'monotonic', clock)
    drain = Mock(side_effect=lambda: setattr(clock, 'return_value', 500))
    runner = Mock(poll=lambda: runner_code)
    assert game_process.monitor_linux(runner, drain) == 0
    assert drain.call_count == 2
    runner.wait.assert_not_called()
    runner.terminate.assert_not_called()


def test_successful_bootstrap_exit_still_waits_for_detached_game(monkeypatch):
    monkeypatch.setattr(game_process, 'game_pids', Mock(side_effect=[set(), set(), {41}, set()]))
    runner = Mock(poll=lambda: 0)
    assert game_process.monitor_linux(runner, Mock()) == 0


def test_failed_runner_reports_exit_code(monkeypatch):
    monkeypatch.setattr(game_process, 'game_pids', lambda: set())
    with pytest.raises(OSError, match='code 7'):
        game_process.monitor_linux(Mock(poll=lambda: 7), Mock())


def test_worker_refuses_game_that_started_during_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(game_process, 'game_pids', lambda: {41})
    spawn = Mock()
    monkeypatch.setattr(game_process.subprocess, 'Popen', spawn)
    with pytest.raises(OSError, match='Close StarCraft II'):
        game_process.run_direct({'linux': True}, tmp_path / 'log')
    spawn.assert_not_called()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux pipe monitoring')
@pytest.mark.parametrize('mode', ['silent', 'noisy', 'closed', 'inherited'])
def test_real_runner_pipe_cannot_block_startup_timeout(tmp_path, monkeypatch, mode):
    pid_file = tmp_path / 'child.pid'
    code = 'import os,sys,time,subprocess\nfrom pathlib import Path\n'
    if mode == 'inherited':
        code += ('child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                 f'Path({str(pid_file)!r}).write_text(str(child.pid))\n')
    elif mode == 'closed':
        code += 'os.close(1); os.close(2); time.sleep(60)\n'
    elif mode == 'noisy':
        code += 'while True: os.write(1,b"x"*65536)\n'
    else:
        code += 'time.sleep(60)\n'
    log = tmp_path / 'launch.log'
    log.write_bytes(b'')
    monitor = game_process.monitor_linux
    monkeypatch.setattr(game_process, 'monitor_linux', lambda p, drain: monitor(p, drain, timeout=.4))
    monkeypatch.setattr(game_process, 'game_pids', lambda: set())
    spawn = subprocess.Popen
    processes = []

    def capture(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(game_process.subprocess, 'Popen', capture)
    started = time.monotonic()
    try:
        with pytest.raises(OSError, match='did not start'):
            game_process.run_direct({'command': [sys.executable, '-c', code], 'root': str(tmp_path),
                                     'linux': True}, log)
        assert time.monotonic() - started < 5
        assert log.stat().st_size <= game_process.MAX_LOG
        if mode == 'noisy':
            assert log.stat().st_size > 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass


def old_launch(tmp_path):
    path = tmp_path / 'launch-old.process'
    atomic_json(path, {'id': 'old', 'slug': 'Test', 'pid': os.getpid(),
                      'identity': game_process.process_identity(os.getpid())})
    path.with_suffix('.log').write_text('original log')
    return path


def test_recovery_releases_legacy_monitor_across_reopening(qapp, tmp_path, monkeypatch):
    path = old_launch(tmp_path)
    monkeypatch.setattr(launching, 'game_running', lambda: False)
    backend = Mock(name='backend')
    backend.name = 'linux'
    manager = LaunchManager(backend, tmp_path)
    assert manager.processes
    manager.release_failed_launches()
    assert not manager.processes
    assert read_json(path)['released'] is True
    assert path.with_suffix('.log').read_text() == 'original log'
    assert not LaunchManager(backend, tmp_path).processes


@pytest.mark.parametrize('checks', [[True], [False, True]])
def test_recovery_refuses_game_even_if_it_starts_during_recovery(qapp, tmp_path, monkeypatch, checks):
    path = old_launch(tmp_path)
    backend = Mock()
    backend.name = 'linux'
    manager = LaunchManager(backend, tmp_path)
    monkeypatch.setattr(launching, 'game_running', Mock(side_effect=checks))
    try:
        with pytest.raises(ValueError, match='StarCraft II'):
            manager.release_failed_launches()
        assert manager.processes and not read_json(path).get('released')
    finally:
        manager.timer.stop()


def test_process_detection_failure_keeps_launch_locked(qapp, tmp_path, monkeypatch):
    path = old_launch(tmp_path)
    backend = Mock()
    backend.name = 'linux'
    manager = LaunchManager(backend, tmp_path)
    monkeypatch.setattr(launching, 'game_running', Mock(side_effect=OSError('process check failed')))
    try:
        with pytest.raises(OSError, match='process check failed'):
            manager.release_failed_launches()
        assert manager.processes and not read_json(path).get('released')
    finally:
        manager.timer.stop()


def test_retry_keeps_old_monitor_logs_and_results_separate(qapp, sc2_root, tmp_path, monkeypatch):
    monkeypatch.setattr(launching, 'game_running', lambda: False)
    worker = Mock(pid=os.getpid())
    worker.poll.return_value = None
    monkeypatch.setattr(launching.subprocess, 'Popen', Mock(return_value=worker))
    backend = Mock()
    backend.name = 'linux'
    backend.command.return_value = (['wine', 'switcher'], dict(os.environ))
    manager = LaunchManager(backend, tmp_path)
    manager.launch_target('Test', LaunchOptions(sc2_root), None)
    first_log = manager.processes['Test'][1]
    request = read_json(first_log.with_suffix('.request'))
    assert request['linux'] is True
    manager.release_failed_launches()
    manager.launch_target('Test', LaunchOptions(sc2_root), None)
    second_log = manager.processes['Test'][1]
    assert second_log != first_log
    atomic_json(first_log.with_suffix('.result'), {'id': request['id'], 'code': 1, 'error': 'old failure'})
    manager.poll()
    assert manager.processes and not second_log.with_suffix('.result').exists()
    manager.timer.stop()


def test_recovery_button_restores_controls_for_legacy_launch(windows, settings, qapp, tmp_path, monkeypatch):  # noqa: F811
    from sc2_campaign_launcher_linux import sc2_campaign_launcher_linux as ui
    old_launch(tmp_path)
    settings.backend.name = 'linux'
    manager = LaunchManager(settings.backend, tmp_path)
    window = windows(launcher=manager)
    window.show()
    spin(qapp, lambda: not window.jobs.busy())
    assert window.recover_launch_btn.isVisible()
    assert not window.settings_btn.isEnabled()
    monkeypatch.setattr(ui.QMessageBox, 'question', lambda *args: ui.QMessageBox.StandardButton.Yes)
    window.recover_launch_btn.click()
    assert not manager.processes
    assert window.settings_btn.isEnabled()
    assert not window.recover_launch_btn.isVisible()
    assert 'recovered' in window.notice.text()


@pytest.mark.parametrize('result', [
    {'id': 'old', 'code': 1},
    {'id': 'another attempt', 'complete': True, 'code': 1},
    {'id': 'old', 'complete': True, 'code': True},
    {'id': 'old', 'complete': True, 'code': '1'},
])
def test_incomplete_or_unrelated_result_cannot_release_live_monitor(qapp, tmp_path, result):
    path = old_launch(tmp_path)
    atomic_json(path.with_suffix('.result'), result)
    manager = LaunchManager(Mock(), tmp_path)
    try:
        assert 'Test' in manager.processes
        manager.poll()
        assert 'Test' in manager.processes
    finally:
        manager.timer.stop()


@pytest.mark.skipif(sys.platform != 'linux', reason='Real worker and pipe lifetime')
@pytest.mark.parametrize('outcome', ['timeout', 'game_exit'])
def test_worker_keeps_output_open_after_completion_and_reopening(qapp, tmp_path, monkeypatch, outcome):
    ready, finish = tmp_path / 'ready', tmp_path / 'finish'
    start_monitor, live_game, observed = (tmp_path / name for name in ('monitor', 'live', 'observed'))
    marker = tmp_path / 'writer-result'
    child = ('import os,time\nfrom pathlib import Path\n'
             f'Path({str(ready)!r}).touch()\n'
             'deadline=time.monotonic()+15\n'
             f'while not Path({str(finish)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n'
             'try:\n'
             '    for _ in range(48): os.write(1,b"x"*65536)\n'
             '    os.write(1,b"late output complete\\n")\n'
             f'    Path({str(marker)!r}).write_text("success")\n'
             'except BrokenPipeError:\n'
             f'    Path({str(marker)!r}).write_text("broken pipe")\n')
    helper = ('import sys,time\nfrom pathlib import Path\n'
              'from sc2_campaign_launcher_linux import game_process as gp\n'
              'def game_pids():\n'
              f'    if Path({str(live_game)!r}).exists():\n'
              f'        Path({str(observed)!r}).touch(); return {{42}}\n'
              '    return set()\n'
              'gp.game_pids=game_pids\n'
              'original=gp.monitor_linux\n'
              'def monitor(process, drain):\n'
              '    deadline=time.monotonic()+15\n'
              f'    while not Path({str(start_monitor)!r}).exists() and time.monotonic()<deadline: time.sleep(.01)\n'
              f'    return original(process,drain,timeout={.2 if outcome == "timeout" else 10})\n'
              'gp.monitor_linux=monitor\n'
              'raise SystemExit(gp.run(Path(sys.argv[1])))\n')
    spawn = subprocess.Popen
    workers = []

    def spawn_worker(command, **kwargs):
        kwargs['env']['PYTHONPATH'] = os.pathsep.join(sys.path)
        process = spawn([sys.executable, '-c', helper, command[-1]], **kwargs)
        workers.append(process)
        return process

    monkeypatch.setattr(launching.subprocess, 'Popen', spawn_worker)
    monkeypatch.setattr(launching, 'game_running', lambda: False)
    backend = Mock()
    backend.name = 'linux'
    backend.command.return_value = ([sys.executable, '-c', child], dict(os.environ))
    manager = LaunchManager(backend, tmp_path / 'logs')
    manager.launch_target('Test', LaunchOptions(tmp_path), None)
    worker, log = manager.processes['Test']
    reopened = LaunchManager(backend, manager.log_dir)
    events = []
    manager.changed.connect(lambda *args: events.append(args))
    try:
        spin(qapp, ready.exists)
        assert reopened.processes
        start_monitor.touch()
        if outcome == 'game_exit':
            live_game.touch()
            spin(qapp, observed.exists)
            live_game.unlink()
        spin(qapp, log.with_suffix('.result').exists)
        result = read_json(log.with_suffix('.result'))
        assert result['complete'] is True
        assert result['code'] == (1 if outcome == 'timeout' else 0)
        assert worker.poll() is None, 'The output reader must survive the launch result'
        manager.poll()
        reopened.poll()
        assert not manager.processes and not reopened.processes
        assert not LaunchManager(backend, manager.log_dir).processes
        assert events[-1][1] == ('failed' if outcome == 'timeout' else 'finished')
        if outcome == 'timeout':
            assert events[-1][2].startswith('Launch failed (code 1).')
        finish.touch()
        assert worker.wait(timeout=10) == result['code']
        assert marker.read_text() == 'success'
        assert log.stat().st_size <= game_process.MAX_LOG
        assert b'late output complete' in log.read_bytes()
        manager.poll()
        assert not manager.output_processes
    finally:
        start_monitor.touch()
        finish.touch()
        live_game.unlink(missing_ok=True)
        for process in workers:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
        manager.timer.stop()
        reopened.timer.stop()
