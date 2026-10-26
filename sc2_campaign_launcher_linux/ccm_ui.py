"""Campaign cards and controls for local CCM packages."""

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (
    QBoxLayout, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMenu, QMessageBox, QProgressBar, QPushButton, QScrollArea, QToolButton,
    QVBoxLayout, QWidget,
)

from .campaign_artwork import load_artwork
from .bank_scanner_ui import BankReferencesDialog
from .campaign_saves import save_profiles
from .catalog import sort_campaigns
from .ccm import CampaignManager, SLOTS
from .files import Cancelled, check_cancel, contained_path, read_json
from .save_backups import SaveBackups
from .save_backups_ui import BackupDialog
from .progress_metadata_ui import ProgressMetadataDialog
from .progress_metadata import bank_scope


class ImportArea(QPushButton):
    imported = pyqtSignal(str)

    def __init__(self):
        super().__init__('Import campaign ZIP\nor drop a ZIP / folder here')
        self.setAccessibleName('Import campaign ZIP or drop a campaign folder')
        self.setMinimumHeight(72)
        self.setAcceptDrops(True)
        self.setStyleSheet('QPushButton { border: 2px dashed #777; border-radius: 6px; padding: 12px; }')
        self.clicked.connect(self.choose)

    def choose(self):
        name, _ = QFileDialog.getOpenFileName(self, 'Import CCM campaign', '', 'CCM packages (*.zip)')
        if name:
            self.imported.emit(name)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.is_dir() or path.suffix.casefold() == '.zip':
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile():
            self.imported.emit(urls[0].toLocalFile())
            event.acceptProposedAction()


class PlayDialog(QDialog):
    def __init__(self, settings, slot, parent):
        super().__init__(parent)
        self.setWindowTitle(SLOTS[slot][0])
        self.resize(440, 160)
        layout = QVBoxLayout(self)
        text = ('The selected custom campaign will be loaded first. '
                'Start or continue the campaign from inside StarCraft II.')
        if settings.backend.name == 'win32':
            text += ' Battle.net handles sign-in. Use the same SC2 installation selected in Settings.'
        note = QLabel(text)
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        cancel = QPushButton('Cancel')
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        open_game = QPushButton('Open through Battle.net' if settings.backend.name == 'win32'
                                else 'Open StarCraft II')
        open_game.setDefault(True)
        open_game.clicked.connect(self.accept)
        buttons.addWidget(open_game)
        layout.addLayout(buttons)


class CCMPage(QWidget):
    busy_changed = pyqtSignal()

    def __init__(self, settings, jobs, launcher, parent=None, *, catalog_available=True):
        super().__init__(parent)
        self.settings, self.jobs, self.launcher = settings, jobs, launcher
        self.catalog_available = catalog_available
        self.manager = CampaignManager(settings.sc2_root(), settings.backend.data_dir())
        self.job = None
        self.auto_scan_scheduled = False
        self.auto_scanning = False
        self.auto_scan_attempted = set()
        self.artwork_job = None
        self.artwork_root = None
        self.artwork = {}
        self.external_busy = False
        self.items = []
        self.buttons = []
        self.remove_actions = []
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        intro = QLabel('Choose Play to load a campaign and open StarCraft II. Use Load to prepare it without playing.')
        intro.setWordWrap(True)
        intro.setObjectName('hint')
        layout.addWidget(intro)
        top = QHBoxLayout()
        self.toolbar = top
        sort_group = QWidget()
        sorting = QHBoxLayout(sort_group)
        sorting.setContentsMargins(0, 0, 0, 0)
        self.sort = QComboBox()
        for title, key in [('Name', 'name'), ('Author', 'author'), ('Last updated', 'updated'), ('Map count', 'maps')]:
            self.sort.addItem(title, key)
        self.sort.setCurrentIndex(max(0, self.sort.findData(settings.settings.value('ccm_sort', 'name'))))
        self.sort.setAccessibleName('Sort imported campaigns')
        self.sort.currentIndexChanged.connect(self.rebuild)
        sorting.addWidget(QLabel('Sort by:'))
        sorting.addWidget(self.sort)
        top.addWidget(sort_group)
        top.addStretch()
        action_group = QWidget()
        actions = QHBoxLayout(action_group)
        actions.setContentsMargins(0, 0, 0, 0)
        self.backups_button = QPushButton('Saved progress')
        self.backups_button.clicked.connect(self.saved_progress)
        actions.addWidget(self.backups_button)
        self.more_button = QToolButton()
        self.more_button.setText('More')
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more_button)
        self.more_button.setMenu(menu)
        self.folder_button = menu.addAction('Import a campaign folder…')
        self.folder_button.triggered.connect(self.import_folder)
        menu.addSeparator()
        self.metadata_button = menu.addAction('Author tools: bank metadata…')
        self.metadata_button.triggered.connect(lambda: ProgressMetadataDialog(self, game_root=self.manager.root).exec())
        actions.addWidget(self.more_button)
        top.addWidget(action_group)
        layout.addLayout(top)
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setAccessibleName('Campaign operation status')
        self.notice.hide()
        layout.addWidget(self.notice)
        self.recovery_button = QPushButton('Recover interrupted change')
        self.recovery_button.setProperty('primary', True)
        self.recovery_button.clicked.connect(self.recover_campaign)
        self.recovery_button.hide()
        layout.addWidget(self.recovery_button)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        self.import_area = ImportArea()
        self.import_area.imported.connect(self.import_path)
        layout.addWidget(self.import_area)
        self.progress = QProgressBar()
        self.progress.setAccessibleName('Campaign operation progress')
        self.progress.hide()
        layout.addWidget(self.progress)
        self.cancel_button = QPushButton('Cancel operation')
        self.cancel_button.clicked.connect(self.cancel_operation)
        self.cancel_button.hide()
        layout.addWidget(self.cancel_button)
        self.launcher.changed.connect(lambda *_: self.update_controls())
        self.reload()

    def reload(self):
        try:
            self.items = self.manager.cards()
            if self.manager.journal.exists() and not self.notice.text():
                self.set_notice('An interrupted campaign change needs recovery. Recover it before playing or changing files.')
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.items = []
            self.set_notice(str(error))
        self.rebuild()
        self.refresh_artwork()

    def refresh_artwork(self):
        root, directory = self.manager.root, self.manager.directory
        if root == self.artwork_root or self.artwork_job is not None:
            return
        self.artwork_root = root
        self.artwork = {}
        self.artwork_job = self.jobs.submit(
            lambda cancel, _: load_artwork(root, directory, cancel),
            lambda result, error: self.artwork_finished(root, result, error), owner=self)

    def missing_bank_scans(self):
        return [item for item in self.items
                if (self.manager.directory, item['id']) not in self.auto_scan_attempted
                and not (self.manager.packages / item['id'] / 'bank-scan.json').is_file()]

    def queue_bank_scans(self):
        if (self.auto_scan_scheduled or self.job or self.external_busy or self.launcher.processes
                or self.manager.journal.exists() or getattr(self.window(), 'closing', False)):
            return
        if self.missing_bank_scans():
            self.auto_scan_scheduled = True
            QTimer.singleShot(0, self.start_bank_scans)

    def start_bank_scans(self):
        self.auto_scan_scheduled = False
        if (self.job or self.external_busy or self.launcher.processes or self.manager.journal.exists()
                or getattr(self.window(), 'closing', False)):
            return
        pending = self.missing_bank_scans()
        if not pending:
            return
        manager = self.manager
        self.auto_scan_attempted.update((manager.directory, item['id']) for item in pending)

        def scan(cancel, notify):
            completed, failures = 0, []
            for index, item in enumerate(pending):
                check_cancel(cancel)
                notify({'operation': 'Scanning bank references for', 'unit': 'campaign', 'index': index + 1,
                        'count': len(pending), 'name': item['name']})
                try:
                    report = manager.scan_banks(item['id'], cancel, only_if_missing=True)
                    completed += report is not None
                except (OSError, ValueError) as error:
                    logging.getLogger(__name__).warning('Automatic bank scan failed for %s: %s', item['name'], error)
                    failures.append(item['name'])
            message = f'Bank reference scans ready. Created {completed} missing reports.'
            if failures:
                message += ' Could not scan: ' + ', '.join(failures) + '. Use the campaign’s More menu to retry.'
            else:
                message += ' Review them from each campaign’s More menu.'
            return message

        self.auto_scanning = True
        self.run('Scanning bank references for previously imported campaigns...', scan)

    def artwork_finished(self, root, result, error):
        self.artwork_job = None
        if root != self.manager.root:
            self.refresh_artwork()
            return
        if error and not isinstance(error, Cancelled):
            logging.getLogger(__name__).info('Campaign artwork unavailable: %s', error)
        self.artwork = result or {}
        self.rebuild()

    def rebuild(self):
        self.settings.settings.setValue('ccm_sort', self.sort.currentData())
        while self.grid.count():
            item = self.grid.takeAt(0)
            item.widget().deleteLater()
        self.buttons = []
        self.remove_actions = []
        columns = max(1, (self.width() - 36) // 296)
        ordered = sort_campaigns(self.items, self.sort.currentData())
        if not ordered:
            empty = QLabel('No campaigns imported yet.\nImport a campaign ZIP below, or choose More to import a folder.'
                           + ('\nFor downloadable campaigns, open Browse campaigns.' if self.catalog_available else ''))
            empty.setWordWrap(True)
            empty.setObjectName('emptyState')
            self.grid.addWidget(empty, 0, 0)
        for index, campaign in enumerate(ordered):
            card = QFrame()
            card.setFixedSize(280, 320)
            card.setStyleSheet('QFrame { background: #2a2a2a; border-radius: 8px; }')
            layout = QVBoxLayout(card)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(6)
            cover = QLabel(SLOTS[campaign['slot']][0])
            cover.setWordWrap(True)
            cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cover.setFixedHeight(104)
            cover.setStyleSheet('background: #191919; color: #ddd; font-size: 19px; padding: 8px;')
            if path := self.artwork.get(campaign['slot']):
                picture = QPixmap(str(path))
                if not picture.isNull():
                    cover.setPixmap(picture.scaled(254, 104, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
            layout.addWidget(cover)
            title = QLabel(campaign['name'])
            title.setTextFormat(Qt.TextFormat.PlainText)
            title.setWordWrap(True)
            title_font = title.font()
            title_font.setPointSize(11)
            title_font.setWeight(QFont.Weight.Bold)
            title.setFont(title_font)
            title.setToolTip(campaign.get('description', ''))
            layout.addWidget(title)
            author = QLabel(f"{campaign['author']}  {campaign['version']}")
            author.setTextFormat(Qt.TextFormat.PlainText)
            author.setObjectName('hint')
            author.setWordWrap(True)
            layout.addWidget(author)
            active = campaign.get('active', False)
            status = ('Loaded' if active else 'Ready to load') + ' · ' + SLOTS[campaign['slot']][0]
            status_label = QLabel(status)
            status_label.setTextFormat(Qt.TextFormat.PlainText)
            status_label.setWordWrap(True)
            layout.addWidget(status_label)
            play = QPushButton('Play')
            play.setProperty('primary', True)
            play.setAccessibleName('Play ' + campaign['name'])
            play.setToolTip('Load this campaign and open StarCraft II.')
            play.clicked.connect(lambda _, c=campaign: self.play(c))
            layout.addWidget(play)
            self.buttons.append(play)
            row = QHBoxLayout()
            load = QPushButton('Revert to base' if active else 'Load')
            load.setToolTip(f"Restore {SLOTS[campaign['slot']][0]} and keep this campaign imported."
                            if active else 'Load this campaign into its campaign slot.')
            load.clicked.connect(lambda _, c=campaign: self.toggle(c))
            row.addWidget(load)
            self.buttons.append(load)
            more = QToolButton()
            more.setText('More')
            more.setAccessibleName('More actions for ' + campaign['name'])
            more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            more.setMenu(QMenu(more))
            banks = more.menu().addAction('Scan and review bank references…')
            banks.triggered.connect(lambda _, c=campaign: self.review_banks(c))
            remove = more.menu().addAction('Remove imported copy…')
            remove.triggered.connect(lambda _, c=campaign: self.remove(c))
            remove.setProperty('inactive_only', active)
            self.remove_actions.append(remove)
            more.setToolTip('Review bank references or remove the imported copy. Revert before removing a loaded campaign.')
            row.addWidget(more)
            self.buttons.append(more)
            layout.addLayout(row)
            self.grid.addWidget(card, index // columns, index % columns)
        self.update_controls()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'grid'):
            widgets = [self.grid.itemAt(i).widget() for i in range(self.grid.count())]
            for widget in widgets:
                self.grid.removeWidget(widget)
            columns = max(1, (self.scroll.viewport().width() - 24) // 296)
            for index, widget in enumerate(widgets):
                self.grid.addWidget(widget, index // columns, index % columns)
            self.toolbar.setDirection(QBoxLayout.Direction.TopToBottom if self.width() < 380
                                      else QBoxLayout.Direction.LeftToRight)

    def update_controls(self):
        available = not self.job and not self.external_busy and not self.launcher.processes
        pending = self.manager.journal.exists()
        self.recovery_button.setVisible(pending)
        self.recovery_button.setEnabled(bool(available and pending))
        for button in self.buttons:
            button.setEnabled(bool(available and not pending))
        for action in self.remove_actions:
            action.setEnabled(bool(available and not pending and not action.property('inactive_only')))
        for control in (self.import_area, self.folder_button, self.backups_button, self.metadata_button, self.more_button):
            control.setEnabled(bool(available and not pending))
        self.queue_bank_scans()

    def set_notice(self, text):
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def cancel_operation(self):
        if self.job:
            self.job.cancel.set()
            self.cancel_button.setText('Stopping…')
            self.cancel_button.setEnabled(False)

    def recover_campaign(self):
        def recover(cancel, notify):
            self.manager.recover()
            return 'Campaign files recovered. Choose Play to continue.'
        self.run('Recovering campaign files…', recover)

    def show_progress(self, data):
        self.set_notice(f"{data.get('operation', 'Working on')} {data.get('unit', 'file')} "
                        f"{data['index']} of {data['count']}: {data['name']}")
        self.progress.setRange(0, max(1, data['count']))
        self.progress.setValue(data['index'])

    def run(self, text, function, after=None):
        if self.job or self.external_busy or self.launcher.processes:
            self.set_notice('Close the game and wait for file operations to finish first.')
            return
        self.set_notice(text)
        self.job = self.jobs.submit(function, lambda result, error: self.finished(result, error, after),
                                    owner=self, priority=True,
                                    progress=self.show_progress)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel_button.setText('Cancel operation')
        self.cancel_button.setEnabled(True)
        self.cancel_button.show()
        self.update_controls()
        self.busy_changed.emit()

    def finished(self, result, error, after):
        automatic = self.auto_scanning
        self.auto_scanning = False
        self.job = None
        self.cancel_button.hide()
        self.progress.hide()
        if isinstance(error, Cancelled):
            self.set_notice('Bank scanning cancelled. Completed reports were kept; missing reports will be retried next launch.'
                            if automatic else
                            'Cancelled. Completed backups were kept. Use recovery before continuing.'
                                if self.manager.journal.exists() else
                                'Cancelled. Completed backups were kept; unfinished changes were rolled back.')
        elif error:
            self.set_notice(str(error))
        else:
            self.set_notice(result if isinstance(result, str) else 'Campaign imported.')
        self.reload()
        self.busy_changed.emit()
        if error is None and after and not self.external_busy:
            after()

    def import_folder(self):
        name = QFileDialog.getExistingDirectory(self, 'Import a CCM campaign folder')
        if name:
            self.import_path(name)

    def import_path(self, path):
        self.run('Importing campaign...', lambda cancel, _: self.manager.import_package(Path(path), cancel))

    def review_banks(self, campaign):
        profile = self.backup_profile()
        if profile is False:
            return

        def show():
            try:
                report = read_json(contained_path(self.manager.packages, campaign['id'], 'bank-scan.json'))
                BankReferencesDialog(self.manager, campaign, report, profile, self).exec()
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.set_notice(str(error))

        self.run('Scanning campaign bank references...',
                 lambda cancel, _: self.manager.scan_banks(campaign['id'], cancel), after=show)

    def toggle(self, campaign):
        if self.job or self.external_busy or self.launcher.processes:
            return
        profile = self.backup_profile()
        if profile is False:
            return
        self.run('Reverting to base campaign...' if campaign['active'] else 'Loading campaign...',
                 lambda cancel, notify: self.switch(campaign, profile, cancel, notify,
                                                   revert=campaign['active']))

    def backup_profile(self):
        selected = self.settings.settings.value('campaign_backup_profile', '', type=str)
        if selected:
            try:
                SaveBackups(Path(selected), self.settings.backend.data_dir())
                return Path(selected)
            except (OSError, ValueError) as error:
                self.set_notice(str(error) + ' Choose the account in Saved progress.')
                return False
        profiles = save_profiles(self.settings.launch_options(), self.settings.backend.name)
        if not profiles:
            return None
        if len(profiles) == 1:
            self.settings.settings.setValue('campaign_backup_profile', str(profiles[0]))
            return profiles[0]
        dialog = BackupDialog(self.settings, self, choose_only=True)
        return Path(dialog.profile.currentData()) if dialog.exec() == QDialog.DialogCode.Accepted else False

    def switch(self, campaign, profile, cancel, notify, *, revert=False):
        with self.manager.lock:
            store = SaveBackups(profile, self.settings.backend.data_dir()) if profile else None
            if store and store.journal.exists():
                raise ValueError('An interrupted save restore needs recovery. Open Saved progress first.')
            active = self.manager._state()['active']
            changed = revert or active.get(campaign['slot']) != campaign['id']
            backup = None
            scoped_count = 0
            if store and changed:
                packages = self.manager.list_packages()
                context = {item['slot']: item['name'] for item in packages if item['active']}
                label = 'Before reverting ' if revert else 'Before loading '
                backup = store.snapshot(label + campaign['name'], cancel, context=context, progress=notify)
                owners = set()
                for item in packages:
                    if (not item.get('progress') or not item['progress']['banks']
                            or not (item['id'] == active.get(campaign['slot']) or
                                    (not revert and item['id'] == campaign['id']))):
                        continue
                    scope = bank_scope(item)
                    signature = (scope['owner'], tuple(scope['banks']))
                    if signature in owners or self.manager.bank_conflicts(item['progress'], scope['owner']):
                        continue
                    owners.add(signature)
                    store.snapshot('Banks: ' + item['name'] + ' ' + item['version'] + ' / ' + label.lower() + campaign['name'], cancel,
                                   context=context, progress=notify, scope=scope)
                    scoped_count += 1
            message = (self.manager.unload(campaign['slot'], cancel, notify) if revert
                       else self.manager.activate(campaign['id'], cancel))
            if backup:
                message += f" Saved progress backed up ({len(backup['files'])} files)."
                if scoped_count:
                    message += f' Campaign bank backups: {scoped_count}.'
            elif profile is None:
                message += ' No account saves or banks were found to back up.'
            return message

    def saved_progress(self):
        if self.job or self.external_busy or self.launcher.processes:
            return
        try:
            dialog = BackupDialog(self.settings, self, manager=self.manager)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.set_notice(str(error))
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            store = SaveBackups(Path(dialog.profile.currentData()), self.settings.backend.data_dir())
        except (OSError, ValueError) as error:
            self.set_notice(str(error))
            return
        action, identity, scope = dialog.action, dialog.identity, dialog.selected_scope()

        def operation(cancel, notify):
            with self.manager.lock:
                if action == 'backup':
                    if scope and self.manager.bank_conflicts({'format': 1, 'banks': scope['banks']}, scope['owner']):
                        raise ValueError('Another imported campaign declares these banks. Resolve the overlap first.')
                    label = 'Banks: ' + scope['campaign'] if scope else 'Manual backup'
                    record = store.snapshot(label, cancel, progress=notify, scope=scope)
                    return f"Saved progress backed up ({len(record['files'])} files)."
                if action == 'restore':
                    with store.lock:
                        saved_scope = store._manifest(identity).get('bank_scope')
                        if saved_scope and self.manager.bank_conflicts(
                                {'format': 1, 'banks': saved_scope['banks']}, saved_scope['owner']):
                            raise ValueError('Another imported campaign declares these banks. Restore was stopped.')
                        store.restore(identity, cancel, notify)
                    return 'Saved progress restored. The previous files were backed up first.'
                store.recover()
                return 'Interrupted restore recovered. Previous progress restored.'

        self.run('Working on saved progress...', operation)

    def remove(self, campaign):
        if self.job or self.external_busy or self.launcher.processes:
            return
        if QMessageBox.question(self, 'Remove imported campaign', f'Remove {campaign["name"]} from this library?') \
                == QMessageBox.StandardButton.Yes:
            self.run('Removing imported campaign...', lambda cancel, _: self.manager.remove(campaign['id']))

    def play(self, campaign):
        if self.job or self.external_busy or self.launcher.processes:
            return
        dialog = PlayDialog(self.settings, campaign['slot'], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        options = self.settings.launch_options()
        try:
            self.settings.backend.command(options, None)
        except (OSError, ValueError) as error:
            self.set_notice(str(error))
            return
        profile = self.backup_profile()
        if profile is False:
            return

        def launch():
            try:
                self.launcher.launch_target(campaign['slug'], options, None)
                self.set_notice('Opening StarCraft II. Start or load your campaign inside the game.')
            except (OSError, ValueError) as error:
                self.set_notice(str(error))

        self.run('Preparing campaign...',
                 lambda cancel, notify: self.switch(campaign, profile, cancel, notify), launch)
