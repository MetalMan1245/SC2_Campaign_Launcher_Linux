#!/bin/bash
set -e

TMPDIR="$(mktemp -d /tmp/SC2CampaingLauncher.XXXXXX)"
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

echo "[Remote Installer] Creating temp workspace..."
cd "$TMPDIR"

echo "[Remote Installer] Downloading repo..."
curl -fsSL \
  -o repo.zip \
    https://github.com/MetalMan1245/SC2_Campaign_Launcher_Linux/archive/refs/heads/main.zip

echo "[Remote Installer] Extracting..."
unzip -q repo.zip
cd SC2_Campaign_Launcher_Linux

echo "[Remote Installer] Running installer..."
chmod +x install-uninstall-SC2CLL.sh
./install-uninstall-SC2CLL.sh

echo "[Remote Installer] Done."
