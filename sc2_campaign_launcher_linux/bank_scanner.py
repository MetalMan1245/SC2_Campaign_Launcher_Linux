"""Collect bank references from imported content. References do not establish ownership."""

import hashlib
import json
import re
import struct
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import PurePosixPath

from .files import check_cancel, contained_path, file_hash, relative_name
from .mpq_reader import MAX_MEMBER, ScriptArchive

SCANNER_VERSION = 1
MAX_DOCUMENTS = 2048
MAX_TEXT = 64 * 1024 * 1024
TEXT_NAMES = {'banklist.xml', 'documentinfo'}
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/|[A-Za-z_][\w]*|[^\s]')


def package_digest(item):
    return hashlib.sha256(json.dumps(item['files'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def script_member(name):
    return name.casefold().endswith('.galaxy') or PurePosixPath(name).name.casefold() in TEXT_NAMES


def string_value(tokens, constants):
    if not tokens or len(tokens) > 63:
        return None
    values = []
    for index, token in enumerate(tokens):
        if index % 2:
            if token != '+':
                return None
        elif token.startswith('"'):
            try:
                values.append(json.loads(token))
            except ValueError:
                return None
        elif token in constants:
            values.append(constants[token])
        else:
            return None
        if sum(map(len, values)) > 512:
            return None
    return ''.join(values) if len(tokens) % 2 else None


class Scan:
    def __init__(self, item, cancel=None):
        self.cancel = cancel
        self.banks = {}
        self.warnings = set()
        self.dependencies = set()
        self.includes = set()
        self.members = set()
        self.documents = 0
        self.bytes = 0
        self.report = {'format': 1, 'scanner_version': SCANNER_VERSION, 'package_digest': package_digest(item),
                       'campaign': item['name'], 'references': [], 'warnings': [], 'documents': 0,
                       'coverage': 'references only; ownership and runtime coverage are not established'}

    def warn(self, message):
        if len(self.warnings) < 250:
            self.warnings.add(message[:1000])
        else:
            self.warnings.add('Further scan warnings omitted.')

    def add(self, name, source, kind, line=0):
        try:
            if not isinstance(name, str) or not name:
                raise ValueError('Empty bank name.')
            relative_name(name + '.SC2Bank')
            if name.lower().endswith('.sc2bank'):
                raise ValueError('Bank name already contains an extension.')
        except (ValueError, TypeError):
            self.warn(f'{source}: unsafe or ambiguous bank name {name!r}.')
            return
        key = name.casefold()
        if key not in self.banks:
            if len(self.banks) >= 256:
                self.warn('More than 256 bank names found; further names omitted.')
                return
            self.banks[key] = {'name': name, 'evidence': [], 'occurrences': 0}
        bank = self.banks[key]
        bank['occurrences'] += 1
        if len(bank['evidence']) < 40:
            bank['evidence'].append({'source': source, 'kind': kind, 'line': line})

    def document(self, name, data, source):
        check_cancel(self.cancel)
        if data is None:
            return
        self.documents += 1
        self.bytes += len(data)
        if self.documents > MAX_DOCUMENTS or self.bytes > MAX_TEXT:
            raise ValueError('Campaign exceeds the scanner document or text limit.')
        if len(data) > MAX_MEMBER:
            raise ValueError('Script exceeds the 4 MiB scanner limit.')
        text = data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        if name.casefold().endswith('.galaxy'):
            self.galaxy(text, source)
        else:
            if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
                raise ValueError('XML entity declarations are not supported.')
            root = ET.fromstring(text)
            if PurePosixPath(name).name.casefold() == 'banklist.xml':
                for node in root.iter('Bank'):
                    if node.get('Name'):
                        self.add(node.get('Name'), source, 'BankList preload')
            else:
                for node in root.findall('./Preload/Value'):
                    if node.text and node.text.startswith('Bank;'):
                        self.add(node.text.split(';')[1], source, 'DocumentInfo preload')
                for node in root.findall('./Dependencies/Value'):
                    if node.text:
                        self.dependencies.add(node.text)

    def galaxy(self, text, source):
        matches = []
        for match in TOKEN.finditer(text):
            if not match.group().startswith(('//', '/*')):
                matches.append(match)
                if len(matches) > 500000:
                    raise ValueError('Script exceeds the scanner token limit.')
        tokens = [m.group() for m in matches]
        constants = {}
        declarations = {}
        depth = 0
        for index in range(len(tokens) - 4):
            if tokens[index] == '{':
                depth += 1
            elif tokens[index] == '}':
                depth = max(0, depth - 1)
            if depth == 0 and tokens[index:index + 2] == ['const', 'string'] and tokens[index + 3] == '=':
                end = index + 4
                while end < min(len(tokens), index + 68) and tokens[end] != ';':
                    end += 1
                declarations.setdefault(tokens[index + 2], []).append(tokens[index + 4:end])
        string_declarations = Counter(tokens[i + 1] for i in range(len(tokens) - 1) if tokens[i] == 'string')
        declarations = {name: expressions for name, expressions in declarations.items() if string_declarations[name] == 1}
        for _ in range(8):
            for name, expressions in declarations.items():
                if len(expressions) == 1 and (value := string_value(expressions[0], constants)) is not None:
                    constants[name] = value
        for index, token in enumerate(tokens[:-1]):
            check_cancel(self.cancel)
            if token == 'include' and tokens[index + 1].startswith('"'):
                if value := string_value([tokens[index + 1]], {}):
                    self.includes.add(value.replace('\\', '/'))
            if token not in ('BankLoad', 'BankExists') or tokens[index + 1] != '(':
                continue
            if index and tokens[index - 1] in ('bank', 'bool'):
                continue
            end = index + 2
            while end < min(len(tokens), index + 68) and tokens[end] not in (',', ')', ';'):
                end += 1
            value = string_value(tokens[index + 2:end], constants)
            line = text.count('\n', 0, matches[index].start()) + 1
            if value:
                self.add(value, source, token, line)
            else:
                self.warn(f'{source}:{line}: {token} uses an unresolved bank-name expression.')

    def archive(self, path, source):
        with path.open('rb') as stream:
            archive = ScriptArchive(stream)
            names = {'BankList.xml', 'MapScript.galaxy', 'DocumentInfo'}
            try:
                listing = archive.read_file('(listfile)')
                if listing is None:
                    self.warn(f'{source}: archive has no file list; only known script names were checked.')
                else:
                    listed = listing.decode('utf-8-sig').splitlines()
                    if len(listed) > 65536:
                        raise ValueError('Archive file list exceeds the scanner limit.')
                    names.update(n.replace('\\', '/') for n in listed if script_member(n.replace('\\', '/')))
            except (ValueError, OSError, RuntimeError, struct.error) as error:
                self.warn(f'{source}: cannot enumerate scripts: {error}')
            if len(names) > MAX_DOCUMENTS:
                raise ValueError('Too many script members in archive.')
            for name in sorted(names):
                check_cancel(self.cancel)
                try:
                    relative_name(name)
                    data = archive.read_file(name.replace('/', '\\'))
                    if data is not None:
                        self.members.add(name.casefold())
                        self.document(name, data, source + '/' + name)
                except (ValueError, OSError, RuntimeError, struct.error, ET.ParseError) as error:
                    self.warn(f'{source}/{name}: {error}')
                if self.documents > MAX_DOCUMENTS or self.bytes > MAX_TEXT:
                    return


def scan_package(payload, item, cancel=None):
    scan = Scan(item, cancel)
    containers = set()
    for file in item['files']:
        parts = file['destination'].replace('\\', '/').split('/')
        for index, part in enumerate(parts):
            if part.casefold().endswith(('.sc2map', '.sc2mod')):
                containers.add('/'.join(parts[:index + 1]).casefold())
                break
    for file in item['files']:
        check_cancel(cancel)
        name = file['source']
        if not (script_member(name) or name.casefold().endswith(('.sc2map', '.sc2mod'))):
            continue
        try:
            path = contained_path(payload, name)
            if file_hash(path, cancel) != file['sha256']:
                raise ValueError('Imported source has changed; reimport before scanning.')
            if name.casefold().endswith(('.sc2map', '.sc2mod')):
                scan.archive(path, name)
            else:
                if path.stat().st_size > MAX_MEMBER:
                    raise ValueError('Script exceeds the 4 MiB scanner limit.')
                scan.members.add(name.casefold())
                scan.document(name, path.read_bytes(), name)
        except (ValueError, OSError, RuntimeError, struct.error, ET.ParseError) as error:
            scan.warn(f'{name}: {error}')
        if scan.documents > MAX_DOCUMENTS or scan.bytes > MAX_TEXT:
            break
    for dependency in sorted(scan.dependencies):
        match = re.search(r'(?:^|,)file:(.+)', dependency)
        if not match or match[1].replace('\\', '/').casefold() not in containers:
            scan.warn('Dependency not scanned outside this package: ' + dependency)
    for include in sorted(scan.includes):
        name = include if include.lower().endswith('.galaxy') else include + '.galaxy'
        if not any(member == name.casefold() or member.endswith('/' + name.casefold()) for member in scan.members):
            scan.warn('Script include not found in this package: ' + include)
    scan.report.update(references=sorted(scan.banks.values(), key=lambda b: b['name'].casefold()),
                       warnings=sorted(scan.warnings), documents=scan.documents)
    check_cancel(cancel)
    return scan.report
