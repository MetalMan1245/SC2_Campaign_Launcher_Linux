"""Locate campaign saves without moving or modifying player data."""

import os
from pathlib import Path

from .ccm import SLOTS

SAVE_NAMES = {'wol': 'LibertyCampaign', 'hots': 'SwarmCampaign',
              'lotv': 'VoidCampaign', 'nco': 'NovaCampaign01'}


def document_roots(options, platform):
    roots = []
    if platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                roots.append(Path(os.path.expandvars(winreg.QueryValueEx(key, 'Personal')[0])))
        except (ImportError, OSError):
            pass
        roots.extend([Path.home() / 'Documents', Path.home() / 'OneDrive/Documents'])
    elif options.prefix:
        users = Path(options.prefix) / 'drive_c/users'
        if users.is_dir():
            for user in users.iterdir():
                roots.extend([user / 'Documents', user / 'My Documents'])
    return list(dict.fromkeys(p.resolve() for p in roots if p.is_dir()))


def save_profiles(options, platform):
    profiles = []
    for documents in document_roots(options, platform):
        profiles.extend((p / 'Saves').absolute() for p in (documents / 'StarCraft II/Accounts').glob('*/*')
                        if p.is_dir() and ((p / 'Saves').is_dir() or (p / 'Banks').is_dir()))
    return sorted(set(profiles), key=str)


def campaign_save(profile, slot, archives=False):
    if slot not in SLOTS or (slot == 'wol' and archives):
        return None
    name = SAVE_NAMES[slot] + ('CompletedSave' if archives else 'Save') + '.SC2Save'
    path = profile / name
    if path.is_file():
        return path
    return next((p for p in profile.iterdir() if p.is_file() and p.name.casefold() == name.casefold()), None)
