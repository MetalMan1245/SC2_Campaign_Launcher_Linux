#!/usr/bin/env bash
set -euo pipefail

RELEASE_REF="${SC2CL_REF:-v1.2.0}"
if [[ ! "$RELEASE_REF" =~ ^v[0-9]+\.[0-9]+(\.[0-9]+)?$ && ! "$RELEASE_REF" =~ ^[0-9a-f]{40}$ ]]; then
    echo 'SC2CL_REF must be a release tag or a full commit ID.' >&2
    exit 1
fi
for dependency in curl unzip mktemp; do
    if ! command -v "$dependency" >/dev/null; then
        echo "Install $dependency before running the remote installer." >&2
        exit 1
    fi
done

WORKSPACE="$(mktemp -d -t sc2-campaign-launcher.XXXXXX)"
cleanup() {
    if [[ -d "$WORKSPACE" && "$(basename -- "$WORKSPACE")" == sc2-campaign-launcher.* ]]; then
        rm -rf -- "$WORKSPACE"
    fi
}
trap cleanup EXIT

echo "Downloading SC2 Campaign Launcher $RELEASE_REF..."
curl --fail --show-error --silent --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 300 --retry 2 \
    "https://github.com/MetalMan1245/SC2_Campaign_Launcher_Linux/archive/$RELEASE_REF.zip" \
    -o "$WORKSPACE/repo.zip"
unzip -q "$WORKSPACE/repo.zip" -d "$WORKSPACE"
shopt -s nullglob
sources=("$WORKSPACE"/SC2_Campaign_Launcher_Linux-*)
if [[ ${#sources[@]} != 1 || ! -f "${sources[0]}/install-uninstall-SC2CLL.sh" ]]; then
    echo 'The release archive is incomplete.' >&2
    exit 1
fi
bash "${sources[0]}/install-uninstall-SC2CLL.sh" "$@"
