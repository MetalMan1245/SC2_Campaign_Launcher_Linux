#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SC2CL_PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null; then
    echo 'Python 3.10 or newer is required. Install Python, then run this script again.' >&2
    exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    echo 'Python 3.10 or newer is required.' >&2
    exit 1
fi

exec "$PYTHON" -B "$SCRIPT_DIR/sc2_campaign_launcher_linux/installer.py" --source "$SCRIPT_DIR" "$@"
