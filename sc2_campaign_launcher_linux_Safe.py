#!/usr/bin/env python3
"""SC2 Campaign Launcher - Map downloading and launcher for Linux/Wine"""

import sys
import json
import os
import shutil
import urllib.request
import urllib.error
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QFileDialog,
    QDialog, QLineEdit, QMessageBox, QGridLayout, QCheckBox, QComboBox, QInputDialog, QStackedWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QRect, QUrl
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont, QDesktopServices

GITHUB_REPO = 'R-P-S/SC2Campaigns'
GITHUB_BRANCH = 'main'
MAPS_JSON_URL = f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/maps.json'
DEFAULT_SC2_ROOT = Path.home() / 'Games'

AUTHOR_OVERRIDE = {
    'uedfl': 'Oracle',
    'ued-fl': 'Oracle',
}

# Default author for most campaigns
DEFAULT_AUTHOR = 'Synergy'

def slugify(name: str) -> str:
    return ''.join(ch for ch in name.lower() if ch.isalnum())

# Heroic-style hardcoded scan paths for Wine/Proton discovery
WINE_SEARCH_DIRS = [
    Path.home() / '.config' / 'heroic' / 'tools' / 'wine',        # user-managed
    Path.home() / '.config' / 'heroic' / 'proton' / 'proton',     # Heroic managed proton
    Path.home() / '.config' / 'heroic' / 'tools' / 'proton',      # Heroic managed proton
    Path.home() / '.steam' / 'root' / 'compatibilitytools.d',
    Path.home() / '.local' / 'share' / 'Steam' / 'compatibilitytools.d',
    Path.home() / '.steam' / 'steam' / 'steamapps' / 'common',
    Path.home() / '.local' / 'share' / 'lutris' / 'runners' / 'wine',
    Path('/usr/share/steam/compatibilitytools.d'),
    Path('/usr/share/steam'),
]

RUNTIME_DIR_NAMES = {'EasyAntiCheat', 'BattlEye', 'SteamLinuxRuntime_sniper',
                     'SteamLinuxRuntime_soldier'}

def _fingerprint_runner(path: Path) -> tuple[str, str] | None:
    """Classify a directory as proton/wine. Returns (type, name) or None."""
    name = path.name
    if (path / 'toolmanifest.vdf').is_file():
        return 'proton', name
    # wine layout: bin/wine directly under the version dir
    if (path / 'bin' / 'wine').is_file():
        return 'wine', name
    # proton layouts: files/bin/wine or dist/bin/wine (some builds ship toolmanifest too)
    if (path / 'files' / 'bin' / 'wine').is_file() or (path / 'dist' / 'bin' / 'wine').is_file():
        return 'proton', name
    return None

def _runner_rank(rtype: str, name: str) -> int:
    """Preference order: CachyOS Proton > Experimental > Valve Proton > GE-Proton > Wine."""
    n = name.lower()
    if 'cachyos' in n:
        return 0
    if 'experimental' in n:
        return 1
    if 'ge' in n:
        return 3          # GE before generic proton check (GE-Proton contains 'proton')
    if 'proton' in n:
        return 2
    if rtype == 'proton':
        return 2          # unnamed proton builds group with valve proton
    return 4              # wine

def discover_wine_versions(custom_paths: list[str] | None = None) -> list[dict]:
    """Scan known directories for Wine/Proton installations.
    Returns [{name, path, type}] sorted by preference rank."""
    found: dict[str, dict] = {}

    def consider(path: Path):
        try:
            real = path.resolve()
            if not path.is_dir() or str(real) in found:
                return
            fp = _fingerprint_runner(path)
            if fp is None:
                return
            rtype, name = fp
            low = name.lower()
            if any(x in low for x in ('easyanticheat', 'battleye', 'steamlinuxruntime')):
                return
            found[str(real)] = {'name': name, 'path': str(path), 'type': rtype}
        except OSError:
            pass

    for d in WINE_SEARCH_DIRS:
        if d.is_dir():
            for child in d.iterdir():
                if child.is_dir():
                    consider(child)
    # Heroic tools dirs: scan one level deep (tools/wine/<version>/ and tools/proton/<version>/)
    for base in (Path.home() / '.config' / 'heroic' / 'tools' / 'wine',
                 Path.home() / '.config' / 'heroic' / 'tools' / 'proton'):
        if base.is_dir():
            for child in base.iterdir():
                consider(child)

    # System wine
    sys_wine = shutil.which('wine')
    if sys_wine:
        found[sys_wine] = {'name': f'System Wine ({sys_wine})',
                           'path': sys_wine, 'type': 'wine'}

    # User-defined custom entries
    for p in (custom_paths or []):
        p = Path(os.path.expanduser(p))
        if p.is_dir():
            fp = _fingerprint_runner(p)
            if fp:
                found[str(p)] = {'name': f'{fp[1]} (custom)', 'path': str(p), 'type': fp[0]}
        elif p.is_file():
            rtype = 'proton' if p.name == 'proton' else 'wine'
            found[str(p)] = {'name': f'{p.name} (custom)', 'path': str(p), 'type': rtype}

    versions = list(found.values())
    versions.sort(key=lambda v: (_runner_rank(v['type'], v['name']), v['name'].lower()))
    return versions

# Directories never descended into during SC2 scans (perf pruning)
SCAN_SKIP_DIRS = {'windows', 'windows.old', '.cache', 'shadercache', 'workshop',
                  'downloading', 'temp', 'node_modules', '.git', '__pycache__',
                  'depotcache', 'appcache', 'logs', 'dumps'}

def _is_sc2_root(p: Path) -> bool:
    """Fingerprint: SC2 installs always ship Support64/SC2Switcher_x64.exe."""
    return (p / 'Support64' / 'SC2Switcher_x64.exe').is_file()

class Sc2Scanner(QThread):
    found = pyqtSignal(str)            # live updates as finds happen
    finished_signal = pyqtSignal(list) # all unique SC2 roots

    def __init__(self, roots: list[Path], parent=None):
        super().__init__(parent)
        self._roots = roots
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        results: dict[str, str] = {}   # realpath → display path
        for root in self._roots:
            if self._cancelled or not root.is_dir():
                continue
            walk_iter = os.walk(root, topdown=True, onerror=None,
                                followlinks=False)
            for dirpath, dirnames, filenames in walk_iter:
                if self._cancelled:
                    break
                p = Path(dirpath)
                if _is_sc2_root(p):
                    real = str(p.resolve())
                    if real not in results:
                        results[real] = dirpath
                        self.found.emit(dirpath)
                    # Don't descend into the install itself
                    dirnames[:] = []
                    continue
                # Prune: performance, plus skip the probe-descend games
                dirnames[:] = [d for d in dirnames
                               if d.lower() not in SCAN_SKIP_DIRS]
        self.finished_signal.emit(list(results.values()))

# Quick-scan locations (checked first, in order)
SC2_QUICK_ROOTS = [
    Path.home() / '.local' / 'share' / 'Steam' / 'steamapps' / 'common',
    Path.home() / '.steam' / 'steam' / 'steamapps' / 'common',
    Path.home() / '.local' / 'share' / 'Steam' / 'steamapps' / 'compatdata',
    Path.home() / '.steam' / 'steam',
    Path.home() / '.wine',
    Path.home() / '.local' / 'share' / 'umu',
    Path.home() / 'Games',
]

class AppSettings:
    def __init__(self):
        self.settings = QSettings('SC2CampaignLauncher', 'App')

    def sc2_root(self) -> Path:
        path = self.settings.value('sc2_root', str(DEFAULT_SC2_ROOT), type=str)
        return Path(path)

    def set_sc2_root(self, path: Path):
        self.settings.setValue('sc2_root', str(path))

    def wine_prefix(self) -> str:
        """Derive Wine prefix from SC2 root by stripping everything after drive_c"""
        root_str = str(self.sc2_root())
        if 'drive_c' in root_str:
            # e.g. /home/f/.../drive_c/Program Files (x86)/StarCraft II → /home/f/...
            drive_c_idx = root_str.index('drive_c')
            return root_str[:drive_c_idx].rstrip('/')
        # Fallback: try stored value
        val = self.settings.value('wine_prefix', type=str)
        return val if val else ''

    def proton_path(self) -> str:
        return self.settings.value('wine_binary',
                                   '/usr/share/steam/compatibilitytools.d/proton-cachyos-slr',
                                   type=str)  # Remove trailing /proton

    def set_wine_binary(self, binary: str):
        self.settings.setValue('wine_binary', binary)

    def sc2_switcher_path(self) -> str:
        """Derive SC2Switcher path from SC2 root"""
        switcher = self.sc2_root() / 'Support64' / 'SC2Switcher_x64.exe'
        if switcher.exists():
            return str(switcher)
        # Fallback: try stored value
        val = self.settings.value('sc2_switcher', type=str)
        return val if val else ''

    def set_sc2_switcher_path(self, path: str):
        self.settings.setValue('sc2_switcher', path)

    def get_installed_campaigns(self) -> list[str]:
        val = self.settings.value('installed_campaigns', [], type=list)
        return val if val else []

    def set_installed_campaigns(self, slugs: list[str]):
        self.settings.setValue('installed_campaigns', slugs)

    def is_campaign_installed(self, slug: str) -> bool:
        return slug in self.get_installed_campaigns()

    def add_campaign_to_installed(self, slug: str):
        installed = self.get_installed_campaigns()
        if slug not in installed:
            installed.append(slug)
            self.set_installed_campaigns(installed)
            print(f'[SETTINGS] Added {slug} to installed campaigns')

    def remove_campaign_from_installed(self, slug: str):
        installed = self.get_installed_campaigns()
        if slug in installed:
            installed.remove(slug)
            self.set_installed_campaigns(installed)
            print(f'[SETTINGS] Removed {slug} from installed campaigns')

    def validate_all_campaign_statuses(self, campaigns: list[dict]) -> list[dict]:
        """Re-check disk state for all campaigns and update their status fields."""
        for camp in campaigns:
            folder = camp.get('folder') or camp['slug']
            maps_dir = self.sc2_root() / 'Maps' / folder

            maps_list = camp.get('maps', [])
            mods_list = camp.get('mods', [])

            maps_ok = all((maps_dir / m['name']).exists() for m in maps_list)
            mods_ok = all(self.is_mod_current(mo['name'], mo.get('sha256')) for mo in mods_list)

            if not maps_ok:
                camp['status'] = 'not_installed'
            elif not mods_ok:
                camp['status'] = 'update_available'
            else:
                camp['status'] = 'installed'

        return campaigns

    def wine_prefix_override(self) -> str | None:
        """Get manually set wine prefix override (if any)"""
        val = self.settings.value('wine_prefix_override', type=str)
        return val if val else None

    def set_wine_prefix_override(self, prefix: str | None):
        """Set or clear manual wine prefix override"""
        if prefix:
            self.settings.setValue('wine_prefix_override', prefix)
        else:
            self.settings.remove('wine_prefix_override')

    def use_auto_prefix(self) -> bool:
        """Check if automatic prefix detection is enabled"""
        return self.settings.value('use_auto_prefix', True, type=bool) == True

    def set_use_auto_prefix(self, enabled: bool):
        """Toggle automatic prefix detection"""
        self.settings.setValue('use_auto_prefix', enabled)

    def wine_prefix(self) -> str:
        """Derive Wine prefix from SC2 root by stripping everything after drive_c"""
        if self.use_auto_prefix():
            root_str = str(self.sc2_root())
            if 'drive_c' in root_str:
                # e.g. /home/f/.../drive_c/Program Files (x86)/StarCraft II → /home/f/...
                drive_c_idx = root_str.index('drive_c')
                return root_str[:drive_c_idx].rstrip('/')
            # Fallback: try stored override
            val = self.wine_prefix_override()
            return val if val else ''
        else:
            # Manual override takes precedence
            val = self.wine_prefix_override()
            return val if val else ''

    def get_shared_mod_registry(self) -> dict[str, dict]:
        """Global registry of downloaded mods: {filename: {'hash': ..., 'version': ..., 'campaigns': [...]}}"""
        val = self.settings.value('shared_mod_registry', {}, type=dict)
        return val if val else {}

    def set_shared_mod_registry(self, registry: dict):
        self.settings.setValue('shared_mod_registry', registry)

    def register_mod_file(self, filename: str, file_hash: str, version: str = '1.0', campaigns: list[str] = None):
        """Track a mod file in the global registry"""
        registry = self.get_shared_mod_registry()
        if filename not in registry:
            registry[filename] = {'hash': file_hash, 'version': version, 'campaigns': []}
        else:
            registry[filename]['hash'] = file_hash
            registry[filename]['version'] = version

        if campaigns:
            for slug in campaigns:
                if slug not in registry[filename]['campaigns']:
                    registry[filename]['campaigns'].append(slug)

        self.set_shared_mod_registry(registry)

    def unregister_campaign_from_mods(self, slug: str):
        """Remove a campaign from all mod registry entries"""
        registry = self.get_shared_mod_registry()
        for filename in registry:
            if slug in registry[filename]['campaigns']:
                registry[filename]['campaigns'].remove(slug)

        # Remove orphaned entries (no campaigns using this mod)
        registry = {k: v for k, v in registry.items() if v['campaigns']}
        self.set_shared_mod_registry(registry)

    # --- replaces is_mod_up_to_date(): self-healing, disk-truth based ---
    def is_mod_current(self, filename: str, remote_sha: str | None) -> bool:
        """True if the shared mod file on disk matches the remote sha256.
        Registry is a fast path; disk hash is the source of truth."""
        dest = self.sc2_root() / 'Mods' / filename
        if not dest.exists():
            return False
        if remote_sha is None:
            return True  # nothing to verify against
        registry = self.get_shared_mod_registry()
        entry = registry.get(filename)
        if entry and entry.get('hash') == remote_sha:
            return True
        local = self.compute_file_hash(dest)
        if local == remote_sha:
            self.register_mod_file(filename, local)  # heal the registry
            return True
        return False

    @staticmethod
    def hash_bytes(data: bytes) -> str:
        import hashlib
        return hashlib.sha256(data).hexdigest()

    def compute_file_hash(self, filepath: Path) -> str:
        """Compute SHA256 hash of a file on disk."""
        import hashlib
        sha256 = hashlib.sha256()
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return sha256.hexdigest()

    def custom_wine_paths(self) -> list[str]:
        val = self.settings.value('custom_wine_paths', [], type=list)
        return val if val else []

    def add_custom_wine_path(self, path: str):
        paths = self.custom_wine_paths()
        if path not in paths:
            paths.append(path)
            self.settings.setValue('custom_wine_paths', paths)

    def wine_discovery_done(self) -> bool:
        return self.settings.value('wine_discovery_done', False, type=bool)

    def set_wine_discovery_done(self):
        self.settings.setValue('wine_discovery_done', True)

    def is_first_run(self) -> bool:
        return not self.settings.value('first_run_done', False, type=bool)

    def set_first_run_done(self):
        self.settings.setValue('first_run_done', True)

    def install_scope(self) -> str:
        return self.settings.value('install_scope', 'local', type=str)

    def set_install_scope(self, scope: str):
        self.settings.setValue('install_scope', scope)

    def asset_dir(self) -> Path:
        scope = self.install_scope()
        if scope == 'global':
            return Path('/usr/share/SC2CampaignLauncher/assets')
        if scope == 'custom':
            custom = self.settings.value('custom_asset_dir', type=str)
            if custom:
                return Path(custom)
        return Path.home() / '.local' / 'share' / 'SC2CampaignLauncher/assets'

def http_get(url: str) -> bytes:
    """Simple HTTP GET with a proper User-Agent. Raises on error."""
    print(f'[HTTP] GET {url}')
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0',
        'Accept': 'application/json, text/plain, */*',
    })
    with urllib.request.urlopen(req, timeout=30) as response:
        data = response.read()
        print(f'[HTTP] Got {response.status}, {len(data)} bytes')
        return data

class MapsJsonFetcher(QThread):
    finished_signal = pyqtSignal(list)

    def __init__(self, sc2_root: Path, settings: AppSettings):
        super().__init__()
        self._sc2_root = sc2_root
        self._settings = settings

    def run(self):
        campaigns = []
        try:
            print('[FETCH] Starting maps.json fetch...')
            raw = http_get(MAPS_JSON_URL)
            print(f'[FETCH] Raw data starts with: {raw[:300]}')

            data = json.loads(raw)
            print(f'[FETCH] JSON type: {type(data).__name__}')

            campaigns = self._parse(data)
            print(f'[FETCH] Parsed {len(campaigns)} campaigns')

        except urllib.error.HTTPError as e:
            print(f'[FETCH] HTTP Error {e.code}: {e.reason}')
        except urllib.error.URLError as e:
            print(f'[FETCH] URL Error: {e.reason}')
        except json.JSONDecodeError as e:
            print(f'[FETCH] JSON parse error: {e}')
        except Exception as e:
            import traceback
            print(f'[FETCH] Unexpected error: {e}')
            traceback.print_exc()

        self.finished_signal.emit(campaigns)

    def _parse(self, data) -> list[dict]:
        if isinstance(data, list):
            entries = data
        elif isinstance(data, dict):
            # tolerate wrapped shapes, but real manifest is a top-level array
            entries = next((v for v in data.values()
                            if isinstance(v, list) and v and isinstance(v[0], dict)),
                           [data])
        else:
            return []

        campaigns, seen = [], set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            # NOTE: distinct variable — never reuse 'name' below this line
            title = entry.get('title') or entry.get('name') or 'Unknown'
            folder = entry.get('folder') or slugify(title)
            slug = folder                      # JSON folder is canonical
            if slug in seen:
                continue
            seen.add(slug)

            mapinfo = self._fetch_mapinfo(title)

            raw_author = (entry.get('author') or mapinfo.get('author'))
            if isinstance(raw_author, dict):
                raw_author = raw_author.get('name')
            author = raw_author or AUTHOR_OVERRIDE.get(slug, DEFAULT_AUTHOR)

            maps_list, mods_list = [], []
            for f in entry.get('maps', []):
                if not isinstance(f, dict) or not f.get('name'):
                    continue
                if f['name'].lower().endswith('.sc2mod'):
                    mods_list.append(f)
                else:
                    maps_list.append(f)

            maps_dir = self._sc2_root / 'Maps' / folder
            maps_ok = all((maps_dir / m['name']).exists() for m in maps_list)
            mods_ok = all(self._settings.is_mod_current(
                mo['name'], mo.get('sha256')) for mo in mods_list)

            if not maps_ok:
                status = 'not_installed'
            elif not mods_ok:
                status = 'update_available'
            else:
                status = 'installed'

            print(f'[PARSE] {slug}: {status} '
                  f'({len(maps_list)} maps, {len(mods_list)} mods)')

            campaigns.append({
                'name': str(title),
                'slug': slug,
                'folder': folder,
                'author': str(author),
                'version': str(entry.get('version', '1.0')),
                'asset': entry.get('asset') or f'{title}.png',
                'description': mapinfo.get('description'),
                'patch_notes': mapinfo.get('patch notes'),
                'maps': maps_list,          # .SC2Map dicts only
                'mods': mods_list,          # .SC2Mod dicts only
                'raw': entry,
                'status': status,
            })
        return campaigns

    @staticmethod
    def _fetch_mapinfo(title: str) -> dict:
        """Fetch campaigns/<Title>/mapinfo/mapinfo.json; {} if absent."""
        from urllib.parse import quote
        url = (f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}'
               f'/campaigns/{quote(title, safe="")}/mapinfo/mapinfo.json')
        try:
            data = json.loads(http_get(url))
            return data.get('mapinfo') or {}
        except Exception as e:
            print(f'[MAPINFO] No mapinfo for {title}: {e}')
            return {}

class CampaignDownloader(QThread):
    progress = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, campaign: dict, sc2_root: Path, settings: AppSettings):
        super().__init__()
        self.campaign = campaign
        self.sc2_root = sc2_root
        self.settings = settings

    def run(self):
        try:
            slug = self.campaign['slug']
            files = self.campaign['maps'] + self.campaign['mods']
            total, downloaded, skipped, failed = len(files), 0, 0, 0

            for i, item in enumerate(files):
                fname = item.get('name', 'unknown')
                url = item.get('url')
                remote_sha = item.get('sha256')
                is_mod = fname.lower().endswith('.sc2mod')
                self.progress.emit(i + 1, total, fname)
                dest = self._dest(fname, is_mod)

                # dedup / up-to-date: verify the file actually on disk
                if dest.exists():
                    if remote_sha is None or \
                       self.settings.compute_file_hash(dest) == remote_sha:
                        skipped += 1
                        continue
                    print(f'[DOWNLOAD] Corrupt/stale {fname}, re-downloading')

                if not url:
                    print(f'[DOWNLOAD] No URL for {fname}')
                    failed += 1
                    continue
                try:
                    data = http_get(url)
                    if remote_sha and self.settings.hash_bytes(data) != remote_sha:
                        raise ValueError(f'sha256 mismatch for {fname}')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    downloaded += 1
                    if is_mod:
                        self.settings.register_mod_file(
                            fname, remote_sha or self.settings.hash_bytes(data),
                            campaigns=[slug])
                except Exception as e:
                    print(f'[DOWNLOAD] Failed {fname}: {e}')
                    failed += 1

            if failed and downloaded == 0 and skipped < total:
                self.finished_signal.emit(False, f'All downloads failed for {slug}')
            elif failed > 0:
                self.finished_signal.emit(False,
                    f'Downloaded {downloaded}/{total} files, {failed} failed')
            else:
                msg = f'Installed {downloaded} files for {slug}'
                if skipped:
                    msg += f' ({skipped} already up to date)'
                self.finished_signal.emit(True, msg)
        except Exception as e:
            import traceback; traceback.print_exc()
            self.finished_signal.emit(False, str(e))

    def _dest(self, filename: str, is_mod: bool = False) -> Path:
        folder = self.campaign.get('folder') or self.campaign['slug']
        if is_mod or filename.lower().endswith('.sc2mod'):
            return self.sc2_root / 'Mods' / filename
        return self.sc2_root / 'Maps' / folder / filename

class CampaignCard(QFrame):
    removed = pyqtSignal(str)   # emitted after a successful delete

    def __init__(self, campaign: dict, settings: AppSettings,
                 all_campaigns: list[dict], parent=None):
        super().__init__(parent)
        self.campaign = campaign
        self.settings = settings
        self.all_campaigns = all_campaigns
        self._downloader = None
        self._setup_ui()

    def _setup_ui(self):
        self.setFixedSize(280, 320)
        self.setStyleSheet("""
            CampaignCard { background: #2a2a2a; border-radius: 8px; border: 1px solid #3a3a3a; }
            CampaignCard:hover { border: 1px solid #6d4aff; }
        """)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 12, 12, 12)

        # Cover with overlay icon buttons (delete top-left, info top-right)
        cover = QLabel()
        cover.setFixedSize(256, 144)
        cover.setStyleSheet('background: #1a1a1a; border-radius: 4px;')
        cover.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Overlay buttons (transparent background, icon from assets)
        self.del_btn = QLabel(cover)
        self.del_btn.setFixedSize(28, 28)
        self.del_btn.move(4, 4)
        self.del_btn.setToolTip('Delete campaign')
        self.del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.del_btn.mousePressEvent = lambda e: self._delete()
        self._load_icon(self.del_btn, 'settings.png', 28, 28)

        self.info_btn = QLabel(cover)
        self.info_btn.setFixedSize(28, 28)
        self.info_btn.move(256 - 32, 4)
        self.info_btn.setToolTip('Campaign info')
        self.info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.info_btn.mousePressEvent = lambda e: self._info()
        self._load_icon(self.info_btn, 'info.png', 28, 28)

        if self.campaign.get('description'):
            # Rich text — HTML in mapinfo.json (e.g. <b>, <br>) renders in the tooltip
            self.info_btn.setToolTip(self.campaign['description'])

        if not self._load_cover_image(cover):
            self._placeholder(cover)
        lay.addWidget(cover, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Title - CENTERED and with word wrap
        t = QLabel(self.campaign['name'])
        t.setFont(QFont('Arial', 12, QFont.Weight.Bold))
        t.setStyleSheet('color: white;')
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)  # CENTERED
        t.setWordWrap(True)  # Allow wrapping for long names
        t.setMinimumHeight(40)  # Give room for multi-line text
        lay.addWidget(t)

        # Author + Version
        meta = QHBoxLayout()
        a = QLabel(f'Author: {self.campaign["author"]}')
        a.setStyleSheet('color: #999; font-size: 11px;')
        meta.addWidget(a)
        meta.addStretch()
        v = QLabel(f'v{self.campaign["version"]}')
        v.setStyleSheet('color: #999; font-size: 11px;')
        meta.addWidget(v)
        lay.addLayout(meta)

        # Status
        s = self.status_label = QLabel(self.campaign['status'].replace('_', ' ').title())
        self.status_label.setStyleSheet('color: #999; font-size: 11px; min-height: 20px;')
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)

        # Button
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        self.btn = QPushButton()
        self.btn.setFixedSize(100, 32)
        self._style_btn()
        self.btn.clicked.connect(self._click)
        btn_lay.addWidget(self.btn)
        btn_lay.addStretch()
        lay.addLayout(btn_lay)

    def _load_cover_image(self, label: QLabel) -> bool:
        """Try to load cover image from cached assets or GitHub"""
        slug = self.campaign['slug']

        # Cache directory
        cache_dir = Path.home() / '.cache' / 'SC2CampaignLauncher' / 'assets' / slug
        cache_dir.mkdir(parents=True, exist_ok=True)

        # First, check if we have cached images
        cached_assets = list(cache_dir.glob('*'))
        if cached_assets:
            pixmap = QPixmap(str(cached_assets[0]))
            if not pixmap.isNull():
                scaled = pixmap.scaled(256, 144, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
                label.setPixmap(scaled)
                print(f'[IMAGE] Loaded from cache: {cached_assets[0]}')
                return True

        # Try to construct GitHub raw URL for campaign's asset
        # Expected format: campaigns/<Campaign Name>/assets/<Campaign Name>.png
        # Examples:
        #   Azeroth Legends → campaigns/Azeroth Legends/assets/Azeroth Legends.png
        #   Wings of Liberty → campaigns/Wings of Liberty/assets/Wings of Liberty.png
        name = self.campaign['name']
        # Use the JSON 'asset' field (e.g. "Azeroth Battlegrounds.png") if present
        asset = self.campaign.get('asset') or f'{name}.png'
        from urllib.parse import quote
        github_asset_path = (f'campaigns/{quote(name, safe="")}'
                             f'/assets/{quote(asset, safe="")}')
        github_url = f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_asset_path}'

        print(f'[IMAGE] Trying GitHub URL: {github_url}')

        try:
            data = http_get(github_url)
            # Cache it locally
            cached_path = cache_dir / asset
            cached_path.write_bytes(data)

            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                scaled = pixmap.scaled(256, 144, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
                label.setPixmap(scaled)
                print(f'[IMAGE] Loaded from GitHub and cached: {github_asset_path}')
                return True
        except Exception as e:
            print(f'[IMAGE] Failed to load from GitHub: {e}')
            # Try alternative extensions
            for ext in ['.jpg', '.jpeg']:
                alt_path = f'campaigns/{name}/assets/{name}{ext}'
                alt_url = f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{alt_path}'
                try:
                    data = http_get(alt_url)
                    cached_path = cache_dir / f'{name}{ext}'
                    cached_path.write_bytes(data)

                    pixmap = QPixmap()
                    if pixmap.loadFromData(data):
                        scaled = pixmap.scaled(256, 144, Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
                        label.setPixmap(scaled)
                        print(f'[IMAGE] Loaded from GitHub ({ext}) and cached: {alt_path}')
                        return True
                except:
                    continue

        print(f'[IMAGE] No image found for {name}')
        return False

    def _placeholder(self, label: QLabel):
        pm = QPixmap(256, 144)
        pm.fill(QColor('#1a1a1a'))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor('#666'))
        p.setFont(QFont('Arial', 16, QFont.Weight.Bold))

        # Wrap text if title is long
        rect = pm.rect()
        margins = QRect(rect.x() + 10, rect.y() + 10, rect.width() - 20, rect.height() - 20)
        p.drawText(margins, Qt.AlignmentFlag.AlignCenter, self.campaign['name'][:30])
        p.end()
        label.setPixmap(pm)

    def _load_icon(self, label: QLabel, filename: str, w: int, h: int) -> bool:
        """Load an icon from assets using scope-aware resolution."""
        # Try app's asset dir first (install location)
        asset_path = self.settings.asset_dir() / filename
        if not asset_path.exists():
            # Fall back to dev layout
            asset_path = Path(__file__).parent / 'assets' / filename

        if not asset_path.exists():
            return False

        pm = QPixmap(str(asset_path))
        if pm.isNull():
            return False
        pm = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        label.setPixmap(pm)
        return True

    def _style_btn(self):
        st = self.campaign['status']
        if st == 'not_installed':
            c, ch = '#3498db', '#2980b9'
            btn_text = 'Install'
        elif st == 'installed':
            c, ch = '#27ae60', '#229954'
            btn_text = 'Play'
        else: # update_available
            c, ch = '#e67e22', '#d35400'
            btn_text = 'Update'
        self.btn.setText(btn_text)
        self.btn.setStyleSheet(
            f'QPushButton {{ background: {c}; color: white; border: none; '
            f'border-radius: 4px; font-weight: bold; }}'
            f'QPushButton:hover {{ background: {ch}; }}'
        )

    def _click(self):
        st = self.campaign['status']
        if st in ('not_installed', 'update_available'):
            self.btn.setEnabled(False)
            self.btn.setText('Updating...' if st == 'update_available' else 'Installing...')
            self._download()
        elif st == 'installed':
            self._launch()

    def _download(self):
        self._downloader = CampaignDownloader(self.campaign, self.settings.sc2_root(), self.settings)
        self._downloader.progress.connect(self._on_progress)
        self._downloader.finished_signal.connect(self._done)
        self._downloader.start()

    def _on_progress(self, current: int, total: int, filename: str):
        pct = int(current * 100 / total) if total else 0
        short = filename if len(filename) <= 28 else filename[:25] + '...'
        self.status_label.setText(f'{short} {current}/{total} ({pct}%)')

    def _done(self, ok: bool, msg: str):
        if ok:
            self.campaign['status'] = 'installed'
            self._style_btn()
            self.btn.setEnabled(True)
            self.settings.add_campaign_to_installed(self.campaign['slug'])
            self.status_label.setText('Ready')
            # Trigger re-validation of OTHER cards that depend on shared mods
            self.removed.emit(self.campaign['slug'])  # existing signal, just re-use it
        else:
            self.btn.setEnabled(True)
            self._style_btn()   # restores correct text/color for current status
            self.status_label.setText('Download failed')
            QMessageBox.warning(self, 'Download Failed', msg)

    def _launch(self):
        wine_prefix = self.settings.wine_prefix()
        switcher = self.settings.sc2_switcher_path()
        proton_path_raw = self.settings.proton_path()

        # Strip trailing '/proton' if present
        proton_path = proton_path_raw.rstrip('/')
        if proton_path.endswith('/proton'):
            proton_path = proton_path[:-7]

        if not wine_prefix or not switcher or not proton_path:
            QMessageBox.warning(self, 'Not Configured',
                                'Set Wine prefix, Proton path, and SC2Switcher in Settings.')
            return

        # --- map selection: pick the first .SC2Map from the parsed campaign ---
        # self.campaign['maps'] now contains ONLY .SC2Map entries (mods were
        # split out by the parser), so the first entry is the launcher map.
        launcher_maps = [m for m in self.campaign['maps']
                         if m['name'].lower().endswith('.sc2map')]
        if not launcher_maps:
            QMessageBox.warning(self, 'No Maps', 'No maps found for this campaign.')
            return
        map_filename = launcher_maps[0]['name']

        # Use the JSON 'folder' field for the directory name (canonical slug)
        folder = self.campaign.get('folder') or self.campaign['slug']
        maps_dir = self.settings.sc2_root() / 'Maps' / folder
        map_linux_path = maps_dir / map_filename

        if not map_linux_path.exists():
            QMessageBox.warning(self, 'Map Not Found',
                                f'The map file does not exist:\n{map_linux_path}\n'
                                'Please install this campaign first.')
            return

        # Wine Z: drive maps to the Linux root
        wine_map_path = 'Z:' + str(map_linux_path).replace('/', '\\')

        from PyQt6.QtCore import QProcess, QProcessEnvironment

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)

        env = QProcessEnvironment.systemEnvironment()
        env.insert('WINEPREFIX', wine_prefix)
        env.insert('PROTONPATH', proton_path)
        env.insert('PROTON_VERB', 'run')
        env.insert('GAMEID', 'umu-default')
        proc.setProcessEnvironment(env)

        print(f'[LAUNCH] Campaign: {self.campaign["name"]}')
        print(f'[LAUNCH] Map file: {map_filename}')
        print(f'[LAUNCH] Wine prefix: {wine_prefix}')
        print(f'[LAUNCH] umu-run {switcher} -run {wine_map_path}')

        proc.start('/usr/bin/umu-run', [switcher, '-run', wine_map_path])

        if not proc.waitForStarted(10000):
            QMessageBox.critical(self, 'Launch Failed',
                                 f'Could not start umu-run:\n{proc.errorString()}')
            return

        self.status_label.setText('Launching...')


    def _info(self):
        c = self.campaign
        body = f'<b>{c["name"]}</b><br>Author: {c["author"]}<br>Version: {c["version"]}<br>Maps: {len(c.get("maps", []))} — Mods: {len(c.get("mods", []))}<br>'
        if c.get('description'):
            body += f'<hr>{c["description"]}'
        if c.get('patch_notes'):
            body += f'<br><b>Patch notes:</b> {c["patch_notes"]}'
        QMessageBox.information(self, c['name'], body)

    def _delete(self):
        if self._downloader is not None and self._downloader.isRunning():
            QMessageBox.information(self, 'Busy',
                                    'Wait for the current download to finish.')
            return

        c = self.campaign
        folder = c.get('folder') or c['slug']
        root = self.settings.sc2_root()
        maps_dir = root / 'Maps' / folder

        # Find mods of THIS campaign that no other on-disk campaign still needs
        orphaned = []
        for mo in c.get('mods', []):
            fname = mo['name']
            used_elsewhere = False
            for other in self.all_campaigns:
                if other['slug'] == c['slug']:
                    continue
                o_folder = other.get('folder') or other['slug']
                o_maps = other.get('maps', [])
                # 'installed' in the disk sense: all of its maps present
                installed = o_maps and all(
                    (root / 'Maps' / o_folder / m['name']).exists() for m in o_maps)
                if installed and any(m['name'] == fname for m in other.get('mods', [])):
                    used_elsewhere = True
                    break
            if not used_elsewhere:
                orphaned.append(fname)

        ret = QMessageBox.question(
            self, 'Delete Campaign',
            f'Delete "{c["name"]}"?<br><br>'
            f'Removes {len(c.get("maps", []))} map(s) from<br>{maps_dir}',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return

        # Delete maps with progress indication (usually fast, but be consistent)
        map_files = sorted(maps_dir.glob('*')) if maps_dir.exists() else []
        for i, f in enumerate(map_files, 1):
            f.unlink(missing_ok=True)
            self.status_label.setText(f'Deleting… {i}/{len(map_files)} '
                                      f'({int(i * 100 / len(map_files))}%)')
        shutil.rmtree(maps_dir, ignore_errors=True)  # sweep leftover subdirs

        # Delete orphaned mods, clean registry entries
        registry = self.settings.get_shared_mod_registry()
        for fname in orphaned:
            mod_path = root / 'Mods' / fname
            if mod_path.exists():
                mod_path.unlink()
            registry.pop(fname, None)
            print(f'[DELETE] Removed orphaned mod {fname}')
        self.settings.set_shared_mod_registry(registry)

        self.settings.remove_campaign_from_installed(c['slug'])
        self.removed.emit(c['slug'])   # triggers grid refresh

class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle('Settings')
        self.resize(600, 320)

        lay = QVBoxLayout(self)

        # SC2 Root - the primary setting
        row = QHBoxLayout()
        row.addWidget(QLabel('SC2 Installation:'))
        self.sc2_in = QLineEdit(str(settings.sc2_root()))
        self.scan_btn = b2 = QPushButton('Scan\u2026')
        b2.clicked.connect(self._scan_sc2)
        row.addWidget(b2)
        b = QPushButton('Browse')
        b.clicked.connect(self._b_sc2)
        row.addWidget(self.sc2_in)
        row.addWidget(b)
        lay.addLayout(row)

        # Wine/Proton version dropdown (Heroic-style discovery)
        row = QHBoxLayout()
        row.addWidget(QLabel('Proton:'))
        self.wine_combo = QComboBox()
        self.wine_combo.setMinimumWidth(340)
        row.addWidget(self.wine_combo, stretch=1)
        r = QPushButton('Rescan')
        r.clicked.connect(self._populate_wine_combo)
        row.addWidget(r)
        lay.addLayout(row)

        self.wine_warn = QLabel('')
        self.wine_warn.setStyleSheet('color: #e67e22; font-size: 11px;')
        self.wine_warn.setWordWrap(True)
        lay.addWidget(self.wine_warn)

        self.wine_combo.currentIndexChanged.connect(self._wine_selected)
        self._populate_wine_combo()

        # Auto-detected section
        lay.addWidget(QLabel('<hr><b>Auto-detected:</b>'))

        # Wine prefix (auto-detected by default, editable when checkbox unchecked)
        row = QHBoxLayout()
        row.addWidget(QLabel('Wine prefix:'))
        self.prefix_in = QLineEdit(settings.wine_prefix())
        self.prefix_in.setEnabled(not settings.use_auto_prefix())
        b = QPushButton('Browse')
        b.clicked.connect(self._b_pfx)
        row.addWidget(self.prefix_in)
        row.addWidget(b)
        lay.addLayout(row)

        self.auto_prefix_check = QCheckBox('Auto-detect Wine prefix from SC2 Installation')
        self.auto_prefix_check.setChecked(settings.use_auto_prefix())
        self.auto_prefix_check.stateChanged.connect(self._toggle_auto_prefix)
        lay.addWidget(self.auto_prefix_check)

        # Switcher path (read-only, below the prefix)
        self.switcher_label = QLabel(f'Switcher: {settings.sc2_switcher_path()}')
        self.switcher_label.setStyleSheet('color: #999; font-size: 11px;')
        self.switcher_label.setWordWrap(True)
        lay.addWidget(self.switcher_label)

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch()

        # Refresh (maintenance only)
        r = QPushButton('Refresh')
        r.setMinimumWidth(80)
        r.setStyleSheet(
            'QPushButton { background: #3a3a3a; color: #aaa; border: none; '
            'border-radius: 4px; padding: 5px 12px; font-size: 11px; }'
            'QPushButton:hover { background: #4a4a4a; color: white; }')
        r.clicked.connect(self._refresh_campaigns)
        btns.addWidget(r)

        s = QPushButton('Save')
        s.setStyleSheet(
            'QPushButton { background: #3498db; color: white; border: none; '
            'border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #2980b9; }')
        s.clicked.connect(self._save)
        c = QPushButton('Cancel')
        c.setStyleSheet(
            'QPushButton { background: #3a3a3a; color: white; border: none; '
            'border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #4a4a4a; }')
        c.clicked.connect(self.reject)
        btns.addWidget(s)
        btns.addWidget(c)
        lay.addLayout(btns)

    STAGE_HOME = Path.home()
    STAGE_ROOT = Path('/')

    def reject(self):
        if getattr(self, '_scanner', None) and self._scanner.isRunning():
            self._scanner.cancel()
        super().reject()

    def _scan_sc2(self):
        self._scan_stage(SC2_QUICK_ROOTS)

    def _scan_stage(self, roots: list[Path], found_so_far: list[str] | None = None):
        prev = found_so_far or []
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText('Scanning\u2026')
        self._scanner = Sc2Scanner(roots)
        self._scanner.found.connect(
            lambda p: self.scan_btn.setText(f'Scanning\u2026 found: {Path(p).name}'))
        self._scanner.finished_signal.connect(
            lambda results: self._scan_done(prev + results, roots))
        self._scanner.start()

    def _scan_done(self, results: list[str], scanned_roots: list[Path]):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText('Scan\u2026')
        uniq = list(dict.fromkeys(results))

        if uniq:
            if len(uniq) == 1:
                self.sc2_in.setText(uniq[0])
                self._preview_paths(uniq[0])
                return
            # Multiple installs: let the user pick
            label, ok = QInputDialog.getItem(
                self, 'Multiple Installs Found',
                'Select a StarCraft II installation:', uniq, 0, False)
            if ok:
                self.sc2_in.setText(label)
                self._preview_paths(label)
            return

        # Nothing found — offer escalation to progressively broader scopes
        if scanned_roots is SC2_QUICK_ROOTS:
            ret = QMessageBox.question(
                self, 'Not Found',
                'StarCraft II was not found in the usual locations\n'
                '(Steam libraries, ~/.wine, ~/Games, umu).\n\n'
                'Scan your home directory instead?\n'
                'This can take several minutes.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret == QMessageBox.StandardButton.Yes:
                self._scan_stage([self.STAGE_HOME])
            return
        if scanned_roots == [self.STAGE_HOME]:
            ret = QMessageBox.question(
                self, 'Still Not Found',
                'StarCraft II was not found in your home directory.\n\n'
                'Scan the entire filesystem from root?\n'
                'WARNING: not recommended \u2014 this can take 10+ minutes '
                'and scans system directories.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret == QMessageBox.StandardButton.Yes:
                self._scan_stage([self.STAGE_ROOT])
            return
        QMessageBox.information(
            self, 'Not Found',
            'No StarCraft II installation found.\n'
            'Use Browse\u2026 to locate it manually.')

    def _preview_paths(self, root_str: str):
        if 'drive_c' in root_str:
            drive_c_idx = root_str.index('drive_c')
            prefix = root_str[:drive_c_idx].rstrip('/')
            self.prefix_in.setText(prefix)
        switcher = Path(root_str) / 'Support64' / 'SC2Switcher_x64.exe'
        self.switcher_label.setText(f'Switcher: {switcher}')

    def _b_sc2(self):
        p = QFileDialog.getExistingDirectory(self, 'Select StarCraft II Directory')
        if p:
            self.sc2_in.setText(p)
            self._preview_paths(p)

    def _refresh_campaigns(self):
        """Reload campaigns without closing settings"""
        if hasattr(self.parent(), 'load_campaigns'):
            self.parent().load_campaigns()
        else:
            print('[SETTINGS] Warning: parent has no load_campaigns method')

    ADD_CUSTOM = '\u2795 Add custom Wine/Proton\u2026'

    def _populate_wine_combo(self):
        self.wine_combo.blockSignals(True)
        self.wine_combo.clear()
        versions = discover_wine_versions(self.settings.custom_wine_paths())
        saved = self.settings.proton_path()

        for v in versions:
            label = f'{v["name"]}  \u2014 {v["type"]}'
            self.wine_combo.addItem(label, v['path'])
            if v['path'] == saved:
                self.wine_combo.setCurrentIndex(self.wine_combo.count() - 1)

        # Saved path not found on disk anymore (uninstalled/unmounted): keep visible
        if saved and saved not in [self.wine_combo.itemData(i)
                                   for i in range(self.wine_combo.count())]:
            self.wine_combo.addItem(f'\u26a0 {saved} (missing)', saved)
            self.wine_combo.setCurrentIndex(self.wine_combo.count() - 1)
        elif self.wine_combo.currentIndex() < 0 and versions:
            self.wine_combo.setCurrentIndex(0)   # best-ranked by default

        self.wine_combo.addItem(self.ADD_CUSTOM, '__custom__')
        self.wine_combo.currentIndexChanged.emit(self.wine_combo.currentIndex())
        self.wine_combo.blockSignals(False)
        print(f'[WINE] Discovered {len(versions)} Wine/Proton versions')

    def _wine_selected(self, index: int):
        path = self.wine_combo.itemData(index)
        if path == '__custom__':
            self._add_custom_wine()
            return
        if path and self.wine_combo.itemText(index).startswith('\u26a0'):
            self.wine_warn.setText('Previously selected version is missing on disk. '
                                   'Pick another or rescan.')
        elif path and 'wine' == self.wine_combo.itemText(index).split('\u2014')[-1].strip():
            self.wine_warn.setText('Plain Wine selected. Proton (CachyOS/Experimental/GE) '
                                   'is recommended for StarCraft II.')
        else:
            self.wine_warn.setText('')

    def _add_custom_wine(self):
        p = QFileDialog.getExistingDirectory(self, 'Select Wine/Proton directory '
                                             '(must contain toolmanifest.vdf or bin/wine)')
        if not p:
            # restore previous selection
            self._populate_wine_combo()
            return
        path = Path(p)
        fp = _fingerprint_runner(path)
        if fp is None and (path / 'proton').is_file():
            fp = ('proton', path.name)
        if fp is None:
            QMessageBox.warning(self, 'Not a Wine/Proton directory',
                                f'{path}\ndoes not contain toolmanifest.vdf, '
                                'bin/wine, files/bin/wine or dist/bin/wine.')
            self._populate_wine_combo()
            return
        self.settings.add_custom_wine_path(str(path))
        self._populate_wine_combo()
        # reselect the newly added entry
        for i in range(self.wine_combo.count()):
            if self.wine_combo.itemData(i) == str(path):
                self.wine_combo.setCurrentIndex(i)
                break

    def _toggle_auto_prefix(self):
        """Enable/disable manual prefix input based on checkbox"""
        auto_enabled = self.auto_prefix_check.isChecked()
        self.prefix_in.setEnabled(not auto_enabled)

        # Update prefix display: show detected path when auto-enabled
        if auto_enabled:
            detected_prefix = self.settings.wine_prefix()
            self.prefix_in.setText(detected_prefix)
        # When disabled, keep the current user-entered value

    def _b_pfx(self):
        """Browse for Wine prefix directory"""
        p = QFileDialog.getExistingDirectory(self, 'Select Wine Prefix Directory')
        if p:
            self.prefix_in.setText(p)

    def _save(self):
        self.settings.set_sc2_root(Path(self.sc2_in.text()))
        data = self.wine_combo.currentData()
        if data and data != '__custom__':
            self.settings.set_wine_binary(data)

        # Save auto-prefix preference
        use_auto = self.auto_prefix_check.isChecked()
        self.settings.set_use_auto_prefix(use_auto)

        # Save manual prefix override only if not using auto-prefix
        if not use_auto:
            self.settings.set_wine_prefix_override(self.prefix_in.text())

        self.accept()

class FirstRunWizard(QDialog):
    def __init__(self, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle('Welcome — SC2 Campaign Launcher')
        self.resize(600, 300)
        self._scanner = None

        lay = QVBoxLayout(self)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)

        # --- Page 1: SC2 ---
        p1 = QWidget(); v1 = QVBoxLayout(p1)
        v1.addWidget(QLabel('<b>Step 1 of 2 — Locate StarCraft II</b>'))
        v1.addWidget(QLabel('Scan common locations, or browse manually.'))
        row = QHBoxLayout()
        self.sc2_in = QLineEdit(str(settings.sc2_root()))
        row.addWidget(self.sc2_in)
        b = QPushButton('Browse…'); b.clicked.connect(self._browse_sc2)
        row.addWidget(b)
        self.scan_btn = QPushButton('Scan…'); self.scan_btn.clicked.connect(self._scan_clicked)
        row.addWidget(self.scan_btn)
        v1.addLayout(row)
        self.sc2_status = QLabel('')
        self.sc2_status.setStyleSheet('color: #999; font-size: 11px;')
        self.sc2_status.setWordWrap(True)
        v1.addWidget(self.sc2_status)
        v1.addStretch()
        self.stack.addWidget(p1)

        # --- Page 2: Wine/Proton ---
        p2 = QWidget(); v2 = QVBoxLayout(p2)
        v2.addWidget(QLabel('<b>Step 2 of 2 — Choose Wine/Proton version</b>'))
        v2.addWidget(QLabel('Best available version is pre-selected.'))
        self.wine_combo = QComboBox()
        v2.addWidget(self.wine_combo)
        self.wine_lbl = QLabel('')
        self.wine_lbl.setStyleSheet('color: #e67e22; font-size: 11px;')
        self.wine_lbl.setWordWrap(True)
        v2.addWidget(self.wine_lbl)
        v2.addStretch()
        self.stack.addWidget(p2)

        # --- Nav ---
        nav = QHBoxLayout()
        nav.addStretch()
        self.back_btn = QPushButton('Back'); self.back_btn.clicked.connect(self._back)
        nav.addWidget(self.back_btn)
        self.next_btn = QPushButton('Next'); self.next_btn.clicked.connect(self._next)
        nav.addWidget(self.next_btn)
        lay.addLayout(nav)
        self._to_page(0)

    # ---------- navigation ----------
    def _to_page(self, idx: int):
        self.stack.setCurrentIndex(idx)
        self.back_btn.setVisible(idx > 0)
        self.next_btn.setText('Finish' if idx == 1 else 'Next')
        if idx == 1:
            self._populate_wine()

    def _back(self):
        self._to_page(0)

    def _next(self):
        if self.stack.currentIndex() == 0:
            root = self.sc2_in.text().strip()
            if not root:
                self.sc2_status.setText('Enter or scan for a path first.')
                return
            if not _is_sc2_root(Path(root)):
                ret = QMessageBox.question(
                    self, 'Unusual Path',
                    f'{root}\ndoes not contain Support64/SC2Switcher_x64.exe.\n'
                    'Use this path anyway?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if ret != QMessageBox.StandardButton.Yes:
                    return
            self._to_page(1)
        else:
            self._finish()

    def _finish(self):
        root = self.sc2_in.text().strip()
        if root:
            self.settings.set_sc2_root(Path(root))
        data = self.wine_combo.currentData()
        if data:
            self.settings.set_wine_binary(data)
        self.settings.set_first_run_done()
        print(f'[WIZARD] SC2 root: {root}; wine: {data}')
        self.accept()

    # ---------- SC2 discovery ----------
    def _browse_sc2(self):
        p = QFileDialog.getExistingDirectory(self, 'Select StarCraft II Directory')
        if p:
            self.sc2_in.setText(p)
            self._validate_sc2(Path(p))

    def _validate_sc2(self, p: Path):
        if _is_sc2_root(p):
            self.sc2_status.setText(f'✓ Looks good — switcher: '
                                     f'{p / "Support64" / "SC2Switcher_x64.exe"}')
        else:
            self.sc2_status.setText('No Support64/SC2Switcher_x64.exe found in that path.')

    def _scan_clicked(self):
        self._scan_stage(SC2_QUICK_ROOTS)

    def _scan_stage(self, roots: list[Path]):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText('Scanning…')
        self._scanner = Sc2Scanner(roots)
        self._scanner.found.connect(
            lambda p: self.sc2_status.setText(f'Found: {p}'))
        self._scanner.finished_signal.connect(
            lambda res, r=roots: self._scan_done(res, r))
        self._scanner.start()

    def _scan_done(self, results: list[str], scanned_roots: list[Path]):
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText('Scan…')
        uniq = list(dict.fromkeys(results))

        if uniq:
            if len(uniq) == 1:
                choice = uniq[0]
            else:
                choice, ok = QInputDialog.getItem(
                    self, 'Multiple Installs Found',
                    'Select a StarCraft II installation:', uniq, 0, False)
                if not ok:
                    return
            self.sc2_in.setText(choice)
            self._validate_sc2(Path(choice))
            return

        if scanned_roots is SC2_QUICK_ROOTS:
            ret = QMessageBox.question(
                self, 'Not Found',
                'StarCraft II was not found in the usual locations.\n\n'
                'Scan your home directory instead?\nThis can take several minutes.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret == QMessageBox.StandardButton.Yes:
                self._scan_stage([Path.home()])
            else:
                self.sc2_status.setText('Use Browse… to locate it manually.')
        elif scanned_roots == [Path.home()]:
            ret = QMessageBox.question(
                self, 'Still Not Found',
                'StarCraft II was not found in your home directory.\n\n'
                'Scan the entire filesystem from root?\n'
                'WARNING: not recommended — this can take 10+ minutes.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret == QMessageBox.StandardButton.Yes:
                self._scan_stage([Path('/')])
            else:
                self.sc2_status.setText('Use Browse… to locate it manually.')
        else:
            self.sc2_status.setText('No installation found — use Browse… manually.')

    # ---------- Wine page ----------
    def _populate_wine(self):
        versions = discover_wine_versions(self.settings.custom_wine_paths())
        self.wine_combo.clear()
        for v in versions:
            self.wine_combo.addItem(f'{v["name"]} — {v["type"]}', v['path'])
        has_proton = any(v['type'] == 'proton' for v in versions)
        if not versions:
            self.wine_lbl.setText(
                'No Wine or Proton installations were found. Install a Proton build '
                '(e.g. CachyOS Proton or Proton-GE into compatibilitytools.d) '
                'and re-run discovery from Settings. You can still finish setup now '
                'and configure it later in Settings.')
        elif not has_proton:
            self.wine_lbl.setText(
                'Only standalone Wine was found. Wine alone may not work well with '
                'StarCraft II — a Proton build is recommended for best results.')
        else:
            self.wine_lbl.setText('')
        print(f'[WIZARD] Discovered {len(versions)} Wine/Proton versions')

class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self._fetcher = None

        # Wayland window identification
        self.setWindowRole('SC2CampaignLauncher')
        self.setWindowTitle('SC2 Campaign Launcher')

        # Also set window icon on the main window itself
        from PyQt6.QtGui import QIcon
        icon_path = self.settings.asset_dir() / 'logo.png'
        if not icon_path.exists():
            icon_path = Path(__file__).parent / 'assets' / 'logo.png'
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._setup_ui()
        self.load_campaigns()

    def _setup_ui(self):
        self.setWindowTitle('SC2 Campaign Launcher')
        self.resize(1200, 800)

        # Prevent window from getting too small
        self.setMinimumSize(1300, 420)

        self.setStyleSheet('QMainWindow, QWidget { background: #1e1e1e; }')

        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(12)

        # Header row with logo, title, and social links
        hdr = QHBoxLayout()

        t = QLabel('SC2 Campaign Launcher')
        t.setFont(QFont('Arial', 18, QFont.Weight.Bold))
        t.setStyleSheet('color: white;')
        hdr.addWidget(t)
        hdr.addStretch()

        # Discord icon
        discord_label = QLabel()
        discord_label.setFixedSize(40, 40)
        if self._load_icon(discord_label, 'discord.png', 40, 40):
            discord_label.setToolTip('Join our Discord')
            discord_label.setCursor(Qt.CursorShape.PointingHandCursor)
            discord_label.mousePressEvent = lambda e: self._open_discord()
        hdr.addWidget(discord_label)

        # Patreon icon
        patreon_label = QLabel()
        patreon_label.setFixedSize(40, 40)
        if self._load_icon(patreon_label, 'patreon.png', 40, 40):
            patreon_label.setToolTip('Support on Patreon')
            patreon_label.setCursor(Qt.CursorShape.PointingHandCursor)
            patreon_label.mousePressEvent = lambda e: self._open_patreon()
        hdr.addWidget(patreon_label)

        s = QPushButton('Settings')
        s.setStyleSheet(
            'QPushButton { background: #3a3a3a; color: white; border: 1px solid #4a4a4a; '
            'border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #4a4a4a; }')
        s.clicked.connect(self._open_settings)
        hdr.addWidget(s)

        main.addLayout(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')

        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(16)
        self.grid.setContentsMargins(8, 8, 8, 8)
        scroll.setWidget(self.grid_widget)
        main.addWidget(scroll)

    def _clear_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _load_header_logo(self, label: QLabel) -> bool:
        """Load the app logo from assets directory"""
        # Try installed location first, fall back to relative path
        asset_path = Path.home() / '.local' / 'share' / 'SC2CampaignLauncher' / 'assets' / 'logo.png'
        if not asset_path.exists():
            asset_path = Path(__file__).parent / 'assets' / 'logo.png'

        pm = QPixmap(str(asset_path))
        if pm.isNull():
            # Fallback: generate placeholder
            pm = QPixmap(48, 48)
            pm.fill(QColor('#6d4aff'))
            p = QPainter(pm)
            p.setPen(QColor('white'))
            p.setFont(QFont('Arial', 14, QFont.Weight.Bold))
            p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, 'SC2')
            p.end()
        else:
            pm = pm.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        label.setPixmap(pm)
        return True

    def _load_icon(self, label: QLabel, filename: str, w: int, h: int) -> bool:
        """Load an icon from assets directory (Discord/Patreon)"""
        # Try installed location first, fall back to relative path
        asset_path = Path.home() / '.local' / 'share' / 'SC2CampaignLauncher' / 'assets' / filename
        if not asset_path.exists():
            asset_path = Path(__file__).parent / 'assets' / filename

        pm = QPixmap(str(asset_path))
        if pm.isNull():
            return False
        pm = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio,
                      Qt.TransformationMode.SmoothTransformation)
        label.setPixmap(pm)
        return True

    def _open_discord(self):
        QDesktopServices.openUrl(QUrl('https://discord.gg/adK8CeHtRa'))

    def _open_patreon(self):
        QDesktopServices.openUrl(QUrl('https://www.patreon.com/SynergySC2'))

    def load_campaigns(self):
        print('[MAIN] Loading campaigns...')
        # Removed: self.refresh_btn.setEnabled(False)
        # Removed: self.refresh_btn.setText('Loading...')

        self._fetcher = MapsJsonFetcher(self.settings.sc2_root(), self.settings)
        self._fetcher.finished_signal.connect(self._on_loaded)
        self._fetcher.start()

    def _on_loaded(self, campaigns: list):
        print(f'[MAIN] Received {len(campaigns)} campaigns from fetcher')
        # Removed: self.refresh_btn.setEnabled(True)
        # Removed: self.refresh_btn.setText('Refresh')

        # Re-validate against current disk state (important after downloads)
        campaigns = self.settings.validate_all_campaign_statuses(campaigns)

        self._clear_grid()

        if not campaigns:
            msg = QLabel('No campaigns loaded. Check terminal output for errors.')
            msg.setStyleSheet('color: #e74c3c; font-size: 14px;')
            self.grid.addWidget(msg, 0, 0, 1, 4)
            return

        for i, camp in enumerate(campaigns):
            card = CampaignCard(camp, self.settings, campaigns)
            card.removed.connect(lambda slug: self.load_campaigns())
            self.grid.addWidget(card, i // 4, i % 4)

    def _open_settings(self):
        d = SettingsDialog(self.settings, self)
        if d.exec():
            print('[MAIN] Settings saved, reloading...')
            self.load_campaigns()

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # Set application-wide window icon (loads from assets)
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtGui import QIcon

    QCoreApplication.setApplicationName('SC2CampaignLauncher')
    QCoreApplication.setOrganizationName('SC2CampaignLauncher')
    QCoreApplication.setApplicationVersion('1.0')

    settings = AppSettings()
    icon_path = settings.asset_dir() / 'logo.png'
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    else:
        # Fallback to dev path
        fallback = Path(__file__).parent / 'assets' / 'logo.png'
        if fallback.exists():
            app.setWindowIcon(QIcon(str(fallback)))

    settings = AppSettings()  # Re-instantiate after icon setup

    if settings.is_first_run():
        wiz = FirstRunWizard(settings)
        wiz.exec()

    w = MainWindow(settings)
    w.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
