#!/usr/bin/env python3
"""Download, verify, and launch custom StarCraft II campaigns."""

import argparse
import logging
import os
import sys
from html import escape
from html.parser import HTMLParser
from pathlib import Path

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = 'sc2_campaign_launcher_linux'

from PyQt6.QtCore import QEvent, QEventLoop, QLockFile, QSize, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QImageReader, QPalette, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from . import __version__
from .catalog import Catalog, CatalogResult
from .files import Cancelled, check_cancel
from .jobs import JobPool
from .launching import LaunchManager
from .library import Library, is_sc2_root
from .platform_backend import (
    MANAGED_PROTON, discover_runners, fingerprint_runner, prefix_from_root,
    validate_prefix, validate_root,
)
from .settings import AppSettings


class PlainHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('p', 'br', 'li', 'hr'):
            self.parts.append('\n')


def plain_html(text):
    parser = PlainHTML()
    parser.feed(text)
    return ''.join(parser.parts).strip()


def show_details(parent, title, text, repair=False):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    dialog.resize(620, 400)
    layout = QVBoxLayout(dialog)
    body = QPlainTextEdit(text)
    body.setReadOnly(True)
    layout.addWidget(body)
    buttons = QHBoxLayout()
    copy = QPushButton('Copy details')
    copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
    close = QPushButton('Close')
    close.clicked.connect(dialog.accept)
    buttons.addWidget(copy)
    buttons.addStretch()
    if repair:
        retry = QPushButton('Verify / repair')
        retry.clicked.connect(lambda: dialog.done(2))
        buttons.addWidget(retry)
    buttons.addWidget(close)
    layout.addLayout(buttons)
    return dialog.exec() == 2


def scan_sc2(roots, cancel, progress):
    skip = {'windows', '.cache', '.git', 'node_modules', 'shadercache', 'proc', 'sys', 'dev'}
    found = {}
    for root in roots:
        check_cancel(cancel)
        if not root.is_dir():
            continue
        for location, directories, _ in os.walk(root, followlinks=False):
            check_cancel(cancel)
            path = Path(location)
            if is_sc2_root(path):
                found[str(path.resolve())] = str(path)
                progress(str(path))
                directories[:] = []
            else:
                directories[:] = [d for d in directories if d.casefold() not in skip]
    return list(found.values())


class SettingsDialog(QDialog):
    refresh_requested = pyqtSignal(bool)

    def __init__(self, settings: AppSettings, jobs: JobPool, parent=None, first_run=False):
        super().__init__(parent)
        self.settings, self.jobs = settings, jobs
        self.first_run = first_run
        self.scanner = None
        self.discovery = None
        self.closed = False
        self.custom = list(settings.custom_runners())
        self.setWindowTitle('Set up StarCraft II' if first_run else 'Settings')
        self.resize(660, 320)
        layout = QVBoxLayout(self)
        intro = QLabel('Choose the existing StarCraft II installation you use to play campaigns.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.sc2_in = QLineEdit(str(settings.sc2_root()))
        self.sc2_in.setAccessibleName('StarCraft II installation')
        root_row = QHBoxLayout()
        root_row.addWidget(self.sc2_in)
        browse = QPushButton('Browse')
        browse.clicked.connect(self._browse_root)
        root_row.addWidget(browse)
        self.scan_btn = QPushButton('Scan')
        self.scan_btn.clicked.connect(self._scan)
        root_row.addWidget(self.scan_btn)
        form.addRow('StarCraft II:', root_row)
        self.prefix_in = QLineEdit(settings.wine_prefix())
        self.auto_prefix = QCheckBox('Detect prefix from the SC2 directory')
        self.auto_prefix.setChecked(settings.auto_prefix())
        self.runner_combo = QComboBox()
        self.runner_combo.setAccessibleName('Wine or Proton version')
        self.umu_in = QLineEdit(settings.umu())
        self.umu_in.setPlaceholderText('Find umu-run on PATH')
        self.umu_in.setAccessibleName('UMU executable')
        if settings.backend.needs_runner_selection:
            runner_row = QHBoxLayout()
            runner_row.addWidget(self.runner_combo, 1)
            add = QPushButton('Add')
            add.clicked.connect(self._add_runner)
            runner_row.addWidget(add)
            rescan = QPushButton('Rescan')
            rescan.clicked.connect(lambda: self._discover())
            runner_row.addWidget(rescan)
            form.addRow('Wine / Proton:', runner_row)
            prefix_row = QHBoxLayout()
            self.prefix_in.setAccessibleName('Wine prefix')
            prefix_row.addWidget(self.prefix_in)
            prefix_browse = QPushButton('Browse')
            prefix_browse.clicked.connect(self._browse_prefix)
            prefix_row.addWidget(prefix_browse)
            form.addRow('Wine prefix:', prefix_row)
            form.addRow('', self.auto_prefix)
            form.addRow('UMU executable:', self.umu_in)
            self.auto_prefix.toggled.connect(self._preview_prefix)
            self.sc2_in.textChanged.connect(self._preview_prefix)
            self._preview_prefix()
        layout.addLayout(form)
        self.status = QLabel('')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        layout.addStretch()
        buttons = QHBoxLayout()
        if not first_run:
            self.refresh_btn = QPushButton('Refresh')
            self.refresh_btn.clicked.connect(lambda: self._refresh(False))
            self.verify_btn = QPushButton('Verify files')
            self.verify_btn.clicked.connect(lambda: self._refresh(True))
            buttons.addWidget(self.refresh_btn)
            buttons.addWidget(self.verify_btn)
        buttons.addStretch()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        self.save_btn = QPushButton('Start' if first_run else 'Save')
        self.save_btn.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_btn)
        layout.addLayout(buttons)
        if settings.backend.needs_runner_selection:
            self._discover(settings.runner())

    def _refresh(self, force):
        self.refresh_requested.emit(force)
        self.status.setText('Checking campaign files...' if force else 'Refreshing campaigns...')

    def _discover(self, selected=None):
        if self.discovery is not None:
            return
        selected = selected or self.runner_combo.currentData() or self.settings.runner()
        custom = tuple(self.custom)
        self.save_btn.setEnabled(False)
        self.discovery = self.jobs.submit(
            lambda cancel, notify: discover_runners(custom),
            lambda result, error: self._discovered(selected, result, error), owner=self,
        )

    def _discovered(self, selected, versions, error):
        self.discovery = None
        if self.closed:
            return
        self.save_btn.setEnabled(True)
        self.runner_combo.clear()
        if error:
            self.status.setText(str(error))
            versions = [{'name': 'UMU managed Proton', 'path': MANAGED_PROTON, 'type': 'proton'}]
        for entry in versions:
            label = entry['name'] if entry['path'] == MANAGED_PROTON else f'{entry["name"]} ({entry["type"]})'
            self.runner_combo.addItem(label, entry['path'])
        index = self.runner_combo.findData(selected)
        if index < 0:
            self.runner_combo.addItem(f'{selected} (missing)', selected)
            index = self.runner_combo.count() - 1
        self.runner_combo.setCurrentIndex(index)

    def _add_runner(self):
        path = QFileDialog.getExistingDirectory(self, 'Select a Wine or Proton directory')
        if not path:
            return
        entry = fingerprint_runner(Path(path))
        if entry is None:
            self.status.setText('Choose a Proton directory containing toolmanifest.vdf and proton, '
                                'or a Wine directory containing bin/wine.')
            return
        selected = str(entry[1])
        if selected not in self.custom:
            self.custom.append(selected)
        self._discover(selected)

    def _browse_root(self):
        path = QFileDialog.getExistingDirectory(self, 'Select StarCraft II')
        if path:
            self.sc2_in.setText(path)

    def _browse_prefix(self):
        path = QFileDialog.getExistingDirectory(self, 'Select the Wine prefix')
        if path:
            self.auto_prefix.setChecked(False)
            self.prefix_in.setText(path)

    def _preview_prefix(self):
        automatic = self.auto_prefix.isChecked()
        self.prefix_in.setEnabled(not automatic)
        if automatic:
            self.prefix_in.setText(prefix_from_root(Path(self.sc2_in.text()).expanduser()))

    def _scan(self):
        if self.scanner is not None:
            self.scanner.cancel.set()
            self.scan_btn.setText('Stopping...')
            return
        self._start_scan(self.settings.backend.sc2_quick_roots())

    def _start_scan(self, roots):
        self.scan_btn.setText('Stop scan')
        self.status.setText('Looking for StarCraft II...')
        self.scanner = self.jobs.submit(
            lambda cancel, notify: scan_sc2(roots, cancel, notify), self._scan_done,
            owner=self, progress=lambda path: self.status.setText(f'Found: {path}'),
        )

    def _scan_done(self, paths, error):
        self.scanner = None
        if self.closed:
            return
        self.scan_btn.setText('Scan')
        if error:
            self.status.setText('Scan cancelled.' if isinstance(error, Cancelled) else str(error))
            return
        if len(paths) == 1:
            self.sc2_in.setText(paths[0])
            self.status.setText('StarCraft II found.')
        elif paths:
            choice, accepted = QInputDialog.getItem(self, 'StarCraft II installations',
                                                   'Choose an installation:', paths, 0, False)
            if accepted:
                self.sc2_in.setText(choice)
        else:
            self.status.setText('No installation found in the usual locations. Browse to the SC2 directory.')
            if QMessageBox.question(self, 'Choose a search directory',
                                    'Search another directory for StarCraft II?') == QMessageBox.StandardButton.Yes:
                folder = QFileDialog.getExistingDirectory(self, 'Choose a directory to search')
                if folder:
                    self._start_scan([Path(folder)])

    def _save(self):
        try:
            root = validate_root(self.sc2_in.text())
            runner = self.runner_combo.currentData() or MANAGED_PROTON
            prefix = self.prefix_in.text().strip()
            if self.settings.backend.needs_runner_selection:
                validate_prefix(prefix)
                if runner != MANAGED_PROTON and fingerprint_runner(Path(runner)) is None:
                    raise ValueError('Choose an installed Wine or Proton version.')
                umu = self.umu_in.text().strip()
                if umu and (not Path(umu).is_absolute() or not Path(umu).is_file()):
                    raise ValueError('The UMU executable must be an existing absolute path.')
            self.settings.save(root, runner, self.auto_prefix.isChecked(), prefix,
                               self.umu_in.text().strip(), self.custom)
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            return
        self.accept()

    def done(self, result):
        self.closed = True
        for job in (self.scanner, self.discovery):
            if job is not None:
                job.cancel.set()
        super().done(result)


class CampaignCard(QFrame):
    requested = pyqtSignal(str, str)

    def __init__(self, campaign, assets, parent=None):
        super().__init__(parent)
        self.campaign = campaign
        self.details = {}
        self.busy = False
        self.running = False
        self.setFixedSize(280, 320)
        self.setStyleSheet(
            'CampaignCard { background: #2a2a2a; border-radius: 8px; border: 1px solid #3a3a3a; }'
            'CampaignCard:hover { border: 1px solid #6d4aff; }')
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)
        self.cover = QLabel(campaign['name'][:30])
        self.cover.setTextFormat(Qt.TextFormat.PlainText)
        self.cover.setWordWrap(True)
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.setFixedSize(256, 144)
        self.cover.setStyleSheet('background: #1a1a1a; color: #666; border-radius: 4px;')
        self.cover.setFont(QFont('Arial', 16, QFont.Weight.Bold))
        layout.addWidget(self.cover, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.remove = QPushButton(QIcon(str(assets / 'settings.png')), '', self.cover)
        self.remove.move(4, 4)
        self.remove.setAccessibleName('Remove campaign')
        self.remove.clicked.connect(lambda: self._request('remove'))
        self.info = QPushButton(QIcon(str(assets / 'info.png')), '', self.cover)
        self.info.move(224, 4)
        self.info.setAccessibleName('Campaign info')
        self.info.clicked.connect(self._info)
        for button in (self.remove, self.info):
            button.setFixedSize(28, 28)
            button.setIconSize(QSize(28, 28))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet('QPushButton { border: none; background: transparent; padding: 0; }'
                                'QPushButton:focus { border: 1px solid #6d4aff; }')
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.title.setFixedHeight(40)
        self.title.setStyleSheet('color: white;')
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setFont(QFont('Arial', 12, QFont.Weight.Bold))
        layout.addWidget(self.title)
        meta = QHBoxLayout()
        self.author = QLabel()
        self.version = QLabel()
        for label in (self.author, self.version):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setStyleSheet('color: #999; font-size: 11px;')
        meta.addWidget(self.author)
        meta.addStretch()
        meta.addWidget(self.version)
        layout.addLayout(meta)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setFixedHeight(23)
        self.status.setStyleSheet('color: #999; font-size: 11px;')
        layout.addWidget(self.status)
        controls = QHBoxLayout()
        controls.addStretch()
        self.play = QPushButton()
        self.play.setFixedSize(100, 32)
        self.play.clicked.connect(self._primary)
        controls.addWidget(self.play)
        controls.addStretch()
        layout.addLayout(controls)
        self.update_campaign(campaign)

    def update_campaign(self, campaign):
        self.campaign = campaign
        self.title.setText(campaign['name'])
        self.author.setText(f'Author: {self.details.get("author", campaign["author"])}')
        self.version.setText(f'v{campaign["version"]}')
        description = plain_html(self.details.get('description') or campaign.get('description', ''))
        self.info.setToolTip('<p>' + escape(description).replace('\n', '<br>') + '</p>'
                             if description else 'Campaign info')
        if not self.busy and not self.running:
            status = campaign.get('status', 'not_installed')
            self.play.setText({'installed': 'Play', 'update_available': 'Update',
                               'not_installed': 'Install', 'error': 'Retry'}.get(status, 'Install'))
            self.status.setText('Check campaign files' if campaign.get('error') else status.replace('_', ' ').title())
            self.status.setToolTip(campaign.get('error', ''))
            color, hover = {'installed': ('#27ae60', '#229954'),
                            'update_available': ('#e67e22', '#d35400')}.get(status, ('#3498db', '#2980b9'))
            self.play.setStyleSheet(
                f'QPushButton {{ background: {color}; color: white; border: none; '
                'border-radius: 4px; font-weight: bold; }'
                f'QPushButton:hover {{ background: {hover}; }}'
                'QPushButton:disabled { background: #3a3a3a; color: #999; }')
        self.remove.setEnabled(campaign.get('removable', False) and not self.busy and not self.running)
        self.remove.setToolTip('Remove campaign')

    def _request(self, action):
        self.requested.emit(self.campaign['slug'], action)

    def _primary(self):
        if self.busy:
            self._request('cancel')
        elif self.campaign.get('status') == 'installed':
            self._request('play')
        else:
            self._request('install')

    def set_busy(self, text):
        self.busy = True
        self.play.setText('Cancel')
        self.play.setEnabled(True)
        self.remove.setEnabled(False)
        self.status.setText(text)

    def set_idle(self):
        self.busy = False
        self.play.setEnabled(not self.running)
        self.update_campaign(self.campaign)

    def set_running(self, running):
        self.running = running
        self.play.setEnabled(not running and not self.busy)
        self.update_campaign(self.campaign)
        if running:
            self.status.setText('Game runner started')
            self.play.setText('Running')

    def show_progress(self, data):
        total, received = data['total'], data['received']
        progress = f'{min(100, int(received * 100 / total))}%' if total else f'{received / 1024 / 1024:.1f} MB'
        self.status.setText(f'File {data["index"]}/{data["count"]}: {progress}')
        self.status.setToolTip(data['name'])

    def set_media(self, details, data):
        self.details = details
        self.update_campaign(self.campaign)
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.cover.setPixmap(pixmap.scaled(256, 144, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))

    def _info(self):
        c = self.campaign
        text = (f'{c["name"]}\nAuthor: {self.details.get("author", c["author"])}\n'
                f'Version: {c["version"]}\nMaps: {len(c["maps"])}\nMods: {len(c["mods"])}')
        for name in ('description', 'patch notes'):
            if self.details.get(name):
                text += '\n\n' + plain_html(self.details[name])
        show_details(self, c['name'], text)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, jobs=None, catalog=None, launcher=None, autoload=True):
        super().__init__()
        self.settings = settings
        self.jobs = jobs or JobPool(self)
        self.catalog = catalog or Catalog(settings.backend.cache_dir())
        self.launcher = launcher or LaunchManager(settings.backend, settings.backend.data_dir() / 'logs', self)
        self.launcher.changed.connect(self._launch_changed)
        self.library = Library(settings.sc2_root(), settings.backend.data_dir())
        self.cards = {}
        self.campaigns = []
        self.fetcher = None
        self.media_jobs = []
        self.mutation = None
        self.mutation_slug = None
        self.queue = []
        self.pending_refresh = False
        self.pending_force = False
        self.generation = 0
        self.closing = False
        self.operation_notice = ''
        self.jobs.idle.connect(self._jobs_idle)
        self.setWindowTitle('SC2 Campaign Launcher')
        self.setWindowRole('SC2CampaignLauncher')
        self.setWindowIcon(QIcon(str(settings.asset_dir() / 'logo.png')))
        self.resize(1300, 800)
        self.setMinimumSize(340, 420)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.setStyleSheet('QMainWindow, QWidget { background: #1e1e1e; }')
        header = QHBoxLayout()
        title = QLabel('SC2 Campaign Launcher')
        title.setFont(QFont('Arial', 18, QFont.Weight.Bold))
        title.setStyleSheet('color: white;')
        title.setWordWrap(True)
        header.addWidget(title, 1)
        for text, asset, url in (
            ('Discord', 'discord.png', 'https://discord.gg/adK8CeHtRa'),
            ('Patreon', 'patreon.png', 'https://www.patreon.com/SynergySC2'),
        ):
            button = QPushButton(QIcon(str(settings.asset_dir() / asset)), '')
            button.setFixedSize(40, 40)
            button.setIconSize(QSize(40, 40))
            button.setStyleSheet('QPushButton { border: none; background: transparent; padding: 0; }'
                                'QPushButton:focus { border: 1px solid #6d4aff; }')
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(text)
            button.setAccessibleName(text)
            button.clicked.connect(lambda checked=False, link=url: QDesktopServices.openUrl(QUrl(link)))
            header.addWidget(button)
        self.settings_btn = QPushButton('Settings')
        self.settings_btn.setStyleSheet(
            'QPushButton { background: #3a3a3a; color: white; border: 1px solid #4a4a4a; '
            'border-radius: 4px; padding: 6px 14px; }'
            'QPushButton:hover { background: #4a4a4a; }')
        self.settings_btn.clicked.connect(self._open_settings)
        header.addWidget(self.settings_btn)
        layout.addLayout(header)
        self.notice = QLabel('')
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QScrollArea { border: none; background: transparent; }')
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setSpacing(16)
        self.grid.setContentsMargins(8, 8, 8, 8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.grid_widget)
        layout.addWidget(self.scroll)
        self.scroll.viewport().installEventFilter(self)
        self.settings_btn.setFocus()
        if autoload:
            self.load_campaigns()

    def load_campaigns(self, force=False):
        if self.closing:
            return
        if self.fetcher is not None or self.mutation is not None or self.queue:
            self.pending_refresh = True
            self.pending_force |= force
            return
        self.generation += 1
        generation = self.generation
        previous = list(self.campaigns)
        library = self.library
        if not self.operation_notice:
            self._set_notice('Checking campaign files...' if force else 'Loading campaigns...')

        def load(cancel, notify):
            result = self.catalog.load(cancel)
            available = result.campaigns or previous
            merged = {c['slug']: c for c in library.recorded_campaigns()}
            merged.update((c['slug'], c) for c in available)
            return CatalogResult(library.statuses(list(merged.values()), cancel, force),
                                 result.notice or library.problem, result.offline)

        self.fetcher = self.jobs.submit(load, lambda result, error: self._loaded(generation, result, error),
                                        owner=self, priority=True)

    def _loaded(self, generation, result, error):
        self.fetcher = None
        if self.closing:
            return
        if generation == self.generation:
            if error:
                if not isinstance(error, Cancelled):
                    self._set_notice(str(error))
            else:
                self._set_notice(result.notice or self.operation_notice)
                self._render(result.campaigns, generation)
        if self.pending_refresh and self.mutation is None and not self.queue:
            force = self.pending_force
            self.pending_refresh = self.pending_force = False
            self.load_campaigns(force)

    def _render(self, campaigns, generation):
        self.campaigns = campaigns
        keep = {c['slug'] for c in campaigns}
        for slug in list(self.cards):
            if slug not in keep and slug != self.mutation_slug and slug not in self.launcher.processes:
                self.cards.pop(slug).deleteLater()
        for job in self.media_jobs:
            job.cancel.set()
        self.media_jobs.clear()
        for campaign in campaigns:
            slug = campaign['slug']
            if slug not in self.cards:
                card = self.cards[slug] = CampaignCard(campaign, self.settings.asset_dir())
                card.requested.connect(self._request)
            else:
                self.cards[slug].update_campaign(campaign)
            self.cards[slug].set_running(slug in self.launcher.processes)

            def media(cancel, notify, c=campaign):
                return self.catalog.details(c, cancel), self.catalog.artwork(c, cancel)

            job = self.jobs.submit(media, lambda result, error, s=slug: self._media(generation, s, result, error),
                                   owner=self)
            self.media_jobs.append(job)
        self._layout_cards()
        self._update_controls()

    def _media(self, generation, slug, result, error):
        if not self.closing and generation == self.generation and slug in self.cards and not error:
            self.cards[slug].set_media(*result)
            self._update_controls()

    def _layout_cards(self):
        while self.grid.count():
            self.grid.takeAt(0)
        margins = self.grid.contentsMargins()
        available = self.scroll.viewport().width() - margins.left() - margins.right()
        columns = max(1, (available + self.grid.spacing()) // (280 + self.grid.spacing()))
        for index, card in enumerate(self.cards.values()):
            self.grid.addWidget(card, index // columns, index % columns, Qt.AlignmentFlag.AlignHCenter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scroll'):
            self._layout_cards()

    def eventFilter(self, watched, event):
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._layout_cards()
        return super().eventFilter(watched, event)

    def _request(self, slug, action):
        if self.closing:
            return
        campaign = self.cards[slug].campaign
        if action == 'cancel':
            if slug == self.mutation_slug:
                self.mutation.cancel.set()
                self.cards[slug].status.setText('Cancelling...')
            else:
                self.queue = [(c, a) for c, a in self.queue if c['slug'] != slug]
                self.cards[slug].set_idle()
                self._update_controls()
            return
        if action == 'play':
            if self.mutation or self.queue:
                self._set_notice('Wait for file operations to finish before playing.')
                return
            try:
                path = self.library.destination(campaign, campaign['maps'][0])
                self.launcher.launch(slug, self.settings.launch_options(), path)
            except (OSError, ValueError) as error:
                self._launch_failed(slug, str(error))
            return
        if self.launcher.processes:
            self._set_notice('Close the game before installing or removing campaign files.')
            return
        if slug == self.mutation_slug or any(c['slug'] == slug for c, _ in self.queue):
            return
        if action == 'remove':
            if QMessageBox.question(self, 'Remove campaign',
                                    f'Remove {campaign["name"]}?\n'
                                    'Files matching a known campaign version will be removed.\n'
                                    'Edited files, other files, and shared mods still in use will be kept.') != QMessageBox.StandardButton.Yes:
                return
        self.operation_notice = ''
        self.queue.append((campaign, action))
        self.cards[slug].set_busy('Queued')
        self._next_mutation()

    def _next_mutation(self):
        if self.mutation is not None or self.closing:
            return
        if not self.queue:
            self._update_controls()
            force = self.pending_force
            self.pending_refresh = self.pending_force = False
            self.load_campaigns(force)
            return
        campaign, action = self.queue.pop(0)
        slug = campaign['slug']
        self.mutation_slug = slug
        self.cards[slug].set_busy('Verifying files...' if action == 'install' else 'Removing files...')
        if self.fetcher is not None:
            self.generation += 1
            self.fetcher.cancel.set()
        library, catalog = self.library, list(self.campaigns)

        def mutate(cancel, notify):
            if action == 'install':
                return library.install(campaign, cancel, notify)
            return library.remove(campaign, catalog, cancel)

        self.mutation = self.jobs.submit(mutate, self._mutation_done, owner=self,
                                         progress=lambda data: self.cards[slug].show_progress(data), priority=True)
        self._update_controls()

    def _mutation_done(self, message, error):
        slug = self.mutation_slug
        self.mutation = self.mutation_slug = None
        if self.closing:
            return
        self.cards[slug].set_idle()
        if error:
            if isinstance(error, Cancelled):
                self.operation_notice = 'Operation cancelled. Completed files were kept.'
            else:
                show_details(self, 'Campaign operation failed', str(error))
        else:
            self.operation_notice = message
        self._set_notice(self.operation_notice)
        self._next_mutation()

    def _launch_changed(self, slug, state, message):
        if slug in self.cards:
            self.cards[slug].set_running(state == 'running')
        self._update_controls()
        if state == 'failed' and not self.closing:
            self._launch_failed(slug, message)
        elif state == 'running':
            self._set_notice('Game runner started. Closing this launcher will leave it running.')

    def _update_controls(self):
        changing = bool(self.mutation or self.queue)
        running = bool(self.launcher.processes)
        self.settings_btn.setEnabled(not changing and not running and not self.closing)
        queued = {c['slug'] for c, _ in self.queue}
        for slug, card in self.cards.items():
            if slug == self.mutation_slug or slug in queued:
                continue
            can_queue = card.campaign.get('status') != 'installed'
            card.play.setEnabled(not running and (not changing or can_queue) and not self.closing)
            card.remove.setEnabled(card.campaign.get('removable', False) and not running and not self.closing)

    def _set_notice(self, text):
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def _launch_failed(self, slug, message):
        if show_details(self, 'Launch failed', message, repair=True) and slug in self.cards:
            self._request(slug, 'install')

    def _open_settings(self):
        dialog = SettingsDialog(self.settings, self.jobs, self)
        dialog.refresh_requested.connect(self.load_campaigns)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.generation += 1
            self.library = Library(self.settings.sc2_root(), self.settings.backend.data_dir())
            self.campaigns = []
            for card in self.cards.values():
                card.deleteLater()
            self.cards.clear()
            if self.fetcher is not None:
                self.fetcher.cancel.set()
            self.load_campaigns()

    def closeEvent(self, event):
        self.closing = True
        self.queue.clear()
        if self.jobs.busy():
            self.jobs.cancel_all()
            self._set_notice('Finishing current operations...')
            self._update_controls()
            event.ignore()
        else:
            event.accept()

    def _jobs_idle(self):
        if self.closing:
            self.close()


def apply_palette(app):
    app.setStyle('Fusion')
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: '#252525', QPalette.ColorRole.WindowText: '#eeeeee',
        QPalette.ColorRole.Base: '#181818', QPalette.ColorRole.AlternateBase: '#303030',
        QPalette.ColorRole.Text: '#eeeeee', QPalette.ColorRole.Button: '#383838',
        QPalette.ColorRole.ButtonText: '#eeeeee', QPalette.ColorRole.Highlight: '#526ea3',
        QPalette.ColorRole.HighlightedText: '#ffffff', QPalette.ColorRole.ToolTipBase: '#eeeeee',
        QPalette.ColorRole.ToolTipText: '#181818', QPalette.ColorRole.PlaceholderText: '#aaaaaa',
    }.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor('#888888'))
    app.setPalette(palette)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('--smoke-test', action='store_true', help='Check the packaged UI and assets, then exit.')
    parser.add_argument('--run-game', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.run_game:
        from .game_process import run
        raise SystemExit(run(args.run_game))
    if sys.platform.startswith('linux'):
        os.environ.setdefault('QT_QPA_PLATFORMTHEME', 'xdgdesktopportal')
    app = QApplication(sys.argv[:1])
    app.setApplicationName('SC2CampaignLauncher')
    app.setOrganizationName('SC2CampaignLauncher')
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(os.environ.get('SC2CL_DESKTOP_FILE', 'sc2-campaign-launcher'))
    apply_palette(app)
    if hasattr(QImageReader, 'setAllocationLimit'):
        QImageReader.setAllocationLimit(64)
    settings = AppSettings()
    app.setWindowIcon(QIcon(str(settings.asset_dir() / 'app.ico')))
    if args.smoke_test:
        if QPixmap(str(settings.asset_dir() / 'logo.png')).isNull():
            raise SystemExit('Packaged logo is missing or invalid.')
        print(f'SC2 Campaign Launcher {__version__}: Qt and assets loaded.')
        return
    state = settings.backend.data_dir()
    state.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(state / 'launcher.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        QMessageBox.information(None, 'SC2 Campaign Launcher', 'The launcher is already running.')
        return
    from logging.handlers import RotatingFileHandler
    logs = state / 'logs'
    logs.mkdir(exist_ok=True)
    handler = RotatingFileHandler(logs / 'launcher.log', maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    jobs = JobPool(app)
    if settings.is_first_run():
        wizard = SettingsDialog(settings, jobs, first_run=True)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            jobs.cancel_all()
            if jobs.busy():
                loop = QEventLoop()
                jobs.idle.connect(loop.quit)
                loop.exec()
            return
    window = MainWindow(settings, jobs)
    window.show()
    app.exec()
    lock.unlock()


if __name__ == '__main__':
    main()
