"""Linux descriptor worker; no Docker invocation and no resource removal.

Restore creates an exclusive private archive copy and payload in a fresh owned
volume. Independent verification requires an actually read-only mount and a
receipt supplied externally by the coordinator. Importing is side effect free.
"""
import argparse
from contextlib import ExitStack, contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

try:
    import fcntl
except ImportError:
    fcntl = None

import qualification_volume_archive as archive

UID = GID = 65534
NONCE = re.compile(r'[0-9a-f]{32}\Z')
MAX_RECEIPT = 64 * 1024 ** 2
SPACE_RESERVE = 64 * 1024 ** 2
CONTROL_NAMES = {'archive-privatecopy.tar', 'restore.lease', 'restore-complete.json'}


class WorkerError(ValueError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


def _linux():
    if (sys.platform != 'linux' or fcntl is None
            or os.geteuid() != UID or os.getegid() != GID):
        raise ValueError('Linux UID/GID65534 worker required')


def _identity(info):
    return {'dev': info.st_dev, 'ino': info.st_ino, 'uid': info.st_uid,
            'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}


def _stamp(info):
    return (_identity(info), info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _validate(info, directory=False, mode=None):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != UID or info.st_gid != GID
            or (not directory and info.st_nlink != 1)
            or (mode is not None and stat.S_IMODE(info.st_mode) != mode)):
        raise ValueError('wrong type, UID/GID, mode or singlelink count')


@contextmanager
def _fd(value):
    try:
        yield value
    finally:
        os.close(value)


def _directory(parent, name, mode=None):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    try:
        _validate(os.fstat(fd), directory=True, mode=mode)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _root(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or str(path) != str(path.absolute()):
        raise ValueError('literal absolute restore root required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = child
        _validate(os.fstat(fd), directory=True, mode=0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_file(parent, name, create=False, readonly=True, mode=0o600):
    flags = (os.O_RDONLY if readonly else os.O_WRONLY) | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    fd = os.open(name, flags, 0o600, dir_fd=parent)
    try:
        _validate(os.fstat(fd), mode=mode)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write_all(fd, raw):
    view = memoryview(raw)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise ValueError('zero-length target write')
        view = view[count:]


def _hash_fd(fd, limit):
    os.lseek(fd, 0, os.SEEK_SET)
    digest, total = hashlib.sha256(), 0
    while True:
        raw = os.read(fd, 1024 ** 2)
        if not raw:
            os.lseek(fd, 0, os.SEEK_SET)
            return digest.hexdigest(), total
        total += len(raw)
        if total > limit:
            raise ValueError('file exceeds byte limit')
        digest.update(raw)


def _copy_archive(control, source):
    digest, count = hashlib.sha256(), 0
    with _fd(_open_file(control, 'archive-privatecopy.tar', create=True, readonly=False)) as fd:
        try:
            while True:
                raw = source.read(1024 ** 2)
                if not raw:
                    break
                if not isinstance(raw, bytes):
                    raise ValueError('binary archive stream required')
                count += len(raw)
                if count > archive.MAX_ARCHIVE:
                    raise ValueError('archive byte limit exceeded')
                _write_all(fd, raw)
                digest.update(raw)
            os.fsync(fd)
            _validate(os.fstat(fd), mode=0o600)
        finally:
            # Preserve partial data durably even if validation or writes fail.
            os.fsync(fd)
    os.fsync(control)
    return digest.hexdigest(), count


def _check_link(parent, name, fd):
    observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if _identity(observed) != _identity(os.fstat(fd)) or observed.st_nlink != os.fstat(fd).st_nlink:
        raise ValueError('opened storage object was replaced: ' + name)


def _check_roots(path, root, payload, control, original):
    with _fd(_root(path)) as fresh:
        if _identity(os.fstat(fresh)) != original['root'] or _identity(os.fstat(root)) != original['root']:
            raise ValueError('restore root was replaced')
        if set(os.listdir(fresh)) != {'payload', 'control'}:
            raise ValueError('unexpected restore root inventory')
        for name, fd in (('payload', payload), ('control', control)):
            _check_link(fresh, name, fd)
            if _identity(os.fstat(fd)) != original[name]:
                raise ValueError('restore child directory was replaced')


def _parent(payload, name, directories):
    fd = os.dup(payload)
    try:
        parts = name.split('/')
        current = []
        for part in parts[:-1]:
            current.append(part)
            child = _directory(fd, part)
            os.close(fd)
            fd = child
            key = '/'.join(current)
            if key not in directories or _identity(os.fstat(fd)) != directories[key]:
                raise ValueError('payload parent directory was replaced')
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def _scan_payload(payload, entries, expected_identities=None):
    expected = {entry['path']: entry for entry in entries}
    observed = {}
    def visit(fd, prefix):
        before = os.fstat(fd)
        names = sorted(os.listdir(fd))
        for name in names:
            archive.safe_path(name)
            key = prefix + name
            entry = expected.get(key)
            if entry is None:
                raise ValueError('unexpected payload entry: ' + key)
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            directory = entry['type'] == 'directory'
            _validate(info, directory=directory, mode=entry['mode'])
            if expected_identities is not None and _identity(info) != expected_identities.get(key):
                raise ValueError('payload identity differs: ' + key)
            if directory:
                with _fd(_directory(fd, name, mode=entry['mode'])) as child:
                    _check_link(fd, name, child)
                    visit(child, key + '/')
                    _check_link(fd, name, child)
                record = dict(entry, identity=_identity(info))
            else:
                with _fd(_open_file(fd, name, mode=entry['mode'])) as child:
                    _check_link(fd, name, child)
                    opened = os.fstat(child)
                    actual, count = _hash_fd(child, archive.MAX_FILE)
                    if count != entry['size'] or actual != entry['sha256'] or _stamp(os.fstat(child)) != _stamp(opened):
                        raise ValueError('payload bytes or identity changed: ' + key)
                    _check_link(fd, name, child)
                    record = dict(entry, identity=_identity(opened))
            observed[key] = record
        if sorted(os.listdir(fd)) != names or _stamp(os.fstat(fd)) != _stamp(before):
            raise ValueError('payload directory changed during scan')
    visit(payload, '')
    if set(observed) != set(expected):
        raise ValueError('missing payload entries')
    return [observed[name] for name in sorted(observed)]


def _json_bytes(value):
    raw = json.dumps(value, separators=(',', ':'), sort_keys=True, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_RECEIPT:
        raise ValueError('worker receipt byte limit exceeded')
    return raw


def _strict_receipt(raw):
    if len(raw) > MAX_RECEIPT:
        raise ValueError('worker receipt byte limit exceeded')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate receipt JSON key')
            result[key] = value
        return result
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite receipt JSON')))


def restore(root, source, archive_sha256, nonce):
    receipt = {'schemaVersion': 1, 'status': 'failed', 'phase': 'platform',
               'formalQualification': False, 'finalSystemAccepted': False,
               'root': str(root), 'nonce': nonce, 'archiveSha256': archive_sha256}
    try:
        _linux()
        if not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
            raise ValueError('32 lowercase hex restore nonce required')
        if not isinstance(archive_sha256, str) or not archive.HEX.fullmatch(archive_sha256):
            raise ValueError('exact archive SHA-256 required')
        with ExitStack() as stack:
            rootfd = stack.enter_context(_fd(_root(root)))
            payload = stack.enter_context(_fd(_directory(rootfd, 'payload', mode=0o700)))
            control = stack.enter_context(_fd(_directory(rootfd, 'control', mode=0o700)))
            original = {'root': _identity(os.fstat(rootfd)), 'payload': _identity(os.fstat(payload)),
                        'control': _identity(os.fstat(control))}
            _check_roots(root, rootfd, payload, control, original)
            if len({os.fstat(fd).st_dev for fd in (rootfd, payload, control)}) != 1:
                raise ValueError('one restore filesystem required')
            if os.listdir(payload) or os.listdir(control):
                raise ValueError('fresh payload and control directories must be literal empty')
            if os.fstatvfs(rootfd).f_flag & os.ST_RDONLY:
                raise ValueError('restore requires its dedicated writable volume')
            receipt['phase'] = 'private-copy'
            actual, count = _copy_archive(control, source)
            receipt['archiveBytes'] = count
            if actual != archive_sha256:
                raise ValueError('worker private archive SHA-256 differs')
            incoming = stack.enter_context(_fd(_open_file(control, 'archive-privatecopy.tar')))
            initial_archive = os.fstat(incoming)
            receipt['phase'] = 'archive-validation'
            with os.fdopen(incoming, 'rb', closefd=False) as opened:
                plan = archive.verify_volume_archive(opened, archive_sha256)
            _check_link(control, 'archive-privatecopy.tar', incoming)
            if _stamp(os.fstat(incoming)) != _stamp(initial_archive):
                raise ValueError('worker private archive changed')
            receipt['phase'] = 'lease'
            lease = stack.enter_context(_fd(_open_file(control, 'restore.lease', create=True, readonly=True)))
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.fsync(lease)
            os.fsync(control)
            lease_identity = _identity(os.fstat(lease))
            _check_roots(root, rootfd, payload, control, original)
            if os.listdir(payload):
                raise ValueError('payload changed before writer admission')
            space = os.fstatvfs(payload)
            required = plan['payloadBytes'] + len(plan['entries']) * 4096 + SPACE_RESERVE
            if space.f_bavail * space.f_frsize < required or (space.f_favail and space.f_favail < len(plan['entries']) + 8):
                raise ValueError('insufficient restore bytes or inodes')
            receipt['phase'] = 'payload-write'
            directories, identities = {}, {}
            for entry in sorted((e for e in plan['entries'] if e['type'] == 'directory'), key=lambda e: (e['path'].count('/'), e['path'])):
                parent, name = _parent(payload, entry['path'], directories)
                with _fd(parent):
                    os.mkdir(name, 0o700, dir_fd=parent)
                    with _fd(_directory(parent, name, mode=0o700)) as child:
                        directories[entry['path']] = _identity(os.fstat(child))
                        identities[entry['path']] = directories[entry['path']]
                        os.fsync(child)
                        _check_link(parent, name, child)
                    os.fsync(parent)
            for entry in (e for e in plan['entries'] if e['type'] == 'file'):
                parent, name = _parent(payload, entry['path'], directories)
                with _fd(parent), _fd(_open_file(parent, name, create=True, readonly=False)) as output:
                    digest, left = hashlib.sha256(), entry['size']
                    os.lseek(incoming, entry['offset'], os.SEEK_SET)
                    try:
                        while left:
                            raw = os.read(incoming, min(left, 1024 ** 2))
                            if not raw:
                                raise ValueError('truncated private archive during payload copy')
                            left -= len(raw)
                            digest.update(raw)
                            _write_all(output, raw)
                        if digest.hexdigest() != entry['sha256']:
                            raise ValueError('payload source bytes changed')
                        os.fchmod(output, entry['mode'])
                        os.fsync(output)
                        _validate(os.fstat(output), mode=entry['mode'])
                        if os.fstat(output).st_size != entry['size']:
                            raise ValueError('written payload file size differs')
                        _check_link(parent, name, output)
                        identities[entry['path']] = _identity(os.fstat(output))
                    finally:
                        os.fsync(output)
                    os.fsync(parent)
            for entry in sorted((e for e in plan['entries'] if e['type'] == 'directory'), key=lambda e: (-e['path'].count('/'), e['path'])):
                parent, name = _parent(payload, entry['path'], directories)
                with _fd(parent), _fd(_directory(parent, name, mode=0o700)) as child:
                    if _identity(os.fstat(child)) != directories[entry['path']]:
                        raise ValueError('new payload directory changed')
                    os.fchmod(child, entry['mode'])
                    os.fsync(child)
                    identities[entry['path']] = _identity(os.fstat(child))
                    _check_link(parent, name, child)
                    os.fsync(parent)
            os.fsync(payload)
            receipt['phase'] = 'payload-verification'
            if _hash_fd(incoming, archive.MAX_ARCHIVE) != (archive_sha256, plan['archiveBytes']):
                raise ValueError('private archive changed during restoration')
            _check_link(control, 'archive-privatecopy.tar', incoming)
            if _stamp(os.fstat(incoming)) != _stamp(initial_archive):
                raise ValueError('private archive identity changed during restoration')
            files = _scan_payload(payload, plan['entries'], identities)
            _check_roots(root, rootfd, payload, control, original)
            _check_link(control, 'restore.lease', lease)
            result = {'schemaVersion': 1, 'status': 'payload-restored', 'nonce': nonce,
                'formalQualification': False, 'finalSystemAccepted': False,
                'archiveSha256': archive_sha256, 'archiveBytes': plan['archiveBytes'],
                'embeddedManifestSha256': plan['manifestSha256'], 'payloadBytes': plan['payloadBytes'],
                'rootIdentity': original['root'], 'payloadIdentity': original['payload'],
                'controlIdentity': original['control'], 'leaseIdentity': lease_identity,
                'privateArchiveIdentity': _identity(initial_archive), 'files': files}
            receipt['phase'] = 'completion-receipt'
            raw = _json_bytes(result)
            with _fd(_open_file(control, 'restore-complete.json', create=True, readonly=False)) as completion:
                _write_all(completion, raw)
                os.fsync(completion)
                completion_identity = _identity(os.fstat(completion))
                _check_link(control, 'restore-complete.json', completion)
            os.fsync(control)
            os.fsync(rootfd)
            _check_roots(root, rootfd, payload, control, original)
            _check_link(control, 'restore.lease', lease)
            if set(os.listdir(control)) != CONTROL_NAMES:
                raise ValueError('control inventory changed')
            result['completionReceipt'] = {'sha256': hashlib.sha256(raw).hexdigest(),
                'bytes': len(raw), 'identity': completion_identity}
            return result
    except Exception as error:
        receipt['error'] = str(error)
        raise WorkerError(str(error), receipt) from error


def verify_readonly(root, expected_receipt):
    receipt = {'schemaVersion': 1, 'status': 'failed', 'phase': 'platform',
               'formalQualification': False, 'finalSystemAccepted': False, 'root': str(root)}
    try:
        _linux()
        if (not isinstance(expected_receipt, dict) or expected_receipt.get('status') != 'payload-restored'
                or expected_receipt.get('formalQualification') is not False
                or expected_receipt.get('finalSystemAccepted') is not False
                or not isinstance(expected_receipt.get('nonce'), str)
                or not NONCE.fullmatch(expected_receipt['nonce'])):
            raise ValueError('external completed restore receipt required')
        expected_core = dict(expected_receipt)
        completion = expected_core.pop('completionReceipt')
        with ExitStack() as stack:
            rootfd = stack.enter_context(_fd(_root(root)))
            payload = stack.enter_context(_fd(_directory(rootfd, 'payload', mode=0o700)))
            control = stack.enter_context(_fd(_directory(rootfd, 'control', mode=0o700)))
            original = {'root': expected_receipt['rootIdentity'], 'payload': expected_receipt['payloadIdentity'],
                        'control': expected_receipt['controlIdentity']}
            _check_roots(root, rootfd, payload, control, original)
            receipt['phase'] = 'read-only-mount'
            if any(not os.fstatvfs(fd).f_flag & os.ST_RDONLY for fd in (rootfd, payload, control)):
                raise ValueError('independent verifier requires an actual read-only filesystem mount')
            if len({os.fstat(fd).st_dev for fd in (rootfd, payload, control)}) != 1:
                raise ValueError('one readonly restore filesystem required')
            control_stamp = _stamp(os.fstat(control))
            payload_stamp = _stamp(os.fstat(payload))
            if set(os.listdir(control)) != CONTROL_NAMES:
                raise ValueError('readonly control inventory differs')
            receipt['phase'] = 'readonly-lease'
            lease = stack.enter_context(_fd(_open_file(control, 'restore.lease')))
            if _identity(os.fstat(lease)) != expected_receipt['leaseIdentity'] or os.fstat(lease).st_size != 0:
                raise ValueError('readonly lease identity differs')
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _check_link(control, 'restore.lease', lease)
            receipt['phase'] = 'external-receipt-binding'
            saved = stack.enter_context(_fd(_open_file(control, 'restore-complete.json')))
            if _identity(os.fstat(saved)) != completion['identity']:
                raise ValueError('completion receipt identity differs')
            saved_stamp = _stamp(os.fstat(saved))
            saved_hash, size = _hash_fd(saved, MAX_RECEIPT)
            if (saved_hash != completion['sha256'] or size != completion['bytes']
                    or _stamp(os.fstat(saved)) != saved_stamp):
                raise ValueError('completion receipt bytes differ')
            raw = bytearray()
            while len(raw) < size:
                chunk = os.read(saved, min(size - len(raw), 1024 ** 2))
                if not chunk:
                    raise ValueError('truncated completion receipt')
                raw.extend(chunk)
            if _strict_receipt(bytes(raw)) != expected_core:
                raise ValueError('volume receipt differs from external expected receipt')
            _check_link(control, 'restore-complete.json', saved)
            receipt['phase'] = 'readonly-archive-validation'
            incoming = stack.enter_context(_fd(_open_file(control, 'archive-privatecopy.tar')))
            initial_archive = os.fstat(incoming)
            if _identity(initial_archive) != expected_receipt['privateArchiveIdentity']:
                raise ValueError('private archive identity differs on readonly reopen')
            with os.fdopen(incoming, 'rb', closefd=False) as source:
                plan = archive.verify_volume_archive(source, expected_receipt['archiveSha256'])
            if (plan['archiveBytes'] != expected_receipt['archiveBytes']
                    or plan['manifestSha256'] != expected_receipt['embeddedManifestSha256']
                    or plan['payloadBytes'] != expected_receipt['payloadBytes']):
                raise ValueError('readonly manifest/archive receipt differs')
            receipt['phase'] = 'readonly-full-inventory'
            files = _scan_payload(payload, plan['entries'])
            if files != expected_receipt['files']:
                raise ValueError('readonly payload identities or bytes differ')
            _check_roots(root, rootfd, payload, control, original)
            _check_link(control, 'restore.lease', lease)
            _check_link(control, 'restore-complete.json', saved)
            _check_link(control, 'archive-privatecopy.tar', incoming)
            if (_stamp(os.fstat(incoming)) != _stamp(initial_archive)
                    or _stamp(os.fstat(saved)) != saved_stamp
                    or _stamp(os.fstat(control)) != control_stamp
                    or _stamp(os.fstat(payload)) != payload_stamp
                    or set(os.listdir(control)) != CONTROL_NAMES):
                raise ValueError('readonly source/control/payload changed during verification')
            return {'schemaVersion': 1, 'status': 'payload-readonly-verified',
                'formalQualification': False, 'finalSystemAccepted': False,
                'nonce': expected_receipt['nonce'], 'archiveSha256': plan['archiveSha256'],
                'archiveBytes': plan['archiveBytes'], 'payloadBytes': plan['payloadBytes'],
                'rootIdentity': original['root'], 'payloadIdentity': original['payload'],
                'controlIdentity': original['control'], 'leaseIdentity': expected_receipt['leaseIdentity'],
                'statvfsReadOnly': True, 'files': files}
    except Exception as error:
        receipt['error'] = str(error)
        raise WorkerError(str(error), receipt) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('restore', 'verify-readonly'))
    parser.add_argument('--root', type=Path, default=Path('/restore'))
    parser.add_argument('--archive-sha256')
    parser.add_argument('--nonce')
    args = parser.parse_args()
    try:
        if args.stage == 'restore':
            result = restore(args.root, sys.stdin.buffer, args.archive_sha256, args.nonce)
        else:
            result = verify_readonly(args.root, _strict_receipt(sys.stdin.buffer.read(MAX_RECEIPT + 1)))
    except WorkerError as error:
        print(json.dumps(error.receipt, separators=(',', ':')))
        print('QUALIFICATION_VOLUME_RESTORE_BLOCKED: ' + str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print(json.dumps({'schemaVersion': 1, 'status': 'failed', 'error': str(error),
            'formalQualification': False, 'finalSystemAccepted': False}, separators=(',', ':')))
        return 1
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
