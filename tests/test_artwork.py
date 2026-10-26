import struct

import pytest

from sc2_campaign_launcher_linux import campaign_artwork
from sc2_campaign_launcher_linux.files import Cancelled
from threading import Event


def dds():
    # A single 4x4 DXT1 block with red endpoints.
    header = [124, 0x81007, 4, 4, 8, 0, 0] + [0] * 11
    header += [32, 4, int.from_bytes(b'DXT1', 'little'), 0, 0, 0, 0, 0]
    header += [0x1000, 0, 0, 0, 0]
    return b'DDS ' + struct.pack('<31I', *header) + struct.pack('<HHI', 0xf800, 0xf800, 0)


def test_artwork_decodes_dds_and_reuses_cache_after_storage_unavailable(tmp_path, monkeypatch):
    from PIL import Image
    (tmp_path / '.build.info').write_text('installed build')

    class Storage:
        calls = 0

        def __init__(self, *args):
            Storage.calls += 1
            if Storage.calls > 1:
                raise OSError('game storage unavailable')

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract(self, name, target, cancel):
            target.write_bytes(dds())

    monkeypatch.setattr(campaign_artwork, 'Storage', Storage)
    paths = campaign_artwork.load_artwork(tmp_path, tmp_path / 'cache')
    assert set(paths) == {'wol', 'hots', 'lotv', 'nco'}
    for path in paths.values():
        with Image.open(path) as image:
            assert image.getpixel((0, 0)) == (255, 0, 0)
    assert campaign_artwork.load_artwork(tmp_path, tmp_path / 'cache') == paths
    assert Storage.calls == 1


def test_cancelled_artwork_does_not_publish_partial_image(tmp_path, monkeypatch):
    (tmp_path / '.build.info').write_text('installed build')
    token = Event()

    class Storage:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract(self, name, target, cancel):
            target.write_bytes(dds())
            token.set()

    monkeypatch.setattr(campaign_artwork, 'Storage', Storage)
    with pytest.raises(Cancelled):
        campaign_artwork.load_artwork(tmp_path, tmp_path / 'cache', token)
    assert not list((tmp_path / 'cache').rglob('*.png'))
    assert not list((tmp_path / 'cache').rglob('*.dds'))
