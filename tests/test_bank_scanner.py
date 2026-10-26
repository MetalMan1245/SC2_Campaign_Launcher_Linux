import io
import struct
import zlib
import subprocess
import sys
from threading import Event

import pytest
from mpyq import MPQArchive

from conftest import write
from sc2_campaign_launcher_linux.ccm import CampaignManager
from sc2_campaign_launcher_linux.bank_scanner import Scan, scan_package
from sc2_campaign_launcher_linux.bank_scanner_ui import BankReferencesDialog
from sc2_campaign_launcher_linux.files import Cancelled, file_hash, read_json, atomic_json
from sc2_campaign_launcher_linux.local_banks import (
    existing_banks, load_local_list, matching_paths, save_local_list, shared_references,
)
from sc2_campaign_launcher_linux.mpq_reader import ScriptArchive, decompress


@pytest.fixture
def manager(sc2_root, tmp_path):
    return CampaignManager(sc2_root, tmp_path / 'state')


def mpq_bytes(members, *, sectors=False, version=0):
    """Small unencrypted MPQ fixture with encrypted lookup tables."""
    hasher = MPQArchive.__new__(MPQArchive)

    def encrypt(data, key):
        seed = 0xEEEEEEEE
        output = bytearray()
        for (plain,) in struct.iter_unpack('<I', data):
            seed = (seed + hasher.encryption_table[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
            output.extend(struct.pack('<I', plain ^ ((key + seed) & 0xFFFFFFFF)))
            key = (((~key << 21) + 0x11111111) | (key >> 11)) & 0xFFFFFFFF
            seed = (plain + seed + (seed << 5) + 3) & 0xFFFFFFFF
        return bytes(output)

    members = {'(listfile)': '\n'.join(members).encode(), **members}
    count = len(members)
    header_size = 208 if version == 3 else 32
    offset = header_size + count * 32
    hashes, blocks, body = bytearray(), bytearray(), bytearray()
    for index, (name, data) in enumerate(members.items()):
        if sectors:
            chunks = []
            for start in range(0, len(data), 512):
                raw = data[start:start + 512]
                compressed = b'\x02' + zlib.compress(raw)
                chunks.append(compressed if len(compressed) < len(raw) else raw)
            positions = [(len(chunks) + 1) * 4]
            for chunk in chunks:
                positions.append(positions[-1] + len(chunk))
            encoded = struct.pack(f'<{len(positions)}I', *positions) + b''.join(chunks)
            flags = 0x80000200
        else:
            encoded = b'\x02' + zlib.compress(data)
            flags = 0x81000200
            if len(encoded) >= len(data):
                encoded = data
        hashes.extend(struct.pack('<IIHHI', hasher._hash(name, 'HASH_A'), hasher._hash(name, 'HASH_B'), 0, 0, index))
        blocks.extend(struct.pack('<IIII', offset + len(body), len(encoded), len(data), flags))
        body.extend(encoded)
    header = struct.pack('<4sIIHHIIII', b'MPQ\x1a', header_size, offset + len(body), version, 0,
                         header_size, header_size + count * 16, count, count)
    if version == 3:
        header += b'\0' * 36 + struct.pack('<QQ', count * 16, count * 16) + b'\0' * 124
    return header + encrypt(hashes, hasher._hash('(hash table)', 'TABLE')) + encrypt(blocks, hasher._hash('(block table)', 'TABLE')) + body


def source_package(tmp_path, name='Example', bank='Progress'):
    source = tmp_path / name
    write(source / 'metadata.txt', f'title={name}\nauthor=Tester\ncampaign=wol\nversion=1'.encode())
    write(source / 'First.SC2Map', mpq_bytes({
        'BankList.xml': f'<BankList><Bank Name="{bank}" Player="1"/></BankList>'.encode(),
        'MapScript.galaxy': b'void Run() { BankLoad("Extra", 1); BankLoad(variable, 1); }',
        'DocumentInfo': b'<DocInfo><Dependencies><Value>file:Mods/Liberty.SC2Mod</Value></Dependencies></DocInfo>',
    }))
    return source


@pytest.mark.parametrize('sectors', [False, True])
@pytest.mark.parametrize('version', [0, 3])
def test_mpq_reads_members_without_extracting(sectors, version):
    data = b'BankLoad("Example", 1);\n' * 100
    with io.BytesIO(mpq_bytes({'MapScript.galaxy': data}, sectors=sectors, version=version)) as stream:
        archive = ScriptArchive(stream)
        assert archive.read_file('MapScript.galaxy') == data
        assert archive.read_file('Missing') is None


def test_mpq_rejects_decompression_bombs_and_oversized_tables():
    with pytest.raises(ValueError, match='size'):
        decompress(b'\x02' + zlib.compress(b'a' * 1000000), 100)
    data = bytearray(mpq_bytes({'MapScript.galaxy': b'text'}))
    struct.pack_into('<I', data, 24, 10000000)
    with pytest.raises(ValueError, match='tables'):
        ScriptArchive(io.BytesIO(data))


def test_missing_reader_does_not_prevent_launcher_import():
    script = '''
import builtins, io
original = builtins.__import__
def import_without_mpyq(name, *args, **kwargs):
    if name == 'mpyq':
        raise ImportError('Reader not installed')
    return original(name, *args, **kwargs)
builtins.__import__ = import_without_mpyq
from sc2_campaign_launcher_linux import sc2_campaign_launcher_linux
from sc2_campaign_launcher_linux.mpq_reader import ScriptArchive
try:
    ScriptArchive(io.BytesIO(b'archive'))
except ValueError as error:
    assert 'MPQ reader is unavailable' in str(error)
else:
    raise AssertionError('Missing reader should be reported')
'''
    subprocess.run([sys.executable, '-c', script], check=True, capture_output=True, timeout=30)


def test_scripts_ignore_comments_strings_and_report_dynamic_names():
    scan = Scan({'name': 'Test', 'files': []})
    scan.document('MapScript.galaxy', b'''
// BankLoad("Comment", 1);
/* BankLoad("BlockComment", 1); */
const string prefix = "Example";
const string full = prefix + "Progress";
string text = "BankLoad(\\"Fake\\", 1)";
void Run() {
    BankLoad(full, 1);
    BankExists("Literal", 1);
    BankLoad("Dynamic" + IntToString(1), 1);
}
''', 'Map/MapScript.galaxy')
    assert {b['name'] for b in scan.banks.values()} == {'ExampleProgress', 'Literal'}
    assert len(scan.warnings) == 1
    assert 'unresolved' in next(iter(scan.warnings))


def test_local_constants_and_shadowed_names_are_not_guessed():
    scan = Scan({'name': 'Test', 'files': []})
    scan.galaxy('const string name = "Global"; void Foo(string name) { BankLoad(name, 1); }', 'script')
    scan.galaxy('void Foo() { const string local = "Local"; } void Bar() { BankLoad(local, 1); }', 'script2')
    assert not scan.banks
    assert len(scan.warnings) == 2


def test_import_scans_and_rescans_without_changing_payload(manager, tmp_path):
    item = manager.import_package(source_package(tmp_path))
    folder = manager.packages / item['id']
    report = read_json(folder / 'bank-scan.json')
    assert {r['name'] for r in report['references']} == {'Progress', 'Extra'}
    assert any('Liberty.SC2Mod' in w for w in report['warnings'])
    assert any('unresolved' in w for w in report['warnings'])
    before = {p: file_hash(p) for p in (folder / 'payload').rglob('*') if p.is_file()}
    assert manager.scan_banks(item['id']) == report
    assert all(file_hash(p) == digest for p, digest in before.items())
    assert not any('bank-scan.json' in f['destination'] for f in item['files'])
    assert not manager._state()['active']


def test_missing_only_scan_keeps_existing_report_and_reviewed_list(manager, tmp_path, monkeypatch):
    from sc2_campaign_launcher_linux import ccm
    item = manager.import_package(source_package(tmp_path))
    path = manager.packages / item['id'] / 'bank-scan.json'
    profile = tmp_path / 'account/Saves'
    profile.mkdir(parents=True)
    save_local_list(manager, item, profile, ['Reviewed.SC2Bank'])
    original = path.read_bytes()
    scan = ccm.scan_package
    calls = []

    def tracked(*args):
        calls.append(1)
        return scan(*args)
    monkeypatch.setattr(ccm, 'scan_package', tracked)
    assert manager.scan_banks(item['id'], only_if_missing=True) is None
    assert not calls and path.read_bytes() == original
    path.unlink()
    assert manager.scan_banks(item['id'], only_if_missing=True)['references']
    assert calls == [1]
    assert load_local_list(manager, item, profile)['banks'] == ['Reviewed.SC2Bank']


def test_bad_archives_and_xml_produce_warnings_not_empty_success(manager, tmp_path):
    source = source_package(tmp_path)
    write(source / 'First.SC2Map', b'not an archive')
    write(source / 'Bad.SC2Map', mpq_bytes({'BankList.xml': b'<!DOCTYPE BankList><BankList/>'}))
    item = manager.import_package(source)
    report = read_json(manager.packages / item['id'] / 'bank-scan.json')
    assert report['references'] == []
    assert len(report['warnings']) == 2


def test_unpacked_maps_mods_and_preloads_are_scanned(manager, tmp_path):
    source = tmp_path / 'unpacked'
    write(source / 'metadata.txt', b'title=Unpacked\ncampaign=wol')
    write(source / 'First.SC2Map/BankList.xml', b'<BankList><Bank Name="Map"/></BankList>')
    write(source / 'Mods/Shared.SC2Mod/DocumentInfo', b'<DocInfo><Preload><Value>Bank;Mod;1</Value></Preload></DocInfo>')
    item = manager.import_package(source)
    assert {r['name'] for r in manager.scan_banks(item['id'])['references']} == {'Map', 'Mod'}


def test_changed_payload_and_cancel_are_detected(manager, tmp_path):
    item = manager.import_package(source_package(tmp_path))
    folder = manager.packages / item['id']
    write(folder / 'payload/First.SC2Map', b'changed')
    report = manager.scan_banks(item['id'])
    assert report['references'] == []
    assert any('source has changed' in w for w in report['warnings'])
    token = Event()
    token.set()
    with pytest.raises(Cancelled):
        scan_package(folder / 'payload', item, token)
    assert read_json(folder / 'bank-scan.json') == report


def test_local_lists_are_account_specific_and_never_modify_banks(manager, tmp_path):
    item = manager.import_package(source_package(tmp_path))
    profile = tmp_path / 'account/Saves'
    profile.mkdir(parents=True)
    other = tmp_path / 'other/Saves'
    other.mkdir(parents=True)
    bank = write(profile.parent / 'Banks/1-S2-1-1/Progress.SC2Bank', b'private progress')
    before = file_hash(bank)
    assert matching_paths('Progress', existing_banks(profile)) == ['1-S2-1-1/Progress.SC2Bank']
    save_local_list(manager, item, profile, ['1-S2-1-1/Progress.SC2Bank'])
    assert load_local_list(manager, item, profile) == {'banks': ['1-S2-1-1/Progress.SC2Bank'], 'stale': False}
    assert load_local_list(manager, item, other)['banks'] == []
    assert file_hash(bank) == before
    assert 'progress' not in manager._package(item['id'])
    changed = {**item, 'files': [{**item['files'][0], 'sha256': '0' * 64}]}
    assert load_local_list(manager, changed, profile)['stale']
    with pytest.raises(ValueError):
        save_local_list(manager, item, profile, ['../outside.SC2Bank'])


def test_shared_references_are_visible_and_stale_reports_ignored(manager, tmp_path):
    first = manager.import_package(source_package(tmp_path, 'First'))
    second = manager.import_package(source_package(tmp_path, 'Second'))
    assert shared_references(manager, first)['progress'] == ['Second']
    path = manager.packages / second['id'] / 'bank-scan.json'
    report = read_json(path)
    report['package_digest'] = 'old'
    atomic_json(path, report)
    assert shared_references(manager, first) == {}


def test_review_dialog_starts_unselected_and_saves_only_local_list(manager, tmp_path, qapp):
    item = manager.import_package(source_package(tmp_path))
    profile = tmp_path / 'account/Saves'
    profile.mkdir(parents=True)
    bank = write(profile.parent / 'Banks/Progress.SC2Bank', b'untouched')
    report = manager.scan_banks(item['id'])
    dialog = BankReferencesDialog(manager, item, report, profile)
    assert dialog.paths.toPlainText() == ''
    for index in range(dialog.references.topLevelItemCount()):
        row = dialog.references.topLevelItem(index)
        if row.text(0) == 'Progress':
            dialog.references.setCurrentItem(row)
    dialog.add_matches()
    assert dialog.paths.toPlainText() == 'Progress.SC2Bank'
    assert 'BankList' in dialog.evidence.toPlainText()
    dialog.save()
    assert load_local_list(manager, item, profile)['banks'] == ['Progress.SC2Bank']
    assert bank.read_bytes() == b'untouched'
    dialog.close()
    no_account = BankReferencesDialog(manager, item, report)
    assert not no_account.save_button.isEnabled()
    no_account.close()
