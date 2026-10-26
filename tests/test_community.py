from threading import Event
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import QSettings

from sc2_campaign_launcher_linux import casc
from sc2_campaign_launcher_linux.campaign_saves import campaign_save, save_profiles
from sc2_campaign_launcher_linux.catalog import Catalog, SOURCES, parse_campaign, raw_campaign, sort_campaigns
from sc2_campaign_launcher_linux.ccm_ui import PlayDialog
from sc2_campaign_launcher_linux.files import read_json
from sc2_campaign_launcher_linux.platform_backend import LaunchOptions
from sc2_campaign_launcher_linux.sc2_campaign_launcher_linux import MainWindow, TAB_SECTIONS
from sc2_campaign_launcher_linux.settings import AppSettings
from conftest import campaign, write
from test_runtime import spin


@pytest.fixture(autouse=True)
def no_real_account_backups(monkeypatch):
    monkeypatch.setattr('sc2_campaign_launcher_linux.ccm_ui.save_profiles', lambda *_: [])


def test_partial_outage_keeps_both_sources_and_source_identity(tmp_path):
    client = Mock()
    client.json.side_effect = [[raw_campaign(campaign('One'))], [raw_campaign(campaign('Two'))]]
    catalog = Catalog(tmp_path, client)
    assert len(catalog.load(Event()).campaigns) == 2
    client.json.side_effect = [[raw_campaign(campaign('One'))], OSError('offline')]
    result = catalog.load(Event())
    assert [c['source'] for c in result.campaigns] == ['Synergy', 'Xenocide']
    assert len(read_json(tmp_path / 'catalog.json')) == 2
    client.json.side_effect = OSError('offline')
    result = catalog.load(Event())
    assert result.offline
    assert [next(t['name'] for t in TAB_SECTIONS if t['shows'](c)) for c in result.campaigns] == ['Synergy', 'Extra']


def test_provider_conflicting_shared_mod_is_reported(tmp_path):
    client = Mock()
    client.json.side_effect = [[raw_campaign(campaign('One'))],
                              [raw_campaign(campaign('Two', mods=[('Shared.SC2Mod', b'different')]))]]
    result = Catalog(tmp_path, client).load(Event())
    assert len(result.campaigns) == 1
    assert 'Conflicting contents' in result.notice


def test_launcher_flag_survives_cache_and_installation_records():
    raw = raw_campaign(campaign())
    raw['launcher'] = False
    raw['updated_at'] = '2026-09-15T18:30:00Z'
    parsed = parse_campaign(raw, SOURCES[1]['base'])
    restored = parse_campaign(raw_campaign(parsed))
    assert restored['has_launcher'] is False
    assert restored['source'] == 'Xenocide'
    assert restored['updated_at'] == raw['updated_at']


def test_date_sort_places_unknown_last_and_orders_equal_dates_by_name():
    entries = [{'name': 'Unknown'}, {'name': 'Older', 'updated_at': '2025-01-01'},
               {'name': 'B', 'updated_at': '2026-01-01T00:00:00Z'},
               {'name': 'A', 'updated_at': '2026-01-01T01:00:00+01:00'}]
    assert [c['name'] for c in sort_campaigns(entries, 'updated')] == ['A', 'B', 'Older', 'Unknown']


def test_profile_discovery_uses_account_and_region_ids(tmp_path):
    prefix = tmp_path / 'wine'
    for account, region in [('123', '1-S2-1-456'), ('789', '2-S2-1-101')]:
        (prefix / 'drive_c/users/player/Documents/StarCraft II/Accounts' / account / region / 'Saves').mkdir(parents=True)
    result = save_profiles(LaunchOptions(tmp_path, prefix=str(prefix)), 'linux')
    assert len(result) == 2
    path = write(result[0] / 'LibertyCampaignSave.SC2Save', b'save')
    assert campaign_save(result[0], 'wol') == path
    assert campaign_save(result[0], 'wol', True) is None


def test_ccm_only_does_not_request_online_catalog(qapp, sc2_root, tmp_path, monkeypatch):
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    settings = AppSettings(QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    settings.settings.setValue('sc2_root', str(sc2_root))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    catalog = Mock()
    window = MainWindow(settings, catalog=catalog, ccm_only=True)
    window.show()
    qapp.processEvents()
    assert window.tabs.count() == 1
    assert window.ccm_page.items == []
    catalog.load.assert_not_called()
    assert window.ccm_page.buttons == []
    window.close()
    spin(qapp, lambda: not window.jobs.busy())


def test_ccm_play_uses_game_menu_instead_of_guessing_save_ownership(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QLabel, QPushButton
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    settings = AppSettings(QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    dialog = PlayDialog(settings, 'hots', None)
    buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
    assert set(buttons) == {'Cancel', 'Open through Battle.net'}
    assert any('inside StarCraft II' in label.text() for label in dialog.findChildren(QLabel))
    buttons['Open through Battle.net'].click()
    assert dialog.result() == dialog.DialogCode.Accepted


@pytest.mark.parametrize('available', [True, False])
def test_ccm_play_checks_client_before_activation(qapp, sc2_root, tmp_path, monkeypatch, available):
    from test_ccm import package
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    monkeypatch.setattr('sc2_campaign_launcher_linux.ccm.game_running', lambda: False)
    settings = AppSettings(QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    settings.settings.setValue('sc2_root', str(sc2_root))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    monkeypatch.setattr(PlayDialog, 'exec', lambda _: PlayDialog.DialogCode.Accepted)
    client = Mock(return_value=(['Battle.net.exe'], {}))
    if not available:
        client.side_effect = ValueError('Battle.net was not found')
    monkeypatch.setattr(settings.backend, 'command', client)
    window = MainWindow(settings, autoload=False, ccm_only=True)
    page = window.ccm_page
    item = page.manager.import_package(package(tmp_path, 'A', 'wol'))
    launch = Mock()

    def check_launch(slug, options, target):
        assert page.manager._state()['active'] == {'wol': item['id']}
        assert target is None

    launch.side_effect = check_launch
    window.launcher.launch_target = launch
    page.play(page.manager.cards()[0])
    spin(qapp, lambda: not window.jobs.busy())
    if available:
        launch.assert_called_once()
    else:
        launch.assert_not_called()
        assert not page.manager._state()['active']
        assert 'Battle.net was not found' in page.notice.text()
    window.close()


def test_casc_extraction_selects_slot_and_installed_locale(tmp_path, monkeypatch):
    write(tmp_path / 'game/.build.info', b'Tags\nenUS text?')
    files = ['campaigns/liberty.sc2campaign/base.sc2maps/maps/campaign/first.sc2map/mapinfo',
             'campaigns/liberty.sc2campaign/base.sc2maps/maps/campaign/first.sc2map/enus.sc2data/strings.txt',
             'campaigns/liberty.sc2campaign/base.sc2maps/maps/campaign/first.sc2map/dede.sc2data/strings.txt',
             'campaigns/swarm.sc2campaign/base.sc2maps/maps/campaign/swarm/zfirst.sc2map/mapinfo']

    class Storage:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def names(self, cancel):
            return iter(files)

        def extract(self, name, target, cancel):
            write(target, name.encode())

    monkeypatch.setattr(casc, 'Storage', Storage)
    output = tmp_path / 'output'
    assert casc.extract_campaign(tmp_path / 'game', 'wol', output) == 2
    assert (output / 'first.sc2map/mapinfo').exists()
    assert not (output / 'first.sc2map/dede.sc2data/strings.txt').exists()


def test_mission_picker_uses_selected_map(qapp, sc2_root, tmp_path, monkeypatch):
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    settings = AppSettings(QSettings(str(tmp_path / 'prefs.ini'), QSettings.Format.IniFormat))
    settings.settings.setValue('sc2_root', str(sc2_root))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    item = campaign(maps=[('One.SC2Map', b'one'), ('Two.SC2Map', b'two')], mods=[])
    item['has_launcher'] = False
    catalog = Mock()
    catalog.details.return_value = {}
    catalog.artwork.return_value = b''
    window = MainWindow(settings, catalog=catalog, autoload=False, include_ccm=False)
    window._render([item], window.generation)
    monkeypatch.setattr('sc2_campaign_launcher_linux.sc2_campaign_launcher_linux.QInputDialog.getItem',
                        lambda *args: ('Two.SC2Map', True))
    window.launcher.launch = Mock()
    window._request(item['slug'], 'play')
    assert window.launcher.launch.call_args.args[-1] == sc2_root.resolve() / 'Maps/Test/Two.SC2Map'
    window.close()
    spin(qapp, lambda: not window.jobs.busy())


def test_build_identity_uses_active_build_and_its_languages(tmp_path):
    write(tmp_path / '.build.info',
          b'Active!DEC:1|Build Key!HEX:16|Version!STRING:0|Tags!STRING:0\n'
          b'0|old|1.0|deDE text?\n1|new|2.0|enUS text? enUS speech?\n')
    build = casc.installed_build(tmp_path)
    assert build['version'] == '2.0'
    assert build['locales'] == ['enus']
    old_key = build['key']
    text = (tmp_path / '.build.info').read_text().replace('enUS text?', 'frFR text?')
    (tmp_path / '.build.info').write_text(text)
    assert casc.installed_build(tmp_path)['key'] != old_key


def test_revert_button_restores_selected_slot_and_keeps_import(qapp, sc2_root, tmp_path, monkeypatch):
    from test_ccm import package
    monkeypatch.setenv('SC2CL_PLATFORM', 'win32')
    monkeypatch.setattr('sc2_campaign_launcher_linux.ccm.game_running', lambda: False)
    settings = AppSettings(QSettings(str(tmp_path / 'settings.ini'), QSettings.Format.IniFormat))
    settings.settings.setValue('sc2_root', str(sc2_root))
    monkeypatch.setattr(settings.backend, 'data_dir', lambda: tmp_path / 'state')
    original = write(sc2_root / 'Maps/Campaign/Original.SC2Map', b'original')
    window = MainWindow(settings, autoload=False, ccm_only=True)
    page = window.ccm_page
    first = page.manager.import_package(package(tmp_path, 'A', 'wol'))
    second = page.manager.import_package(package(tmp_path, 'B', 'hots'))
    page.manager.activate(first['id'])
    page.manager.activate(second['id'])
    page.reload()
    assert len(page.items) == 2
    next(b for b in page.buttons if b.text() == 'Revert to base').click()
    spin(qapp, lambda: not window.jobs.busy())
    assert original.read_bytes() == b'original'
    assert page.manager.game_path('Maps/Campaign/swarm/First.SC2Map').exists()
    assert page.manager._state()['active'] == {'hots': second['id']}
    assert len(page.items) == 2
    assert [b.text() for b in page.buttons].count('Revert to base') == 1
    assert [b.text() for b in page.buttons].count('Load') == 1
    window.close()
