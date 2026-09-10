"""Keep game output bounded for as long as the game owns its output pipe."""

import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'sc2_campaign_launcher_linux'

from .files import atomic_json, read_json

MAX_LOG = 1024 * 1024


@contextmanager
def external_dll_search():
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        yield
        return
    import ctypes
    setter = ctypes.windll.kernel32.SetDllDirectoryW
    setter.argtypes = [ctypes.c_wchar_p]
    setter.restype = ctypes.c_int
    if not setter(None):
        raise ctypes.WinError()
    try:
        yield
    finally:
        setter(sys._MEIPASS)


def process_identity(pid):
    """Include the process start time so a reused PID cannot match an old record."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            status = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            if (kernel.GetExitCodeProcess(handle, ctypes.byref(status)) and status.value == 259
                    and kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in times])):
                return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
        return None
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if stat[0] in ('Z', 'X'):
            return None
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return f'{boot}:{stat[19]}'
    except (OSError, IndexError):
        return None


def log_tail(path, limit=64 * 1024):
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - limit))
            return stream.read(limit).decode('utf-8', errors='replace')
    except OSError:
        return ''


def append_log(path, data):
    with path.open('r+b') as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() + len(data) > MAX_LOG:
            stream.seek(max(0, stream.tell() - 64 * 1024))
            tail = stream.read(64 * 1024)
            stream.seek(0)
            stream.write(tail)
            stream.truncate()
        stream.write(data)


def run(request_path):
    request = read_json(request_path)
    log = request_path.with_suffix('.log')
    result = request_path.with_suffix('.result')
    code, error = 1, ''
    try:
        for _ in range(100):
            record = read_json(request_path.with_suffix('.process'), {})
            if record.get('id') == request['id']:
                break
            time.sleep(.05)
        else:
            raise OSError('The launcher could not record the game process. No game was started.')
        with external_dll_search():
            process = subprocess.Popen(request['command'], cwd=request['root'], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with process.stdout:
            while chunk := process.stdout.read1(64 * 1024):
                try:
                    append_log(log, chunk)
                except OSError:
                    pass  # Drain the pipe even if the log directory becomes unwritable.
        code = process.wait()
    except (OSError, ValueError) as failure:
        error = str(failure)
    atomic_json(result, {'id': request['id'], 'code': code, 'error': error})
    return code


if __name__ == '__main__':
    raise SystemExit(run(Path(sys.argv[1])))
