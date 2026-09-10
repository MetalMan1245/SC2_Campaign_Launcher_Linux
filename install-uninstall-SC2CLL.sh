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

if [[ " ${*:-} " == *' --install '* ]]; then
    if ! "$PYTHON" -c 'from PyQt6.QtCore import PYQT_VERSION; from PyQt6.QtWidgets import QApplication; assert PYQT_VERSION >= 0x060203' 2>/dev/null; then
        echo 'PyQt6 6.2.3 or newer could not be loaded in the selected Python environment.'
        package=()
        manager=()
        if [[ -r /etc/os-release ]]; then
            # shellcheck source=/dev/null
            source /etc/os-release
            case "${ID:-}" in
                arch|cachyos|endeavouros|manjaro|garuda)
                    manager=(sudo pacman -S --needed); package=(python-pyqt6) ;;
                debian|ubuntu|linuxmint|pop)
                    manager=(sudo apt-get install); package=(python3-pyqt6) ;;
                fedora|nobara)
                    manager=(sudo dnf install); package=(python3-qt6) ;;
            esac
        fi
        if [[ ${#manager[@]} -gt 0 && "$PYTHON" == python3 ]]; then
            read -r -p "Install ${package[*]} using the package manager? [y/N]: " answer
            if [[ "$answer" == [yY] ]]; then
                "${manager[@]}" "${package[@]}"
            fi
        fi
        if ! "$PYTHON" -c 'from PyQt6.QtCore import PYQT_VERSION; from PyQt6.QtWidgets import QApplication; assert PYQT_VERSION >= 0x060203' 2>/dev/null; then
            echo 'Install PyQt6 6.2.3 or newer in this Python environment before installing the launcher.' >&2
            echo 'For a virtual environment, see the manual installation instructions in README.md.' >&2
            exit 1
        fi
    fi
    if ! command -v umu-run >/dev/null && ! command -v wine >/dev/null; then
        echo 'UMU or Wine is required to play. The launcher can be installed now and configured later.'
        echo 'UMU installation: https://github.com/Open-Wine-Components/umu-launcher#installing'
    fi
fi

exec "$PYTHON" -B "$SCRIPT_DIR/sc2_campaign_launcher_linux/installer.py" --source "$SCRIPT_DIR" "$@"
