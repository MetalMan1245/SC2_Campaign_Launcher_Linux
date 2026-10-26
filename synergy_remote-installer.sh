#!/usr/bin/env bash
set -euo pipefail

RELEASE_REF="${SC2CL_REF:-feature/ccm-manager}"
REPOSITORY="RogerRoger29/SC2_Campaign_Launcher_Linux"
if [[ ! "$RELEASE_REF" =~ ^[a-zA-Z0-9._/-]+$ || "$RELEASE_REF" == *..* ]]; then
    echo 'SC2CL_REF must be a branch name, tag, or commit ID.' >&2
    exit 1
fi
for dependency in curl python3 unzip mktemp; do
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
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 60 \
    "https://api.github.com/repos/$REPOSITORY/commits/$RELEASE_REF" --output "$WORKSPACE/commit.json"
COMMIT="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["sha"])' "$WORKSPACE/commit.json")"
if [[ ! "$COMMIT" =~ ^[a-fA-F0-9]{40}$ ]]; then
    echo 'GitHub returned an invalid commit ID.' >&2
    exit 1
fi
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
    --connect-timeout 15 --max-time 300 \
    "https://codeload.github.com/$REPOSITORY/zip/$COMMIT" --output "$WORKSPACE/repo.zip"
unzip -q "$WORKSPACE/repo.zip" -d "$WORKSPACE"
shopt -s nullglob
sources=("$WORKSPACE"/*/)
if [[ ${#sources[@]} != 1 || ! -f "${sources[0]}/install-uninstall-SC2CLL.sh" ]]; then
    echo 'The repository archive is incomplete.' >&2
    exit 1
fi
printf '{"commit":"%s"}\n' "$COMMIT" > "${sources[0]}/build-info.json"
bash "${sources[0]}/install-uninstall-SC2CLL.sh" "$@"
