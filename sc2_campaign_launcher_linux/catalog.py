"""Campaign metadata and the last usable catalog."""

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.parse import quote, urlsplit

from .files import Cancelled, atomic_json, check_cancel, contained_path, read_json, relative_name
from .network import HttpClient, validate_url, HttpClient, ALLOWED_HOSTS

REPOSITORY = 'R-P-S/SC2Campaigns'
BASE_URL = f'https://raw.githubusercontent.com/{REPOSITORY}/main'
MAPS_JSON_URL = f'{BASE_URL}/maps.json'
AUTHOR_OVERRIDE = {'uedfl': 'Oracle', 'ued-fl': 'Oracle'}


def slugify(name: str) -> str:
    return ''.join(char for char in name.lower() if char.isalnum())

SOURCES = (
    {'name': 'Synergy', 'maps': MAPS_JSON_URL, 'base': BASE_URL},
    {'name': 'Xenocide', 'maps': 'https://sc2.sarl/xenocide/maps.json', 'base': 'https://sc2.sarl/xenocide'},
)

def parse_campaign(entry: dict, source_base: str = '') -> dict:
    if not isinstance(entry, dict):
        raise ValueError('A campaign must be an object.')
    title = entry.get('title') or entry.get('name')
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise ValueError('A campaign title is required.')
    folder = relative_name(entry.get('folder') or slugify(title))
    files = entry.get('maps')
    if not isinstance(files, list) or not files or len(files) > 10000:
        raise ValueError(f'{title}: a file list is required.')
    maps, mods, seen = [], [], set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError(f'{title}: invalid file entry.')
        name = relative_name(item.get('name'))
        extension = Path(name).suffix.lower()
        if extension not in ('.sc2map', '.sc2mod'):
            raise ValueError(f'{title}: unsupported file type: {name}')
        if name.casefold() in seen:
            raise ValueError(f'{title}: duplicate filename: {name}')
        seen.add(name.casefold())
        sha = item.get('sha256')
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', sha):
            raise ValueError(f'{title}: invalid SHA-256 for {name}')
        file = {'name': name, 'sha256': sha.lower(), 'url': validate_url(item.get('url'))}
        (mods if extension == '.sc2mod' else maps).append(file)
    if not maps:
        raise ValueError(f'{title}: no launcher map was listed.')
    asset = relative_name(entry.get('asset') or f'{title}.png')
    if Path(asset).suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
        raise ValueError(f'{title}: unsupported cover image.')
    author = entry.get('author') or AUTHOR_OVERRIDE.get(folder.casefold(), 'Synergy')
    if isinstance(author, dict):
        author = author.get('name', 'Synergy')
    game = entry.get('game', 'SC2')
    if game not in ('SC2', 'SC1', 'WC3'):
        raise ValueError(f'{title}: unsupported game: {game!r}')
    kind = entry.get('type', 'discrete')
    if kind not in ('discrete', 'custom'):
        raise ValueError(f'{title}: unsupported campaign type: {kind!r}')
    has_launcher = entry.get('launcher', True)
    if not isinstance(has_launcher, bool):
        raise ValueError(f'{title}: launcher must be a boolean.')
    if kind == 'custom':
        has_launcher = False
    tags = entry.get('tags', 'none')
    if not isinstance(tags, str) or len(tags) > 200:
        raise ValueError(f'{title}: invalid tags.')
    base = source_base or entry.get('source_base') or BASE_URL
    if (not isinstance(base, str) or urlsplit(base).scheme != 'https'
            or urlsplit(base).hostname not in ALLOWED_HOSTS):
        raise ValueError(f'{title}: unsupported source: {base!r}')
    return {
        'name': title.strip(), 'slug': folder, 'folder': folder,
        'version': str(entry.get('version', '1.0'))[:80], 'author': str(author)[:200],
        'asset': asset, 'maps': maps, 'mods': mods,
        'source_base': base,
        'game': game, 'type': kind, 'has_launcher': has_launcher, 'tags': tags.strip(),
    }


def parse_catalog(data, source_base: str = ''):
    if not isinstance(data, list) or not data or len(data) > 1000:
        raise ValueError('The catalog must contain a list of campaigns.')
    campaigns, errors, seen, destinations = [], [], set(), {}
    for entry in data:
        try:
            campaign = parse_campaign(entry, source_base)
            key = campaign['slug'].casefold()
            if key in seen:
                raise ValueError(f'Duplicate campaign folder: {campaign["folder"]}')
            paths = {}
            for file in campaign['maps'] + campaign['mods']:
                location = file_key(campaign, file).casefold()
                if location in destinations and destinations[location] != file['sha256']:
                    raise ValueError(f'Conflicting contents for {file["name"]}')
                paths[location] = file['sha256']
            destinations.update(paths)
            seen.add(key)
            campaigns.append(campaign)
        except (ValueError, TypeError) as error:
            errors.append(str(error))
    if not campaigns:
        raise ValueError('No usable campaigns were found. ' + '; '.join(errors[:3]))
    return campaigns, errors


def file_key(campaign: dict, file: dict) -> str:
    if file['name'].lower().endswith('.sc2mod'):
        return f'Mods/{relative_name(file["name"])}'
    return f'Maps/{relative_name(campaign["folder"])}/{relative_name(file["name"])}'


def raw_campaign(campaign: dict) -> dict:
    return {
        'title': campaign['name'], 'folder': campaign['folder'],
        'version': campaign['version'], 'author': campaign['author'],
        'asset': campaign['asset'], 'maps': campaign['maps'] + campaign['mods'],
        'game': campaign['game'], 'type': campaign['type'],
        'has_launcher': campaign['has_launcher'], 'tags': campaign['tags'],
        'source_base': campaign.get('source_base', BASE_URL),
    }


@dataclass
class CatalogResult:
    campaigns: list[dict]
    notice: str = ''
    offline: bool = False


class Catalog:
    def __init__(self, cache_dir: Path, client: HttpClient | None = None):
        self.cache_dir = cache_dir
        self.client = client or HttpClient()

    def load(self, cancel: Event) -> CatalogResult:
        cached_path = self.cache_dir / 'catalog.json'
        campaigns, notice = [], ''
        for source in SOURCES:
            try:
                data = self.client.json(source['maps'], cancel)
                result, errors = parse_catalog(data, source['base'])
                check_cancel(cancel)
                campaigns.extend(result)
                if errors:
                    notice = f'{notice}; {"; ".join(errors[:3])}'.strip('; ')
            except Cancelled:
                raise
            except (OSError, ValueError) as error:
                notice = f'{notice}; {source["name"]}: {error}'.strip('; ')
        check_cancel(cancel)
        if not campaigns:
            try:
                campaigns, _ = parse_catalog(read_json(cached_path))
                return CatalogResult(campaigns, f'{notice}\nOffline catalog.', True)
            except (OSError, ValueError, TypeError):
                return CatalogResult([], f'Could not load any catalog: {notice}', True)
        unique: dict[str, dict] = {}
        for campaign in campaigns:
            key = campaign['slug'].casefold()
            if key in unique:
                notice = f'{notice}; Duplicate campaign folder: {campaign["slug"]}'.strip('; ')
            unique.setdefault(key, campaign)
        campaigns = list(unique.values())
        try:
            atomic_json(cached_path, [raw_campaign(c) for c in campaigns])
        except OSError as error:
            notice = f'{notice}\nCould not cache the catalog: {error}'.strip('; ')
        return CatalogResult(campaigns, notice)

    def details(self, campaign: dict, cancel: Event) -> dict:
        title = quote(campaign['name'], safe='')
        base = campaign.get('source_base', BASE_URL)
        url = f'{base}/campaigns/{title}/mapinfo/mapinfo.json'
        key = hashlib.sha256(url.encode()).hexdigest()
        cache = contained_path(self.cache_dir, 'details', f'{key}.json')
        try:
            if cache.exists() and time.time() - cache.stat().st_mtime < 86400:
                return read_json(cache, {})
            data = self.client.json(url, cancel)
            info = data.get('mapinfo', {}) if isinstance(data, dict) else {}
            result = {k: v[:20000] for k, v in info.items()
                      if k in ('description', 'patch notes', 'author') and isinstance(v, str)}
            atomic_json(cache, result)
            return result
        except Cancelled:
            raise
        except (OSError, ValueError, TypeError, AttributeError):
            return {}

    def artwork(self, campaign: dict, cancel: Event) -> bytes:
        from .files import atomic_bytes

        title = quote(campaign['name'], safe='')
        asset = quote(campaign['asset'], safe='/')
        url = f'{campaign.get('source_base', BASE_URL)}/campaigns/{title}/assets/{asset}'
        key = hashlib.sha256((url + campaign['version']).encode()).hexdigest()
        cache = contained_path(self.cache_dir, 'covers', key)
        try:
            if cache.is_file() and cache.stat().st_size <= 10 * 1024 * 1024:
                data = cache.read_bytes()
                if is_image(data):
                    return data
            data = self.client.get(url, cancel, limit=10 * 1024 * 1024)
            if not is_image(data):
                raise ValueError('The cover response is not an image.')
            atomic_bytes(cache, data)
            return data
        except Cancelled:
            raise
        except (OSError, ValueError):
            return b''


def is_image(data: bytes) -> bool:
    return (data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff')
            or (data.startswith(b'RIFF') and data[8:12] == b'WEBP'))
