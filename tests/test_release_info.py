import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import write
from sc2_campaign_launcher_linux import release_info
from sc2_campaign_launcher_linux.files import atomic_json, file_hash
from tools import build_release


@pytest.fixture
def source(tmp_path):
    for name in release_info.BUILD_FILES:
        write(tmp_path / name, b'fixture input')
    write(tmp_path / 'sc2_campaign_launcher_linux/__init__.py', b'__version__="test"')
    write(tmp_path / 'assets/logo.png', b'logo')
    write(tmp_path / 'native/CascLib-LICENSE.txt', b'license')
    return tmp_path


def test_clean_dirty_and_untracked_sources_have_honest_provenance(source):
    subprocess.run(['git', 'init', str(source)], check=True, capture_output=True)
    subprocess.run(['git', 'add', '.'], cwd=source, check=True, capture_output=True)
    subprocess.run(['git', '-c', 'user.name=Build test', '-c', 'user.email=build-test@example.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-m', 'Fixture'], cwd=source, check=True, capture_output=True)
    clean = release_info.create_build_info(source, 'launcher', require_clean=True)
    assert clean['dirty'] is False and len(clean['commit']) == 40
    write(source / 'sc2_campaign_launcher_linux/untracked.py', b'new input')
    dirty = release_info.create_build_info(source, 'launcher')
    assert dirty['dirty'] is True and dirty['commit'] == clean['commit']
    assert dirty['source_sha256'] != clean['source_sha256']
    assert 'sc2_campaign_launcher_linux/untracked.py' in dirty['inputs']
    with pytest.raises(ValueError, match='clean Git'):
        release_info.create_build_info(source, 'launcher', require_clean=True)


def test_missing_git_does_not_fabricate_clean_commit(source, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('git')
    monkeypatch.setattr(release_info.subprocess, 'run', unavailable)
    info = release_info.create_build_info(source, 'ccm')
    assert info['commit'] is None and info['dirty'] is None
    assert source.as_posix() not in json.dumps(info)
    assert release_info.validate_build_info(info, 'ccm') == info
    with pytest.raises(ValueError, match='clean Git'):
        release_info.validate_build_info(info, 'ccm', require_clean=True)


def test_changed_manifest_and_wrong_edition_are_rejected(source):
    info = release_info.create_build_info(source, 'ccm')
    with pytest.raises(ValueError, match='provenance'):
        release_info.validate_build_info(info, 'launcher')
    info['inputs']['assets/logo.png'] = '0' * 64
    with pytest.raises(ValueError, match='digest'):
        release_info.validate_build_info(info)


def test_legacy_installer_identity_does_not_claim_content_verification(tmp_path, monkeypatch):
    atomic_json(tmp_path / 'build-info.json', {'commit': 'a' * 40})
    monkeypatch.setattr(sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    info = release_info.packaged_build_info()
    assert info['commit'] == 'a' * 40
    assert info['dirty'] is None and info['source_sha256'] is None
    assert 'unknown' in info['provenance']


def test_archive_and_checksum_match_complete_bundle(tmp_path):
    bundle = tmp_path / 'Manager'
    write(bundle / 'Manager.exe', b'executable')
    write(bundle / '_internal/build-info.json', b'{}')
    archive = tmp_path / 'Manager.zip'
    build_release.package_bundle(bundle, archive)
    with zipfile.ZipFile(archive) as package:
        assert package.read('Manager/Manager.exe') == b'executable'
        assert package.testzip() is None
    assert archive.with_suffix('.zip.sha256').read_text() == f'{file_hash(archive)}  Manager.zip\n'
    assert not list(tmp_path.glob('.sc2cl-package-*'))


@pytest.mark.parametrize('failure', [OSError('disk full'), KeyboardInterrupt()])
def test_interrupted_packaging_keeps_previous_archive(tmp_path, monkeypatch, failure):
    bundle = tmp_path / 'Manager'
    write(bundle / 'Manager.exe', b'executable')
    archive = write(tmp_path / 'Manager.zip', b'previous complete archive')
    sidecar = write(tmp_path / 'Manager.zip.sha256', b'previous checksum')

    def interrupted(*args, **kwargs):
        raise failure
    monkeypatch.setattr(zipfile.ZipFile, 'write', interrupted)
    with pytest.raises(type(failure)):
        build_release.package_bundle(bundle, archive)
    assert archive.read_bytes() == b'previous complete archive'
    assert sidecar.read_bytes() == b'previous checksum'
    assert not list(tmp_path.glob('.sc2cl-package-*'))


def test_failed_zip_validation_keeps_previous_archive(tmp_path, monkeypatch):
    bundle = tmp_path / 'Manager'
    write(bundle / 'Manager.exe', b'executable')
    archive = write(tmp_path / 'Manager.zip', b'previous')
    monkeypatch.setattr(zipfile.ZipFile, 'testzip', lambda _: 'broken')
    with pytest.raises(ValueError, match='integrity'):
        build_release.package_bundle(bundle, archive)
    assert archive.read_bytes() == b'previous'
    assert not list(tmp_path.glob('.sc2cl-package-*'))


def test_skip_build_uses_bundled_identity_without_relabelling(source, monkeypatch):
    info = release_info.create_build_info(source, 'launcher')
    bundle = source / 'dist/SC2CampaignLauncher'
    write(bundle / 'SC2CampaignLauncher.exe', b'executable')
    write(bundle / '_internal/python312.dll', b'runtime')
    atomic_json(bundle / '_internal/build-info.json', info)
    atomic_json(source / 'build-info.json', {'commit': 'different checkout'})
    write(source / 'sc2_campaign_launcher_linux/__init__.py', b'changed since build')
    monkeypatch.setattr(build_release, '__file__', str(source / 'tools/build_release.py'))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'argv', ['build_release.py', '--skip-build'])
    monkeypatch.setattr(build_release.subprocess, 'run', lambda *a, **kw: None)
    build_release.main()
    with zipfile.ZipFile(source / 'dist/SC2CampaignLauncher-Windows-x86_64.zip') as package:
        assert json.loads(package.read('SC2CampaignLauncher/_internal/build-info.json')) == info


def test_source_change_during_build_stops_before_archive_replacement(source, monkeypatch):
    info = release_info.create_build_info(source, 'launcher')
    archive = write(source / 'dist/SC2CampaignLauncher-Windows-x86_64.zip', b'previous archive')
    monkeypatch.setattr(build_release, '__file__', str(source / 'tools/build_release.py'))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'argv', ['build_release.py'])
    monkeypatch.setattr(build_release, 'create_build_info', lambda *a, **kw: info)

    def changed(*args, **kwargs):
        write(source / 'assets/logo.png', b'changed during build')
    monkeypatch.setattr(build_release.subprocess, 'run', changed)
    with pytest.raises(ValueError, match='changed during'):
        build_release.main()
    assert archive.read_bytes() == b'previous archive'


def test_skip_build_refuses_legacy_record(source, monkeypatch):
    bundle = source / 'dist/SC2CampaignLauncher'
    write(bundle / 'SC2CampaignLauncher.exe', b'executable')
    write(bundle / '_internal/python312.dll', b'runtime')
    atomic_json(bundle / '_internal/build-info.json', {'commit': 'a' * 40})
    monkeypatch.setattr(build_release, '__file__', str(source / 'tools/build_release.py'))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'argv', ['build_release.py', '--skip-build'])
    with pytest.raises(ValueError, match='provenance'):
        build_release.main()
    assert not (source / 'dist/SC2CampaignLauncher-Windows-x86_64.zip').exists()


def test_build_info_diagnostic_can_write_without_a_console(tmp_path):
    output = tmp_path / 'diagnostic.json'
    subprocess.run([sys.executable, '-B', '-m', 'sc2_campaign_launcher_linux', '--build-info', str(output)],
                   cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True, timeout=30)
    info = json.loads(output.read_text())
    assert info['format'] == 1 and len(info['source_sha256']) == 64
    assert info['source_sha256'] == release_info.manifest_digest(info['inputs'])
