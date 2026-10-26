"""Cache campaign card images from the installed game."""

import io
import tempfile
from pathlib import Path

from .casc import Storage, installed_build
from .files import atomic_bytes, check_cancel

TEXTURES = {
    'wol': 'mods/liberty.sc2mod/base.sc2assets/assets/textures/ui_hots_glue_campaign_image_wol.dds',
    'hots': 'mods/liberty.sc2mod/base.sc2assets/assets/textures/ui_hots_glue_campaign_image_hots.dds',
    'lotv': 'mods/core.sc2mod/base.sc2assets/assets/textures/ui_battlenet_glue_campaign_purchase_thumb_void_standard_01.dds',
    'nco': 'mods/core.sc2mod/base.sc2assets/assets/textures/ui_battlenet_glue_campaign_purchase_thumb_nova_mp1_01.dds',
}


def load_artwork(root, directory, cancel=None, library=None):
    if not (root / '.build.info').is_file():
        return {}
    build = installed_build(root)
    output = directory / 'artwork' / build['key']
    paths = {slot: output / (slot + '.png') for slot in TEXTURES}
    if all(path.is_file() for path in paths.values()):
        return paths
    from PIL import Image
    output.mkdir(parents=True, exist_ok=True)
    with Storage(root, library) as storage, tempfile.TemporaryDirectory(prefix='art-', dir=output) as scratch:
        for slot, texture in TEXTURES.items():
            check_cancel(cancel)
            if paths[slot].is_file():
                continue
            source = Path(scratch) / (slot + '.dds')
            storage.extract(texture, source, cancel)
            with Image.open(source) as picture:
                if picture.width * picture.height > 16 * 1024 * 1024:
                    raise ValueError('Campaign artwork is too large.')
                picture.thumbnail((560, 320))
                data = io.BytesIO()
                picture.convert('RGB').save(data, format='PNG')
            check_cancel(cancel)
            atomic_bytes(paths[slot], data.getvalue())
    return paths
