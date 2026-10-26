"""Review detected references and save a local bank list without changing game data."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from .local_banks import existing_banks, load_local_list, matching_paths, save_local_list, shared_references


class BankReferencesDialog(QDialog):
    def __init__(self, manager, campaign, report, profile=None, parent=None):
        super().__init__(parent)
        self.manager, self.campaign, self.profile = manager, campaign, profile
        self.setWindowTitle('Bank references: ' + campaign['name'])
        self.resize(850, 700)
        layout = QVBoxLayout(self)
        note = QLabel('These files reference the banks below. References do not prove ownership. '
                      'Local lists are saved for review; this feature does not move, restore, or change banks.')
        note.setWordWrap(True)
        layout.addWidget(note)
        account = QLabel('Account: ' + str(profile) if profile else 'No account selected. Choose one in Saved progress to save a local list.')
        account.setTextFormat(Qt.TextFormat.PlainText)
        account.setWordWrap(True)
        layout.addWidget(account)
        self.references = QTreeWidget()
        self.references.setHeaderLabels(['Bank reference', 'Existing paths', 'Also referenced by'])
        self.references.setRootIsDecorated(False)
        self.references.setColumnWidth(0, 190)
        self.references.setColumnWidth(1, 350)
        self.references.setAccessibleName('Detected bank references')
        paths = existing_banks(profile) if profile else []
        shared = shared_references(manager, campaign)
        for reference in report['references']:
            matches = matching_paths(reference['name'], paths)
            row = QTreeWidgetItem([reference['name'], '\n'.join(matches) or 'No matching file found',
                                   ', '.join(shared.get(reference['name'].casefold(), []))])
            row.setData(0, Qt.ItemDataRole.UserRole, matches)
            for column in range(3):
                row.setToolTip(column, row.text(column))
            evidence = '\n'.join(e['source'] + (f":{e['line']}" if e['line'] else '') + f" ({e['kind']})"
                                 for e in reference['evidence'])
            row.setData(1, Qt.ItemDataRole.UserRole, evidence)
            self.references.addTopLevelItem(row)
        self.references.currentItemChanged.connect(self.show_evidence)
        layout.addWidget(self.references, 2)
        self.evidence = QPlainTextEdit()
        self.evidence.setReadOnly(True)
        self.evidence.setPlaceholderText('Select a reference to see its source files.')
        self.evidence.setMaximumHeight(100)
        layout.addWidget(self.evidence)
        add = QPushButton('Add matching paths for selected reference')
        add.clicked.connect(self.add_matches)
        add.setEnabled(profile is not None)
        layout.addWidget(add)
        layout.addWidget(QLabel('Local bank list: exact paths relative to this account’s Banks folder, one per line.'))
        self.paths = QPlainTextEdit()
        self.paths.setAccessibleName('Local bank paths')
        self.paths.setPlaceholderText('Example.SC2Bank\n1-S2-1-1234567/Example.SC2Bank')
        local = load_local_list(manager, campaign, profile) if profile else {'banks': [], 'stale': False}
        self.paths.setPlainText('\n'.join(local['banks']))
        self.paths.setEnabled(profile is not None)
        layout.addWidget(self.paths, 1)
        warnings = QPlainTextEdit()
        warnings.setReadOnly(True)
        warnings.setAccessibleName('Bank scan limitations')
        warnings.setPlainText('\n'.join(report['warnings']) or
                              'No scan errors reported. Dynamic bank names and exclusive ownership still require review.')
        warnings.setMaximumHeight(100)
        layout.addWidget(warnings)
        self.notice = QLabel('The package changed since this local list was saved. Review it before saving again.'
                             if local['stale'] else f"Scanned {report['documents']} documents; {len(report['references'])} bank names found.")
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        buttons = QHBoxLayout()
        self.save_button = QPushButton('Save local bank list')
        self.save_button.setEnabled(profile is not None)
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(self.save_button)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def show_evidence(self, current, previous):
        self.evidence.setPlainText(current.data(1, Qt.ItemDataRole.UserRole) if current else '')

    def add_matches(self):
        row = self.references.currentItem()
        if row is None:
            return
        matches = row.data(0, Qt.ItemDataRole.UserRole)
        names = [line.strip() for line in self.paths.toPlainText().splitlines() if line.strip()]
        seen = {name.casefold() for name in names}
        names.extend(name for name in matches if name.casefold() not in seen)
        self.paths.setPlainText('\n'.join(names))
        self.notice.setText('Review each path, especially when multiple author folders match. Nothing has been saved yet.'
                            if matches else 'No existing bank matched. You can enter a known exact path manually.')

    def save(self):
        try:
            save_local_list(self.manager, self.campaign, self.profile,
                            [name.strip() for name in self.paths.toPlainText().splitlines() if name.strip()])
            self.notice.setText('Local bank list saved for this account and package. Game banks are unchanged.')
        except (OSError, ValueError) as error:
            self.notice.setText(str(error))
