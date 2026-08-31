#!/usr/bin/env python3
"""SC2 Campaign Launcher - Map downloading and launcher for Linux/Wine"""

import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QScrollArea, QFrame, QFileDialog,
    QDialog, QLineEdit, QMessageBox, QGridLayout, QCheckBox
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSettings, QRect
from PyQt6.QtGui import QPixmap, QPainter, QColor, QFont

# ============================================================================
# HARD-CODED CONFIGURATION
# ============================================================================
GITHUB_REPO = 'R-P-S/SC2Campaigns'
GITHUB_BRANCH = 'main'
MAPS_JSON_URL = f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/maps.json'
DEFAULT_SC2_ROOT = Path.home() / '.local' / 'share' / 'StarCraft II'

# Author override: specific campaigns have different authors than R-P-S
AUTHOR_OVERRIDE = {
    'uedfl': 'Oracle',
    'ued-fl': 'Oracle',
}

# Default author for most campaigns
DEFAULT_AUTHOR = 'Synergy'

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

    def set_wine_prefix(self, prefix: str):
        self.settings.setValue('wine_prefix', prefix)

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
        campaigns = {}

        if isinstance(data, dict):
            for key in data:
                if isinstance(data[key], list) and len(data[key]) > 0:
                    if isinstance(data[key][0], dict):
                        print(f'[PARSE] Using key "{key}" ({len(data[key])} entries)')
                        entries = data[key]
                        break
            else:
                entries = [data]
        elif isinstance(data, list):
            entries = data
        else:
            return []

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            name = (entry.get('name') or entry.get('title')
                    or entry.get('displayName') or entry.get('id') or 'Unknown')
            slug = str(name).lower().replace(' ', '').replace('-', '').replace('_', '')

            if slug in campaigns:
                continue

            # Get author - check entry first, then fallback to override/default
            raw_author = entry.get('author')
            if raw_author and isinstance(raw_author, dict):
                raw_author = raw_author.get('name')
            author = raw_author or AUTHOR_OVERRIDE.get(slug, DEFAULT_AUTHOR)

            print(f'[PARSE] {slug}: author="{author}", raw_author={raw_author}')

            campaigns[slug] = {
                'name': str(name),
                'slug': slug,
                'author': str(author),
                'version': str(entry.get('version', '1.0')),
                'description': entry.get('description'),
                'thumbnail': entry.get('thumbnail') or entry.get('cover') or entry.get('image'),
                'maps': entry.get('maps', []),
                'mods': entry.get('mods', []),
                'raw': entry,
                'status': 'not_installed',
            }

            if self._settings.is_campaign_installed(slug):
                campaigns[slug]['status'] = 'installed'

        return list(campaigns.values())

class CampaignDownloader(QThread):
    progress = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, campaign: dict, sc2_root: Path):
        super().__init__()
        self.campaign = campaign
        self.sc2_root = sc2_root

    def run(self):
        try:
            raw = self.campaign.get('raw', {})
            slug = self.campaign['slug']
            files_downloaded = 0
            files_failed = 0

            print(f'[DOWNLOAD] Processing campaign: {slug}')
            print(f'[DOWNLOAD] Raw data keys: {list(raw.keys())}')

            # Get maps array from raw JSON
            maps_list = raw.get('maps', [])
            mods_list = raw.get('mods', [])

            print(f'[DOWNLOAD] Found {len(maps_list)} maps and {len(mods_list)} mods')

            # Process maps
            for i, map_item in enumerate(maps_list):
                if isinstance(map_item, str):
                    # Old format: just a filename string
                    fname = map_item
                    url = f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{fname}'
                elif isinstance(map_item, dict):
                    # New format: object with url field
                    fname = map_item.get('name', 'unknown.map')
                    url = map_item.get('url')  # <<< USE THE URL FROM JSON

                    if not url:
                        print(f'[DOWNLOAD] WARNING: No URL for {fname}, skipping')
                        files_failed += 1
                        continue

                    print(f'[DOWNLOAD] Using URL from JSON: {url}')
                else:
                    print(f'[DOWNLOAD] Unknown map item type: {type(map_item)}')
                    files_failed += 1
                    continue

                self.progress.emit(i + 1, len(maps_list) + len(mods_list), f'{fname}')
                dest = self._dest(fname, is_mod=False)

                try:
                    data = http_get(url)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    print(f'[DOWNLOAD] ✓ Saved {dest}')
                    files_downloaded += 1
                except Exception as e:
                    print(f'[DOWNLOAD] ✗ Failed {fname}: {e}')
                    self.progress.emit(i + 1, len(maps_list) + len(mods_list), f'Failed: {fname}')
                    files_failed += 1

            # Process mods
            total = len(maps_list) + len(mods_list)
            for j, mod_item in enumerate(mods_list):
                idx = len(maps_list) + j + 1

                if isinstance(mod_item, str):
                    fname = mod_item
                    url = f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{fname}'
                elif isinstance(mod_item, dict):
                    fname = mod_item.get('name', 'unknown.mod')
                    url = mod_item.get('url')

                    if not url:
                        print(f'[DOWNLOAD] WARNING: No URL for mod {fname}, skipping')
                        files_failed += 1
                        continue

                    print(f'[DOWNLOAD] Using URL from JSON: {url}')
                else:
                    files_failed += 1
                    continue

                self.progress.emit(idx, total, f'{fname}')
                dest = self._dest(fname, is_mod=True)

                try:
                    data = http_get(url)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                    print(f'[DOWNLOAD] ✓ Saved mod {dest}')
                    files_downloaded += 1
                except Exception as e:
                    print(f'[DOWNLOAD] ✗ Failed mod {fname}: {e}')
                    files_failed += 1

            # Report actual results
            if files_failed > 0 and files_downloaded == 0:
                self.finished_signal.emit(False, f'All downloads failed for {slug}')
            elif files_failed > 0:
                self.finished_signal.emit(False, f'Downloaded {files_downloaded}/{total} files. {files_failed} failed')
            else:
                self.finished_signal.emit(True, f'Installed {files_downloaded} files for {slug}')

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.finished_signal.emit(False, str(e))

    def _dest(self, filename: str, is_mod: bool = False) -> Path:
        slug = self.campaign['slug']
        lower = filename.lower()

        # Check if it's actually a mod file based on extension
        if is_mod or lower.endswith('.sc2mod'):
            return self.sc2_root / 'Mods' / filename
        elif lower.endswith('.sc2map'):
            return self.sc2_root / 'Maps' / slug / filename
        elif lower.endswith(('.png', '.jpg', '.jpeg')):
            cache_dir = Path.home() / '.cache' / 'sc2_campaign_launcher' / 'assets' / slug
            return cache_dir / filename
        else:
            # Default to Maps folder for unknown files
            return self.sc2_root / 'Maps' / slug / filename

class CampaignCard(QFrame):
    def __init__(self, campaign: dict, settings: AppSettings, parent=None):
        super().__init__(parent)
        self.campaign = campaign
        self.settings = settings
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

        # Cover with asset from GitHub repo
        cover = QLabel()
        cover.setFixedSize(256, 144)
        cover.setStyleSheet('background: #1a1a1a; border-radius: 4px;')
        cover.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Try to load from cached asset first, then GitHub
        if not self._load_cover_image(cover):
            self._placeholder(cover)
        lay.addWidget(cover)

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
        asset_filename = f'{name}.png'
        # URL-encode spaces and special characters in paths
        from urllib.parse import quote
        asset_filename_encoded = quote(asset_filename, safe='')
        campaign_name_encoded = quote(name, safe='')
        github_asset_path = f'campaigns/{campaign_name_encoded}/assets/{asset_filename_encoded}'
        github_url = f'https://raw.githubusercontent.com/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_asset_path}'

        print(f'[IMAGE] Trying GitHub URL: {github_url}')

        try:
            data = http_get(github_url)
            # Cache it locally
            cached_path = cache_dir / asset_filename
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
        if self.campaign['status'] == 'not_installed':
            self.btn.setEnabled(False)
            self.btn.setText('Installing...')  # Just show state, not filename
            self._download()
        elif self.campaign['status'] == 'installed':
            self._launch()

    def _download(self):
        self._downloader = CampaignDownloader(self.campaign, self.settings.sc2_root())
        # Send to status_label instead of button
        self._downloader.progress.connect(
            lambda c, t, m: self.status_label.setText(f'{m[:40]}...' if len(m) > 40 else m)
        )
        self._downloader.finished_signal.connect(self._done)
        self._downloader.start()

    def _done(self, ok: bool, msg: str):
        if ok:
            self.campaign['status'] = 'installed'
            self._style_btn()
            self.btn.setText('Play')
            self.settings.add_campaign_to_installed(self.campaign['slug'])
            self.status_label.setText('Ready')  # Reset status text

            maps_dir = self.settings.sc2_root() / 'Maps' / self.campaign['slug']
            print(f'[CARD] Download complete: {msg}')
            print(f'[CARD] Files installed to: {maps_dir}')
            QMessageBox.information(self, 'Install Complete',
                                    f'{msg}\n\nSaved to:\n{maps_dir}')
        else:
            self.btn.setEnabled(True)
            self.btn.setText('Install')
            self.status_label.setText('Download failed')
            print(f'[CARD] Download failed: {msg}')
            QMessageBox.warning(self, 'Download Failed', msg)

    def _launch(self):
        wine_prefix = self.settings.wine_prefix()
        switcher = self.settings.sc2_switcher_path()
        proton_path_raw = self.settings.proton_path()  # Could include "/proton" filename

        # Strip trailing '/proton' if present
        proton_path = proton_path_raw.rstrip('/')
        if proton_path.endswith('/proton'):
            proton_path = proton_path[:-7]  # Remove "/proton"

        if not wine_prefix or not switcher or not proton_path:
            QMessageBox.warning(self, 'Not Configured',
                                'Set Wine prefix, Proton path, and SC2Switcher in Settings.')
            return

        # Get the maps list from the campaign JSON data
        maps_list = self.campaign.get('maps', [])
        if not maps_list:
            QMessageBox.warning(self, 'No Maps', 'No maps found for this campaign.')
            return

        # The first map is typically the launcher/entry point
        # If it's a dict with 'name' field, extract the filename
        # If it's just a string, use it directly
        map_entry = maps_list[0]
        if isinstance(map_entry, dict):
            map_filename = map_entry.get('name', 'unknown.SC2Map')
        else:
            map_filename = str(map_entry)

        # Build the full Linux path to the map
        maps_dir = self.settings.sc2_root() / 'Maps' / self.campaign['slug']
        map_linux_path = maps_dir / map_filename

        if not map_linux_path.exists():
            QMessageBox.warning(self, 'Map Not Found',
                                f'The map file does not exist:\n{map_linux_path}\n'
                                'Please install this campaign first.')
            return

        # Convert Linux path to Wine Windows path
        # Z: drive in Wine maps to Linux root /, so prepend Z: to the FULL Linux path
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

        print(f'[LAUNCH] Proton path raw: {proton_path_raw}')
        print(f'[LAUNCH] Proton path normalized: {proton_path}')
        print(f'[LAUNCH] Campaign: {self.campaign["name"]}')
        print(f'[LAUNCH] Map file: {map_filename}')
        print(f'[LAUNCH] Wine prefix: {wine_prefix}')
        print(f'[LAUNCH] Proton path: {proton_path}')
        print(f'[LAUNCH] umu-run {switcher} -run {wine_map_path}')

        proc.start('/usr/bin/umu-run', [switcher, '-run', wine_map_path])

        if not proc.waitForStarted(10000):
            QMessageBox.critical(self, 'Launch Failed',
                                 f'Could not start umu-run:\n{proc.errorString()}')
            return

        self.status_label.setText('Launching...')

        QMessageBox.information(self, 'Launching',
                                f'Launching {self.campaign["name"]}\n'
                                f'Map: {map_filename}')

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
        b = QPushButton('Browse')
        b.clicked.connect(self._b_sc2)
        row.addWidget(self.sc2_in)
        row.addWidget(b)
        lay.addLayout(row)

        # Proton path
        row = QHBoxLayout()
        row.addWidget(QLabel('Proton path:'))
        self.wine_in = QLineEdit(settings.proton_path())
        b = QPushButton('Browse')
        b.clicked.connect(self._b_wine)
        row.addWidget(self.wine_in)
        row.addWidget(b)
        lay.addLayout(row)

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

        # Buttons (keep the existing buttons block below this)

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch()
        s = QPushButton('Save')
        s.clicked.connect(self._save)
        c = QPushButton('Cancel')
        c.clicked.connect(self.reject)
        btns.addWidget(s)
        btns.addWidget(c)
        lay.addLayout(btns)

    def _b_sc2(self):
        p = QFileDialog.getExistingDirectory(self, 'Select StarCraft II Directory')
        if p:
            self.sc2_in.setText(p)
            # Preview derived paths
            root_str = p
            if 'drive_c' in root_str:
                drive_c_idx = root_str.index('drive_c')
                prefix = root_str[:drive_c_idx].rstrip('/')
                self.prefix_label.setText(f'Wine prefix: {prefix}')
            switcher = Path(p) / 'Support64' / 'SC2Switcher_x64.exe'
            self.switcher_label.setText(f'Switcher: {switcher}')

    def _b_wine(self):
        p = QFileDialog.getExistingDirectory(self, 'Select Proton Directory')
        if p:
            manifest = Path(p) / 'toolmanifest.vdf'
            if not manifest.exists():
                parent = Path(p).parent
                if (parent / 'toolmanifest.vdf').exists():
                    p = str(parent)
            self.wine_in.setText(p)

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
        self.settings.set_wine_binary(self.wine_in.text())

        # Save auto-prefix preference
        use_auto = self.auto_prefix_check.isChecked()
        self.settings.set_use_auto_prefix(use_auto)

        # Save manual prefix override only if not using auto-prefix
        if not use_auto:
            self.settings.set_wine_prefix_override(self.prefix_in.text())

        self.accept()

    def _save(self):
        self.settings.set_sc2_root(Path(self.sc2_in.text()))
        self.settings.set_wine_binary(self.wine_in.text())
        # self.accept()

class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self._fetcher = None
        self._setup_ui()
        self.load_campaigns()

    def _setup_ui(self):
        self.setWindowTitle('SC2 Campaign Launcher')
        self.resize(1200, 800)

        # CRITICAL: Prevent window from getting too small
        self.setMinimumSize(1300, 420)  # 4 cards wide (280 * 4 + margins) + header/footer

        self.setStyleSheet('QMainWindow, QWidget { background: #1e1e1e; }')

        central = QWidget()
        self.setCentralWidget(central)
        main = QVBoxLayout(central)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(12)

        hdr = QHBoxLayout()
        t = QLabel('SC2 Campaign Launcher')
        t.setFont(QFont('Arial', 18, QFont.Weight.Bold))
        t.setStyleSheet('color: white;')
        hdr.addWidget(t)
        hdr.addStretch()
        self.refresh_btn = QPushButton('Refresh')
        self.refresh_btn.setStyleSheet(
            'QPushButton { background: #3a3a3a; color: white; border: 1px solid #4a4a4a; '
            'border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #4a4a4a; }'
            'QPushButton:disabled { color: #666; }'
        )
        self.refresh_btn.clicked.connect(self.load_campaigns)
        hdr.addWidget(self.refresh_btn)

        s = QPushButton('Settings')
        s.setStyleSheet(
            'QPushButton { background: #3a3a3a; color: white; border: 1px solid #4a4a4a; '
            'border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #4a4a4a; }'
        )
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

    def load_campaigns(self):
        print('[MAIN] Loading campaigns...')
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText('Loading...')

        self._fetcher = MapsJsonFetcher(self.settings.sc2_root(), self.settings)
        self._fetcher.finished_signal.connect(self._on_loaded)
        self._fetcher.start()

    def _on_loaded(self, campaigns: list):
        print(f'[MAIN] Received {len(campaigns)} campaigns from fetcher')
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText('Refresh')
        self._clear_grid()

        if not campaigns:
            msg = QLabel('No campaigns loaded. Check terminal output for errors.')
            msg.setStyleSheet('color: #e74c3c; font-size: 14px;')
            self.grid.addWidget(msg, 0, 0, 1, 4)
            return

        for i, camp in enumerate(campaigns):
            card = CampaignCard(camp, self.settings)
            self.grid.addWidget(card, i // 4, i % 4)

    def _open_settings(self):
        d = SettingsDialog(self.settings, self)
        if d.exec():
            print('[MAIN] Settings saved, reloading...')
            self.load_campaigns()

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    settings = AppSettings()
    w = MainWindow(settings)
    w.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
