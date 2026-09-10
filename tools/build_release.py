"""Build the Windows folder bundle and package its complete runtime."""

import argparse
import os
import subprocess
import sys
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build', action='store_true', help='Package an existing dist folder')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows release on Windows.')
    root = Path(__file__).resolve().parent.parent
    output = root / 'dist'
    if not args.skip_build:
        subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                        str(root / 'SC2CampaignLauncher.spec')], cwd=root, check=True)
    bundle = output / 'SC2CampaignLauncher'
    if not (bundle / 'SC2CampaignLauncher.exe').is_file() or not (bundle / '_internal').is_dir():
        raise SystemExit('The complete Windows bundle is missing.')
    if not list((bundle / '_internal').glob('python3*.dll')):
        raise SystemExit('The Windows bundle is missing Python.')
    environment = dict(os.environ, QT_QPA_PLATFORM='offscreen')
    subprocess.run([str(bundle / 'SC2CampaignLauncher.exe'), '--smoke-test'],
                   env=environment, cwd=bundle, check=True, timeout=30)
    archive = output / 'SC2CampaignLauncher-Windows-x86_64.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for file in sorted(bundle.rglob('*')):
            if file.is_file():
                package.write(file, file.relative_to(output))
    with zipfile.ZipFile(archive) as package:
        if package.testzip() is not None:
            raise SystemExit('The release archive failed its integrity check.')
    print(archive)


if __name__ == '__main__':
    main()
