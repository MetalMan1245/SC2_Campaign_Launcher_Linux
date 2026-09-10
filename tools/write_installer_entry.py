"""Keep the release tag and desktop quoting in sync with the installer."""

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from sc2_campaign_launcher_linux import __version__  # noqa: E402
from sc2_campaign_launcher_linux.installer import desktop_entry  # noqa: E402

command = (
    'set -eu; '
    "command -v curl >/dev/null || { echo 'Install curl first.'; exit 1; }; "
    'task_dir=$(mktemp -d -t sc2-installer.XXXXXX); '
    "trap 'rm -rf -- \"$task_dir\"' EXIT; "
    "curl --fail --show-error --silent --location --proto '=https' --proto-redir '=https' "
    '--connect-timeout 15 --max-time 60 '
    f'https://raw.githubusercontent.com/MetalMan1245/SC2_Campaign_Launcher_Linux/v{__version__}/synergy_remote-installer.sh '
    '-o "$task_dir/install.sh"; bash "$task_dir/install.sh"'
)
path = root / 'SC2-Campaign-Launcher-Linux_Install-Uninstall.desktop'
path.write_bytes(desktop_entry('SC2 Campaign Launcher Installer', ['sh', '-c', command],
                               'system-software-install', terminal=True))
