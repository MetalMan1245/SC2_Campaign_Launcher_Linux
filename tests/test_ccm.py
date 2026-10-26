import zipfile
from threading import Event

import pytest

from sc2_campaign_launcher_linux import ccm
from sc2_campaign_launcher_linux.ccm import CampaignManager
from sc2_campaign_launcher_linux.files import Cancelled
from conftest import write


@pytest.fixture
def interrupted_switch(manager, tmp_path, monkeypatch):
    write(manager.root / 'Maps/Campaign/First.SC2Map', b'original map')
    write(manager.root / 'Mods/Shared.SC2Mod', b'original mod')
    item = manager.import_package(package(tmp_path))
    copy = ccm.copy_file

    def crash(source, target, token=None, **kwargs):
        copy(source, target, token, **kwargs)
        if target.is_relative_to(manager.root):
            raise OSError('simulated process interruption')

    with monkeypatch.context() as patch:
        patch.setattr(ccm, 'copy_file', crash)
        patch.setattr(manager, 'recover', lambda: None)
        with pytest.raises(OSError, match='simulated'):
            manager.activate(item['id'])
    assert manager.journal.exists()
    return manager


def game_files(manager):
    return {p.relative_to(manager.root).as_posix(): p.read_bytes()
            for p in manager.root.rglob('*') if p.is_file()}


def test_recovery_preserves_external_edits(interrupted_switch):
    manager = interrupted_switch
    write(manager.root / 'Maps/Campaign/First.SC2Map', b'user edit after crash')
    before = game_files(manager)
    with pytest.raises(ValueError, match='changed after'):
        manager.recover()
    assert game_files(manager) == before
    assert manager.journal.exists()


def test_recovery_verifies_every_backup_before_changing_files(interrupted_switch):
    from sc2_campaign_launcher_linux.files import read_json
    manager = interrupted_switch
    journal = read_json(manager.journal)
    backup = next(name for name in journal['before'].values() if name)
    (manager.directory / backup).write_bytes(b'damaged backup')
    before = game_files(manager)
    with pytest.raises(ValueError, match='backup.*damaged'):
        manager.recover()
    assert game_files(manager) == before
    assert manager.journal.exists()


def test_legacy_recovery_requires_preservation_before_manual_review(interrupted_switch):
    from sc2_campaign_launcher_linux.files import atomic_json, read_json
    manager = interrupted_switch
    journal = read_json(manager.journal)
    journal.pop('before_hashes', None)
    journal.pop('after_hashes', None)
    atomic_json(manager.journal, journal)
    before = game_files(manager)
    with pytest.raises(ValueError, match='integrity'):
        manager.recover()
    assert game_files(manager) == before
    assert manager.journal.exists()


def test_corrupt_original_backup_stops_revert(manager, tmp_path):
    write(manager.root / 'Maps/Campaign/First.SC2Map', b'original map')
    item = manager.import_package(package(tmp_path))
    manager.activate(item['id'])
    original = manager._state()['originals']['maps/campaign/first.sc2map']
    (manager.directory / original).write_bytes(b'damaged original')
    before = game_files(manager)
    with pytest.raises(ValueError, match='backup.*damaged'):
        manager.unload('wol')
    assert game_files(manager) == before
    assert manager._state()['active'] == {'wol': item['id']}
    assert not manager.journal.exists()


def test_backup_copy_rejects_changed_bytes_without_replacing_destination(tmp_path):
    import hashlib
    source = write(tmp_path / 'source', b'changed bytes')
    target = write(tmp_path / 'target', b'keep this')
    with pytest.raises(ValueError, match='damaged or changed'):
        ccm.copy_file(source, target, expected_sha=hashlib.sha256(b'expected bytes').hexdigest())
    assert target.read_bytes() == b'keep this'
    assert not list(tmp_path.glob('.ccm-*'))


def test_legacy_completed_state_adopts_original_backup_hashes(manager, tmp_path):
    from sc2_campaign_launcher_linux.files import atomic_json
    original = write(manager.root / 'Maps/Campaign/First.SC2Map', b'original map')
    first = manager.import_package(package(tmp_path, 'One'))
    second = manager.import_package(package(tmp_path, 'Two'))
    manager.activate(first['id'])
    state = manager._state()
    state.pop('original_hashes')
    atomic_json(manager.state_path, state)
    manager.activate(second['id'])
    assert manager._state()['original_hashes']['maps/campaign/first.sc2map']
    manager.unload('wol')
    assert original.read_bytes() == b'original map'


@pytest.mark.parametrize('unpacked', [False, True])
@pytest.mark.parametrize('checkpoint', ['remove', 'copy', 'state', 'rollback'])
def test_recovery_survives_process_death_at_switch_boundaries(manager, tmp_path, unpacked, checkpoint):
    import subprocess
    import sys
    # Exercise both packed-to-unpacked and unpacked-to-packed transitions.
    original_path = 'Maps/Campaign/First.SC2Map' + ('' if unpacked else '/MapInfo')
    write(manager.root / original_path, b'original map')
    write(manager.root / 'Maps/Campaign/Old.SC2Map', b'old loose map')
    write(manager.root / 'Mods/Shared.SC2Mod', b'original mod')
    original_files = game_files(manager)
    if unpacked:
        source = tmp_path / 'unpacked'
        write(source / 'metadata.txt', b'title=Unpacked\ncampaign=wol')
        write(source / 'First.SC2Map/MapInfo', b'custom map')
        write(source / 'Shared.SC2Mod/DocumentInfo', b'custom mod')
    else:
        source = package(tmp_path)
    item = manager.import_package(source)
    # Include the immutable imported package in the expected surviving files.
    expected = {**game_files(manager), **original_files}
    code = """
import os, sys
from pathlib import Path
from sc2_campaign_launcher_linux import ccm
ccm.game_running = lambda: False
manager = ccm.CampaignManager(Path(sys.argv[1]), Path(sys.argv[2]))
checkpoint = sys.argv[4]
replace, unlink = os.replace, Path.unlink
def stop_replace(source, target):
    result = replace(source, target)
    if ((checkpoint in ('copy', 'rollback') and Path(target).is_relative_to(manager.root))
            or (checkpoint == 'state' and Path(target) == manager.state_path)):
        os._exit(29)
    return result
def stop_unlink(path, *args, **kwargs):
    result = unlink(path, *args, **kwargs)
    if checkpoint == 'remove' and path.is_relative_to(manager.root) and not path.name.startswith('.ccm-'):
        os._exit(29)
    return result
os.replace, Path.unlink = stop_replace, stop_unlink
if checkpoint == 'rollback':
    manager.recover()
else:
    manager.activate(sys.argv[3])
"""
    args = [sys.executable, '-c', code, str(manager.root), str(tmp_path / 'ccm-state'), item['id']]
    # First leave a pending switch, then terminate its rollback as well.
    first_checkpoint = 'copy' if checkpoint == 'rollback' else checkpoint
    assert subprocess.run([*args, first_checkpoint], timeout=30).returncode == 29
    if checkpoint == 'rollback':
        assert subprocess.run([*args, checkpoint], timeout=30).returncode == 29
    assert manager.journal.exists()
    manager.recover()
    assert game_files(manager) == expected
    assert manager._state()['active'] == {}
    assert not manager.journal.exists()
    assert not list(manager.root.rglob('.ccm-*'))


def package(tmp_path, name='Example', slot='wol', mod=b'custom mod', map_data=b'custom map', extra=None):
    path = tmp_path / (name + '.zip')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(f'{name}/metadata.txt', f'title={name}\nauthor=Tester\ncampaign={slot}\nversion=1\ndesc=Test')
        archive.writestr(f'{name}/First.SC2Map', map_data)
        archive.writestr('Dependencies/Shared.SC2Mod', mod)
        for key, value in (extra or {}).items():
            archive.writestr(key, value)
    return path


@pytest.fixture
def manager(sc2_root, tmp_path, monkeypatch):
    monkeypatch.setattr(ccm, 'game_running', lambda: False)
    return CampaignManager(sc2_root, tmp_path / 'ccm-state')


def test_switch_restores_original_maps_and_mods(manager, sc2_root, tmp_path):
    original = write(sc2_root / 'Maps/Campaign/Original.SC2Map', b'original map')
    shared = write(sc2_root / 'Mods/Shared.SC2Mod', b'original mod')
    one = manager.import_package(package(tmp_path, 'One'))
    two = manager.import_package(package(tmp_path, 'Two', map_data=b'another map'))
    manager.activate(one['id'])
    assert not original.exists()
    assert shared.read_bytes() == b'custom mod'
    manager.activate(two['id'])
    assert manager.game_path('Maps/Campaign/First.SC2Map').read_bytes() == b'another map'
    manager.unload('wol')
    assert original.read_bytes() == b'original map'
    assert shared.read_bytes() == b'original mod'
    assert not manager.game_path('Maps/Campaign/First.SC2Map').exists()


def test_one_active_per_slot_and_shared_mod_survives_other_slot(manager, sc2_root, tmp_path):
    one = manager.import_package(package(tmp_path, 'One'))
    two = manager.import_package(package(tmp_path, 'Two', slot='hots'))
    manager.activate(one['id'])
    manager.activate(two['id'])
    manager.unload('wol')
    assert manager.game_path('Mods/Shared.SC2Mod').read_bytes() == b'custom mod'
    assert manager.game_path('Maps/Campaign/swarm/First.SC2Map').exists()
    manager.unload('hots')
    assert not manager.game_path('Mods/Shared.SC2Mod').exists()


def test_conflicting_mods_block_activation_without_touching_files(manager, tmp_path):
    one = manager.import_package(package(tmp_path, 'One'))
    two = manager.import_package(package(tmp_path, 'Two', slot='hots', mod=b'incompatible'))
    manager.activate(one['id'])
    with pytest.raises(ValueError, match='different versions'):
        manager.activate(two['id'])
    assert manager.game_path('Mods/Shared.SC2Mod').read_bytes() == b'custom mod'
    assert manager._state()['active'] == {'wol': one['id']}


def test_external_edits_are_preserved(manager, tmp_path):
    item = manager.import_package(package(tmp_path))
    manager.activate(item['id'])
    path = manager.game_path('Maps/Campaign/First.SC2Map')
    path.write_bytes(b'user edit')
    with pytest.raises(ValueError, match='changed outside'):
        manager.unload('wol')
    assert path.read_bytes() == b'user edit'


def test_cancel_rolls_back_the_whole_switch(manager, sc2_root, tmp_path, monkeypatch):
    original = write(sc2_root / 'Maps/Campaign/Original.SC2Map', b'original')
    item = manager.import_package(package(tmp_path))
    cancel = Event()
    copy = ccm.copy_file

    def interrupted(source, target, token=None, **kwargs):
        copy(source, target, token, **kwargs)
        if token is cancel and target.is_relative_to(sc2_root):
            cancel.set()

    monkeypatch.setattr(ccm, 'copy_file', interrupted)
    with pytest.raises(Cancelled):
        manager.activate(item['id'], cancel)
    assert original.read_bytes() == b'original'
    assert manager._state()['active'] == {}
    assert not manager.journal.exists()


def test_restart_recovers_interrupted_switch(manager, sc2_root, tmp_path, monkeypatch):
    original = write(sc2_root / 'Maps/Campaign/Original.SC2Map', b'original')
    item = manager.import_package(package(tmp_path))
    copy = ccm.copy_file

    def crash(source, target, token=None, **kwargs):
        copy(source, target, token, **kwargs)
        if target.is_relative_to(sc2_root):
            raise OSError('disk disconnected')

    monkeypatch.setattr(ccm, 'copy_file', crash)
    monkeypatch.setattr(manager, 'recover', lambda: None)
    with pytest.raises(OSError):
        manager.activate(item['id'])
    assert manager.journal.exists()
    monkeypatch.setattr(ccm, 'copy_file', copy)
    recovered = CampaignManager(sc2_root, tmp_path / 'ccm-state')
    recovered.recover()
    assert original.read_bytes() == b'original'
    assert recovered._state()['active'] == {}


@pytest.mark.parametrize('entry', ['../outside', '/absolute', 'C:/outside', 'Example/../outside',
                                 'Example/CON', 'Example/First.SC2Map'])
def test_bad_zip_entries_do_not_install(manager, tmp_path, entry):
    source = package(tmp_path, extra={entry: b'bad'})
    with pytest.raises((ValueError, FileExistsError)):
        manager.import_package(source)
    assert manager.list_packages() == []


def test_unpacked_map_and_mod_directories(manager, tmp_path):
    source = tmp_path / 'source'
    write(source / 'metadata.txt', b'title=Directory mod\ncampaign=hots')
    write(source / 'First.SC2Map/MapInfo', b'map info')
    write(source / 'Dependencies/Shared.SC2Mod/DocumentInfo', b'mod info')
    item = manager.import_package(source)
    manager.activate(item['id'])
    assert manager.game_path('Mods/Shared.SC2Mod/DocumentInfo').read_bytes() == b'mod info'
    assert manager.game_path('Maps/Campaign/swarm/First.SC2Map/MapInfo').read_bytes() == b'map info'
    manager.unload('hots')


def test_imported_package_is_verified_before_activation(manager, tmp_path):
    item = manager.import_package(package(tmp_path))
    file = item['files'][0]
    (manager.packages / item['id'] / 'payload' / file['source']).write_bytes(b'edited')
    with pytest.raises(ValueError, match='changed'):
        manager.activate(item['id'])


def test_remove_requires_unload(manager, tmp_path):
    item = manager.import_package(package(tmp_path))
    manager.activate(item['id'])
    with pytest.raises(ValueError, match='Revert to base'):
        manager.remove(item['id'])
    manager.unload('wol')
    manager.remove(item['id'])
    assert manager.list_packages() == []


def test_metadata_keeps_equals_in_description(tmp_path):
    path = write(tmp_path / 'metadata.txt', b'title=Example\ncampaign=Nova Covert Ops\ndesc=A=B\n')
    assert ccm.metadata(path)['description'] == 'A=B'


@pytest.mark.parametrize('source_kind', ['folder', 'zip'])
def test_imported_update_dates_survive_reload_and_sort(manager, tmp_path, source_kind):
    from sc2_campaign_launcher_linux.catalog import sort_campaigns
    from sc2_campaign_launcher_linux.files import atomic_json, read_json

    dates = {'Oldest': '1969-12-31', 'Older': '2025-01-01',
             'A': '2026-10-25T01:00:00+01:00', 'B': '2026-10-25T00:00:00Z',
             'Unknown': '', 'Legacy': ''}
    for name, date in dates.items():
        source = tmp_path / name
        write(source / 'metadata.txt',
              f'title={name}\ncampaign=wol\nupdated_at= {date} \n'.encode())
        write(source / 'First.SC2Map', b'map')
        if source_kind == 'zip':
            archive_path = tmp_path / (name + '.zip')
            with zipfile.ZipFile(archive_path, 'w') as archive:
                for path in source.iterdir():
                    archive.write(path, f'{name}/{path.name}')
            source = archive_path
        item = manager.import_package(source)
        assert item['updated_at'] == date
        if name == 'Legacy':
            record = manager.packages / item['id'] / 'package.json'
            saved = read_json(record)
            saved.pop('updated_at')
            atomic_json(record, saved)

    reopened = CampaignManager(manager.root, tmp_path / 'ccm-state')
    cards = reopened.cards()
    assert {c['name']: c['updated_at'] for c in cards} == dates
    assert [c['name'] for c in sort_campaigns(cards, 'updated')] == [
        'A', 'B', 'Older', 'Oldest', 'Legacy', 'Unknown']


@pytest.mark.parametrize('date', ['yesterday', '2026-02-30', '2026-10-25T12:30:00'])
def test_invalid_author_update_date_stops_import(manager, tmp_path, date):
    source = tmp_path / 'source'
    write(source / 'metadata.txt', f'title=Example\ncampaign=wol\nupdated_at={date}\n'.encode())
    write(source / 'First.SC2Map', b'map')
    with pytest.raises(ValueError, match='updated_at must be'):
        manager.import_package(source)
    assert manager.list_packages() == []
    assert not manager.journal.exists()


def test_wol_package_cannot_modify_other_slot(manager, tmp_path):
    with pytest.raises(ValueError, match='another campaign slot'):
        manager.import_package(package(tmp_path, extra={'Example/swarm/First.SC2Map': b'bad'}))



def test_switch_between_directory_and_archive_maps(manager, sc2_root, tmp_path):
    original = write(sc2_root / 'Maps/Campaign/First.SC2Map/MapInfo', b'original data')
    item = manager.import_package(package(tmp_path))
    manager.activate(item['id'])
    assert manager.game_path('Maps/Campaign/First.SC2Map').read_bytes() == b'custom map'
    manager.unload('wol')
    assert original.read_bytes() == b'original data'


def test_unpacked_replacement_restores_original_archive(manager, sc2_root, tmp_path):
    original = write(sc2_root / 'Maps/Campaign/First.SC2Map', b'packed original')
    source = tmp_path / 'unpacked'
    write(source / 'metadata.txt', b'title=Unpacked\ncampaign=wol')
    write(source / 'First.SC2Map/MapInfo', b'custom data')
    item = manager.import_package(source)
    manager.activate(item['id'])
    manager.unload('wol')
    assert original.read_bytes() == b'packed original'


def test_new_files_block_switching_instead_of_being_lost(manager, tmp_path):
    item = manager.import_package(package(tmp_path))
    manager.activate(item['id'])
    extra = write(manager.root / 'Maps/Campaign/New.SC2Map', b'user file')
    with pytest.raises(ValueError, match='added outside'):
        manager.unload('wol')
    assert extra.read_bytes() == b'user file'


def test_running_game_blocks_file_switches(manager, tmp_path, monkeypatch):
    item = manager.import_package(package(tmp_path))
    monkeypatch.setattr(ccm, 'game_running', lambda: True)
    with pytest.raises(ValueError, match='Close StarCraft II'):
        manager.activate(item['id'])


def test_second_manager_cannot_modify_library_during_operation(manager, tmp_path):
    other = CampaignManager(manager.root, tmp_path / 'ccm-state')
    archive = package(tmp_path)
    with manager.lock, pytest.raises(ValueError, match='Another launcher'):
        other.import_package(archive)
    assert other.import_package(archive)['name'] == 'Example'


def test_base_install_and_unload_custom_restore_extracted_maps(manager, tmp_path, monkeypatch):
    from sc2_campaign_launcher_linux import casc

    def extract(root, slot, output, cancel, progress):
        write(output / 'metadata.txt', b'title=Base\nauthor=Blizzard\ncampaign=wol\nversion=game')
        write(output / 'Original.SC2Map', b'base maps')

    monkeypatch.setattr(casc, 'extract_campaign', extract)
    manager.install_base('wol')
    custom = manager.import_package(package(tmp_path))
    manager.activate(custom['id'])
    assert not manager.game_path('Maps/Campaign/Original.SC2Map').exists()
    manager.unload('wol')
    assert manager.game_path('Maps/Campaign/Original.SC2Map').read_bytes() == b'base maps'
    assert not manager.game_path('Maps/Campaign/First.SC2Map').exists()


def test_catalog_files_cannot_replace_or_remove_active_ccm_dependency(manager, tmp_path):
    item = manager.import_package(package(tmp_path))
    manager.activate(item['id'])
    shared = manager.game_path('Mods/Shared.SC2Mod')
    with pytest.raises(ValueError, match='Revert that campaign'):
        manager.check_campaign_files([shared])
    manager.check_campaign_files([manager.game_path('Mods/Unrelated.SC2Mod')])
    manager.unload('wol')
    manager.check_campaign_files([shared])


def test_game_patch_refreshes_base_maps_and_preserves_original_backup(manager, tmp_path, monkeypatch):
    from sc2_campaign_launcher_linux import casc
    build_file = write(manager.root / '.build.info', b'build one')
    original = write(manager.root / 'Maps/Campaign/Original.SC2Map', b'user original')

    def extract(root, slot, output, cancel, progress):
        write(output / 'metadata.txt', b'title=Base\nauthor=Blizzard\ncampaign=wol\nversion=game')
        write(output / 'Original.SC2Map', build_file.read_bytes())

    monkeypatch.setattr(casc, 'extract_campaign', extract)
    manager.install_base('wol')
    before = manager.base_id('wol')
    manager.activate(manager.import_package(package(tmp_path))['id'])
    build_file.write_bytes(b'build two')
    assert manager.base_statuses()['wol']['base_state'] == 'stale'
    manager.unload('wol')
    assert manager.base_id('wol') != before
    assert original.read_bytes() == b'build two'
    assert manager.base_statuses()['wol']['base_state'] == 'current'
    manager._change('wol', None, None)
    assert original.read_bytes() == b'user original'


def test_game_update_during_extraction_leaves_active_campaign_unchanged(manager, tmp_path, monkeypatch):
    from sc2_campaign_launcher_linux import casc
    item = manager.import_package(package(tmp_path))
    manager.activate(item['id'])
    build_file = write(manager.root / '.build.info', b'build one')

    def extract(root, slot, output, cancel, progress):
        write(output / 'metadata.txt', b'title=Base\ncampaign=wol')
        write(output / 'Original.SC2Map', b'base')
        build_file.write_bytes(b'build two')

    monkeypatch.setattr(casc, 'extract_campaign', extract)
    with pytest.raises(ValueError, match='updated during extraction'):
        manager.install_base('wol')
    assert manager._state()['active']['wol'] == item['id']
    assert manager.game_path('Maps/Campaign/First.SC2Map').read_bytes() == b'custom map'


def test_legacy_base_package_requires_refresh(manager, tmp_path, monkeypatch):
    from sc2_campaign_launcher_linux.files import atomic_json
    item = manager.import_package(package(tmp_path))
    item['base_campaign'] = True
    atomic_json(manager.packages / item['id'] / 'package.json', item)
    assert manager.base_statuses()['wol']['base_state'] == 'stale'
    assert manager.base_id('wol') is None


def test_process_death_during_copy_cleans_temporary_files_on_recovery(manager, tmp_path):
    import subprocess
    import sys
    original = write(manager.root / 'Maps/Campaign/Original.SC2Map', b'original')
    item = manager.import_package(package(tmp_path))
    code = """
import os, sys
from pathlib import Path
from sc2_campaign_launcher_linux import ccm
ccm.game_running = lambda: False
manager = ccm.CampaignManager(Path(sys.argv[1]), Path(sys.argv[2]))
replace = os.replace
def crash(source, target):
    if Path(source).name.startswith('.ccm-') and Path(target).suffix.casefold() == '.sc2map':
        os._exit(27)
    return replace(source, target)
os.replace = crash
manager.activate(sys.argv[3])
"""
    result = subprocess.run([sys.executable, '-c', code, str(manager.root), str(tmp_path / 'ccm-state'), item['id']],
                            timeout=30)
    assert result.returncode == 27
    assert manager.journal.exists()
    assert list(manager.root.rglob('.ccm-*'))
    manager.recover()
    assert original.read_bytes() == b'original'
    assert not list(manager.root.rglob('.ccm-*'))
    assert not manager.journal.exists()
    assert manager._state()['active'] == {}
