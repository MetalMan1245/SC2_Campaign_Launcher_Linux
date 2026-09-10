import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt' or not shutil.which('unzip'),
                                reason='Linux shell installer requires unzip')
ROOT = Path(__file__).resolve().parents[1]


def test_remote_installer_pins_the_ref_and_preserves_arguments(tmp_path):
    archive = tmp_path / 'release.zip'
    with zipfile.ZipFile(archive, 'w') as package:
        package.writestr('SC2_Campaign_Launcher_Linux-1.2.0/install-uninstall-SC2CLL.sh',
                         '#!/bin/bash\nprintf "%s\\n" "$@" > "$RESULT"\n')
    binary = tmp_path / 'bin'
    binary.mkdir()
    curl = binary / 'curl'
    curl.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$CURL_ARGS"\n'
                    'while [[ $# -gt 0 ]]; do\n'
                    '  if [[ "$1" == -o ]]; then cp -- "$ARCHIVE" "$2"; exit; fi\n'
                    '  shift\ndone\nexit 1\n')
    curl.chmod(0o755)
    scratch = tmp_path / 'temporary'
    scratch.mkdir()
    keep = scratch / 'personal.txt'
    keep.write_text('keep')
    result, args = tmp_path / 'result', tmp_path / 'curl-args'
    env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'],
               TMPDIR=str(scratch), ARCHIVE=str(archive), RESULT=str(result), CURL_ARGS=str(args))
    env.pop('SC2CL_REF', None)
    ref = 'a' * 40
    for reference in (None, ref):
        if reference:
            env['SC2CL_REF'] = reference
        subprocess.run(['bash', str(ROOT / 'synergy_remote-installer.sh'), '--install', 'custom',
                        '--directory', '/games/My Apps'], env=env, check=True, capture_output=True, timeout=10)
        assert result.read_text().splitlines() == ['--install', 'custom', '--directory', '/games/My Apps']
        assert f'/archive/{reference or "v1.2.0"}.zip' in args.read_text()
        assert list(scratch.iterdir()) == [keep]
    env['SC2CL_REF'] = 'main'
    rejected = subprocess.run(['bash', str(ROOT / 'synergy_remote-installer.sh')], env=env,
                              capture_output=True, text=True, timeout=10)
    assert rejected.returncode == 1
    assert 'release tag or a full commit ID' in rejected.stderr
