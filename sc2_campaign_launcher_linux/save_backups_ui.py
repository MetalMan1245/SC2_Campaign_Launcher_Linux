"""Account selection and explicit saved-progress backup actions."""

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QVBoxLayout

from .campaign_saves import save_profiles
from .save_backups import SaveBackups
from .progress_metadata import bank_scope


class BackupDialog(QDialog):
    def __init__(self, settings, parent=None, *, choose_only=False, manager=None):
        super().__init__(parent)
        self.settings = settings
        self.choose_only = choose_only
        self.manager = manager
        self.records = {}
        self.action = None
        self.identity = None
        self.setWindowTitle('Saved progress')
        self.resize(640, 440 if not choose_only else 180)
        layout = QVBoxLayout(self)
        note = QLabel('Choose the SC2 account to back up before changing campaigns.' if choose_only else
                      'Back up or restore progress for the account you play with. '
                      'Current progress is backed up before a restore.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.profile = QComboBox()
        self.profile.setAccessibleName('StarCraft II account')
        self.profile.addItem('Choose an account', None)
        for path in save_profiles(settings.launch_options(), settings.backend.name):
            self.add_profile(path)
        selected = settings.settings.value('campaign_backup_profile', '', type=str)
        if selected and self.profile.findData(selected) < 0:
            self.add_profile(Path(selected))
        index = self.profile.findData(selected)
        self.profile.setCurrentIndex(index if index > 0 else 1 if self.profile.count() == 2 else 0)
        layout.addWidget(self.profile)
        browse = QPushButton('Choose another Saves folder')
        browse.clicked.connect(self.browse)
        layout.addWidget(browse)
        self.scope = QComboBox(self)
        self.scope.setAccessibleName('Backup contents')
        self.scope.addItem('Whole account: Saves and Banks', None)
        if manager is not None:
            seen = set()
            for campaign in manager.list_packages():
                if not campaign.get('progress') or not campaign['progress']['banks']:
                    continue
                scope = bank_scope(campaign)
                signature = (scope['owner'], tuple(scope['banks']))
                if signature in seen:
                    continue
                seen.add(signature)
                self.scope.addItem(f"Campaign banks: {campaign['name']} ({campaign['version']})", scope)
        if not choose_only:
            layout.addWidget(self.scope)
        else:
            self.scope.hide()
        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.notice)
        self.entries = QListWidget(self)
        self.entries.setAccessibleName('Saved progress backups')
        if choose_only:
            self.entries.hide()
        else:
            layout.addWidget(self.entries)
        self.buttons = {}
        row = QHBoxLayout()
        actions = [('Use this account', 'choose')] if choose_only else [
            ('Back up now', 'backup'), ('Restore selected', 'restore'), ('Recover restore', 'recover')]
        for text, action in actions:
            button = QPushButton(text)
            button.clicked.connect(lambda _, a=action: self.select(a))
            self.buttons[action] = button
            row.addWidget(button)
        close = QPushButton('Cancel' if choose_only else 'Close')
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)
        self.profile.currentIndexChanged.connect(self.refresh)
        self.scope.currentIndexChanged.connect(self.refresh)
        self.entries.currentRowChanged.connect(self.update_buttons)
        self.refresh()

    def browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose the account’s Saves folder')
        if folder:
            self.add_profile(Path(folder))
            self.profile.setCurrentIndex(self.profile.count() - 1)

    def add_profile(self, path):
        self.profile.addItem(f'{path.parent.parent.name} / {path.parent.name}', str(path))
        self.profile.setItemData(self.profile.count() - 1, str(path), Qt.ItemDataRole.ToolTipRole)

    def refresh(self):
        self.entries.clear()
        self.valid = False
        self.pending = False
        self.notice.clear()
        self.records = {}
        self.notice.setText('Choose an account above, or choose its Saves folder.')
        scope = self.selected_scope()
        if value := self.profile.currentData():
            try:
                store = SaveBackups(Path(value), self.settings.backend.data_dir())
                self.pending = store.journal.exists()
                for item in store.list_backups():
                    if scope and item.get('bank_scope', {}).get('owner') != scope['owner']:
                        continue
                    date = datetime.fromisoformat(item['created']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
                    count = len(item['files'])
                    files = f"{count} {'file' if count == 1 else 'files'}"
                    kind = 'Campaign banks' if item.get('bank_scope') else 'Whole account'
                    self.entries.addItem(f"{date} | {kind} | {item['label']} | {files}")
                    self.entries.item(self.entries.count() - 1).setData(Qt.ItemDataRole.UserRole, item['id'])
                    self.records[item['id']] = item
                self.valid = True
                self.notice.setText('An interrupted restore needs recovery.' if self.pending else
                                    'Restoring replaces files present in the backup. Newer files remain. '
                                    'Current progress is backed up first.')
                if scope:
                    conflicts = self.conflicts(scope)
                    if conflicts and not self.pending:
                        self.valid = False
                        self.notice.setText('Bank ownership overlaps another imported campaign: ' + '; '.join(conflicts))
                    elif not self.pending:
                        self.notice.setText('This scope backs up and restores only author-declared banks. '
                                            'SC2 saves remain under Whole account.')
                if self.choose_only and self.valid and not self.pending:
                    self.notice.setText('Backups will use this account’s Saves and Banks.')
                elif not self.records and not self.pending and not scope:
                    self.notice.setText('No backups yet. Choose Back up now to create your first backup.')
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.valid = False
                self.notice.setText(str(error))
        self.update_buttons()

    def selected_scope(self):
        return self.scope.currentData()

    def conflicts(self, scope):
        return self.manager.bank_conflicts({'format': 1, 'banks': scope['banks']}, scope['owner']) if self.manager else []

    def update_buttons(self):
        for action, button in self.buttons.items():
            enabled = self.valid and (self.pending if action == 'recover' else not self.pending)
            if action == 'restore':
                item = self.entries.currentItem()
                enabled = enabled and item is not None and bool(
                    self.records.get(item.data(Qt.ItemDataRole.UserRole), {}).get('files'))
            button.setEnabled(bool(enabled))

    def select(self, action):
        if action == 'restore':
            item = self.entries.currentItem()
            if item is None:
                return
            record = self.records[item.data(Qt.ItemDataRole.UserRole)]
            if not record['files']:
                self.notice.setText('This backup contains no files to restore. Newer files are kept.')
                return
            scope = record.get('bank_scope')
            try:
                conflicts = self.conflicts(scope) if scope else []
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.notice.setText(str(error))
                return
            if conflicts:
                self.notice.setText('Restore stopped. Another campaign declares the same banks: ' + '; '.join(conflicts))
                return
            details = ('Campaign banks only: ' + scope['campaign'] + '\n' + '\n'.join(sorted(record['files']))
                       if scope else 'This restores saves and banks for all campaigns in this backup.')
            if QMessageBox.question(self, 'Restore saved progress',
                                    f'Restore {item.text()}?\n\nAccount: {self.profile.currentData()}\n\n'
                                    + details + '\n\n'
                                    'Current files are backed up first; newer files are kept.',
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            self.identity = item.data(Qt.ItemDataRole.UserRole)
        self.action = action
        self.settings.settings.setValue('campaign_backup_profile', self.profile.currentData())
        self.accept()
