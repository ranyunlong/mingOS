"""Linux-only read-only developer pack byte verifier; no package code is loaded.

Prototype schema, not a qualification/export format or a boot/build tool.
Origin receipts are byte-bound claims, not independently replayed build proof.
"""
import argparse
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import stat
import sys

MANIFEST = 'developer-manifest.json'
PROFILE = 'developer-vm-v1'
ROLES = frozenset(('host', 'applications', 'probe', 'guest', 'launcher', 'kernel', 'runtimeTools'))
HEX = re.compile('[0-9a-f]{64}\\Z')
MAX_JSON, MAX_FILE, MAX_TOTAL = 1024 ** 2, 16 * 1024 ** 3, 32 * 1024 ** 3
MAX_MEMBERS, MAX_DEPTH = 4096, 24


def require(condition, message):
    if not condition:
        raise ValueError('Developer pack: ' + message)


def relative(name):
    require(isinstance(name, str) and 0 < len(name) <= 1024
            and '\\' not in name and ':' not in name and all(ord(c) >= 32 for c in name), 'unsafe path')
    parts = name.split('/')
    require(all(part not in ('', '.', '..') and len(part) <= 240 for part in parts)
            and PurePosixPath(name).as_posix() == name, 'noncanonical path')
    return name


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid, info.st_gid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def invalid(value):
        require(False, 'non-finite JSON number')
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
        pending = [(value, 0)]
        while pending:
            item, depth = pending.pop()
            require(depth <= 64, 'JSON nesting limit exceeded')
            if type(item) is dict:
                pending.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                pending.extend((child, depth + 1) for child in item)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError('Developer pack: invalid JSON') from error


def open_root(path):
    text = os.fspath(path)
    require(isinstance(text, str) and text.startswith('/') and text != '/'
            and text == '/' + relative(text[1:]), 'canonical absolute directory path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in text[1:].split('/'):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_file(directory, name, *, limit=None, data=False):
    if limit is None:
        limit = MAX_FILE
    before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    require(stat.S_ISREG(before.st_mode), 'regular file required; links/special files forbidden')
    require(before.st_nlink == 1, 'single-link regular file required')
    require(before.st_size <= limit, 'file byte limit exceeded')
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        require(stamp(os.fstat(fd)) == stamp(before), 'file changed before read')
        digest, size, chunks = hashlib.sha256(), 0, []
        while True:
            chunk = os.read(fd, min(1024 ** 2, limit + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            require(size <= before.st_size and size <= limit, 'file changed/grown during read')
            digest.update(chunk)
            if data:
                chunks.append(chunk)
        require(size == before.st_size and stamp(os.fstat(fd)) == stamp(before)
                and stamp(os.stat(name, dir_fd=directory, follow_symlinks=False)) == stamp(before),
                'file changed during read')
        record = {'sha256': digest.hexdigest(), 'bytes': size, 'mode': stat.S_IMODE(before.st_mode)}
        return (record, b''.join(chunks)) if data else record
    finally:
        os.close(fd)


def inventory(root_fd):
    files, directories, snapshots, total = {}, {}, {}, 0
    def visit(fd, prefix, depth):
        nonlocal total
        require(depth <= MAX_DEPTH, 'directory depth limit exceeded')
        before = os.fstat(fd)
        names = sorted(os.listdir(fd))
        require(len(names) <= MAX_MEMBERS, 'directory member limit exceeded')
        snapshots[prefix[:-1]] = (stamp(before), tuple(names))
        for leaf in names:
            name = relative(prefix + leaf)
            require(len(files) + len(directories) < MAX_MEMBERS, 'member limit exceeded')
            info = os.stat(leaf, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    require(stamp(os.fstat(child)) == stamp(info), 'directory changed before scan')
                    directories[name] = {'mode': stat.S_IMODE(info.st_mode)}
                    visit(child, name + '/', depth + 1)
                    require(stamp(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) == stamp(info),
                            'directory changed during scan')
                finally:
                    os.close(child)
            else:
                files[name] = read_file(fd, leaf)
                require(stamp(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) == stamp(info),
                        'file changed during inventory')
                snapshots[name] = (stamp(info), None)
                total += files[name]['bytes']
                require(total <= MAX_TOTAL, 'total byte limit exceeded')
        require(stamp(os.fstat(fd)) == stamp(before), 'directory changed during scan')
    visit(root_fd, '', 0)
    return files, directories, snapshots


def confirm_inventory(root_fd, snapshots):
    """Reopen every member without following links and compare the hashed snapshot.

    This observes changes; it is not an atomic snapshot against a malicious host.
    The caller must provide immutable input, mounted read-only for formal VM use.
    """
    seen = set()
    def visit(fd, prefix):
        name = prefix[:-1]
        expected, members = snapshots[name]
        require(stamp(os.fstat(fd)) == expected, 'directory identity/stamp changed after inventory')
        names = sorted(os.listdir(fd))
        require(tuple(names) == members, 'directory members changed after inventory')
        seen.add(name)
        for leaf in names:
            path = relative(prefix + leaf)
            require(path in snapshots, 'member changed after inventory')
            entry_stamp, child_members = snapshots[path]
            require(stamp(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) == entry_stamp,
                    'member identity/stamp changed after inventory')
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if child_members is not None:
                flags |= os.O_DIRECTORY
            child = os.open(leaf, flags, dir_fd=fd)
            try:
                require(stamp(os.fstat(child)) == entry_stamp, 'opened member identity/stamp changed')
                if child_members is not None:
                    visit(child, path + '/')
                else:
                    require(stat.S_ISREG(os.fstat(child).st_mode)
                            and os.fstat(child).st_nlink == 1, 'regular single-link member changed')
                    seen.add(path)
                require(stamp(os.fstat(child)) == entry_stamp
                        and stamp(os.stat(leaf, dir_fd=fd, follow_symlinks=False)) == entry_stamp,
                        'member changed during final scan')
            finally:
                os.close(child)
        require(stamp(os.fstat(fd)) == expected and tuple(sorted(os.listdir(fd))) == members,
                'directory changed during final scan')
    visit(root_fd, '')
    require(seen == set(snapshots), 'complete member set changed after inventory')


def validate_schema(value):
    require(type(value) is dict and set(value) == {'schemaVersion', 'profile', 'purpose', 'algorithm',
            'files', 'directories', 'origins', 'trust', 'formalQualification', 'finalSystemAccepted'}
            and type(value['schemaVersion']) is int and value['schemaVersion'] == 1,
            'closed manifest schema differs')
    require(value['profile'] == PROFILE and value['purpose'] == 'developer-preview-input-only'
            and value['algorithm'] == 'sha256', 'closed dev-only profile differs')
    require(value['formalQualification'] is False and value['finalSystemAccepted'] is False,
            'acceptance flags must be false')
    require(type(value['trust']) is dict and value['trust'] == {
            'purpose': 'fixture-development-only', 'publisherKeyId': 'mingos-dev-2026', 'releaseSigning': False}
            and value['trust']['releaseSigning'] is False, 'fixture-only trust schema differs')
    files, directories, origins = value['files'], value['directories'], value['origins']
    require(type(files) is dict and 1 <= len(files) <= MAX_MEMBERS
            and type(directories) is dict and len(files) + len(directories) + 1 <= MAX_MEMBERS,
            'file/directory record count differs')
    for name, record in files.items():
        relative(name)
        require(name != MANIFEST and type(record) is dict
                and set(record) == {'sha256', 'bytes', 'mode', 'origin'}
                and isinstance(record['sha256'], str) and HEX.fullmatch(record['sha256'])
                and type(record['bytes']) is int and 0 <= record['bytes'] <= MAX_FILE
                and type(record['mode']) is int and record['mode'] in (0o600, 0o644, 0o700, 0o755),
                'file record differs')
        require(isinstance(record['origin'], str) and record['origin'] in ROLES, 'file origin differs')
    for name, record in directories.items():
        relative(name)
        require(name not in files and type(record) is dict and set(record) == {'mode'}
                and type(record['mode']) is int and record['mode'] in (0o700, 0o755), 'directory record differs')
    require(type(origins) is dict and set(origins) == ROLES
            and {record['origin'] for record in files.values()} == ROLES, 'closed origin roles differ')
    for role, record in origins.items():
        require(type(record) is dict, 'origin record differs')
        if role in ('kernel', 'runtimeTools'):
            require(set(record) == {'kind', 'imageId', 'receipt'} and record['kind'] == 'tool-image'
                    and isinstance(record['imageId'], str) and record['imageId'].startswith('sha256:')
                    and HEX.fullmatch(record['imageId'][7:]), 'tool image origin differs')
            references = (record['receipt'],)
        else:
            require(set(record) == {'kind', 'sourceManifest', 'buildReceipt'}
                    and record['kind'] == 'source-build', 'source origin differs')
            references = (record['sourceManifest'], record['buildReceipt'])
        for name in references:
            relative(name)
            require(name in files, 'origin reference is not a sealed file')
    return value


def verify_pack(pack, manifest_sha256):
    require(isinstance(manifest_sha256, str) and HEX.fullmatch(manifest_sha256),
            'exact lowercase manifest SHA256 required')
    require(sys.platform == 'linux', 'Linux descriptor/no-follow semantics required')
    fd = None
    try:
        fd = open_root(pack)
        root_stamp = stamp(os.fstat(fd))
        observed, raw = read_file(fd, MANIFEST, limit=MAX_JSON, data=True)
        require(observed['sha256'] == manifest_sha256, 'manifest SHA256 differs from external pin')
        require(observed['mode'] == 0o644, 'closed manifest mode must be 0644')
        value = validate_schema(strict_json(raw))
        files, directories, snapshots = inventory(fd)
        require(files.pop(MANIFEST, None) == observed, 'manifest changed during inventory')
        expected = {name: {key: record[key] for key in ('sha256', 'bytes', 'mode')}
                    for name, record in value['files'].items()}
        require(files == expected, 'complete file inventory differs')
        require(directories == value['directories'], 'complete directory inventory differs')
        final_record, final_raw = read_file(fd, MANIFEST, limit=MAX_JSON, data=True)
        require(final_record == observed and final_raw == raw and stamp(os.fstat(fd)) == root_stamp,
                'manifest/root changed during verification')
        reopened = open_root(pack)
        try:
            require(stamp(os.fstat(reopened)) == root_stamp, 'canonical root changed during verification')
            confirm_inventory(reopened, snapshots)
        finally:
            os.close(reopened)
        return {'schemaVersion': 1, 'status': 'developer-pack-bytes-verified', 'profile': PROFILE,
                'manifestSha256': manifest_sha256, 'fileCount': len(files), 'directoryCount': len(directories),
                'origins': value['origins'], 'originClaimsByteBound': True,
                'trustPurpose': value['trust']['purpose'], 'publisherAuthenticated': False,
                'guestBooted': False, 'sourceBuildReplayed': False,
                'formalQualification': False, 'finalSystemAccepted': False}
    except OSError as error:
        raise ValueError('Developer pack: unsafe/inaccessible directory, link or file: ' + str(error)) from error
    finally:
        if fd is not None:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('verify',))
    parser.add_argument('--pack', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_pack(args.pack, args.manifest_sha256), sort_keys=True, allow_nan=False))
    except (ValueError, RecursionError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
