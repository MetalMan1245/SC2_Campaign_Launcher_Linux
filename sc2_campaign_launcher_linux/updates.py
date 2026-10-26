"""Check published releases and development revisions on the public fork."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__
from .network import HttpClient

REPOSITORY = 'RogerRoger29/SC2_Campaign_Launcher_Linux'
BRANCH = 'feature/ccm-manager'


def run(arguments, **kwargs):
    options = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=30, **options, **kwargs)
    if result.returncode:
        raise ValueError('The installed revision could not be read.')
    return result.stdout


def current_revision():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    info = root / 'build-info.json'
    if info.is_file():
        return json.loads(info.read_text(encoding='utf-8')).get('commit', '')
    if (root / '.git').exists() and shutil.which('git'):
        return run(['git', 'rev-parse', 'HEAD'], cwd=root).strip()
    return ''


def check_update(platform):
    url = f'https://github.com/{REPOSITORY}'
    client = HttpClient()
    if platform == 'win32':
        releases = client.json(f'https://api.github.com/repos/{REPOSITORY}/releases')
        releases = [r for r in releases if not r.get('draft') and not r.get('prerelease')]
        if not releases:
            return ('No Windows release has been published in this fork yet.', url + '/releases')
        latest = releases[0]
        tag = latest['tag_name']
        if tag.lstrip('v') == __version__:
            return (f'You have release {tag}.', latest['html_url'])
        return (f'Latest available release: {tag}. Installed version: {__version__}.', latest['html_url'])
    commit = client.json(f'https://api.github.com/repos/{REPOSITORY}/commits/{BRANCH}')
    current = current_revision()
    if current == commit['sha']:
        return ('This checkout is current.', url + '/tree/' + BRANCH)
    return ('A different development revision is available. Download or pull it when you are ready.',
            url + '/tree/' + BRANCH)
