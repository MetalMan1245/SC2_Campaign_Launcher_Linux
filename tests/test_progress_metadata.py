import json

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMessageBox

from sc2_campaign_launcher_linux import ccm, save_backups
from sc2_campaign_launcher_linux.ccm import CampaignManager
from sc2_campaign_launcher_linux.files import atomic_json, read_json
from sc2_campaign_launcher_linux.progress_metadata import PROGRESS_FILE, bank_scope, campaign_owner, validate_progress
from sc2_campaign_launcher_linux.progress_metadata_ui import ProgressMetadataDialog
from sc2_campaign_launcher_linux.save_backups import SaveBackups
from sc2_campaign_launcher_linux.save_backups_ui import BackupDialog
from sc2_campaign_launcher_linux.sc2_campaign_launcher_linux import MainWindow
from sc2_campaign_launcher_linux.settings import AppSettings
from conftest import write
from test_ccm import package
from test_runtime import spin


@pytest.fixture
def manager(sc2_root, tmp_path, monkeypatch):
    monkeypatch.setattr(ccm, 'game_running', lambda: False)
    monkeypatch.setattr(save_backups, 'game_running', lambda: False)
    return CampaignManager(sc2_root, tmp_path / 'state')


def import_with_banks(manager, tmp_path, name='Example', banks=None):
    value = {'format': 1, 'banks': banks if banks is not None else ['Progress.SC2Bank']}
    return manager.import_package(package(tmp_path, name, extra={name + '/' + PROGRESS_FILE: json.dumps(value)}))


@pytest.mark.parametrize('value', [
    {'format': True, 'banks': []}, {'format': 2, 'banks': []}, {'format': 1, 'banks': '*.SC2Bank'},
    {'format': 1, 'banks': ['../outside.SC2Bank']}, {'format': 1, 'banks': ['C:/outside.SC2Bank']},
    {'format': 1, 'banks': ['Banks/Progress.SC2Bank']}, {'format': 1, 'banks': ['progress.txt']},
    {'format': 1, 'banks': ['Progress.SC2Bank', 'progress.sc2bank']},
    {'format': 1, 'banks': ['*.SC2Bank']}, {'format': 1, 'banks': [], 'typo': True},
])
def test_bank_declarations_reject_unsafe_or_ambiguous_paths(value):
    with pytest.raises(ValueError):
        validate_progress(value)


def test_import_keeps_declaration_and_does_not_install_it_as_game_content(manager, tmp_path):
    item = import_with_banks(manager, tmp_path, banks=['1-S2-1-1234567/Progress.SC2Bank', 'Local.SC2Bank'])
    assert item['progress']['banks'] == ['1-S2-1-1234567/Progress.SC2Bank', 'Local.SC2Bank']
    assert not any(PROGRESS_FILE in entry['destination'] for entry in item['files'])
    manager.activate(item['id'])
    assert not (manager.root / 'Maps/Campaign' / PROGRESS_FILE).exists()
    assert manager.list_packages()[0]['progress'] == item['progress']


def test_invalid_declaration_aborts_import(manager, tmp_path):
    source = package(tmp_path, extra={'Example/' + PROGRESS_FILE: '{"format":1,"banks":["../bad.SC2Bank"]}'})
    with pytest.raises(ValueError):
        manager.import_package(source)
    assert manager.list_packages() == []


def test_bank_conflicts_detect_other_campaigns_but_allow_versions_of_same_campaign(manager, tmp_path):
    first = import_with_banks(manager, tmp_path, 'First')
    assert manager.bank_conflicts(first['progress'], campaign_owner(first)) == []
    second = import_with_banks(manager, tmp_path, 'Second', banks=['progress.sc2bank'])
    assert manager.bank_conflicts(first['progress'], campaign_owner(first)) == ['Second: progress.sc2bank']
    assert manager.bank_conflicts(second['progress'], campaign_owner(second)) == ['First: progress.sc2bank']
    assert campaign_owner({**first, 'version': '2.0'}) == campaign_owner(first)


def test_scoped_backup_restores_only_declared_banks_and_keeps_saves(manager, tmp_path):
    item = import_with_banks(manager, tmp_path, banks=['Progress.SC2Bank', '1-S2-1-1234567/Published.SC2Bank'])
    profile = tmp_path / 'account/region/Saves'
    save = write(profile / 'LibertyCampaignSave.SC2Save', b'save')
    bank = write(profile.parent / 'Banks/Progress.SC2Bank', b'campaign bank')
    published = write(profile.parent / 'Banks/1-S2-1-1234567/Published.SC2Bank', b'published bank')
    other = write(profile.parent / 'Banks/OtherCampaign.SC2Bank', b'other bank')
    store = SaveBackups(profile, tmp_path / 'state')
    record = store.snapshot('Campaign banks', scope=bank_scope(item))
    assert set(record['files']) == {'Banks/Progress.SC2Bank', 'Banks/1-S2-1-1234567/Published.SC2Bank'}
    bank.write_bytes(b'later campaign bank')
    published.write_bytes(b'later published bank')
    save.write_bytes(b'later save')
    other.write_bytes(b'later other bank')
    before = store.restore(record['id'])
    assert bank.read_bytes() == b'campaign bank'
    assert published.read_bytes() == b'published bank'
    assert save.read_bytes() == b'later save'
    assert other.read_bytes() == b'later other bank'
    assert before['bank_scope'] == record['bank_scope']
    store.restore(before['id'])
    assert bank.read_bytes() == b'later campaign bank'


def test_bank_manifest_cannot_smuggle_in_account_save(manager, tmp_path):
    item = import_with_banks(manager, tmp_path)
    profile = tmp_path / 'account/region/Saves'
    write(profile / 'LibertyCampaignSave.SC2Save', b'save')
    store = SaveBackups(profile, tmp_path / 'state')
    record = store.snapshot('Campaign banks', scope=bank_scope(item))
    record['files']['Saves/LibertyCampaignSave.SC2Save'] = '0' * 64
    atomic_json(store.directory / record['id'] / 'backup.json', record)
    with pytest.raises(ValueError, match='outside its declared bank list'):
        store.restore(record['id'])


def test_missing_declared_banks_do_not_pick_up_similar_names(manager, tmp_path):
    item = import_with_banks(manager, tmp_path)
    profile = tmp_path / 'account/region/Saves'
    profile.mkdir(parents=True)
    write(profile.parent / 'Banks/ProgressBackup.SC2Bank', b'unrelated')
    store = SaveBackups(profile, tmp_path / 'state')
    assert store.snapshot('No bank yet', scope=bank_scope(item))['files'] == {}


def test_metadata_editor_writes_paths_only_and_loads_existing_file(qapp, tmp_path):
    write(tmp_path / 'metadata.txt', b'title=Campaign\ncampaign=wol')
    editor = ProgressMetadataDialog()
    editor.open_folder(tmp_path)
    editor.paths.setPlainText('Progress.SC2Bank\n1-S2-1-1234567/Extra.SC2Bank')
    editor.save()
    assert read_json(tmp_path / PROGRESS_FILE) == {
        'format': 1, 'banks': ['1-S2-1-1234567/Extra.SC2Bank', 'Progress.SC2Bank']}
    assert 'Only paths' in editor.notice.text()
    second = ProgressMetadataDialog()
    second.open_folder(tmp_path)
    assert 'Extra.SC2Bank' in second.paths.toPlainText()
    editor.close()
    second.close()


def test_metadata_editor_refuses_live_installation(qapp, sc2_root):
    write(sc2_root / 'Maps/Campaign/metadata.txt', b'title=Loaded campaign')
    editor = ProgressMetadataDialog(game_root=sc2_root)
    with pytest.raises(ValueError, match='outside the SC2 installation'):
        editor.open_folder(sc2_root / 'Maps/Campaign')
    assert not (sc2_root / 'Maps/Campaign' / PROGRESS_FILE).exists()


def test_bank_dialog_filters_scopes_and_blocks_conflicting_restore(qapp, manager, tmp_path, monkeypatch):
    item = import_with_banks(manager, tmp_path, 'First')
    profile = tmp_path / 'account/region/Saves'
    write(profile / 'LibertyCampaignSave.SC2Save', b'save')
    write(profile.parent / 'Banks/Progress.SC2Bank', b'bank')
    store = SaveBackups(profile, tmp_path / 'state')
    store.snapshot('Account')
    store.snapshot('First banks', scope=bank_scope(item))
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    settings = AppSettings(QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    monkeypatch.setattr('sc2_campaign_launcher_linux.save_backups_ui.save_profiles', lambda *_: [profile])
    dialog = BackupDialog(settings, manager=manager)
    assert dialog.entries.count() == 2
    dialog.scope.setCurrentIndex(1)
    assert dialog.entries.count() == 1
    dialog.entries.setCurrentRow(0)
    assert dialog.buttons['restore'].isEnabled()
    import_with_banks(manager, tmp_path, 'Second')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: pytest.fail('Conflicting restore must stop before confirmation'))
    dialog.select('restore')
    assert 'Another campaign' in dialog.notice.text()
    assert dialog.result() != dialog.DialogCode.Accepted
    dialog.refresh()
    assert not dialog.buttons['backup'].isEnabled()


def test_campaign_switch_creates_account_and_declared_bank_backups(qapp, manager, sc2_root, tmp_path, monkeypatch):
    item = import_with_banks(manager, tmp_path)
    profile = tmp_path / 'account/region/Saves'
    write(profile / 'LibertyCampaignSave.SC2Save', b'save')
    write(profile.parent / 'Banks/Progress.SC2Bank', b'bank')
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    monkeypatch.setattr('sc2_campaign_launcher_linux.ccm_ui.save_profiles', lambda *_: [profile])
    settings = AppSettings(QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    settings.settings.setValue('sc2_root', str(sc2_root))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    window = MainWindow(settings, autoload=False, ccm_only=True)
    page = window.ccm_page
    page.toggle({**item, 'active': False})
    spin(qapp, lambda: not window.jobs.busy())
    records = SaveBackups(profile, tmp_path / 'state').list_backups()
    assert len(records) == 2
    assert len(next(r for r in records if 'bank_scope' in r)['files']) == 1
    assert len(next(r for r in records if 'bank_scope' not in r)['files']) == 2
    assert page.manager._state()['active'] == {'wol': item['id']}
    window.close()
    spin(qapp, lambda: not window.jobs.busy())
