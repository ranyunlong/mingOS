"""Linux POSIX storage observations, not formal build or guest qualification.

Callers supply a dedicated empty 0700 root. Keep storage_session open while a
cooperative stage writes; probe/reopen release their lease before returning.
Receipts must be frozen outside this root by the coordinating driver.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys

MINIMUM_FREE_BYTES = 2 * 1024 ** 3
CHILD_TIMEOUT_SECONDS = 5
UID = GID = 65534


class StorageError(ValueError):
    pass


def _identity(info):
    return {'dev': info.st_dev, 'ino': info.st_ino, 'uid': info.st_uid,
            'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}


def _validate(info, directory=False):
    wanted_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (not wanted_type(info.st_mode) or info.st_uid != UID or info.st_gid != GID
            or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
            or (not directory and info.st_nlink != 1)):
        raise StorageError('wrong type, owner, mode or link count')


def _open_root(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise StorageError('absolute root without parent traversal required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                              | os.O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        _validate(os.fstat(fd), directory=True)
        return fd
    except (OSError, StorageError) as error:
        os.close(fd)
        raise StorageError('unsafe storage root') from error


def _space(fd, required):
    if type(required) is not int or required < MINIMUM_FREE_BYTES:
        raise StorageError('required bytes must include at least 2GiB reserve')
    usage = os.fstatvfs(fd)
    available = usage.f_bavail * usage.f_frsize
    if available < required:
        raise StorageError('insufficient available bytes')
    return {'availableBytes': available, 'requiredBytes': required,
            'blockSize': usage.f_frsize, 'filesystemId': usage.f_fsid}


def _open_file(fd, name, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        child = os.open(name, flags, 0o600, dir_fd=fd)
        try:
            _validate(os.fstat(child))
        except BaseException:
            os.close(child)
            raise
        return child
    except OSError as error:
        raise StorageError('unsafe storage file: ' + name) from error


def _read_marker(fd):
    marker = _open_file(fd, 'marker')
    try:
        content = os.read(marker, 4097)
        if len(content) != 32:
            raise StorageError('marker content length changed')
        return dict(_identity(os.fstat(marker)), sha256=hashlib.sha256(content).hexdigest())
    finally:
        os.close(marker)


def _write_all(fd, content):
    while content:
        written = os.write(fd, content)
        if not written:
            raise StorageError('zero length write')
        content = content[written:]
    os.fsync(fd)


def _check_child(path, blocked):
    code = ('import fcntl,os,sys; '
            'f=os.open(sys.argv[1],os.O_RDWR|os.O_NOFOLLOW); '
            '\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
            '\nexcept BlockingIOError: sys.exit(23)')
    child = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(path / 'lease')],
                           capture_output=True, timeout=CHILD_TIMEOUT_SECONDS, check=False)
    if child.returncode != (23 if blocked else 0):
        raise StorageError('real child lock result differs: ' + repr(child.stderr))


class StorageSession:
    def __init__(self, path, fd, lease):
        self.path, self.fd, self.lease = Path(path), fd, lease
        self.identity = _identity(os.fstat(fd))

    def check_identity(self):
        current = _open_root(self.path)
        try:
            if _identity(os.fstat(current)) != self.identity:
                raise StorageError('storage directory replaced')
            expected_lease = _identity(os.fstat(self.lease))
            _validate(os.stat('lease', dir_fd=current, follow_symlinks=False))
            if _identity(os.stat('lease', dir_fd=current, follow_symlinks=False)) != expected_lease:
                raise StorageError('lease replaced')
        finally:
            os.close(current)


@contextlib.contextmanager
def storage_session(path, receipt, required_bytes):
    """Hold the cooperative lease; reject existing objects before yielding."""
    if os.geteuid() != UID or os.getegid() != GID or sys.platform != 'linux':
        raise StorageError('Linux UID65534/GID65534 required')
    fd = _open_root(path)
    lease = None
    try:
        _space(fd, required_bytes)
        if receipt is None:
            if os.listdir(fd):
                raise StorageError('fresh storage root is not empty')
        else:
            if (receipt.get('schemaVersion') != 1 or receipt.get('root') != _identity(os.fstat(fd))
                    or set(os.listdir(fd)) != {'marker', 'lease'}):
                raise StorageError('root identity or inventory differs')
            if _read_marker(fd) != receipt.get('marker'):
                raise StorageError('marker identity or content differs')
        lease = _open_file(fd, 'lease', create=receipt is None)
        if receipt is not None and _identity(os.fstat(lease)) != receipt.get('lease'):
            raise StorageError('lease identity differs')
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StorageError('extra cooperative writer holds lease') from error
        session = StorageSession(path, fd, lease)
        session.check_identity()
        if receipt is not None and _read_marker(fd) != receipt.get('marker'):
            raise StorageError('marker changed before lease acquisition')
        yield session
        session.check_identity()
        if receipt is not None and _read_marker(fd) != receipt.get('marker'):
            raise StorageError('marker changed during storage session')
    finally:
        if lease is not None:
            os.close(lease)
        os.close(fd)


def _observe(path, receipt, required_bytes):
    path = Path(path)
    with storage_session(path, receipt, required_bytes) as session:
        fd = session.fd
        if receipt is None:
            marker = _open_file(fd, 'pending-marker', create=True)
            try:
                _write_all(marker, secrets.token_bytes(32))
            finally:
                os.close(marker)
            os.rename('pending-marker', 'marker', src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        # Prove deletion and parent synchronization on each invocation too.
        temporary = _open_file(fd, 'delete-probe', create=True)
        try:
            _write_all(temporary, b'fsync-delete-probe')
        finally:
            os.close(temporary)
        os.rename('delete-probe', 'delete-ready', src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        os.unlink('delete-ready', dir_fd=fd)
        os.fsync(fd)
        _check_child(path, blocked=True)
        result = {'schemaVersion': 1, 'path': str(path), 'root': session.identity,
                  'marker': _read_marker(fd), 'lease': _identity(os.fstat(session.lease)),
                  'space': _space(fd, required_bytes), 'lockCompetitorBlocked': True,
                  'operations': ['create', 'write', 'file-fsync', 'rename',
                                 'directory-fsync', 'delete', 'directory-fsync'],
                  'finalSystemAccepted': False}
    _check_child(path, blocked=False)
    result['lockAvailableAfterRelease'] = True
    return result


def probe(path, required_bytes):
    return _observe(path, None, required_bytes)


def reopen(path, receipt, required_bytes):
    return _observe(path, receipt, required_bytes)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('probe', 'reopen'))
    parser.add_argument('--root', required=True)
    parser.add_argument('--receipt', help='external receipt JSON, required by reopen')
    parser.add_argument('--required-bytes', type=int, required=True)
    arguments = parser.parse_args()
    if arguments.stage == 'reopen':
        if not arguments.receipt:
            parser.error('reopen requires external --receipt')
        prior = json.loads(Path(arguments.receipt).read_text())
        observed = reopen(arguments.root, prior, arguments.required_bytes)
    else:
        observed = probe(arguments.root, arguments.required_bytes)
    print(json.dumps(observed, sort_keys=True))
