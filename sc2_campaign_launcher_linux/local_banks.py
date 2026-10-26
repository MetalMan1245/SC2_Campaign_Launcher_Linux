"""Account-specific bank lists kept locally, separate from author declarations."""

import hashlib
import os
from pathlib import Path

from .bank_scanner import package_digest
from .files import atomic_json, contained_path, read_json
from .progress_metadata import validate_progress


def profile_path(profile):
    profile = Path(profile).expanduser().absolute()
    if profile.name.casefold() != 'saves' or not profile.parent.is_dir():
        raise ValueError('Choose an account Saves folder in Saved progress first.')
    return contained_path(profile.parent, profile.name)


def list_path(manager, item, profile):
    profile = profile_path(profile)
    key = hashlib.sha256(os.path.normcase(str(profile.resolve())).encode()).hexdigest()
    return contained_path(manager.directory, 'bank-lists', item['id'], key + '.json')


def load_local_list(manager, item, profile):
    record = read_json(list_path(manager, item, profile))
    if record is None:
        return {'banks': [], 'stale': False}
    if (not isinstance(record, dict) or record.get('format') != 1
            or record.get('profile') != str(profile_path(profile).resolve())):
        raise ValueError('The local bank list is invalid or belongs to another account.')
    banks = validate_progress({'format': 1, 'banks': record.get('banks')})['banks']
    return {'banks': banks, 'stale': record.get('package_digest') != package_digest(item)}


def save_local_list(manager, item, profile, banks):
    banks = validate_progress({'format': 1, 'banks': banks})['banks']
    with manager.lock:
        current = manager._package(item['id'])
        if package_digest(current) != package_digest(item):
            raise ValueError('The imported campaign changed. Open its bank references again.')
        record = {'format': 1, 'profile': str(profile_path(profile).resolve()),
                  'package_digest': package_digest(item), 'banks': banks}
        atomic_json(list_path(manager, item, profile), record)
    return record


def existing_banks(profile):
    """List filenames only. Never open a player's bank contents."""
    root = profile_path(profile).parent
    matches = [p for p in root.iterdir() if p.name.casefold() == 'banks']
    if len(matches) > 1:
        raise ValueError('The account has ambiguous Banks folders.')
    if not matches:
        return []
    banks = contained_path(root, matches[0].name)
    result = []
    count = 0
    for directory, folders, files in os.walk(banks, followlinks=False):
        for name in folders + files:
            count += 1
            if count > 10000:
                raise ValueError('The account exceeds the 10,000-entry bank listing limit.')
            path = contained_path(banks, (Path(directory) / name).relative_to(banks).as_posix())
            if name.lower().endswith('.sc2bank') and path.is_file():
                result.append(path.relative_to(banks).as_posix())
    return sorted(result, key=str.casefold)


def matching_paths(name, paths):
    expected = (name + '.SC2Bank').casefold()
    return [path for path in paths if path.casefold() == expected or path.casefold().endswith('/' + expected)]


def shared_references(manager, item):
    shared = {}
    for other in manager.list_packages():
        if other['id'] == item['id']:
            continue
        report = read_json(contained_path(manager.packages, other['id'], 'bank-scan.json'), {})
        if report.get('package_digest') != package_digest(other):
            continue
        for reference in report.get('references', []):
            shared.setdefault(reference['name'].casefold(), set()).add(other['name'])
    return {name: sorted(campaigns) for name, campaigns in shared.items()}
