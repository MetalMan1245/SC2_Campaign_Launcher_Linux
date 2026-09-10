import hashlib
import io
from threading import Event
from unittest.mock import patch

import pytest

from sc2_campaign_launcher_linux.catalog import parse_catalog
from sc2_campaign_launcher_linux.files import Cancelled, contained_path, read_json
from sc2_campaign_launcher_linux.library import Library
from sc2_campaign_launcher_linux.network import HttpClient
from conftest import campaign, write


class Response(io.BytesIO):
    status = 200

    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data) if length is None else length)}
        self.read_sizes = []

    def geturl(self):
        return 'https://raw.githubusercontent.com/test/file'

    def read(self, size=-1):
        assert size > 0, 'Downloads must not read an entire response into memory.'
        self.read_sizes.append(size)
        return super().read(size)


class Opener:
    def __init__(self, data):
        self.data = data
        self.responses = []

    def open(self, request, timeout):
        name = request.full_url.rsplit('/', 1)[-1]
        response = Response(self.data[name])
        self.responses.append(response)
        return response


def use_downloads(library, data=None):
    opener = Opener(data or {'Launcher.SC2Map': b'current map', 'Shared.SC2Mod': b'current mod'})
    library.client = HttpClient(opener)
    return opener


def test_map_only_update_and_corruption_are_detected(library):
    c = campaign(mods=[])
    path = write(library.destination(c, c['maps'][0]), b'old map')
    assert library.statuses([c], Event())[0]['status'] == 'update_available'
    path.write_bytes(b'current map')
    assert library.statuses([c], Event())[0]['status'] == 'installed'
    path.write_bytes(b'')
    assert library.statuses([c], Event())[0]['status'] == 'update_available'


def test_hash_cache_notices_mod_changes(library):
    c = campaign()
    use_downloads(library)
    library.install(c, Event())
    assert library.statuses([c], Event())[0]['status'] == 'installed'
    library.destination(c, c['mods'][0]).write_bytes(b'corrupt mod')
    assert library.statuses([c], Event())[0]['status'] == 'update_available'


def test_records_are_scoped_to_sc2_root(library, tmp_path):
    c = campaign()
    use_downloads(library)
    library.install(c, Event())
    other = Library(tmp_path / 'another SC2', library.state_path.parent.parent)
    assert other.state_path != library.state_path
    assert other.recorded_campaigns() == []
    assert other.statuses([c], Event())[0]['status'] == 'not_installed'


def test_shared_mod_download_is_reused_and_survives_one_removal(library):
    a, b = campaign('A'), campaign('B')
    opener = use_downloads(library)
    library.install(a, Event())
    library.install(b, Event())
    assert len(opener.responses) == 3
    library.remove(a, [a, b], Event())
    assert library.destination(b, b['mods'][0]).exists()
    assert not library.destination(a, a['maps'][0]).exists()
    library.remove(b, [a, b], Event())
    assert not library.destination(b, b['mods'][0]).exists()


def test_remove_preserves_unknown_nested_and_modified_files(library):
    c = campaign()
    use_downloads(library)
    library.install(c, Event())
    extra = write(library.root / 'Maps/Test/backup.SC2Map', b'personal map')
    nested = write(library.root / 'Maps/Test/subfolder/notes.txt', b'notes')
    edited = library.destination(c, c['maps'][0])
    edited.write_bytes(b'edited map')
    message = library.remove(c, [c], Event())
    assert extra.read_bytes() == b'personal map'
    assert nested.read_bytes() == b'notes'
    assert edited.read_bytes() == b'edited map'
    assert 'Kept 1' in message


def test_remove_never_adopts_preexisting_files(library):
    c = campaign()
    use_downloads(library)
    existing = write(library.destination(c, c['mods'][0]), b'current mod')
    library.install(c, Event())
    library.remove(c, [c], Event())
    assert existing.read_bytes() == b'current mod'


def test_hash_mismatch_preserves_previous_file(library):
    c = campaign(mods=[])
    path = write(library.destination(c, c['maps'][0]), b'old usable map')
    use_downloads(library, {'Launcher.SC2Map': b'bad data'})
    with pytest.raises(ValueError, match='Checksum mismatch'):
        library.install(c, Event())
    assert path.read_bytes() == b'old usable map'
    assert list(path.parent.glob('*.part')) == []
    assert list(path.parent.glob('.sc2cl-*')) == []


def test_failed_replacement_preserves_previous_file(library):
    c = campaign(mods=[])
    path = write(library.destination(c, c['maps'][0]), b'old usable map')
    use_downloads(library)
    with patch('sc2_campaign_launcher_linux.network.os.replace', side_effect=OSError('disk error')):
        with pytest.raises(OSError, match='disk error'):
            library.install(c, Event())
    assert path.read_bytes() == b'old usable map'
    assert list(path.parent.glob('.sc2cl-*')) == []


def test_cancelled_download_preserves_previous_file(library):
    c = campaign(mods=[])
    path = write(library.destination(c, c['maps'][0]), b'old usable map')
    use_downloads(library)
    cancel = Event()
    with pytest.raises(Cancelled):
        library.install(c, cancel, lambda _: cancel.set())
    assert path.read_bytes() == b'old usable map'
    assert list(path.parent.glob('.sc2cl-*')) == []


def test_incomplete_response_never_replaces_file(library):
    c = campaign(mods=[])
    path = write(library.destination(c, c['maps'][0]), b'old usable map')
    response = Response(b'current map', length=999)
    with patch.object(library.client, 'open', return_value=response):
        with pytest.raises(OSError, match='Incomplete'):
            library.install(c, Event())
    assert path.read_bytes() == b'old usable map'


def test_download_reads_bounded_chunks(library):
    data = b'a' * (3 * 1024 * 1024)
    c = campaign(mods=[], maps=[('Launcher.SC2Map', data)])
    opener = use_downloads(library, {'Launcher.SC2Map': data})
    library.install(c, Event())
    assert max(opener.responses[0].read_sizes) <= 256 * 1024
    assert len(opener.responses[0].read_sizes) > 10


@pytest.mark.parametrize('value', ['../outside', '/outside', 'a/../../x', 'C:/outside',
                                 'a\\b', 'a//b', './x', 'NUL.SC2Map', 'a/..', 'file:stream'])
def test_paths_are_confined(tmp_path, value):
    with pytest.raises(ValueError):
        contained_path(tmp_path, value)


def test_symlink_is_not_followed(tmp_path):
    root, other = tmp_path / 'root', tmp_path / 'other'
    root.mkdir()
    other.mkdir()
    try:
        (root / 'Maps').symlink_to(other, target_is_directory=True)
    except OSError:
        pytest.skip('Symbolic links require privileges on this system.')
    with pytest.raises(ValueError):
        contained_path(root, 'Maps/Test.SC2Map')


def test_empty_or_bad_manifest_entry_does_not_hide_valid_campaigns():
    raw = {'title': 'Valid', 'maps': [{'name': 'Launcher.SC2Map',
           'sha256': hashlib.sha256(b'x').hexdigest(),
           'url': 'https://raw.githubusercontent.com/test/map'}]}
    campaigns, errors = parse_catalog([{'title': 'Empty'}, raw])
    assert [c['name'] for c in campaigns] == ['Valid']
    assert errors


def test_success_records_files_and_restores_after_restart(library):
    c = campaign()
    use_downloads(library)
    library.install(c, Event())
    saved = read_json(library.state_path)
    assert saved['files']['Maps/Test/Launcher.SC2Map']['owned']
    restarted = Library(library.root, library.state_path.parent.parent)
    assert restarted.statuses(restarted.recorded_campaigns(), Event())[0]['status'] == 'installed'
