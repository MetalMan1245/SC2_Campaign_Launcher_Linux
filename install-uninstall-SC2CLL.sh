#!/usr/bin/env bash
# SC2 Campaign Launcher installer for Linux (interactive CLI)
# Usage: ./install-uninstall-SC2CLL.sh
set -euo pipefail

SCRIPT_NAME='sc2_campaign_launcher_linux.py'
APP_TITLE='SC2 Campaign Launcher'
LOCAL_BIN="$HOME/.local/bin"
LOCAL_SHARE="$HOME/.local/share"
GLOBAL_BIN='/usr/local/bin'
GLOBAL_SHARE='/usr/share'
DEST_BIN=""
DESKTOP_DIR=""
ASSET_DEST=""

# Detect installation state
LOCAL_INSTALLED=0
GLOBAL_INSTALLED=0
INSTALLED_SCOPE=""  # "local", "global", "both", or "custom"

check_install_state() {
    [[ -f "$LOCAL_BIN/$SCRIPT_NAME" ]] && LOCAL_INSTALLED=1
    [[ -f "$GLOBAL_BIN/$SCRIPT_NAME" ]] && GLOBAL_INSTALLED=1

    if [[ $LOCAL_INSTALLED -eq 1 ]] && [[ $GLOBAL_INSTALLED -eq 1 ]]; then
        INSTALLED_SCOPE="both"
    elif [[ $LOCAL_INSTALLED -eq 1 ]]; then
        INSTALLED_SCOPE="local"
    elif [[ $GLOBAL_INSTALLED -eq 1 ]]; then
        INSTALLED_SCOPE="global"
    else
        INSTALLED_SCOPE="custom"
    fi
}

uninstall_local() {
    rm -fv "$LOCAL_BIN/$SCRIPT_NAME"
    rm -fv "$LOCAL_SHARE/applications/sc2-campaign-launcher.desktop"
    rm -rfv "$LOCAL_SHARE/SC2CampaignLauncher"
    update-desktop-database "$LOCAL_SHARE/applications" 2>/dev/null || true
    echo "Local uninstall complete."
}

uninstall_global() {
    [[ $EUID -ne 0 ]] && { echo "Global uninstall requires root (sudo)."; return 1; }
    rm -fv "$GLOBAL_BIN/$SCRIPT_NAME"
    rm -fv "$GLOBAL_SHARE/applications/sc2-campaign-launcher.desktop"
    rm -rfv "$GLOBAL_SHARE/SC2CampaignLauncher"
    update-desktop-database "$GLOBAL_SHARE/applications" 2>/dev/null || true
    echo "Global uninstall complete."
}

do_uninstall() {
    case "$INSTALLED_SCOPE" in
        local)
            uninstall_local
            ;;
        global)
            uninstall_global
            ;;
        both)
            echo "Detected both local and global installations."
            echo "What would you like to uninstall?"
            PS3="Select option [1-3]: "
            options=("Local only" "Global only" "Both")
            select opt in "${options[@]}"; do
                case $opt in
                    "Local only") uninstall_local ;;
                    "Global only") uninstall_global ;;
                    "Both") uninstall_local; uninstall_global ;;
                    *) echo "Invalid selection"; return 1 ;;
                esac
                break
            done
            ;;
        *)
            echo "No installation detected."
            ;;
    esac
}

install_local() {
    DEST_BIN="$LOCAL_BIN"
    DESKTOP_DIR="$LOCAL_SHARE/applications"
    ASSET_DEST="$LOCAL_SHARE/SC2CampaignLauncher/assets"
    SCOPE="local"
    install_common
}

install_global() {
    [[ $EUID -ne 0 ]] && { echo "Global install requires root (sudo)."; exit 1; }
    DEST_BIN="$GLOBAL_BIN"
    DESKTOP_DIR="$GLOBAL_SHARE/applications"
    ASSET_DEST="$GLOBAL_SHARE/SC2CampaignLauncher/assets"
    SCOPE="global"
    install_common
}

install_common() {
    # ---- Location checks ----
    [[ -f "./$SCRIPT_NAME" ]] || { echo "ERROR: $SCRIPT_NAME not found in $(pwd) — run from the project directory."; exit 1; }
    [[ -d ./assets ]] || echo "WARNING: ./assets not found — branding will be missing."

    # ---- Dependency detection & install ----
    need_deps=0
    command -v python3 >/dev/null || need_deps=1
    python3 -c 'import PyQt6' 2>/dev/null || need_deps=1
    command -v umu-run >/dev/null || need_deps=1

    if [[ $need_deps -eq 1 ]]; then
        source /etc/os-release
        DISTRO_ID="${ID:-unknown}"
        echo "Missing dependencies. Distro: $DISTRO_ID"
        case "$DISTRO_ID" in
            arch|cachyos|endeavouros|manjaro|garuda)
                PKGS='python python-pyqt6 umu-launcher'; PM='pacman -S --needed'; SUDO='sudo' ;;
            debian|ubuntu|linuxmint|pop)
                PKGS='python3 python3-pyqt6'; PM='apt-get install -y'; SUDO='sudo'
                echo "NOTE: umu-launcher is not packaged on Debian-family — install it from"
                echo "      https://github.com/Open-Wine-Components/umu-launcher/releases first." ;;
            fedora)
                PKGS='python3 python3-qt6'; PM='dnf install -y'; SUDO='sudo'
                echo "NOTE: install umu-launcher from upstream releases." ;;
            *)
                echo "Unrecognized distro — install python3, PyQt6 and umu-launcher manually, then re-run."
                read -rp "Continue anyway? [y/N] " yn
                [[ $yn =~ ^[Yy] ]] || exit 1
                PKGS='' ;;
        esac
        if [[ -n "${PKGS:-}" ]]; then
            read -rp "Install '$PKGS' with sudo now? [y/N] " yn
            if [[ $yn =~ ^[Yy] ]]; then
                $SUDO $PM $PKGS
            else
                echo "Continuing without deps — the app will not run until they are installed."
            fi
        fi
    fi

    # ---- File installation ----
    mkdir -p "$DEST_BIN" "$DESKTOP_DIR" "$ASSET_DEST"
    install -m 755 "./$SCRIPT_NAME" "$DEST_BIN/$SCRIPT_NAME"
    [[ -d ./assets ]] && cp -r ./assets/. "$ASSET_DEST/"

    # ---- Icon installation (CRITICAL FIX: system icon theme directories) ----
    if [[ "$SCOPE" == "global" ]]; then
        # Global: /usr/share/icons/
        ICON_DIR="/usr/share/icons/hicolor/48x48/apps"
        mkdir -p "$ICON_DIR"
        cp "$ASSET_DEST/logo.png" "$ICON_DIR/sc2-campaign-launcher.png"
    else
        # Local: ~/.local/share/icons/
        ICON_DIR="$HOME/.local/share/icons/hicolor/48x48/apps"
        mkdir -p "$ICON_DIR"
        cp "$ASSET_DEST/logo.png" "$ICON_DIR/sc2-campaign-launcher.png"
    fi

    # ---- Write .desktop file ONCE (no duplicates) ----
    DESKTOP_FILE="$DESKTOP_DIR/sc2-campaign-launcher.desktop"
    {
        echo '[Desktop Entry]'
        echo 'Type=Application'
        echo "Name=$APP_TITLE"
        echo 'Comment=Synergys Mod Launcher for Linux'
        echo "Exec=env QT_QPA_PLATFORM=xcb $DEST_BIN/$SCRIPT_NAME"
        echo 'Icon=sc2-campaign-launcher'
        echo 'Terminal=false'
        echo "StartupWMClass=SC2CampaignLauncher"
        echo 'X-KDE-StartupNotify=true'
        echo 'X-GNOME-Autostart-enabled=true'
        echo 'Categories=Game;'
        echo 'Keywords=StarCraft;SC2;Campaign;Launcher;'
    } > "$DESKTOP_FILE"

    # Record install scope in App.conf
    python3 - "$SCOPE" <<'PY' 2>/dev/null || \
    echo "NOTE: could not record install scope in App.conf — set install_scope manually in Settings if assets don't load."
import sys
from PyQt6.QtCore import QSettings
QSettings('SC2CampaignLauncher', 'App').setValue('install_scope', sys.argv[1])
PY

    # Update desktop database
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

    # Refresh icon cache (critical for icon to appear)
    if [[ "$SCOPE" == "global" ]]; then
        sudo gtk-update-icon-cache -t -f /usr/share/icons/hicolor/ 2>/dev/null || true
    else
        gtk-update-icon-cache -t -f "$HOME/.local/share/icons/hicolor/" 2>/dev/null || true
    fi

    echo "Installed successfully:"
    echo "  Script  → $DEST_BIN/$SCRIPT_NAME"
    echo "  Assets  → $ASSET_DEST"
    echo "  Icon    → sc2-campaign-launcher (theme icon)"
    echo "  Desktop → $DESKTOP_FILE"
    if [[ ":$PATH:" != *":$LOCAL_BIN:"* ]] && [[ "$DEST_BIN" == "$LOCAL_BIN" ]]; then
        echo "NOTE: $LOCAL_BIN is not in PATH — add it, or launch via the desktop entry."
    fi
}

echo "========================================"
echo "  SC2 Campaign Launcher Installer"
echo "========================================"
echo

check_install_state

case "$INSTALLED_SCOPE" in
    "")
        echo "No existing installation found."
        echo "Where would you like to install?"
        PS3="Select [1-2]: "
        options=("Local (~/.local)" "Global (/usr)")
        select opt in "${options[@]}"; do
            case $opt in
                "Local (~/.local)") install_local; break ;;
                "Global (/usr)") install_global; break ;;
                *) echo "Invalid selection" ;;
            esac
        done
        ;;
    local)
        echo "Local installation detected ($LOCAL_BIN)."
        echo "Options:"
        PS3="Select [1-2]: "
        options=("Reinstall Local" "Uninstall")
        select opt in "${options[@]}"; do
            case $opt in
                "Reinstall Local") install_local; break ;;
                "Uninstall") do_uninstall; break ;;
                *) echo "Invalid selection" ;;
            esac
        done
        ;;
    global)
        echo "Global installation detected ($GLOBAL_BIN)."
        echo "Options:"
        PS3="Select [1-2]: "
        options=("Reinstall Global" "Uninstall")
        select opt in "${options[@]}"; do
            case $opt in
                "Reinstall Global") install_global; break ;;
                "Uninstall") do_uninstall; break ;;
                *) echo "Invalid selection" ;;
            esac
        done
        ;;
    both)
        echo "Both local and global installations detected!"
        echo "What would you like to do?"
        PS3="Select [1-2]: "
        options=("Manage Existing Installations" "Install New Scope")
        select opt in "${options[@]}"; do
            case $opt in
                "Manage Existing Installations") do_uninstall ;;
                "Install New Scope")
                    # This shouldn't happen — if both exist, there's nowhere new to install
                    echo "Nothing new to install — both scopes are occupied."
                    ;;
                *) echo "Invalid selection" ;;
            esac
        done
        ;;
esac

echo
echo "Done."
exit 0
