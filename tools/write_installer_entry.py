"""Generate the desktop bootstrap for the CCM development branch."""

import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from sc2_campaign_launcher_linux.installer import desktop_entry  # noqa: E402

command = (
    'set -eu; '
    "command -v curl >/dev/null || { echo 'Install curl first.'; echo; read _; exit 1; }; "
    'task_dir=$(mktemp -d -t sc2-installer.XXXXXX); '
    "trap 'rm -rf -- \"$task_dir\"' EXIT; "
    "curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' "
    '--connect-timeout 15 --max-time 60 '
    "'https://raw.githubusercontent.com/RogerRoger29/SC2_Campaign_Launcher_Linux/feature/ccm-manager/synergy_remote-installer.sh' "
    '--output "$task_dir/install.sh"; bash "$task_dir/install.sh"; '
    "echo; echo 'Press Enter to close...'; read _"
)
path = root / 'SC2CampaignLauncher-Linux-x86_64.desktop'
path.write_bytes(desktop_entry('SC2 Campaign Launcher Installer', ['sh', '-c', command],
                               'system-software-install', terminal=True))
