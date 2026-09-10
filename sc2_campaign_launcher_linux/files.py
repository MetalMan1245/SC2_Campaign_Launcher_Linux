"""Path checks and file writes shared by the installer and campaign library."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Event


class Cancelled(Exception):
    pass


def check_cancel(cancel: Event | None):
    if cancel is not None and cancel.is_set():
        raise Cancelled('Cancelled')


def relative_name(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('A nonempty relative path is required.')
    parts = value.split('/')
    if (PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
            or any(p in ('', '.', '..') for p in parts)
            or any(re.search(r'[\\:*?"<>|\x00-\x1f\x7f]', p) for p in parts)
            or any(p.endswith((' ', '.')) for p in parts)
            or any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p)
                   for p in parts)):
        raise ValueError(f'Unsafe relative path: {value!r}')
    return value


def contained_path(root: Path, *names: str) -> Path:
    root = root.resolve()
    path = root
    for name in names:
        path = path.joinpath(*relative_name(name).split('/'))
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f'Path leaves the installation directory: {path}')
    # Refuse aliases even when they currently resolve inside the installation.
    # Otherwise replacing one campaign's link can modify another campaign.
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f'Symbolic links cannot be modified: {current}')
    return path


def file_hash(path: Path, cancel: Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            check_cancel(cancel)
            digest.update(chunk)
    check_cancel(cancel)
    return digest.hexdigest()


def signature(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.sc2cl-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.sc2cl-', suffix='.tmp', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path, default=None):
    try:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError(f'JSON file is too large: {path}')
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default


def prune_empty(root: Path, directory: Path):
    root = root.resolve()
    directory = directory.resolve()
    while directory != root and directory.is_relative_to(root):
        try:
            directory.rmdir()
        except OSError:
            break
        directory = directory.parent
