#!/usr/bin/env bash
# SC2 Campaign Launcher installer for Linux (interactive CLI)
# Usage: ./install-uninstall-SC2CLL.sh
set -euo pipefail

SCRIPT_NAME='sc2_campaign_launcher_linux.py'
APP_SRC_DIR='sc2_campaign_launcher_linux'          # package dir in the repo
APP_TITLE='SC2 Campaign Launcher'
APP_DEST=""                                         # set per-scope in install_*()
LOCAL_BIN="$HOME/.local/bin"
LOCAL_SHARE="$HOME/.local/share"
STATE_DIR="$HOME/.config/SC2CampaignLauncher"
STATE_FILE="$STATE_DIR/install_path"   # records the custom install location
DEST_BIN=""
DESKTOP_DIR=""
ASSET_DEST=""
SELF="$(readlink -f "$0")"
UNINSTALL_PATH="$HOME/.local/bin/install-uninstall-SC2CLL.sh"

# Detect installation state
LOCAL_INSTALLED=0
CUSTOM_INSTALLED=0
INSTALLED_SCOPE=""  # "local", "custom", or "both"

check_install_state() {
    CUSTOM_PATH=""
    [[ -f "$STATE_FILE" ]] && CUSTOM_PATH="$(head -n1 "$STATE_FILE")"
    [[ -n "$CUSTOM_PATH" && -f "$CUSTOM_PATH/$SCRIPT_NAME" ]] && CUSTOM_INSTALLED=1
    [[ -f "$LOCAL_SHARE/SC2CampaignLauncher/$SCRIPT_NAME" ]] && LOCAL_INSTALLED=1

    if [[ $LOCAL_INSTALLED -eq 1 ]] && [[ $CUSTOM_INSTALLED -eq 1 ]]; then
        INSTALLED_SCOPE="both"
    elif [[ $LOCAL_INSTALLED -eq 1 ]]; then
        INSTALLED_SCOPE="local"
    elif [[ $CUSTOM_INSTALLED -eq 1 ]]; then
        INSTALLED_SCOPE="custom"
    else
        INSTALLED_SCOPE=""
    fi
}

uninstall_local() {
    rm -fv "$LOCAL_BIN/$SCRIPT_NAME"
    rm -fv "$LOCAL_SHARE/applications/sc2-campaign-launcher.desktop"
    rm -rfv "$LOCAL_SHARE/SC2CampaignLauncher"
    update-desktop-database "$LOCAL_SHARE/applications" 2>/dev/null || true
    rm -fv "$UNINSTALL_PATH"   # last — while the running copy is fine, anything after would still work
    rm -fv "$HOME/.local/share/icons/hicolor/48x48/apps/sc2-campaign-launcher.png"
    rm -fv "$STATE_FILE"
    echo "Local uninstall complete."
}

uninstall_custom() {
    local target="$(head -n1 "$STATE_FILE" 2>/dev/null || echo)"
    if [[ -z "$target" || ! -f "$target/$SCRIPT_NAME" ]]; then
        echo "Custom install path unknown or already removed — cleaning state file only."
        rm -fv "$STATE_FILE"
        return
    fi
    rm -rfv "$target"
    rm -fv "$LOCAL_SHARE/applications/sc2-campaign-launcher-custom.desktop" 2>/dev/null
    rm -fv "$STATE_FILE"
    update-desktop-database "$LOCAL_SHARE/applications" 2>/dev/null || true
    echo "Custom uninstall complete (removed $target)."
}

do_uninstall() {
    case "$INSTALLED_SCOPE" in
        local)
            uninstall_local
            ;;
        custom)
            uninstall_custom
            ;;
        both)
            echo "Detected both local and custom installations."
            echo "What would you like to uninstall?"
            PS3="Select option [1-3]: "
            options=("Local only" "Custom only" "Both")
            select opt in "${options[@]}"; do
                case $opt in
                    "Local only") uninstall_local ;;
                    "Custom only") uninstall_custom ;;
                    "Both") uninstall_local; uninstall_custom ;;
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
    SHARE_DEST="$LOCAL_SHARE/SC2CampaignLauncher"
    ASSET_DEST="$SHARE_DEST/assets"
    APP_DEST="$SHARE_DEST"
    SCOPE="local"
    install_common
}

install_custom() {
    read -rp "Install directory for app files (e.g. /opt/sc2cl or $HOME/apps/sc2cl): " USER_DIR
    USER_DIR="${USER_DIR/#\~/$HOME}"
    if [[ -z "$USER_DIR" ]]; then echo "No directory given."; exit 1; fi
    if [[ -d "$USER_DIR" && -n "$(ls -A "$USER_DIR" 2>/dev/null)" ]]; then
        read -rp "'$USER_DIR' is not empty — install into it anyway? [y/N] " yn
        [[ $yn =~ ^[Yy] ]] || exit 1
    fi
    mkdir -p "$USER_DIR" || { echo "Cannot create $USER_DIR (permission denied?)"; exit 1; }

    DEST_BIN="$LOCAL_BIN"
    DESKTOP_DIR="$LOCAL_SHARE/applications"
    APP_DEST="$USER_DIR"
    ASSET_DEST="$APP_DEST/assets"
    SCOPE="custom"
    install_common
    # Persist for uninstall + record scope for the app
    mkdir -p "$STATE_DIR"; echo "$USER_DIR" > "$STATE_FILE"
    python3 - "$SCOPE" "$ASSET_DEST" <<'PY' 2>/dev/null || \
    echo "NOTE: could not record scope in QSettings — set install_scope=custom in Settings if assets don't load."
import sys
from PyQt6.QtCore import QSettings
QSettings('SC2CampaignLauncher', 'App').setValue('install_scope', sys.argv[1])
QSettings('SC2CampaignLauncher', 'App').setValue('custom_asset_dir', sys.argv[2])
PY
}

install_common() {
    # ---- Location checks ----
    [[ -f "./$APP_SRC_DIR/$SCRIPT_NAME" ]] || { echo "ERROR: ./$APP_SRC_DIR/$SCRIPT_NAME not found in $(pwd) — run from the project directory."; exit 1; }
    [[ -f "./$APP_SRC_DIR/platform_backend.py" ]] || { echo "ERROR: ./$APP_SRC_DIR/platform_backend.py missing — repo checkout is incomplete (use 'Download ZIP', not a partial copy)."; exit 1; }
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
    mkdir -p "$DEST_BIN" "$DESKTOP_DIR" "$ASSET_DEST" "$APP_DEST"
    install -m 755 "./$APP_SRC_DIR/$SCRIPT_NAME" "./$APP_SRC_DIR/platform_backend.py" "$APP_DEST/"
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

    # create_uninstall_method
    mkdir -p "$HOME/.local/bin"
    if [ "$(readlink -f "$UNINSTALL_PATH" 2>/dev/null || echo)" != "$SELF" ]; then
        cp "$SELF" "$UNINSTALL_PATH"
        chmod +x "$UNINSTALL_PATH"
    fi

    # ---- Write .desktop file ----
    if [[ "$SCOPE" == "custom" ]]; then
        DESKTOP_FILE="$DESKTOP_DIR/sc2-campaign-launcher-custom.desktop"
    else
        DESKTOP_FILE="$DESKTOP_DIR/sc2-campaign-launcher.desktop"
    fi
    {
        echo '[Desktop Entry]'
        echo 'Type=Application'
        echo "Name=$APP_TITLE"
        echo 'Comment=Synergys Mod Launcher for Linux'
        echo "Exec=env QT_QPA_PLATFORM=xcb python3 $APP_DEST/$SCRIPT_NAME"
        echo 'Icon=sc2-campaign-launcher'
        echo 'Terminal=false'
        echo "StartupWMClass=SC2CampaignLauncher"
        echo 'X-KDE-StartupNotify=true'
        echo 'X-GNOME-Autostart-enabled=true'
        echo 'Categories=Game;'
        echo 'Keywords=StarCraft;SC2;Campaign;Launcher;'
        echo 'Actions=uninstall;'
        echo ''
        echo '[Desktop Action uninstall]'
        echo 'Name=Uninstall'
        echo 'Name[en_US]=Uninstall'
        echo "Exec=konsole -e $UNINSTALL_PATH"
        echo 'Icon=edit-delete-remove'

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
    echo "  Script      → $APP_DEST/$SCRIPT_NAME"
    echo "  Assets      → $ASSET_DEST"
    echo "  Icon        → sc2-campaign-launcher (theme icon)"
    echo "  Desktop     → $DESKTOP_FILE"
    echo "  Uninstaller → $UNINSTALL_PATH"
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
        options=("Local (~/.local)  [recommended]" "Custom directory")
        select opt in "${options[@]}"; do
            case $opt in
                "Local (~/.local)  [recommended]") install_local; break ;;
                "Custom directory") install_custom; break ;;
                *) echo "Invalid selection" ;;
            esac
        done
        ;;
    local)
        echo "Local installation detected ($LOCAL_SHARE/SC2CampaignLauncher)."
        echo "Options:"
        PS3="Select [1-3]: "
        options=("Reinstall Local" "Also Install Custom" "Uninstall")
        select opt in "${options[@]}"; do
            case $opt in
                "Reinstall Local") install_local; break ;;
                "Also Install Custom") install_custom; break ;;
                "Uninstall") do_uninstall; break ;;
                *) echo "Invalid selection" ;;
            esac
        done
        ;;
    custom)
        echo "Custom installation detected ($(head -n1 "$STATE_FILE" 2>/dev/null))."
        echo "Options:"
        PS3="Select [1-3]: "
        options=("Reinstall Custom" "Also Install Local" "Uninstall")
        select opt in "${options[@]}"; do
            case $opt in
                "Reinstall Custom") install_custom; break ;;
                "Also Install Local") install_local; break ;;
                "Uninstall") do_uninstall; break ;;
                *) echo "Invalid selection" ;;
            esac
        done
        ;;
    both)
        echo "Both local and custom installations detected!"
        echo "What would you like to do?"
        PS3="Select [1-2]: "
        options=("Manage Existing Installations" "Reinstall a Scope")
        select opt in "${options[@]}"; do
            case $opt in
                "Manage Existing Installations") do_uninstall; break ;;
                "Reinstall a Scope")
                    PS3="Which scope? [1-2]: "
                    scopes=("Local" "Custom")
                    select s in "${scopes[@]}"; do
                        case $s in
                            "Local") install_local; break ;;
                            "Custom") install_custom; break ;;
                            *) echo "Invalid selection" ;;
                        esac
                    done
                    break
                    ;;
                *) echo "Invalid selection" ;;
            esac
        done
        ;;
esac
