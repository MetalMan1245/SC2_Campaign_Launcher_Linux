"""Game processes live independently of campaign widgets and the launcher window."""

import hashlib
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .files import contained_path
from .platform_backend import LaunchOptions

MAX_LOG = 1024 * 1024


def external_environment(environment):
    env = dict(environment)
    bundle = getattr(sys, '_MEIPASS', None)
    if not bundle:
        return env
    root = Path(bundle).resolve()

    def bundled(value):
        return bool(value) and Path(value).resolve().is_relative_to(root)

    env['PATH'] = os.pathsep.join(value for value in env.get('PATH', '').split(os.pathsep)
                                if not bundled(value))
    for key in ('LD_LIBRARY_PATH', 'LIBPATH'):
        if key + '_ORIG' in env:
            env[key] = env[key + '_ORIG']
        else:
            env.pop(key, None)
    for key in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QML2_IMPORT_PATH'):
        if bundled(env.get(key, '')):
            env.pop(key, None)
    return env


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


def log_tail(path: Path, limit=64 * 1024) -> str:
    try:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - limit))
            return stream.read(limit).decode('utf-8', errors='replace')
    except OSError:
        return ''


class LaunchManager(QObject):
    changed = pyqtSignal(str, str, str)

    def __init__(self, backend, log_dir: Path, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.log_dir = log_dir
        self.processes = {}
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)

    def launch(self, slug: str, options: LaunchOptions, map_path: Path):
        if slug in self.processes:
            raise ValueError('This campaign already has a running process.')
        if not map_path.is_file():
            raise ValueError('The launcher map is missing. Use Verify / repair first.')
        root = options.root.resolve()
        try:
            relative = map_path.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError('The launcher map is outside this SC2 installation.') from error
        contained_path(root, relative)
        command, env = self.backend.command(options, map_path)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256((str(root) + slug).encode()).hexdigest()[:24]
        path = self.log_dir / f'launch-{key}.log'
        path.write_text(
            f'SC2: {root}\nRunner: {options.runner}\nPrefix: {options.prefix}\n'
            f'Map: {map_path}\nCommand: {command!r}\n\n', encoding='utf-8')
        kwargs = {'start_new_session': True} if os.name != 'nt' else {
            'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        }
        with path.open('ab') as output, external_dll_search():
            process = subprocess.Popen(
                command, cwd=root, env=external_environment(env), stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, **kwargs,
            )
        self.processes[slug] = (process, path)
        self.timer.start()
        self.changed.emit(slug, 'running', str(path))

    def poll(self):
        for slug, (process, path) in list(self.processes.items()):
            code = process.poll()
            if code is None:
                try:
                    if path.stat().st_size > MAX_LOG:
                        tail = log_tail(path).encode()
                        with path.open('r+b') as stream:
                            stream.write(tail)
                            stream.truncate()
                except OSError:
                    pass
                continue
            del self.processes[slug]
            if code:
                self.changed.emit(slug, 'failed',
                                  f'The game runner exited with code {code}.\nLog: {path}\n\n{log_tail(path)}')
            else:
                self.changed.emit(slug, 'finished', str(path))
        if not self.processes:
            self.timer.stop()
