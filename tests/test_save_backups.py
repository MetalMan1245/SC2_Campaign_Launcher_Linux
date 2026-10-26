import os
import subprocess
import sys
from threading import Event

import pytest
from PyQt6.QtCore import QSettings

from sc2_campaign_launcher_linux import save_backups
from sc2_campaign_launcher_linux.files import Cancelled, atomic_json
from sc2_campaign_launcher_linux.save_backups import SaveBackups
from sc2_campaign_launcher_linux.save_backups_ui import BackupDialog
from sc2_campaign_launcher_linux.settings import AppSettings
from sc2_campaign_launcher_linux.sc2_campaign_launcher_linux import MainWindow
from conftest import write
from test_ccm import package
from test_runtime import spin


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(save_backups, 'game_running', lambda: False)
    profile = tmp_path / 'account/region/Saves'
    write(profile / 'Campaign/mission.SC2Save', b'original save')
    write(profile.parent / 'Banks/1-S2-1-42/progress.SC2Bank', b'original bank')
    return SaveBackups(profile, tmp_path / 'state')


def test_restore_backs_up_current_progress_and_preserves_new_files(store):
    first = store.snapshot('Before loading a campaign')
    save = store.profile / 'Campaign/mission.SC2Save'
    bank = store.root / 'Banks/1-S2-1-42/progress.SC2Bank'
    save.write_bytes(b'later save')
    bank.write_bytes(b'later bank')
    extra = write(store.profile / 'new.SC2Save', b'new save')
    before = store.restore(first['id'])
    assert save.read_bytes() == b'original save'
    assert bank.read_bytes() == b'original bank'
    assert extra.read_bytes() == b'new save'
    assert (store.directory / before['id'] / 'payload/Saves/Campaign/mission.SC2Save').read_bytes() == b'later save'
    assert not store.journal.exists()
    store.restore(before['id'])
    assert save.read_bytes() == b'later save'
    assert bank.read_bytes() == b'later bank'


def test_other_accounts_are_never_restored(store, tmp_path):
    first = store.snapshot('Account one')
    other = tmp_path / 'other/region/Saves'
    path = write(other / 'Campaign/mission.SC2Save', b'other account')
    second = SaveBackups(other, tmp_path / 'state')
    with pytest.raises(ValueError, match='missing or damaged'):
        second.restore(first['id'])
    assert path.read_bytes() == b'other account'


def test_damaged_payload_stops_restore_before_any_write(store):
    first = store.snapshot('Original')
    path = store.profile / 'Campaign/mission.SC2Save'
    path.write_bytes(b'current save')
    (store.directory / first['id'] / 'payload/Saves/Campaign/mission.SC2Save').write_bytes(b'bad backup')
    with pytest.raises(ValueError, match='missing or damaged'):
        store.restore(first['id'])
    assert path.read_bytes() == b'current save'
    assert not store.journal.exists()
    assert len(store.list_backups()) == 1


def test_cancelled_snapshot_is_not_listed_and_does_not_change_sources(store):
    cancel = Event()
    cancel.set()
    with pytest.raises(Cancelled):
        store.snapshot('Cancelled', cancel)
    assert store.list_backups() == []
    assert (store.profile / 'Campaign/mission.SC2Save').read_bytes() == b'original save'


def test_snapshot_rejects_files_that_change_while_being_copied(store, monkeypatch):
    original = save_backups.copy_file

    def change(source, target, *args, **kwargs):
        original(source, target, *args, **kwargs)
        source.write_bytes(b'changed externally')

    monkeypatch.setattr(save_backups, 'copy_file', change)
    with pytest.raises(ValueError, match='changed while being copied'):
        store.snapshot('Unstable')
    assert store.list_backups() == []


def test_unreadable_source_folder_cannot_create_incomplete_backup(store, monkeypatch):
    def unreadable(root, **kwargs):
        kwargs['onerror'](PermissionError('Cannot read this folder'))
        return iter(())

    monkeypatch.setattr('sc2_campaign_launcher_linux.ccm.os.walk', unreadable)
    with pytest.raises(PermissionError, match='Cannot read'):
        store.snapshot('Incomplete')
    assert store.list_backups() == []


def test_cancelled_restore_rolls_back_all_written_files(store, monkeypatch):
    first = store.snapshot('Old progress')
    save = store.profile / 'Campaign/mission.SC2Save'
    save.write_bytes(b'current progress')
    original = save_backups.copy_file
    cancel = Event()

    def stop_after_first(source, target, *args, **kwargs):
        original(source, target, *args, **kwargs)
        if kwargs.get('prefix', '').startswith('.save-restore-'):
            cancel.set()

    monkeypatch.setattr(save_backups, 'copy_file', stop_after_first)
    with pytest.raises(Cancelled):
        store.restore(first['id'], cancel)
    assert save.read_bytes() == b'current progress'
    assert not store.journal.exists()


def interrupted_restore(store):
    old = store.snapshot('Old progress')
    save = store.profile / 'Campaign/mission.SC2Save'
    save.write_bytes(b'current progress')
    before = store.snapshot('Before restoring')
    prefix = '.save-restore-' + '0' * 32 + '-'
    atomic_json(store.journal, {'backup': old['id'], 'before': before['id'],
                                'targets': {name: name for name in old['files']}, 'temporary_prefix': prefix})
    save.write_bytes(b'original save')
    temporary = write(save.parent / (prefix + 'unfinished'), b'incomplete')
    return save, temporary, before


def test_recovery_restores_previous_progress_and_cleans_its_temporary_copy(store):
    save, temporary, _ = interrupted_restore(store)
    SaveBackups(store.profile, store.directory.parent.parent).recover()
    assert save.read_bytes() == b'current progress'
    assert not temporary.exists()
    assert not store.journal.exists()


def test_recovery_preserves_external_edits_and_missing_backups(store):
    save, _, before = interrupted_restore(store)
    save.write_bytes(b'an external edit')
    with pytest.raises(ValueError, match='changed after'):
        store.recover()
    assert save.read_bytes() == b'an external edit'
    assert store.journal.exists()
    save.write_bytes(b'original save')
    (store.directory / before['id'] / 'payload/Saves/Campaign/mission.SC2Save').unlink()
    with pytest.raises(ValueError, match='missing or damaged'):
        store.recover()
    assert save.read_bytes() == b'original save'


def test_backup_and_restore_refuse_running_game(store, monkeypatch):
    record = store.snapshot('Original')
    monkeypatch.setattr(save_backups, 'game_running', lambda: True)
    for action in (lambda: store.snapshot('Blocked'), lambda: store.restore(record['id']), store.recover):
        with pytest.raises(ValueError, match='Close StarCraft II'):
            action()


def test_manifest_cannot_restore_outside_account(store, tmp_path):
    record = store.snapshot('Original')
    record['files']['../../outside.txt'] = '0' * 64
    atomic_json(store.directory / record['id'] / 'backup.json', record)
    outside = write(tmp_path / 'outside.txt', b'keep')
    with pytest.raises(ValueError, match='Unsafe relative path'):
        store.restore(record['id'])
    assert outside.read_bytes() == b'keep'


def test_linked_save_folder_is_rejected(store, tmp_path):
    link = store.profile / 'linked'
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip('Symbolic links require privileges on this system')
    with pytest.raises(ValueError, match='Links are not supported'):
        store.snapshot('Unsafe')


@pytest.mark.parametrize('phase', ['copy', 'replace'])
def test_abruptly_stopped_restore_recovers_on_reopening(store, phase):
    old = store.snapshot('Original')
    save = store.profile / 'Campaign/mission.SC2Save'
    save.write_bytes(b'current progress')
    script = '''import os, sys
from pathlib import Path
import sc2_campaign_launcher_linux.save_backups as backups
backups.game_running = lambda: False
original = backups.copy_file
def stop(source, target, *args, **kwargs):
    original(source, target, *args, **kwargs)
    if kwargs.get('prefix', '').startswith('.save-restore-'):
        os._exit(17)
if sys.argv[4] == 'copy':
    backups.copy_file = stop
else:
    replace = backups.SaveBackups._restore_file
    def stop_after_replace(self, *args):
        replace(self, *args)
        os._exit(17)
    backups.SaveBackups._restore_file = stop_after_replace
store = backups.SaveBackups(Path(sys.argv[1]), Path(sys.argv[2]))
store.restore(sys.argv[3])
'''
    result = subprocess.run([sys.executable, '-c', script, str(store.profile),
                             str(store.directory.parent.parent), old['id'], phase],
                            env=dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path)),
                            capture_output=True, timeout=10)
    assert result.returncode == 17, result.stderr.decode(errors='replace')
    assert store.journal.exists()
    store.recover()
    assert save.read_bytes() == b'current progress'
    assert not store.journal.exists()


@pytest.fixture
def ccm_page(qapp, sc2_root, tmp_path, monkeypatch, store):
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    monkeypatch.setattr('sc2_campaign_launcher_linux.ccm.game_running', lambda: False)
    monkeypatch.setattr('sc2_campaign_launcher_linux.ccm_ui.save_profiles', lambda *_: [store.profile])
    settings = AppSettings(QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    settings.settings.setValue('sc2_root', str(sc2_root))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    window = MainWindow(settings, autoload=False, ccm_only=True)
    yield window.ccm_page
    window.close()
    spin(qapp, lambda: not window.jobs.busy())


def test_switch_backs_up_progress_before_loading_and_reverting(ccm_page, store, tmp_path, qapp):
    item = ccm_page.manager.import_package(package(tmp_path))
    ccm_page.toggle({**item, 'active': False})
    spin(qapp, lambda: not ccm_page.jobs.busy())
    assert ccm_page.manager._state()['active'] == {'wol': item['id']}
    assert len(store.list_backups()) == 1
    ccm_page.toggle({**item, 'active': True})
    spin(qapp, lambda: not ccm_page.jobs.busy())
    assert not ccm_page.manager._state()['active']
    assert len(store.list_backups()) == 2


def test_backup_failure_keeps_campaign_files_and_blocks_launch(ccm_page, store, tmp_path, monkeypatch, qapp):
    item = ccm_page.manager.import_package(package(tmp_path))
    monkeypatch.setattr(SaveBackups, 'snapshot', lambda *a, **kw: (_ for _ in ()).throw(OSError('Disk full')))
    ccm_page.toggle({**item, 'active': False})
    spin(qapp, lambda: not ccm_page.jobs.busy())
    assert not ccm_page.manager._state()['active']
    assert 'Disk full' in ccm_page.notice.text()


def test_backup_dialog_requires_explicit_selection_for_multiple_accounts(qapp, store, tmp_path, monkeypatch):
    other = tmp_path / 'other/region/Saves'
    other.mkdir(parents=True)
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    settings = AppSettings(QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    monkeypatch.setattr('sc2_campaign_launcher_linux.save_backups_ui.save_profiles', lambda *_: [store.profile, other])
    dialog = BackupDialog(settings, choose_only=True)
    assert dialog.profile.currentData() is None
    assert not dialog.buttons['choose'].isEnabled()
    dialog.profile.setCurrentIndex(1)
    assert dialog.buttons['choose'].isEnabled()
    dialog.select('choose')
    assert settings.settings.value('campaign_backup_profile') == str(store.profile)


def test_pending_restore_blocks_campaign_switch(ccm_page, store, tmp_path, qapp):
    item = ccm_page.manager.import_package(package(tmp_path))
    interrupted_restore(store)
    ccm_page.toggle({**item, 'active': False})
    spin(qapp, lambda: not ccm_page.jobs.busy())
    assert not ccm_page.manager._state()['active']
    assert 'needs recovery' in ccm_page.notice.text()
