import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

root = Path(SPECPATH)
application = 'SC2CampaignManager' if os.environ.get('SC2CL_CCM_ONLY') == '1' else 'SC2CampaignLauncher'
if sys.platform == 'win32':
    # Resolve Windows DLLs before unrelated libraries installed on PATH.
    system = str(Path(os.environ['SystemRoot']) / 'System32')
    os.environ['PATH'] = os.pathsep.join([system, os.environ.get('PATH', '')])
data = [(str(root / 'assets'), 'assets'), (str(root / 'LICENSE'), '.'),
        (str(root / 'native'), 'native')]
if (root / 'build-info.json').is_file():
    data.append((str(root / 'build-info.json'), '.'))
for distribution in ('PyQt6', 'PyQt6-Qt6', 'PyQt6-sip', 'Pillow', 'mpyq'):
    data += copy_metadata(distribution)

analysis = Analysis(
    [str(root / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py')],
    pathex=[str(root)],
    datas=data,
    hiddenimports=collect_submodules('sc2_campaign_launcher_linux'),
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True,
    name=application, console=False,
    icon=str(root / 'assets/app.ico'),
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas,
    name=application,
)
