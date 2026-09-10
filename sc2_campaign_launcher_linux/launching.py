"""Game processes live independently of campaign widgets and the launcher window."""

import hashlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from .files import atomic_json, contained_path, read_json
from .game_process import external_dll_search, log_tail, process_identity
from .platform_backend import LaunchOptions


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


class RecordedProcess:
    def __init__(self, record, result):
        self.record, self.result = record, result

    def poll(self):
        identity = process_identity(self.record['pid'])
        if identity is not None and identity == self.record['identity']:
            return None
        try:
            result = read_json(self.result, {})
            if (isinstance(result, dict) and result.get('id') == self.record['id']
                    and isinstance(result.get('code'), int)):
                return result['code']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return -1


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
        for path in self.log_dir.glob('launch-*.process'):
            try:
                record = read_json(path)
                if (not isinstance(record, dict) or not isinstance(record.get('slug'), str)
                        or not isinstance(record.get('identity'), str) or not isinstance(record.get('id'), str)):
                    continue
                process = RecordedProcess(record, path.with_suffix('.result'))
                if process.poll() is None:
                    self.processes[record['slug']] = (process, path.with_suffix('.log'))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        if self.processes:
            self.timer.start()

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
        job_id = uuid.uuid4().hex
        request = path.with_suffix('.request')
        atomic_json(request, {'id': job_id, 'command': command, 'root': str(root)})
        if getattr(sys, 'frozen', False):
            worker = [sys.executable, '--run-game', str(request)]
        else:
            worker = [sys.executable, '-B', str(Path(__file__).with_name('game_process.py')), str(request)]
        kwargs = {'start_new_session': True} if os.name != 'nt' else {
            'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        }
        with external_dll_search():
            process = subprocess.Popen(
                worker, cwd=root, env=external_environment(env), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs,
            )
        try:
            identity = process_identity(process.pid)
            if identity is None:
                raise OSError('The game monitor could not be started.')
            atomic_json(path.with_suffix('.process'), {'id': job_id, 'slug': slug, 'pid': process.pid,
                                                     'identity': identity})
        except OSError:
            process.terminate()
            process.wait(timeout=5)
            raise
        self.processes[slug] = (process, path)
        self.timer.start()
        self.changed.emit(slug, 'running', str(path))

    def poll(self):
        for slug, (process, path) in list(self.processes.items()):
            code = process.poll()
            if code is None:
                continue
            del self.processes[slug]
            detail = ''
            try:
                result = read_json(path.with_suffix('.result'), {})
                record = read_json(path.with_suffix('.process'), {})
                if (isinstance(result, dict) and isinstance(record, dict)
                        and result.get('id') == record.get('id')):
                    detail = result.get('error', '')
            except (OSError, ValueError, TypeError):
                pass
            if code:
                self.changed.emit(slug, 'failed',
                                  f'The game runner exited with code {code}.\n{detail}\nLog: {path}\n\n{log_tail(path)}')
            else:
                self.changed.emit(slug, 'finished', str(path))
        if not self.processes:
            self.timer.stop()
