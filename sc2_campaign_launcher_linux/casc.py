"""Read installed SC2 campaign maps through CascLib 3.x."""

import ctypes as C
import ctypes.util
import csv
import hashlib
import os
import re
import sys
from pathlib import Path

from .ccm import CampaignManager, SLOTS
from .files import check_cancel, contained_path


def installed_build(root):
    path = root / '.build.info'
    if not path.is_file():
        return {'key': 'unknown', 'version': 'Installed game', 'locales': ['enus']}
    if path.stat().st_size > 64 * 1024:
        raise ValueError('The installed game build record is too large.')
    text = path.read_text(encoding='utf-8-sig')
    reader = csv.DictReader(text.splitlines(), delimiter='|')
    records = [{k.split('!', 1)[0]: v for k, v in row.items() if k} for row in reader]
    active = next((row for row in records if row.get('Active') == '1'), None)
    if active is None:
        # Older installations can omit the active-build columns.
        return {'key': hashlib.sha256(text.encode()).hexdigest(), 'version': 'Installed game',
                'locales': sorted({v.casefold() for v in re.findall(r'\b[a-z]{2}[A-Z]{2}\b', text)}) or ['enus']}
    locales = sorted({v.casefold() for v in re.findall(r'\b[a-z]{2}[A-Z]{2}\b', active.get('Tags', ''))}) or ['enus']
    identity = active.get('Build Key', '') + '\n' + active.get('Version', '') + '\n' + ','.join(locales)
    return {'key': hashlib.sha256(identity.encode()).hexdigest(),
            'version': active.get('Version') or 'Installed game', 'locales': locales}


class FindData(C.Structure):
    _fields_ = [('name', C.c_char * (260 if os.name == 'nt' else 1024)),
                ('ckey', C.c_ubyte * 16), ('ekey', C.c_ubyte * 16),
                ('tags', C.c_uint64), ('size', C.c_uint64), ('plain', C.c_void_p),
                ('data_id', C.c_uint32), ('locale', C.c_uint32), ('content', C.c_uint32),
                ('spans', C.c_uint32), ('available', C.c_uint32), ('name_type', C.c_uint32)]


def library_path():
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    if configured := os.environ.get('SC2CL_CASCLIB'):
        path = Path(configured).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError('SC2CL_CASCLIB must name an existing library using its absolute path.')
        return str(path)
    if os.name == 'nt':
        for path in (root / 'native/CascLib.dll', Path(sys.prefix) / 'share/SC2CampaignLauncher/native/CascLib.dll'):
            if path.is_file():
                return str(path)
    else:
        path = ctypes.util.find_library('casc')
        if path:
            return path
    raise ValueError('CascLib 3.x is required to extract base maps. Install libcasc on Linux or use the Windows bundle.')


class Storage:
    def __init__(self, root, library=None):
        self.dll = (C.WinDLL if os.name == 'nt' else C.CDLL)(library or library_path())
        pointer, number, boolean = C.c_void_p, C.c_uint32, C.c_bool
        signatures = {
            'CascOpenStorage': ([C.c_char_p, number, C.POINTER(pointer)], boolean),
            'CascCloseStorage': ([pointer], boolean),
            'CascOpenFile': ([pointer, C.c_char_p, number, number, C.POINTER(pointer)], boolean),
            'CascReadFile': ([pointer, pointer, number, C.POINTER(number)], boolean),
            'CascCloseFile': ([pointer], boolean),
            'CascGetFileSize64': ([pointer, C.POINTER(C.c_uint64)], boolean),
            'CascFindFirstFile': ([pointer, C.c_char_p, C.POINTER(FindData), C.c_char_p], pointer),
            'CascFindNextFile': ([pointer, C.POINTER(FindData)], boolean),
            'CascFindClose': ([pointer], boolean),
            'GetCascError': ([], number),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, result
        self.encoding = 'mbcs' if os.name == 'nt' else 'utf-8'
        self.handle = pointer()
        if not self.dll.CascOpenStorage(str(root).encode(self.encoding), 0, C.byref(self.handle)):
            raise OSError(f'Cannot open the installed CASC data (error {self.dll.GetCascError()}).')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.dll.CascCloseStorage(self.handle)

    def names(self, cancel=None):
        data = FindData()
        find = self.dll.CascFindFirstFile(self.handle, b'*', C.byref(data), None)
        if not find or find == C.c_void_p(-1).value:
            raise OSError('Could not list the installed CASC files.')
        try:
            while True:
                check_cancel(cancel)
                if data.name_type == 0:
                    yield data.name.decode(self.encoding).replace('\\', '/')
                if not self.dll.CascFindNextFile(find, C.byref(data)):
                    break
        finally:
            self.dll.CascFindClose(find)

    def extract(self, name, target, cancel=None):
        handle = C.c_void_p()
        if not self.dll.CascOpenFile(self.handle, name.encode(self.encoding), 0, 0, C.byref(handle)):
            raise OSError(f'Cannot read {name} from the installed game (error {self.dll.GetCascError()}).')
        try:
            length = C.c_uint64()
            if not self.dll.CascGetFileSize64(handle, C.byref(length)) or length.value > 8 * 1024**3:
                raise OSError(f'Invalid size for {name}.')
            target.parent.mkdir(parents=True, exist_ok=True)
            buffer = C.create_string_buffer(1024 * 1024)
            remaining = length.value
            with target.open('wb') as stream:
                while remaining:
                    check_cancel(cancel)
                    received = C.c_uint32()
                    if not self.dll.CascReadFile(handle, buffer, min(remaining, len(buffer)), C.byref(received)):
                        raise OSError(f'Could not finish reading {name}.')
                    if not received.value or received.value > remaining:
                        raise OSError(f'Incomplete CASC file: {name}')
                    stream.write(buffer.raw[:received.value])
                    remaining -= received.value
        finally:
            self.dll.CascCloseFile(handle)


def campaign_map(name, slot):
    normalized = name.replace('\\', '/').casefold()
    marker = '.sc2maps/maps/campaign/'
    if marker not in normalized:
        return None
    sources = {'wol': ('campaigns/liberty.sc2campaign/',),
               'hots': ('campaigns/swarm.sc2campaign/',),
               'lotv': ('campaigns/void.sc2campaign/', 'campaigns/swarm.sc2campaign/'),
               'nco': ('mods/novastoryassets.sc2mod/',)}
    if not normalized.startswith(sources[slot]):
        return None
    relative = normalized.split(marker, 1)[1]
    destination = 'maps/campaign/' + relative
    if '.sc2map/' not in relative or CampaignManager._slot(destination) != slot:
        return None
    if slot == 'wol':
        return relative
    if slot == 'lotv' and relative.startswith('voidprologue/'):
        return 'lotvprologue/' + relative[len('voidprologue/'):]
    return relative.split('/', 1)[1]


def extract_campaign(root, slot, output, cancel=None, progress=lambda _: None, library=None):
    if slot not in SLOTS:
        raise ValueError('Unknown base campaign.')
    build = installed_build(root)
    locales = set(build['locales'])
    with Storage(root, library) as storage:
        selected = {}
        for name in storage.names(cancel):
            relative = campaign_map(name, slot)
            if relative:
                language = re.search(r'/([a-z]{4})\.sc2data/', '/' + relative)
                if language and language[1] not in locales:
                    continue
                if relative in selected and selected[relative].casefold() != name.casefold():
                    raise ValueError(f'Two game archives provide {relative}; extraction needs an explicit source.')
                selected[relative] = name
        if not selected:
            raise ValueError('No maps for this campaign were found in the installed game.')
        for index, (relative, name) in enumerate(sorted(selected.items()), 1):
            check_cancel(cancel)
            progress({'name': relative, 'index': index, 'count': len(selected)})
            storage.extract(name, contained_path(output, relative), cancel)
    (output / 'metadata.txt').write_text(
        f'title={SLOTS[slot][0]}\nauthor=Blizzard\ncampaign={slot}\nversion=Installed game\n', encoding='utf-8')
    return len(selected)
