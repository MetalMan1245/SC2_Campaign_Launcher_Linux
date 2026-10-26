"""Author declarations of exact bank paths relative to an account's Banks folder."""

import hashlib
import re
from pathlib import PurePosixPath

from .files import read_json, relative_name

PROGRESS_FILE = 'campaign-progress.json'


def validate_progress(value):
    if (not isinstance(value, dict) or set(value) != {'format', 'banks'}
            or type(value.get('format')) is not int or value['format'] != 1
            or not isinstance(value.get('banks'), list) or len(value['banks']) > 256):
        raise ValueError('Campaign progress metadata needs format 1 and a banks list of at most 256 paths.')
    banks = []
    seen = set()
    for name in value['banks']:
        relative_name(name)
        if PurePosixPath(name).suffix.casefold() != '.sc2bank' or name.split('/')[0].casefold() in ('banks', 'saves'):
            raise ValueError('List exact .SC2Bank paths relative to Banks, without a Banks/ prefix.')
        if name.casefold() in seen:
            raise ValueError(f'The bank path is listed more than once: {name}')
        seen.add(name.casefold())
        banks.append(name)
    return {'format': 1, 'banks': sorted(banks, key=str.casefold)}


def load_progress(folder):
    matches = [p for p in folder.iterdir() if p.name.casefold() == PROGRESS_FILE]
    if len(matches) > 1:
        raise ValueError('The campaign has more than one campaign-progress.json.')
    if not matches:
        return None
    if matches[0].stat().st_size > 256 * 1024:
        raise ValueError('campaign-progress.json is too large.')
    return validate_progress(read_json(matches[0]))


def campaign_owner(item):
    value = '\n'.join(str(item[key]).strip().casefold() for key in ('author', 'name', 'slot'))
    return hashlib.sha256(value.encode()).hexdigest()


def validate_scope(scope):
    if (not isinstance(scope, dict) or set(scope) != {'owner', 'campaign', 'banks'}
            or not isinstance(scope.get('owner'), str) or not re.fullmatch('[0-9a-f]{64}', scope['owner'])
            or not isinstance(scope.get('campaign'), str) or not 1 <= len(scope['campaign']) <= 200):
        raise ValueError('The campaign bank scope is invalid.')
    banks = validate_progress({'format': 1, 'banks': scope['banks']})['banks']
    return {**scope, 'banks': banks}


def bank_scope(item):
    return validate_scope({'owner': campaign_owner(item), 'campaign': item['name'], 'banks': item['progress']['banks']})
