from threading import Event
from unittest.mock import Mock

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QObject, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QPushButton

from conftest import campaign
from test_ccm import package
from test_runtime import FakeCatalog, spin
from sc2_campaign_launcher_linux import ccm_ui, save_backups_ui
from sc2_campaign_launcher_linux import sc2_campaign_launcher_linux as ui
from sc2_campaign_launcher_linux.files import Cancelled
from sc2_campaign_launcher_linux.platform_backend import LinuxBackend, MANAGED_PROTON
from sc2_campaign_launcher_linux.settings import AppSettings


class NoGameLauncher(QObject):
    changed = pyqtSignal(str, str, str)

    def __init__(self):
        super().__init__()
        self.processes = {}

    def launch_target(self, *args):
        raise AssertionError('Usability checks must not launch SC2.')


def test_campaign_bank_review_scans_before_opening_dialog(screen, qapp, tmp_path, monkeypatch):
    from test_bank_scanner import source_package
    page = screen.ccm_page
    item = page.manager.import_package(source_package(tmp_path))
    profile = tmp_path / 'account/Saves'
    profile.mkdir(parents=True)
    monkeypatch.setattr(page, 'backup_profile', lambda: profile)
    shown = []

    class Dialog:
        def __init__(self, manager, campaign, report, selected_profile, parent):
            shown.append((campaign['id'], report['documents'], selected_profile))

        def exec(self):
            return 0

    monkeypatch.setattr(ccm_ui, 'BankReferencesDialog', Dialog)
    page.reload()
    page.review_banks(item)
    spin(qapp, lambda: page.job is None)
    assert shown == [(item['id'], 3, profile)]


def test_older_imports_scan_when_page_opens_without_repeating(screen, qapp, tmp_path, monkeypatch):
    from test_bank_scanner import source_package
    from sc2_campaign_launcher_linux.jobs import JobPool
    item = screen.ccm_page.manager.import_package(source_package(tmp_path))
    path = screen.ccm_page.manager.packages / item['id'] / 'bank-scan.json'
    path.unlink()
    jobs = JobPool()
    page = ccm_ui.CCMPage(screen.settings, jobs, NoGameLauncher())
    scan = Mock(wraps=page.manager.scan_banks)
    monkeypatch.setattr(page.manager, 'scan_banks', scan)
    spin(qapp, lambda: path.is_file() and page.job is None)
    assert scan.call_count == 1
    assert scan.call_args.kwargs == {'only_if_missing': True}
    original = path.read_bytes()
    for _ in range(3):
        page.reload()
        qapp.processEvents()
    assert scan.call_count == 1 and path.read_bytes() == original
    assert not (page.manager.directory / 'bank-lists').exists()
    page.close()
    spin(qapp, lambda: not jobs.busy())


def test_automatic_scan_waits_for_operations_and_keeps_going_after_failure(screen, qapp, tmp_path, monkeypatch):
    from test_bank_scanner import source_package
    page = screen.ccm_page
    first = page.manager.import_package(source_package(tmp_path, 'First'))
    second = page.manager.import_package(source_package(tmp_path, 'Second'))
    for item in (first, second):
        (page.manager.packages / item['id'] / 'bank-scan.json').unlink()
    scan = page.manager.scan_banks
    calls = []

    def fail_first(identity, *args, **kwargs):
        calls.append(identity)
        if identity == first['id']:
            raise OSError('Unavailable package')
        return scan(identity, *args, **kwargs)
    monkeypatch.setattr(page.manager, 'scan_banks', fail_first)
    page.external_busy = True
    page.reload()
    qapp.processEvents()
    assert calls == []
    page.external_busy = False
    page.update_controls()
    spin(qapp, lambda: len(calls) == 2 and page.job is None)
    assert (page.manager.packages / second['id'] / 'bank-scan.json').is_file()
    assert 'Could not scan: First' in page.notice.text()
    page.reload()
    qapp.processEvents()
    assert len(calls) == 2


def test_close_cancels_automatic_scan_without_restarting(screen, qapp, tmp_path, monkeypatch):
    from test_bank_scanner import source_package
    from sc2_campaign_launcher_linux.files import check_cancel
    page = screen.ccm_page
    item = page.manager.import_package(source_package(tmp_path))
    path = page.manager.packages / item['id'] / 'bank-scan.json'
    path.unlink()
    entered = Event()
    calls = []

    def scanning(identity, cancel, **kwargs):
        calls.append(identity)
        entered.set()
        cancel.wait(5)
        check_cancel(cancel)
        raise AssertionError('The scan should have been cancelled')
    monkeypatch.setattr(page.manager, 'scan_banks', scanning)
    page.reload()
    spin(qapp, entered.is_set)
    screen.close()
    spin(qapp, lambda: not screen.jobs.busy())
    qapp.processEvents()
    assert calls == [item['id']]
    assert not path.exists()


@pytest.fixture
def screen(qapp, sc2_root, tmp_path, monkeypatch):
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    monkeypatch.setattr(ccm_ui, 'load_artwork', lambda *args: {})
    monkeypatch.setattr(ccm_ui, 'save_profiles', lambda *args: [])
    monkeypatch.setattr(save_backups_ui, 'save_profiles', lambda *args: [])
    monkeypatch.setattr('sc2_campaign_launcher_linux.ccm.game_running', lambda: False)
    settings = AppSettings(QSettings(str(tmp_path / 'ui.ini'), QSettings.Format.IniFormat))
    settings.settings.setValue('sc2_root', str(sc2_root))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    monkeypatch.setattr(settings.backend, 'cache_dir', lambda: tmp_path / 'cache')
    ui.apply_palette(qapp)
    window = ui.MainWindow(settings, catalog=FakeCatalog(), launcher=NoGameLauncher(), autoload=False)
    window.resize(1000, 720)
    window.show()
    spin(qapp, lambda: not window.jobs.busy())
    yield window
    window.launcher.processes.clear()
    window.close()
    spin(qapp, lambda: not window.jobs.busy())


def test_first_run_continue_validates_focus_and_ignores_repeated_accept(screen, qapp, monkeypatch):
    dialog = ui.SettingsDialog(screen.settings, screen.jobs, first_run=True)
    save = Mock(wraps=screen.settings.save)
    monkeypatch.setattr(screen.settings, 'save', save)
    dialog.show()
    dialog.activateWindow()
    qapp.processEvents()
    root = dialog.sc2_in.text()
    dialog.sc2_in.clear()
    QTest.keyClick(dialog.save_btn, Qt.Key.Key_Return)
    assert 'Choose' in dialog.status.text()
    assert QApplication.focusWidget() is dialog.sc2_in
    assert save.call_count == 0
    dialog.sc2_in.setText(root)
    QTest.keyClick(dialog.sc2_in, Qt.Key.Key_Return)
    dialog.save_btn.click()
    assert dialog.result() == dialog.DialogCode.Accepted and save.call_count == 1


def test_linux_advanced_settings_expand_for_an_invalid_value(screen, qapp, monkeypatch):
    screen.settings.backend = LinuxBackend()
    monkeypatch.setattr(ui, 'discover_runners', lambda _: [
        {'name': 'UMU managed Proton', 'path': MANAGED_PROTON, 'type': 'proton'}])
    dialog = ui.SettingsDialog(screen.settings, screen.jobs, first_run=True)
    dialog.show()
    spin(qapp, lambda: dialog.discovery is None)
    assert dialog.advanced_fields.isHidden()
    dialog.umu_in.setText('/missing/umu-run')
    dialog.save_btn.click()
    assert dialog.advanced.isChecked() and dialog.advanced_fields.isVisible()
    assert QApplication.focusWidget() is dialog.umu_in
    assert 'UMU executable' in dialog.status.text()
    dialog.reject()


def test_search_filters_by_author_and_keyboard_clear_restores_cards(screen, qapp):
    assert screen.tabs.currentIndex() == 1
    assert screen.tabs.tabText(0) == 'My campaigns'
    first, second = campaign('Alpha'), campaign('Beta')
    second['author'] = 'Oracle'
    screen._render([first, second], screen.generation)
    spin(qapp, lambda: not screen.jobs.busy())
    screen.search.setFocus()
    QTest.keyClicks(screen.search, 'oracle')
    assert screen.cards['Alpha'].isHidden() and not screen.cards['Beta'].isHidden()
    QTest.keyClick(screen.search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(screen.search, 'no such campaign')
    assert screen.empty_labels['Synergy'].isVisible()
    assert 'clear the search' in screen.empty_labels['Synergy'].text()
    QTest.keyClick(screen.search, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(screen.search, Qt.Key.Key_Backspace)
    assert not screen.cards['Alpha'].isHidden() and not screen.cards['Beta'].isHidden()
    QTest.keyClick(screen.search, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is screen.sort


def test_visible_refresh_coalesces_repeated_clicks(screen, qapp):
    for _ in range(8):
        QTest.mouseClick(screen.refresh_btn, Qt.MouseButton.LeftButton)
    spin(qapp, lambda: not screen.jobs.busy() and screen.fetcher is None)
    assert screen.catalog.calls == 2
    assert len(screen.cards) == 1


def test_import_dialog_cancel_does_not_start_an_operation(screen, monkeypatch):
    page = screen.ccm_page
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: ('', ''))
    page.import_area.click()
    assert page.job is None and not page.manager.list_packages()


def test_loaded_card_has_clear_primary_and_preserves_focus_on_resize(screen, qapp, tmp_path):
    page = screen.ccm_page
    item = page.manager.import_package(package(tmp_path))
    page.manager.activate(item['id'])
    page.reload()
    screen.tabs.setCurrentIndex(0)
    play = next(button for button in page.buttons if isinstance(button, QPushButton) and button.text() == 'Play')
    assert play.property('primary') and 'Play Example' == play.accessibleName()
    assert not page.remove_actions[0].isEnabled()
    play.setFocus()
    screen.resize(380, 600)
    qapp.processEvents()
    assert not sip.isdeleted(play) and play in page.buttons
    assert QApplication.focusWidget() is play
    assert page.scroll.horizontalScrollBar().maximum() == 0


def test_advanced_menu_is_keyboard_accessible_without_triggering_actions(screen):
    screen.tabs.setCurrentIndex(0)
    button = screen.ccm_page.more_button
    shown = []
    button.menu().aboutToShow.connect(lambda: shown.append(True))
    QTimer.singleShot(30, lambda: QTest.keyClick(button.menu(), Qt.Key.Key_Escape))
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert shown and not button.menu().isVisible()
    assert any('bank metadata' in action.text() for action in button.menu().actions())


@pytest.mark.parametrize('finish', ['cancel', 'close'])
def test_busy_operation_deduplicates_clicks_and_finishes_before_close(screen, qapp, finish):
    page = screen.ccm_page
    entered = Event()
    calls = []

    def operation(cancel, notify):
        calls.append('started')
        notify({'operation': 'Backing up', 'index': 1, 'count': 3, 'name': 'fixture.SC2Bank'})
        entered.set()
        cancel.wait(5)
        if cancel.is_set():
            raise Cancelled()
        return 'Done'

    page.run('Working…', operation)
    page.run('Duplicate', operation)
    spin(qapp, entered.is_set)
    assert not screen.refresh_btn.isEnabled()
    if finish == 'cancel':
        page.cancel_button.click()
        assert not page.cancel_button.isEnabled() and 'Stopping' in page.cancel_button.text()
        page.cancel_button.click()
    else:
        screen.close()
        assert screen.closing
    spin(qapp, lambda: not screen.jobs.busy() and page.job is None)
    assert calls == ['started'] and page.progress.isHidden()
    if finish == 'close':
        assert not screen.isVisible()
    else:
        assert 'Cancelled' in page.notice.text() and page.import_area.isEnabled()


def test_recovery_preserves_exact_error_and_offers_explicit_retry(screen, qapp, monkeypatch):
    screen.tabs.setCurrentIndex(0)
    page = screen.ccm_page
    page.manager.journal.parent.mkdir(parents=True, exist_ok=True)
    page.manager.journal.write_text('{}')
    page.finished(None, ValueError('A recovery backup is damaged; keep the files intact.'), None)
    assert 'backup is damaged' in page.notice.text()
    assert page.recovery_button.isVisible() and not page.import_area.isEnabled()
    recover = Mock(side_effect=ValueError('A recovery backup is still damaged.'))
    monkeypatch.setattr(page.manager, 'recover', recover)
    page.recovery_button.click()
    page.recovery_button.click()
    spin(qapp, lambda: page.job is None)
    assert recover.call_count == 1 and 'still damaged' in page.notice.text()
    assert page.manager.journal.exists() and page.recovery_button.isEnabled()
    monkeypatch.setattr(page.manager, 'recover', lambda: page.manager.journal.unlink())
    page.recovery_button.click()
    spin(qapp, lambda: page.job is None)
    assert page.recovery_button.isHidden() and page.import_area.isEnabled()
    assert 'Choose Play' in page.notice.text()


def test_account_chooser_does_not_offer_a_missing_backup_action(screen, tmp_path):
    profile = tmp_path / 'Accounts/example/player/Saves'
    profile.mkdir(parents=True)
    screen.settings.settings.setValue('campaign_backup_profile', str(profile))
    dialog = save_backups_ui.BackupDialog(screen.settings, choose_only=True)
    assert dialog.buttons['choose'].isEnabled()
    assert 'Back up now' not in dialog.notice.text()
    assert 'Saves and Banks' in dialog.notice.text()
    dialog.reject()


def test_play_loads_once_and_revert_restores_fixture_files(screen, qapp, tmp_path, monkeypatch):
    page = screen.ccm_page
    item = page.manager.import_package(package(tmp_path))
    page.reload()
    screen.tabs.setCurrentIndex(0)
    monkeypatch.setattr(ccm_ui.PlayDialog, 'exec', lambda _: ccm_ui.PlayDialog.DialogCode.Accepted)
    monkeypatch.setattr(screen.settings.backend, 'command', lambda *args: (['fixture-only'], {}))

    def started(slug, options, target):
        assert page.manager._state()['active'] == {'wol': item['id']}
        screen.launcher.processes[slug] = ('fixture', None)
        screen.launcher.changed.emit(slug, 'running', 'Fixture launch only')

    launch = Mock(side_effect=started)
    monkeypatch.setattr(screen.launcher, 'launch_target', launch)
    play = next(button for button in page.buttons if isinstance(button, QPushButton) and button.text() == 'Play')
    QTest.mouseClick(play, Qt.MouseButton.LeftButton)
    QTest.mouseClick(play, Qt.MouseButton.LeftButton)
    spin(qapp, lambda: page.job is None)
    assert launch.call_count == 1 and all(not button.isEnabled() for button in page.buttons)
    screen.launcher.processes.clear()
    screen.launcher.changed.emit('ccm-' + item['id'], 'finished', '')
    revert = next(button for button in page.buttons if isinstance(button, QPushButton)
                  and button.text() == 'Revert to base')
    QTest.mouseClick(revert, Qt.MouseButton.LeftButton)
    spin(qapp, lambda: page.job is None)
    assert not page.manager._state()['active']
    assert len(page.manager.list_packages()) == 1
    assert not page.manager.game_path('Maps/Campaign/First.SC2Map').exists()


def test_failed_import_can_be_retried_without_losing_the_error(screen, qapp, tmp_path):
    page = screen.ccm_page
    screen.tabs.setCurrentIndex(0)
    bad = tmp_path / 'bad.zip'
    bad.write_bytes(b'not a ZIP')
    page.import_path(str(bad))
    spin(qapp, lambda: page.job is None)
    assert 'zip' in page.notice.text().lower() and page.notice.isVisible()
    assert page.import_area.isEnabled() and not page.manager.list_packages()
    page.import_path(str(package(tmp_path)))
    spin(qapp, lambda: page.job is None)
    assert page.notice.text() == 'Campaign imported.'
    assert len(page.manager.list_packages()) == 1
