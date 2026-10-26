from pathlib import Path
import subprocess
import sys
import time

import pytest

from sc2_campaign_launcher_linux.game_state import linux_game_pids


@pytest.mark.parametrize('state', ['R', 'S', 'D', 'T', 'I', 'Z', 'X', 'x'])
@pytest.mark.parametrize('name', ['SC2.exe', 'SC2_x64.exe', 'sc2_X64.EXE', 'SC2Switcher_x64.exe', 'wine'])
def test_proc_name_and_state_are_checked_together(tmp_path, name, state):
    path = tmp_path / '42/stat'
    path.parent.mkdir()
    path.write_text(f'42 ({name}) {state} 1 2 3\n')
    expected = {42} if name.casefold() in ('sc2.exe', 'sc2_x64.exe') and state not in ('Z', 'X', 'x') else set()
    assert linux_game_pids(tmp_path) == expected


@pytest.mark.parametrize('contents', ['', '42 (SC2.exe)', '42 SC2.exe S', '42 (other (process)) Z 1'])
def test_incomplete_or_unrelated_proc_records_are_ignored(tmp_path, contents):
    path = tmp_path / '42/stat'
    path.parent.mkdir()
    path.write_text(contents)
    assert linux_game_pids(tmp_path) == set()


@pytest.mark.skipif(sys.platform != 'linux', reason='Real Linux process states')
def test_live_named_process_is_detected_but_its_unreaped_zombie_is_not():
    code = ('import ctypes,os\n'
            'assert ctypes.CDLL(None).prctl(15,b"SC2_x64.exe",0,0,0)==0\n'
            'os.write(1,b"ready\\n"); os.read(0,1); os._exit(0)\n')
    child = subprocess.Popen([sys.executable, '-c', code], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        assert child.stdout.readline() == b'ready\n'
        assert child.pid in linux_game_pids()
        child.stdin.close()
        deadline = time.monotonic() + 5
        state = ''
        while time.monotonic() < deadline:
            state = Path(f'/proc/{child.pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
            if state == 'Z':
                break
            time.sleep(.01)
        assert state == 'Z'
        assert child.pid not in linux_game_pids()
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        child.stdout.close()
        if not child.stdin.closed:
            child.stdin.close()
