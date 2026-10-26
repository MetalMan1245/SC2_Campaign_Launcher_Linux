"""Build provenance without machine paths, account details, or credentials."""

import hashlib
import importlib.metadata
import json
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .files import contained_path, file_hash, read_json, relative_name

BUILD_FILES = ('SC2CampaignLauncher.spec', 'pyproject.toml', 'LICENSE', 'README.md', 'tools/build_release.py')


def manifest_digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_snapshot(root):
    root = Path(root)
    paths = [root / name for name in BUILD_FILES]
    paths += list((root / 'sc2_campaign_launcher_linux').rglob('*.py'))
    for directory in ('assets', 'native'):
        paths += [p for p in (root / directory).rglob('*') if p.is_file()]
    manifest = {}
    for path in sorted(paths):
        name = path.relative_to(root).as_posix()
        relative_name(name)
        path = contained_path(root, name)
        if not path.is_file():
            raise ValueError(f'A build input is missing or linked: {name}')
        manifest[name] = file_hash(path)
    return manifest


def create_build_info(root, edition, *, require_clean=False):
    root = Path(root)
    manifest = source_snapshot(root)
    commit, dirty = None, None
    try:
        if not (root / '.git').exists():
            raise FileNotFoundError('This source archive has no Git metadata.')
        revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True,
                                  text=True, timeout=10, check=True).stdout.strip()
        status = subprocess.run(['git', '--no-optional-locks', 'status', '--porcelain', '--untracked-files=normal'],
                                cwd=root, capture_output=True, text=True, timeout=10, check=True).stdout
        if re.fullmatch('[0-9a-f]{40}', revision):
            commit, dirty = revision, bool(status)
    except (OSError, subprocess.SubprocessError):
        pass  # Source archives still get a reproducible source digest, with Git state explicitly unknown.
    if source_snapshot(root) != manifest:
        raise ValueError('Source files changed while recording build provenance. Retry from stable source.')
    info = {'format': 1, 'version': __version__, 'edition': edition, 'commit': commit, 'dirty': dirty,
            'source_sha256': manifest_digest(manifest), 'inputs': manifest,
            'created': datetime.now(timezone.utc).isoformat(), 'python': platform.python_version(),
            'platform': sys.platform, 'dependencies': {}}
    for distribution in ('PyInstaller', 'PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'Pillow', 'mpyq'):
        try:
            info['dependencies'][distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            info['dependencies'][distribution] = None
    validate_build_info(info, edition, require_clean=require_clean)
    return info


def validate_build_info(info, edition=None, *, require_clean=False):
    if (not isinstance(info, dict) or info.get('format') != 1
            or info.get('edition') not in ('launcher', 'ccm')
            or (edition is not None and info['edition'] != edition)
            or not isinstance(info.get('version'), str) or not info['version']
            or not isinstance(info.get('inputs'), dict) or not info['inputs']):
        raise ValueError('The bundle has no valid build provenance. Rebuild it before packaging.')
    for name, digest in info['inputs'].items():
        relative_name(name)
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('The build input manifest is damaged.')
    if manifest_digest(info['inputs']) != info.get('source_sha256'):
        raise ValueError('The build source digest does not match its manifest.')
    if (info.get('commit') is not None and
            (not isinstance(info['commit'], str) or not re.fullmatch('[0-9a-f]{40}', info['commit']))):
        raise ValueError('The build commit is invalid.')
    if info.get('dirty') is not None and type(info['dirty']) is not bool:
        raise ValueError('The build working-tree state is invalid.')
    if require_clean and (info.get('commit') is None or info.get('dirty') is not False):
        raise ValueError('A clean Git checkout with a known commit is required. Keep local changes and commit reviewed work first.')
    return info


def packaged_build_info():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    if not getattr(sys, 'frozen', False) and (root / 'pyproject.toml').is_file():
        return create_build_info(root, 'launcher')
    info = read_json(root / 'build-info.json')
    if isinstance(info, dict) and set(info) == {'commit'} and isinstance(info['commit'], str) \
            and re.fullmatch('[0-9a-f]{40}', info['commit']):
        return {'version': __version__, 'commit': info['commit'], 'dirty': None, 'source_sha256': None,
                'provenance': 'Legacy installer record; source content and working-tree state are unknown.'}
    return validate_build_info(info) if info is not None else {'version': __version__, 'packaged': False}
