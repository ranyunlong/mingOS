"""Strict Linux handoff import and read-only reopen; integrity claims only."""
import argparse
from contextlib import contextmanager
import hashlib
import os
import pathlib
import stat
import sys
import tarfile

import current_source as c
import handoff_manifest as h

MAX_MEMBERS = 4096
MAX_FILE = 16 * 1024 ** 3
MAX_MANIFEST = 8 * 1024 ** 2
MAX_TOTAL = 32 * 1024 ** 3
RECEIPT = 'import-restore-result.json'
require = c.require


def _absolute(path):
    path = pathlib.Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute no-link path required')
    return path


def _admit(archive_path, archive_pin, handoff_pin, destination, source_pin=None):
    c.digest(archive_pin)
    c.digest(handoff_pin)
    if source_pin is not None:
        c.digest(source_pin)
    archive_path, destination = _absolute(archive_path), _absolute(destination)
    require(sys.platform == 'linux', 'Linux descriptor import/reopen required')
    return archive_path, destination


def _hash(source):
    source.seek(0)
    digest = hashlib.sha256()
    while raw := source.read(1024 ** 2):
        digest.update(raw)
    return digest.hexdigest()


def _stable(source, path, stamp, pin):
    require(c.stamp(os.fstat(source.fileno())) == stamp, 'archive identity changed')
    require(_hash(source) == pin, 'external archive pin differs or archive changed')
    require(c.stamp(os.fstat(source.fileno())) == stamp, 'archive changed while hashing')
    c.no_links(path)
    require(c.stamp(path.lstat()) == stamp, 'archive path identity changed')


@contextmanager
def _archive(path, pin):
    parent = c.open_directory(path.parent)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'single-link regular archive required')
        stamp = c.stamp(info)
        _stable(source, path, stamp, pin)
        yield source
        _stable(source, path, stamp, pin)


def _mkdir(path, mode=0o755):
    parent = c.open_directory(path.parent)
    try:
        os.mkdir(path.name, mode, dir_fd=parent)
        fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(parent)
    finally:
        os.close(parent)


def _fresh(path):
    require(not os.path.lexists(path), 'exclusive absent output required')
    fd = c.open_directory(path.parent)
    os.close(fd)


def _allowlist(tar, handoff_pin):
    seen = {}
    total = 0
    for member in tar:
        require(len(seen) < MAX_MEMBERS, 'archive member cap exceeded')
        c.relative(member.name)
        require(member.name == 'delivery' or member.name.startswith('delivery/'), 'archive root differs')
        require(member.name not in seen, 'duplicate archive path')
        require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE), 'nonregular archive member')
        require(member.sparse is None and not set(member.pax_headers) - {'path', 'mtime', 'atime', 'ctime'},
                'unsupported PAX/sparse metadata')
        require(not member.linkname, 'archive link metadata forbidden')
        require(0 <= member.size <= MAX_FILE and (not member.isdir() or member.size == 0), 'archive size differs')
        total += member.size
        require(total <= MAX_TOTAL, 'total extraction byte cap exceeded')
        seen[member.name] = member
    manifest = seen.get('delivery/handoff-manifest.json')
    require(manifest is not None and manifest.isreg() and manifest.size <= MAX_MANIFEST,
            'bounded handoff manifest required')
    with tar.extractfile(manifest) as incoming:
        raw = incoming.read(MAX_MANIFEST + 1)
    require(len(raw) == manifest.size and c.sha(raw) == handoff_pin, 'external handoff pin differs')
    value = h.validate(c.strict.strict_json(raw))
    expected = {component['root'] + '/' + name: row
                for component in value['components'].values() for name, row in component['files'].items()}
    expected['handoff-manifest.json'] = {'sha256': handoff_pin, 'bytes': len(raw), 'mode': 0o644}
    directories = c.closure(expected)
    require(sum(row['bytes'] for row in expected.values()) <= MAX_TOTAL, 'manifest total byte cap exceeded')
    require(set(seen) == {'delivery'} | {'delivery/' + name for name in set(expected) | set(directories)},
            'complete archive allowlist differs')
    for name, row in expected.items():
        member = seen['delivery/' + name]
        require(member.isreg() and member.size == row['bytes'] and member.mode == row['mode'],
                'archive file type/size/mode differs')
    for name in [''] + directories:
        member = seen['delivery' + ('/' + name if name else '')]
        require(member.isdir() and member.mode in (0o700, 0o755), 'archive directory type/mode differs')
    return seen, expected, directories, raw


def _extract(source, handoff_pin, destination):
    source.seek(0)
    with tarfile.open(fileobj=source, mode='r:') as tar:
        seen, expected, directories, raw = _allowlist(tar, handoff_pin)
        _mkdir(destination, seen['delivery'].mode)
        for name in sorted(directories, key=lambda name: (name.count('/'), name)):
            _mkdir(destination / name, seen['delivery/' + name].mode)
        for name, row in expected.items():
            path = destination / name
            parent = c.open_directory(path.parent)
            try:
                fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
                try:
                    digest = hashlib.sha256()
                    remaining = row['bytes']
                    with tar.extractfile(seen['delivery/' + name]) as incoming:
                        while remaining:
                            raw_chunk = incoming.read(min(remaining, 1024 ** 2))
                            require(raw_chunk, 'truncated archive member')
                            remaining -= len(raw_chunk)
                            digest.update(raw_chunk)
                            while raw_chunk:
                                count = os.write(fd, raw_chunk)
                                require(count > 0, 'extraction write stalled')
                                raw_chunk = raw_chunk[count:]
                    require(digest.hexdigest() == row['sha256'], 'extracted member digest differs')
                    os.fchmod(fd, row['mode'])
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(parent)
            finally:
                os.close(parent)
    return h.verify(destination, raw, handoff_pin)


def restore(archive_path, archive_pin, handoff_pin, destination):
    archive_path, destination = _admit(archive_path, archive_pin, handoff_pin, destination)
    _fresh(destination)
    with _archive(archive_path, archive_pin) as source:
        result = _extract(source, handoff_pin, destination)
    return result


def _verified_source(delivery, source_pin):
    incoming = delivery / 'current-source'
    descriptor_raw = (incoming / 'source-descriptor.json').read_bytes()
    with _archive(incoming / 'source.tar', c.strict.strict_json(descriptor_raw)['archiveSha256']) as source:
        verified = c.verify(source, descriptor_raw, source_pin)
    require((incoming / 'source-inventory.json').read_bytes() == c.encoded(verified['manifest']),
            'standalone source inventory differs')
    return verified, descriptor_raw


def _complete_source(root, verified):
    manifest = verified['manifest']
    observed = h.scan_component(root)
    expected = {name: {key: row[key] for key in ('sha256', 'bytes', 'mode')}
                for name, row in manifest['files'].items()}
    require(observed == {'files': expected, 'directories': manifest['directories']},
            'complete restored source paths/bytes/modes differ')
    require(c.source_inventory(root, manifest['files']) == manifest['files'], 'restored source inventory differs')
    require(all(stat.S_IMODE((root / name).lstat().st_mode) == 0o755 for name in manifest['directories']),
            'restored source directory modes differ')


def _source_result(verified):
    return {'status': 'current-source-exclusively-restored-and-verified',
            'fileCount': len(verified['manifest']['files']),
            'repositoryCommit': verified['manifest']['repositoryCommit'], **c.FLAGS}


def _receipt(archive_pin, handoff_pin, source_pin, imported, verified):
    return {'status': 'handoff-imported-and-current-source-restored', 'archiveSha256': archive_pin,
            'handoffSha256': handoff_pin, 'sourceDescriptorSha256': source_pin,
            'importVerification': imported, 'sourceRestore': _source_result(verified), **c.FLAGS}


def _root_set(root):
    fd = c.open_directory(root)
    try:
        require(set(os.listdir(fd)) == {'delivery', 'source-repo', RECEIPT}, 'complete output root differs')
        for name in ('delivery', 'source-repo'):
            require(stat.S_ISDIR(os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode), 'output directory required')
        info = os.stat(RECEIPT, dir_fd=fd, follow_symlinks=False)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o644,
                'single-link receipt mode differs')
    finally:
        os.close(fd)


def import_restore(archive_path, archive_pin, handoff_pin, source_descriptor_pin, output_directory):
    archive_path, output = _admit(archive_path, archive_pin, handoff_pin, output_directory, source_descriptor_pin)
    _fresh(output)
    _mkdir(output)
    with _archive(archive_path, archive_pin) as source:
        imported = _extract(source, handoff_pin, output / 'delivery')
        verified, descriptor_raw = _verified_source(output / 'delivery', source_descriptor_pin)
        with (output / 'delivery/current-source/source.tar').open('rb') as incoming:
            restored = c.restore(incoming, descriptor_raw, source_descriptor_pin, output / 'source-repo')
        require(c.encoded(restored) == c.encoded(_source_result(verified)), 'source restore result differs')
        verified_after, _ = _verified_source(output / 'delivery', source_descriptor_pin)
        require(verified_after == verified, 'source changed during restore')
        _complete_source(output / 'source-repo', verified)
        imported_after = h.verify(output / 'delivery', (output / 'delivery/handoff-manifest.json').read_bytes(), handoff_pin)
        require(imported_after == imported, 'delivery changed during restore')
        result = _receipt(archive_pin, handoff_pin, source_descriptor_pin, imported, verified)
    # Create the receipt atomically after the final external archive identity check.
    raw = c.encoded(result)
    parent = c.open_directory(output)
    try:
        fd = os.open('.receipt-pending', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, 'wb') as receipt:
            receipt.write(raw)
            receipt.flush()
            os.fchmod(receipt.fileno(), 0o644)
            os.fsync(receipt.fileno())
        # link is exclusive even if another process creates the final path meanwhile.
        os.link('.receipt-pending', RECEIPT, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.unlink('.receipt-pending', dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
    _root_set(output)
    return result


def _readonly(path):
    c.no_links(path)
    require(os.statvfs(path).f_flag & os.ST_RDONLY, 'read-only archive/imported/verifier filesystem required')


def reopen(archive_path, archive_pin, handoff_pin, source_descriptor_pin, imported_directory):
    archive_path, root = _admit(archive_path, archive_pin, handoff_pin, imported_directory, source_descriptor_pin)
    for path in (archive_path, root, pathlib.Path(__file__).parent):
        _readonly(path)
    _root_set(root)
    with _archive(archive_path, archive_pin):
        imported = h.verify(root / 'delivery', (root / 'delivery/handoff-manifest.json').read_bytes(), handoff_pin)
        verified, _ = _verified_source(root / 'delivery', source_descriptor_pin)
        _complete_source(root / 'source-repo', verified)
        expected = _receipt(archive_pin, handoff_pin, source_descriptor_pin, imported, verified)
        require((root / RECEIPT).read_bytes() == c.encoded(expected), 'canonical recomputed import receipt differs')
        _root_set(root)
    return {'status': 'handoff-and-source-readonly-reopened', 'archiveSha256': archive_pin,
            'handoffSha256': handoff_pin, 'sourceDescriptorSha256': source_descriptor_pin, **c.FLAGS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('import-restore', 'reopen'):
        command = sub.add_parser(action)
        command.add_argument('--archive', required=True)
        command.add_argument('--archive-sha256', required=True)
        command.add_argument('--handoff-sha256', required=True)
        command.add_argument('--source-descriptor-sha256', required=True)
        command.add_argument('--output-directory' if action == 'import-restore' else '--imported-directory', required=True)
    args = parser.parse_args()
    try:
        function = import_restore if args.action == 'import-restore' else reopen
        destination = args.output_directory if args.action == 'import-restore' else args.imported_directory
        result = function(args.archive, args.archive_sha256, args.handoff_sha256,
                          args.source_descriptor_sha256, destination)
    except (ValueError, OSError, tarfile.TarError) as error:
        parser.exit(1, str(error) + '\n')
    print(c.encoded(result).decode(), end='')


if __name__ == '__main__':
    main()
