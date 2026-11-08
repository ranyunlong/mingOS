"""Linux payload seals, with a separate effective read-only replay gate.

This is an inventory primitive, not compiler/provenance acceptance. The caller
supplies an independently trusted seal digest and checks its expected context.
Failed writes remain in place. No function removes or retries an output.
"""
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat

CHUNK = 1024 * 1024
MAX_SEAL_BYTES = 64 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _linux():
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
        raise ValueError('Linux no-follow descriptor semantics required')


def _relative(name):
    if (type(name) is not str or not name or name.startswith('/') or '\\' in name or ':' in name
            or any(ord(c) < 32 or ord(c) == 127 or 0xd800 <= ord(c) <= 0xdfff for c in name)
            or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in name.split('/'))
            or PurePosixPath(name).as_posix() != name):
        raise ValueError('canonical relative POSIX name required')
    for part in name.split('/'):
        stem = part.split('.', 1)[0].upper()
        if stem in ('CON', 'PRN', 'AUX', 'NUL') or re.fullmatch(r'(COM|LPT)[1-9]', stem):
            raise ValueError('reserved path alias forbidden')
    return name


def _absolute(path):
    _linux()
    text = os.fspath(path)
    if (not isinstance(text, str) or not text.startswith('/') or text.startswith('//')
            or text != Path(text).as_posix() or text == '/'):
        raise ValueError('canonical absolute Linux path required')
    _relative(text[1:])
    return Path(text)


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_absolute(path, *, directory=False):
    """Walk every ancestor through anchored O_NOFOLLOW directory descriptors."""
    path = _absolute(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, name in enumerate(path.parts[1:]):
            is_dir = index < len(path.parts) - 2 or directory
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
            if is_dir:
                flags |= os.O_DIRECTORY
            child = os.open(name, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


def _same_path(path, fd, *, directory=False):
    other = _open_absolute(path, directory=directory)
    try:
        if _stamp(os.fstat(other)) != _stamp(os.fstat(fd)):
            raise ValueError('path/descriptor identity changed')
    finally:
        os.close(other)


def _regular(info):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('single-link regular file required; links/specials forbidden')


def _hash_fd(fd):
    before = os.fstat(fd)
    _regular(before)
    digest = hashlib.sha256()
    size = 0
    while True:
        block = os.read(fd, CHUNK)
        if not block:
            break
        size += len(block)
        if size > before.st_size:
            raise ValueError('file grew during hashing')
        digest.update(block)
    if size != before.st_size or _stamp(os.fstat(fd)) != _stamp(before):
        raise ValueError('file mutated during hashing')
    return {'mode': stat.S_IMODE(before.st_mode), 'bytes': size, 'sha256': digest.hexdigest()}


def file_pin(path):
    """Observe a canonical single-link file without following any path links."""
    path = _absolute(path)
    fd = _open_absolute(path)
    try:
        result = _hash_fd(fd)
        _same_path(path, fd)
        return {'path': str(path), **result}
    finally:
        os.close(fd)


def _inventory(root, *, capture_identity=False, read_only=False):
    root = _absolute(root)
    root_fd = _open_absolute(root, directory=True)
    directories, files, identities = {}, {}, {}
    try:
        root_before = os.fstat(root_fd)
        identities[''] = _stamp(root_before)
        def require_readonly(fd, name):
            if read_only and not os.fstatvfs(fd).f_flag & os.ST_RDONLY:
                raise ValueError('payload-member ' + name + ' must be effectively read-only')
        def visit(fd, prefix):
            require_readonly(fd, prefix or '.')
            before = os.fstat(fd)
            names = sorted(os.listdir(fd))
            aliases = set()
            for name in names:
                _relative(name)
                if '/' in name or name.casefold() in aliases:
                    raise ValueError('duplicate/aliased directory member')
                aliases.add(name.casefold())
                relative = prefix + name
                entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
                identities[relative] = _stamp(entry)
                if stat.S_ISDIR(entry.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                    try:
                        if _stamp(os.fstat(child)) != _stamp(entry):
                            raise ValueError('directory identity changed before open')
                        directories[relative] = {'mode': stat.S_IMODE(entry.st_mode)}
                        visit(child, relative + '/')
                        if _stamp(os.fstat(child)) != _stamp(entry):
                            raise ValueError('directory mutated during traversal')
                    finally:
                        os.close(child)
                else:
                    _regular(entry)
                    child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=fd)
                    try:
                        if _stamp(os.fstat(child)) != _stamp(entry):
                            raise ValueError('file identity changed before open')
                        require_readonly(child, relative)
                        files[relative] = _hash_fd(child)
                    finally:
                        os.close(child)
                if _stamp(os.stat(name, dir_fd=fd, follow_symlinks=False)) != _stamp(entry):
                    raise ValueError('directory entry changed during traversal')
            if sorted(os.listdir(fd)) != names or _stamp(os.fstat(fd)) != _stamp(before):
                raise ValueError('directory membership/identity mutated')
        visit(root_fd, '')
        if _stamp(os.fstat(root_fd)) != _stamp(root_before):
            raise ValueError('payload root mutated')
        _same_path(root, root_fd, directory=True)
        inventory = {'rootMode': stat.S_IMODE(root_before.st_mode),
                     'directories': dict(sorted(directories.items())), 'files': dict(sorted(files.items()))}
        return (inventory, identities) if capture_identity else inventory
    finally:
        os.close(root_fd)


def _json_value(value):
    if value is None or type(value) in (str, bool, int):
        if isinstance(value, str):
            value.encode('utf8', errors='strict')
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key:
                raise ValueError('nonempty JSON object keys required')
            key.encode('utf8', errors='strict')
            _json_value(item)
        return
    raise ValueError('finite JSON provenance values required')


def _canonical(value):
    _json_value(value)
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                       allow_nan=False) + '\n').encode('ascii')


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('closed seal object required')


def _integer(value, maximum=None):
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise ValueError('nonnegative exact integer required')


def _schema(value):
    _object(value, ('schemaVersion', 'kind', 'context', 'rootMode', 'directories', 'files'))
    if type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or value['kind'] != 'p01-s3-payload-seal':
        raise ValueError('seal schema identity differs')
    if type(value['context']) is not dict:
        raise ValueError('seal provenance context object required')
    _json_value(value['context'])
    _integer(value['rootMode'], 0o7777)
    directories, files = value['directories'], value['files']
    if type(directories) is not dict or type(files) is not dict:
        raise ValueError('directory/file maps required')
    aliases = set()
    for name in (*directories, *files):
        _relative(name)
        if name.casefold() in aliases:
            raise ValueError('overlapping/aliased seal entries')
        aliases.add(name.casefold())
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and str(parent) not in directories:
                raise ValueError('complete parent directory closure required')
    for record in directories.values():
        _object(record, ('mode',))
        _integer(record['mode'], 0o7777)
    for record in files.values():
        _object(record, ('mode', 'bytes', 'sha256'))
        _integer(record['mode'], 0o7777)
        _integer(record['bytes'])
        if type(record['sha256']) is not str or not _SHA.fullmatch(record['sha256']):
            raise ValueError('lowercase file SHA256 required')
    return value


def _siblings(root, output):
    root, output = _absolute(root), _absolute(output)
    if output.parent != root.parent or output == root:
        raise ValueError('seal must be an exclusive sibling of payload root')
    return root, output


def _write_exclusive(path, raw, mode):
    """Durably publish bytes, retaining the partial file on every failure."""
    path = _absolute(path)
    parent = _open_absolute(path.parent, directory=True)
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        try:
            view = memoryview(raw)
            while view:
                written = os.write(fd, view[:CHUNK])
                if written <= 0:
                    raise OSError('zero-progress output write')
                view = view[written:]
            os.fchmod(fd, mode)
            os.fsync(fd)
            if _stamp(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != _stamp(os.fstat(fd)):
                raise ValueError('output path/descriptor changed')
            _same_path(path, fd)
        finally:
            os.close(fd)
        os.fsync(parent)
        _same_path(path.parent, parent, directory=True)
    finally:
        os.close(parent)


def seal(root: Path, *, context: dict, output: Path) -> dict:
    root, output = _siblings(root, output)
    if type(context) is not dict:
        raise ValueError('seal context object required')
    # Roundtrip freezes caller-owned nested provenance before any filesystem work.
    frozen_context = json.loads(_canonical(context))
    inventory, identities = _inventory(root, capture_identity=True)
    if _inventory(root, capture_identity=True) != (inventory, identities):
        raise ValueError('payload changed between seal observations')
    value = _schema({'schemaVersion': 1, 'kind': 'p01-s3-payload-seal',
                     'context': frozen_context, **inventory})
    raw = _canonical(value)
    if len(raw) > MAX_SEAL_BYTES:
        raise ValueError('seal metadata exceeds bound')
    _write_exclusive(output, raw, 0o444)
    # Output durability is not authority if payload changed while publishing it.
    if _inventory(root, capture_identity=True) != (inventory, identities):
        raise ValueError('payload changed while publishing seal; output retained')
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _read_seal(path, digest):
    if type(digest) is not str or not _SHA.fullmatch(digest):
        raise ValueError('external lowercase seal SHA256 required')
    fd = _open_absolute(path)
    try:
        before = os.fstat(fd)
        _regular(before)
        if before.st_size > MAX_SEAL_BYTES:
            raise ValueError('seal metadata exceeds bound')
        raw = bytearray()
        while True:
            block = os.read(fd, min(CHUNK, MAX_SEAL_BYTES + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            if len(raw) > MAX_SEAL_BYTES:
                raise ValueError('seal metadata exceeds bound')
        if len(raw) != before.st_size or _stamp(os.fstat(fd)) != _stamp(before):
            raise ValueError('seal changed while reading')
        _same_path(path, fd)
    finally:
        os.close(fd)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('external seal/context pin differs')
    value = json.loads(raw, object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
    _schema(value)
    if _canonical(value) != raw:
        raise ValueError('canonical seal JSON required')
    return value


def _verify_contents(root, seal_path, digest, *, read_only=False):
    """Internal byte/membership check only; never an effective RO verdict."""
    root, seal_path = _siblings(root, seal_path)
    value = _read_seal(seal_path, digest)
    expected = {key: value[key] for key in ('rootMode', 'directories', 'files')}
    first = _inventory(root, capture_identity=True, read_only=read_only)
    if first[0] != expected or _inventory(root, capture_identity=True, read_only=read_only) != first:
        raise ValueError('payload directory/file/mode/byte inventory differs')
    if _read_seal(seal_path, digest) != value:
        raise ValueError('seal changed during verification')
    return value


def verify(root: Path, seal: Path, seal_sha256: str) -> dict:
    root, seal = _siblings(root, seal)
    verifier = _absolute(Path(__file__).parent)
    opened = []
    try:
        for role, path, is_directory in [('payload', root, True), ('seal', seal, False),
                                         ('verifier', verifier, True),
                                         ('verifier-file', _absolute(Path(__file__)), False)]:
            fd = _open_absolute(path, directory=is_directory)
            opened.append((path, fd, is_directory))
            if not is_directory:
                _regular(os.fstat(fd))
            if not os.fstatvfs(fd).f_flag & os.ST_RDONLY:
                raise ValueError(role + ' must be effectively read-only')
        value = _verify_contents(root, seal, seal_sha256, read_only=True)
        # Actual module file and its root are checked separately from payload bytes.
        file_pin(_absolute(Path(__file__)))
        for path, fd, directory in opened:
            _same_path(path, fd, directory=directory)
            if not os.fstatvfs(fd).f_flag & os.ST_RDONLY:
                raise ValueError('replay mount became writable')
        return value
    finally:
        for _, fd, _ in reversed(opened):
            os.close(fd)
