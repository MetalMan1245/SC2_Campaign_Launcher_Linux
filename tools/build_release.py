"""Build the Windows folder bundle and package its complete runtime."""

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sc2_campaign_launcher_linux.files import atomic_bytes, atomic_json, file_hash, read_json
from sc2_campaign_launcher_linux.release_info import create_build_info, source_snapshot, validate_build_info


def package_bundle(bundle, archive):
    """Keep the previous ZIP intact if writing or validation is interrupted."""
    descriptor, temporary_name = tempfile.mkstemp(prefix='.sc2cl-package-', suffix='.zip', dir=archive.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as package:
            for file in sorted(bundle.rglob('*')):
                if file.is_symlink():
                    raise ValueError('A Windows bundle cannot contain symbolic links.')
                if file.is_file():
                    package.write(file, file.relative_to(bundle.parent))
        with zipfile.ZipFile(temporary) as package:
            if package.testzip() is not None:
                raise ValueError('The release archive failed its integrity check.')
        digest = file_hash(temporary)
        os.replace(temporary, archive)
        atomic_bytes(archive.with_suffix(archive.suffix + '.sha256'), f'{digest}  {archive.name}\n'.encode())
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ccm-only', action='store_true', help='Build the Custom Campaign Manager interface')
    parser.add_argument('--skip-build', action='store_true', help='Package an existing dist folder')
    parser.add_argument('--require-clean', action='store_true', help='Require recorded clean Git provenance')
    parser.add_argument('--output-dir', type=Path, help='Build into a separate directory, leaving an open demo bundle intact')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows release on Windows.')
    root = Path(__file__).resolve().parent.parent
    output = args.output_dir.expanduser().resolve() if args.output_dir else root / 'dist'
    name = 'SC2CampaignManager' if args.ccm_only else 'SC2CampaignLauncher'
    environment = dict(os.environ, SC2CL_CCM_ONLY='1' if args.ccm_only else '0')
    edition = 'ccm' if args.ccm_only else 'launcher'
    info = None
    if not args.skip_build:
        info = create_build_info(root, edition, require_clean=args.require_clean)
        atomic_json(root / 'build-info.json', info)
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                        '--distpath', str(output),
                        str(root / 'SC2CampaignLauncher.spec')], cwd=root, env=environment, check=True)
        if source_snapshot(root) != info['inputs']:
            raise ValueError('Source files changed during the build. No ZIP was replaced; rebuild from stable source.')
    bundle = output / name
    if not (bundle / (name + '.exe')).is_file() or not (bundle / '_internal').is_dir():
        raise SystemExit('The complete Windows bundle is missing.')
    if not list((bundle / '_internal').glob('python3*.dll')):
        raise SystemExit('The Windows bundle is missing Python.')
    recorded = validate_build_info(read_json(bundle / '_internal/build-info.json'), edition,
                                   require_clean=args.require_clean)
    if info is not None and recorded != info:
        raise ValueError('The bundle does not contain this build’s provenance. Rebuild before packaging.')
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    subprocess.run([str(bundle / (name + '.exe')), '--smoke-test'],
                   env=environment, cwd=bundle, check=True, timeout=30)
    archive = output / (name + '-Windows-x86_64.zip')
    package_bundle(bundle, archive)
    print(f"Build {recorded['source_sha256']} (commit={recorded['commit']}, dirty={recorded['dirty']})")
    print(archive)


if __name__ == '__main__':
    main()
