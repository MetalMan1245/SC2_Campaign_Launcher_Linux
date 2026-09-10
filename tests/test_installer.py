from pathlib import Path

import pytest

from sc2_campaign_launcher_linux.installer import Installer, Locations, desktop_argument
from conftest import write


@pytest.fixture
def installation(tmp_path):
    locations = Locations(tmp_path / 'home', tmp_path / 'data', tmp_path / 'config')
    source = tmp_path / 'source'
    write(source / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py', b'print("test")')
    write(source / 'sc2_campaign_launcher_linux/installer.py', b'print("installer")')
    write(source / 'sc2_campaign_launcher_linux/files.py', b'# file utilities')
    write(source / 'sc2_campaign_launcher_linux/__init__.py', b'')
    write(source / 'assets/logo.png', b'icon')
    return Installer(locations), source


def test_uninstall_keeps_unrelated_files_and_directories(installation, tmp_path):
    installer, source = installation
    parent = tmp_path / 'custom parent'
    unrelated_parent = write(parent / 'personal.txt', b'personal')
    target = installer.install(source, 'custom', parent)
    unrelated_child = write(target / 'notes/private.txt', b'notes')
    installer.uninstall('custom')
    assert unrelated_parent.read_bytes() == b'personal'
    assert unrelated_child.read_bytes() == b'notes'
    assert not (target / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py').exists()


def test_uninstall_keeps_locally_edited_source(installation):
    installer, source = installation
    target = installer.install(source)
    edited = target / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py'
    edited.write_bytes(b'local modifications')
    assert str(edited) in installer.uninstall('local')
    assert edited.read_bytes() == b'local modifications'


def test_scopes_have_independent_records_and_resources(installation, tmp_path):
    installer, source = installation
    installer.install(source, 'local')
    custom = installer.install(source, 'custom', tmp_path / 'custom')
    installer.uninstall('local')
    restarted = Installer(installer.locations)
    assert 'custom' in restarted.installations()
    assert (custom / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py').exists()
    assert (installer.locations.data / 'applications/sc2-campaign-launcher-custom.desktop').exists()
    restarted.uninstall('custom')
    assert restarted.installations() == {}


def test_both_scope_removal_does_not_lose_custom_path(installation, tmp_path):
    installer, source = installation
    local = installer.install(source)
    custom = installer.install(source, 'custom', tmp_path / 'custom')
    for scope in list(installer.installations()):
        installer.uninstall(scope)
    assert not local.exists()
    assert not custom.exists()


def test_nonempty_unowned_application_directory_is_rejected(installation, tmp_path):
    installer, source = installation
    user_file = write(tmp_path / 'custom/SC2CampaignLauncher/personal.txt', b'keep')
    with pytest.raises(ValueError, match='not empty'):
        installer.install(source, 'custom', tmp_path / 'custom')
    assert user_file.read_bytes() == b'keep'


def test_missing_marker_prevents_uninstall(installation):
    installer, source = installation
    target = installer.install(source)
    (target / '.installation.json').unlink()
    with pytest.raises(ValueError, match='marker'):
        installer.uninstall('local')
    assert (target / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py').exists()


def test_reinstall_refuses_to_overwrite_changed_application_files(installation):
    installer, source = installation
    target = installer.install(source)
    edited = target / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py'
    edited.write_bytes(b'local modifications')
    with pytest.raises(ValueError, match='changed'):
        installer.install(source)
    assert edited.read_bytes() == b'local modifications'


def test_previous_desktop_entry_is_restored(installation):
    installer, source = installation
    desktop = write(installer.locations.data / 'applications/sc2-campaign-launcher.desktop', b'old entry')
    installer.install(source)
    assert b'old entry' != desktop.read_bytes()
    installer.install(source)
    installer.uninstall('local')
    assert desktop.read_bytes() == b'old entry'


def test_paths_with_spaces_and_shell_characters_are_quoted(installation, tmp_path):
    installer, source = installation
    target = installer.install(source, 'custom', tmp_path / 'custom space $variable %value')
    desktop = (installer.locations.data / 'applications/sc2-campaign-launcher-custom.desktop').read_text()
    assert desktop_argument(str(target / 'sc2_campaign_launcher_linux/sc2_campaign_launcher_linux.py')) in desktop
    assert 'QT_QPA_PLATFORM=xcb' not in desktop
    assert 'konsole' not in desktop
    uninstaller = (installer.locations.data / 'applications/sc2-campaign-launcher-custom-uninstall.desktop').read_text()
    assert 'Terminal=true' in uninstaller


def test_custom_paths_must_be_absolute(installation):
    installer, source = installation
    with pytest.raises(ValueError, match='absolute'):
        installer.install(source, 'custom', Path('relative/path'))


def test_legacy_uninstaller_is_replaced_and_backed_up(installation):
    installer, source = installation
    helper = write(installer.locations.home / '.local/bin/install-uninstall-SC2CLL.sh',
                   b'#!/bin/bash\nrm -rf "$target"\n')
    installer.install(source)
    assert b'rm -rf' not in helper.read_bytes()
    backups = list((installer.locations.config / 'SC2CampaignLauncher').glob('legacy-uninstaller-*.txt'))
    assert len(backups) == 1
    assert backups[0].read_bytes() == b'#!/bin/bash\nrm -rf "$target"\n'
    installer.install(source)
    assert len(list(backups[0].parent.glob('legacy-uninstaller-*.txt'))) == 1


def test_failed_uninstall_keeps_its_entrypoint_for_retry(installation, monkeypatch):
    installer, source = installation
    target = installer.install(source)
    blocked = target / 'assets/logo.png'
    unlink = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError('File is busy')
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', fail_one)
        with pytest.raises(OSError, match='File is busy'):
            installer.uninstall('local')
    for name in ('installer.py', 'files.py', '__init__.py'):
        assert (target / 'sc2_campaign_launcher_linux' / name).is_file()
    assert (installer.locations.data / 'applications/sc2-campaign-launcher-uninstall.desktop').is_file()
    Installer(installer.locations).uninstall('local')
    assert not target.exists()


def test_interrupted_install_can_be_removed(installation, monkeypatch):
    import sc2_campaign_launcher_linux.installer as module
    installer, source = installation
    atomic = module.atomic_bytes

    def fail_asset(path, data):
        if path.name == 'logo.png':
            raise OSError('Disk is full')
        atomic(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(module, 'atomic_bytes', fail_asset)
        with pytest.raises(OSError, match='Disk is full'):
            installer.install(source)
    target = Path(installer.installations()['local']['directory'])
    Installer(installer.locations).uninstall('local')
    assert not target.exists()


def test_corrupt_records_are_rejected_before_removing_files(installation):
    from sc2_campaign_launcher_linux.files import atomic_json
    installer, source = installation
    target = installer.install(source)
    record = installer.installations()['local']
    record['external']['applications/sc2-campaign-launcher.desktop']['previous'] = 'bad backup'
    atomic_json(installer.locations.state, installer.records)
    with pytest.raises(ValueError):
        Installer(installer.locations)
    assert (target / 'sc2_campaign_launcher_linux/installer.py').is_file()


@pytest.mark.parametrize('blocked_relative', ['icons/hicolor/48x48/apps/sc2-campaign-launcher-local.png',
                                              'applications/sc2-campaign-launcher-uninstall.desktop'])
def test_late_uninstall_failure_keeps_the_desktop_action(installation, monkeypatch, blocked_relative):
    installer, source = installation
    target = installer.install(source)
    blocked = installer.locations.data / blocked_relative
    unlink = Path.unlink

    def fail_one(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError('Cannot remove external file')
        return unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', fail_one)
        with pytest.raises(OSError, match='Cannot remove external file'):
            installer.uninstall('local')
    desktop = installer.locations.data / 'applications/sc2-campaign-launcher.desktop'
    assert '[Desktop Action uninstall]' in desktop.read_text()
    assert (target / 'sc2_campaign_launcher_linux/installer.py').is_file()
    Installer(installer.locations).uninstall('local')
    assert not target.exists()


def test_desktop_uninstall_uses_a_dialog_and_keeps_the_console_entry_hidden(installation, monkeypatch, qapp):
    from PyQt6.QtWidgets import QMessageBox
    from sc2_campaign_launcher_linux.installer import gui_uninstall
    installer, source = installation
    target = installer.install(source)
    desktop = (installer.locations.data / 'applications/sc2-campaign-launcher.desktop').read_text()
    assert 'Actions=uninstall;' in desktop
    assert '"--uninstall" "local" "--gui"' in desktop
    console = (installer.locations.data / 'applications/sc2-campaign-launcher-uninstall.desktop').read_text()
    assert 'NoDisplay=true' in console
    messages = []
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: messages.append(a[-1]))
    gui_uninstall(installer, 'local')
    assert not target.exists()
    assert messages and 'uninstalled' in messages[0]


def test_interactive_install_offers_dependencies_after_location_selection(installation, monkeypatch):
    import sc2_campaign_launcher_linux.installer as module
    installer, source = installation
    answers = iter(['1', 'y'])
    available = iter([False, True])
    commands = []
    monkeypatch.setattr(module, 'Installer', lambda: installer)
    monkeypatch.setattr(module.sys, 'argv', ['installer.py', '--source', str(source)])
    monkeypatch.setattr('builtins.input', lambda _: next(answers))
    monkeypatch.setattr(module, 'qt_available', lambda: next(available))
    monkeypatch.setattr(module, 'dependency_command', lambda: ['sudo', 'apt-get', 'install', 'python3-pyqt6'])
    monkeypatch.setattr(module.subprocess, 'run', lambda command, **kwargs: commands.append(command))
    module.main()
    assert commands == [['sudo', 'apt-get', 'install', 'python3-pyqt6']]
    assert 'local' in installer.installations()


def test_default_menu_reuses_the_existing_custom_directory(installation, tmp_path, monkeypatch, capsys):
    import sc2_campaign_launcher_linux.installer as module
    installer, source = installation
    target = installer.install(source, 'custom', tmp_path / 'custom parent')
    monkeypatch.setattr(module, 'Installer', lambda: installer)
    monkeypatch.setattr(module.sys, 'argv', ['installer.py', '--source', str(source)])
    prompts = []
    monkeypatch.setattr('builtins.input', lambda prompt: prompts.append(prompt) or '1')
    monkeypatch.setattr(module, 'ensure_dependencies', lambda: None)
    module.main()
    assert len(prompts) == 1
    assert installer.installations()['custom']['directory'] == str(target)
    output = capsys.readouterr().out
    assert 'Update custom installation' in output
    assert 'Uninstall custom installation' in output


def test_uninstall_without_scope_detects_the_installed_scope(installation, monkeypatch):
    import sc2_campaign_launcher_linux.installer as module
    installer, source = installation
    target = installer.install(source)
    monkeypatch.setattr(module, 'Installer', lambda: installer)
    monkeypatch.setattr(module.sys, 'argv', ['installer.py', '--uninstall', '--yes'])
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Scope should be detected'))
    module.main()
    assert not target.exists()


def test_upgrade_detects_the_legacy_local_install(installation, monkeypatch):
    from argparse import Namespace
    from sc2_campaign_launcher_linux.installer import choose_action
    installer, _ = installation
    write(installer.locations.home / '.local/share/SC2CampaignLauncher/sc2_campaign_launcher_linux.py', b'old')
    args = Namespace(install=None, uninstall=None, directory=None)
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('Existing scope should be detected'))
    choose_action(installer, args)
    assert args.install == 'local'


def test_virtual_environment_interpreter_is_kept_in_desktop_entry(installation, tmp_path):
    import os
    import subprocess
    import venv
    if os.name == 'nt':
        pytest.skip('Linux virtual environments use interpreter symlinks')
    installer, source = installation
    environment = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
    python = environment / 'bin/python'
    assert python.is_symlink()
    installer.install(source, python=python)
    desktop = (installer.locations.data / 'applications/sc2-campaign-launcher.desktop').read_text()
    assert desktop_argument(str(python)) in desktop
    result = subprocess.check_output([str(python), '-c', 'import sys; print(sys.prefix)'], text=True).strip()
    assert result == str(environment)
