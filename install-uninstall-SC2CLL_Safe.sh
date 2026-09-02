#!/usr/bin/env bash
# SC2 Campaign Launcher installer for Linux
# Usage: ./install-uninstall-SC2CLL.sh [--global] [--uninstall]
set -euo pipefail

SCRIPT_NAME='sc2_campaign_launcher_linux.py'
APP_TITLE='SC2 Campaign Launcher'
LOCAL_BIN="$HOME/.local/bin"
LOCAL_SHARE="$HOME/.local/share"
GLOBAL_BIN='/usr/local/bin'
GLOBAL_SHARE='/usr/share'

IS_GLOBAL=0
UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --global) IS_GLOBAL=1 ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

if [[ $IS_GLOBAL -eq 1 ]]; then
  DEST_BIN="$GLOBAL_BIN"
  DESKTOP_DIR="$GLOBAL_SHARE/applications"
  ASSET_DEST="$GLOBAL_SHARE/SC2CampaignLauncher/assets"
  [[ $EUID -ne 0 ]] && { echo "Global install requires root (sudo)."; exit 1; }
else
  DEST_BIN="$LOCAL_BIN"
  DESKTOP_DIR="$LOCAL_SHARE/applications"
  ASSET_DEST="$LOCAL_SHARE/SC2CampaignLauncher/assets"
fi

uninstall() {
  rm -fv "$DEST_BIN/$SCRIPT_NAME"
  rm -fv "$DESKTOP_DIR/sc2-campaign-launcher.desktop"
  rm -rfv "$LOCAL_SHARE/SC2CampaignLauncher" "$GLOBAL_SHARE/SC2CampaignLauncher"
  update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  echo "Uninstalled."
  exit 0
}

[[ $UNINSTALL -eq 1 ]] && uninstall

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

# ---- Icon: prefer PNG, fall back to ICO ----
ICON_FILE=""
for cand in "$ASSET_DEST/logo.png" "$ASSET_DEST/app.ico"; do
  [[ -f "$cand" ]] && ICON_FILE="$cand" && break
done

DESKTOP_FILE="$DESKTOP_DIR/sc2-campaign-launcher.desktop"
{
  echo '[Desktop Entry]'
  echo 'Type=Application'
  echo "Name=$APP_TITLE"
  echo 'Comment=Download and launch SC2 campaigns on Linux via umu/Proton'
  echo "Exec=$DEST_BIN/$SCRIPT_NAME"
  [[ -n "$ICON_FILE" ]] && echo "Icon=$ICON_FILE"
  echo 'Terminal=false'
  echo 'Categories=Game;'
} > "$DESKTOP_FILE"

update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "Installed successfully:"
echo "  Script  → $DEST_BIN/$SCRIPT_NAME"
echo "  Assets  → $ASSET_DEST"
echo "  Icon    → ${ICON_FILE:-none}"
echo "  Desktop → $DESKTOP_FILE"
if [[ $IS_GLOBAL -eq 0 ]] && [[ ":$PATH:" != *":$LOCAL_BIN:"* ]]; then
  echo "NOTE: $LOCAL_BIN is not in PATH — add it, or launch via the desktop entry."
fi
