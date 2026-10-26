import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt' or not shutil.which('unzip'),
                                reason='Linux shell installer requires unzip')
ROOT = Path(__file__).resolve().parents[1]


def test_public_installer_tracks_selected_ref_and_preserves_arguments(tmp_path):
    archive = tmp_path / 'release.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('RogerRoger29-SC2_Campaign_Launcher_Linux-abc/install-uninstall-SC2CLL.sh',
                         '#!/bin/bash\nprintf "%s\\n" "$@" > "$RESULT"\n')
    binary = tmp_path / 'bin'
    binary.mkdir()
    curl = binary / 'curl'
    curl.write_text('#!/bin/bash\n'
                    'printf "%s\\n" "$@" >> "$CURL_ARGS"\n'
                    'while [[ $# -gt 0 ]]; do\n'
                    '  case "$1" in\n'
                    '    https://*) url="$1" ;;\n'
                    '    --output) shift; output="$1" ;;\n'
                    '  esac\n'
                    '  shift\n'
                    'done\n'
                    'if [[ "$url" == */zip/* ]]; then cp "$ARCHIVE" "$output"; '
                    'else printf \'{"sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}\' > "$output"; fi\n')
    curl.chmod(0o755)
    scratch = tmp_path / 'temporary'
    scratch.mkdir()
    keep = scratch / 'personal.txt'
    keep.write_text('keep')
    result, args = tmp_path / 'result', tmp_path / 'curl-args'
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
               TMPDIR=str(scratch), ARCHIVE=str(archive), RESULT=str(result), CURL_ARGS=str(args))
    env.pop('SC2CL_REF', None)
    for reference in (None, 'a' * 40, 'community-backend', 'v1.2.0'):
        if reference:
            env['SC2CL_REF'] = reference
        subprocess.run(['bash', str(ROOT / 'synergy_remote-installer.sh'), '--install', 'custom',
                        '--directory', '/games/My Apps'], env=env, check=True, capture_output=True, timeout=10)
        assert result.read_text().splitlines() == ['--install', 'custom', '--directory', '/games/My Apps']
        assert f'/commits/{reference or "feature/ccm-manager"}' in args.read_text()
        assert "/zip/" + "b" * 40 in args.read_text()
        assert 'MetalMan1245' not in args.read_text()
        assert 'RogerRoger29/SC2_Campaign_Launcher_Linux' in args.read_text()
        assert 'Private' not in args.read_text()
        assert list(scratch.iterdir()) == [keep]


@pytest.mark.parametrize('reference', ['../main', 'main?path=other', 'branch name'])
def test_remote_installer_rejects_invalid_ref_before_downloading(tmp_path, reference):
    result = subprocess.run(['bash', str(ROOT / 'synergy_remote-installer.sh')],
                            env=dict(os.environ, SC2CL_REF=reference), capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'SC2CL_REF must be' in result.stderr
