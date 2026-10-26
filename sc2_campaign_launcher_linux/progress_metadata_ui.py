"""Write campaign bank declarations beside the author's CCM metadata.txt."""

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout

from .files import atomic_json, contained_path
from .progress_metadata import PROGRESS_FILE, load_progress, validate_progress


class ProgressMetadataDialog(QDialog):
    def __init__(self, parent=None, *, game_root=None):
        super().__init__(parent)
        self.folder = None
        self.game_root = Path(game_root).resolve() if game_root else None
        self.setWindowTitle('Campaign bank metadata')
        self.resize(620, 420)
        layout = QVBoxLayout(self)
        note = QLabel('List the bank files this campaign uses, one path per line. Paths start inside '
                      'the player’s Banks folder. Include an author-ID subfolder when used. '
                      'Leave out banks shared with unrelated campaigns.')
        note.setWordWrap(True)
        layout.addWidget(note)
        choose = QPushButton('Open campaign folder')
        choose.clicked.connect(self.choose_folder)
        layout.addWidget(choose)
        self.location = QLabel('Choose the folder containing metadata.txt.')
        self.location.setTextFormat(Qt.TextFormat.PlainText)
        self.location.setWordWrap(True)
        layout.addWidget(self.location)
        self.paths = QPlainTextEdit()
        self.paths.setPlaceholderText('CampaignProgress.SC2Bank\n1-S2-1-1234567/CampaignProgress.SC2Bank')
        layout.addWidget(self.paths)
        add = QPushButton('Add existing bank files')
        add.clicked.connect(self.add_existing)
        layout.addWidget(add)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        row = QHBoxLayout()
        save = QPushButton('Save campaign-progress.json')
        save.clicked.connect(self.save)
        row.addWidget(save)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        row.addWidget(close)
        layout.addLayout(row)

    def open_folder(self, folder):
        folder = Path(folder)
        if self.game_root is not None and folder.resolve().is_relative_to(self.game_root):
            raise ValueError('Choose an unpacked campaign source folder outside the SC2 installation.')
        metas = [p for p in folder.iterdir() if p.name.casefold() == 'metadata.txt' and p.is_file()]
        if len(metas) != 1:
            raise ValueError('Choose the campaign folder containing one metadata.txt.')
        progress = load_progress(folder)
        self.folder = folder
        self.location.setText(str(folder))
        self.paths.setPlainText('\n'.join(progress['banks']) if progress is not None else '')
        self.notice.setText('Existing metadata loaded.' if progress is not None else 'No bank list has been saved yet.')

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose the folder containing metadata.txt')
        if folder:
            try:
                self.open_folder(folder)
            except (OSError, ValueError) as error:
                self.notice.setText(str(error))

    def add_existing(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose this account’s Banks folder')
        if not folder:
            return
        root = Path(folder)
        if root.name.casefold() != 'banks':
            self.notice.setText('Choose the Banks folder itself so paths are relative to it.')
            return
        filenames, _ = QFileDialog.getOpenFileNames(self, 'Choose this campaign’s banks', folder,
                                                   'StarCraft II banks (*.SC2Bank)')
        try:
            names = self.paths.toPlainText().splitlines()
            names.extend(Path(name).resolve().relative_to(root.resolve()).as_posix() for name in filenames)
            progress = validate_progress({'format': 1, 'banks': list(dict.fromkeys(n.strip() for n in names if n.strip()))})
            self.paths.setPlainText('\n'.join(progress['banks']))
        except (OSError, ValueError) as error:
            self.notice.setText(str(error))

    def save(self):
        if self.folder is None:
            self.notice.setText('Choose the campaign folder first.')
            return
        try:
            if self.game_root is not None and self.folder.resolve().is_relative_to(self.game_root):
                raise ValueError('Save author metadata outside the SC2 installation.')
            progress = validate_progress({'format': 1, 'banks': [n.strip() for n in self.paths.toPlainText().splitlines()
                                                               if n.strip()]})
            existing = [p for p in self.folder.iterdir() if p.name.casefold() == PROGRESS_FILE]
            if len(existing) > 1:
                raise ValueError('The folder contains duplicate campaign-progress.json files.')
            path = contained_path(self.folder, existing[0].name if existing else PROGRESS_FILE)
            if path.exists() and QMessageBox.question(
                    self, 'Replace bank metadata', 'Replace the existing campaign bank list?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
            atomic_json(path, progress)
            self.notice.setText('Saved. Include campaign-progress.json beside metadata.txt in the campaign ZIP. '
                                'Only paths are included; player bank contents stay on this computer.')
        except (OSError, ValueError) as error:
            self.notice.setText(str(error))
