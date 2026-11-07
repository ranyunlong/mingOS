"""Strict raw USTAR qualification-cache archive verifier.

Import and all functions are pure with respect to Docker and target storage.
No extractall, filesystem writes, subprocesses, or resource deletion exists.
"""
import hashlib
import json
import re

MANIFEST_NAME = 'restore-manifest.json'
UID = GID = 65534
MAX_ARCHIVE = 64 * 1024 ** 3
MAX_FILE = 32 * 1024 ** 3
MAX_MANIFEST = 16 * 1024 ** 2
MAX_ENTRIES = 100000
HEX = re.compile(r'[0-9a-f]{64}\Z')
FILE_MODES = (0o600, 0o644, 0o700, 0o755)
DIRECTORY_MODES = (0o700, 0o755)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    if len(raw) > MAX_MANIFEST:
        raise ValueError('manifest byte limit exceeded')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid manifest JSON') from error


def safe_path(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > 255
            or '\\' in value or ':' in value
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('unsafe archive path')
    return value


def _integer(value, label, minimum=0, maximum=MAX_FILE):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('invalid ' + label)
    return value


def validate_manifest(value):
    if (not isinstance(value, dict)
            or set(value) != {'schemaVersion', 'uid', 'gid', 'entries'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or type(value['uid']) is not int or value['uid'] != UID
            or type(value['gid']) is not int or value['gid'] != GID
            or not isinstance(value['entries'], list)
            or not 1 <= len(value['entries']) <= MAX_ENTRIES):
        raise ValueError('strict volume manifest schema required')
    result = {}
    for entry in value['entries']:
        if not isinstance(entry, dict):
            raise ValueError('invalid manifest entry')
        kind = entry.get('type')
        fields = {'path', 'type', 'uid', 'gid', 'mode'}
        if kind == 'file':
            fields |= {'size', 'sha256', 'nlink'}
        if kind not in ('file', 'directory') or set(entry) != fields:
            raise ValueError('manifest supports only regular singlelink files and directories')
        name = safe_path(entry['path'])
        if name == MANIFEST_NAME or name.startswith(MANIFEST_NAME + '/') or name in result:
            raise ValueError('reserved or duplicate manifest path')
        if (type(entry['uid']) is not int or entry['uid'] != UID
                or type(entry['gid']) is not int or entry['gid'] != GID):
            raise ValueError('manifest owner must be UID/GID65534')
        mode = _integer(entry['mode'], 'manifest mode', 0, 0o7777)
        if mode not in (FILE_MODES if kind == 'file' else DIRECTORY_MODES):
            raise ValueError('manifest mode has forbidden permission bits')
        if kind == 'file':
            _integer(entry['size'], 'manifest size')
            if (type(entry['nlink']) is not int or entry['nlink'] != 1
                    or not isinstance(entry['sha256'], str) or not HEX.fullmatch(entry['sha256'])):
                raise ValueError('regular singlelink file digest required')
        result[name] = dict(entry)
    for name in result:
        parts = name.split('/')
        for index in range(1, len(parts)):
            parent = result.get('/'.join(parts[:index]))
            if parent is None or parent['type'] != 'directory':
                raise ValueError('every parent must be an explicit directory')
    return result


def _cstring(raw, label):
    content, separator, tail = raw.partition(b'\0')
    if separator and tail.strip(b'\0'):
        raise ValueError('nonzero data after ' + label + ' terminator')
    try:
        return content.decode('utf-8')
    except UnicodeError as error:
        raise ValueError('invalid UTF-8 ' + label) from error


def _octal(raw, label):
    if raw[0] & 0x80:
        raise ValueError('GNU/base256 numeric field forbidden')
    value = raw.rstrip(b'\0 ').lstrip(b' ')
    if not value or any(char not in b'01234567' for char in value):
        raise ValueError('invalid octal ' + label)
    return int(value, 8)


def _header(raw):
    if raw[257:263] != b'ustar\0' or raw[263:265] != b'00':
        raise ValueError('strict USTAR magic/version required')
    checksum = sum(raw[:148]) + 8 * 32 + sum(raw[156:])
    if _octal(raw[148:156], 'checksum') != checksum:
        raise ValueError('tar checksum mismatch')
    kind = raw[156:157]
    if kind not in (b'0', b'5'):
        raise ValueError('links/devices/GNU/PAX/extensions forbidden')
    if raw[157:257].strip(b'\0') or raw[500:].strip(b'\0'):
        raise ValueError('unexpected USTAR link/reserved field')
    # Numeric device fields are zero for regular files/directories. Empty
    # zero-filled fields are accepted, never a nonzero device specification.
    for start in (329, 337):
        field = raw[start:start + 8]
        if field.strip(b'\0 ') and _octal(field, 'device') != 0:
            raise ValueError('USTAR device fields must be zero')
    _cstring(raw[265:297], 'uname')
    _cstring(raw[297:329], 'gname')
    name = _cstring(raw[:100], 'name')
    prefix = _cstring(raw[345:500], 'prefix')
    if not name:
        raise ValueError('USTAR name field must not be empty')
    if prefix:
        name = prefix + '/' + name
    if kind == b'5' and name.endswith('/'):
        name = name[:-1]
    name = safe_path(name)
    mode = _octal(raw[100:108], 'mode')
    uid, gid = _octal(raw[108:116], 'uid'), _octal(raw[116:124], 'gid')
    size = _octal(raw[124:136], 'size')
    _octal(raw[136:148], 'mtime')
    if uid != UID or gid != GID:
        raise ValueError('archive owner must be UID/GID65534')
    if mode not in (FILE_MODES if kind == b'0' else DIRECTORY_MODES):
        raise ValueError('archive mode has forbidden permission bits')
    if size > MAX_FILE or (kind == b'5' and size):
        raise ValueError('invalid archive member size')
    return {'path': name, 'type': 'file' if kind == b'0' else 'directory',
            'mode': mode, 'uid': uid, 'gid': gid, 'size': size}


def _hash(source):
    source.seek(0)
    digest, count = hashlib.sha256(), 0
    while True:
        chunk = source.read(1024 ** 2)
        if not chunk:
            source.seek(0)
            return digest.hexdigest(), count
        count += len(chunk)
        if count > MAX_ARCHIVE:
            raise ValueError('archive byte limit exceeded')
        digest.update(chunk)


def _read_exact(source, size):
    chunks = []
    while size:
        raw = source.read(size)
        if not raw:
            raise ValueError('truncated tar data')
        chunks.append(raw)
        size -= len(raw)
    return b''.join(chunks)


def verify_volume_archive(source, expected_sha256):
    """Return a complete byte-bound plan without extracting or calling Docker.

    Input must be a seekable binary stream of uncompressed strict USTAR bytes.
    Numeric offsets refer only to that exact already-opened stream. A worker
    must freshly validate its private copied archive rather than trusting a
    host-produced plan or reopening any original pathname.
    """
    if not isinstance(expected_sha256, str) or not HEX.fullmatch(expected_sha256):
        raise ValueError('exact lowercase archive SHA-256 required')
    before, archive_bytes = _hash(source)
    if before != expected_sha256:
        raise ValueError('archive SHA-256 differs')
    if archive_bytes % 512:
        raise ValueError('tar archive must be block aligned')
    members, manifest, count = {}, None, 0
    while True:
        raw = _read_exact(source, 512)
        if raw == b'\0' * 512:
            if _read_exact(source, 512) != b'\0' * 512:
                raise ValueError('two tar end markers required')
            while True:
                tail = source.read(1024 ** 2)
                if not tail:
                    break
                if source.tell() > archive_bytes:
                    raise ValueError('archive grew during validation')
                if tail.strip(b'\0'):
                    raise ValueError('nonzero trailing archive data')
            break
        member = _header(raw)
        name, size = member['path'], member['size']
        if name in members:
            raise ValueError('duplicate archive member')
        count += 1
        if count > MAX_ENTRIES + 1:
            raise ValueError('archive member limit exceeded')
        member['offset'] = source.tell()
        digest = hashlib.sha256()
        remaining, manifest_raw = size, bytearray()
        if name == MANIFEST_NAME and (member['type'] != 'file' or size > MAX_MANIFEST):
            raise ValueError('manifest must be bounded regular file')
        while remaining:
            chunk = _read_exact(source, min(remaining, 1024 ** 2))
            remaining -= len(chunk)
            digest.update(chunk)
            if name == MANIFEST_NAME:
                manifest_raw.extend(chunk)
        if _read_exact(source, (-size) % 512).strip(b'\0'):
            raise ValueError('nonzero tar member padding')
        member['sha256'] = digest.hexdigest()
        members[name] = member
        if name == MANIFEST_NAME:
            manifest = strict_json(bytes(manifest_raw))
    expected = validate_manifest(manifest)
    if set(members) != set(expected) | {MANIFEST_NAME}:
        raise ValueError('manifest/archive inventory differs')
    plan = []
    for name, entry in sorted(expected.items()):
        observed = members[name]
        for field in ('type', 'uid', 'gid', 'mode'):
            if observed[field] != entry[field]:
                raise ValueError('manifest metadata differs: ' + name)
        if entry['type'] == 'file' and (observed['size'] != entry['size'] or observed['sha256'] != entry['sha256']):
            raise ValueError('manifest file bytes differ: ' + name)
        plan.append(dict(entry, offset=observed['offset']))
    if _hash(source) != (before, archive_bytes):
        raise ValueError('archive changed during validation')
    return {'schemaVersion': 1, 'archiveSha256': before, 'archiveBytes': archive_bytes,
            'manifestSha256': members[MANIFEST_NAME]['sha256'], 'uid': UID, 'gid': GID,
            'entries': plan, 'payloadBytes': sum(e.get('size', 0) for e in plan)}
