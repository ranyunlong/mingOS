#!/usr/bin/env python3
"""Snapshot source into fresh Linux volumes; imports and --help have no effects.

This root initializer copies only the qualification source inventory, preserves
empty source directories and projects two verified Kotlin packages. It never
downloads dependencies, runs a build, formats a disk, or starts a guest.
"""
import argparse
import os
from pathlib import Path
import shutil
import sys

import linux_host_qualification as q


def require_empty_volumes(paths):
    for path in map(Path, paths):
        if path.is_symlink() or not path.is_dir() or list(path.iterdir()):
            raise ValueError('new generation volume must be literal empty: ' + str(path))


def source_directories(source):
    result = []
    for current, directories, _files in os.walk(source):
        directories[:] = sorted(name for name in directories if name not in q.EXCLUDED)
        result.append(Path(current).relative_to(source))
    return result


def copy_source(source, destination):
    source, destination = Path(source), Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    before = q.source_inventory(source)
    directories = source_directories(source)
    destination.mkdir(mode=0o755)
    for relative in directories:
        (destination / relative).mkdir(mode=0o755, parents=True, exist_ok=True)
    for relative in before:
        incoming, outgoing = source / relative, destination / relative
        with incoming.open('rb') as raw, outgoing.open('xb') as copied:
            shutil.copyfileobj(raw, copied)
            copied.flush()
            os.fsync(copied.fileno())
        outgoing.chmod(0o755 if incoming.stat().st_mode & 0o111 else 0o644)
    if (q.source_inventory(destination) != before or q.source_inventory(source) != before
            or source_directories(source) != directories or source_directories(destination) != directories):
        raise ValueError('source snapshot file/directory set or bytes differ')
    return before


def initialize_volume(path):
    # Root has CAP_CHOWN only. Once ownership becomes uid65534/mode0700,
    # reopening the directory would require an intentionally absent capability.
    fd = q.open_directory(path)
    try:
        os.fchmod(fd, 0o700)
        os.fchown(fd, 65534, 65534)
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(source, source_volume, prep, target, packages):
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Linux root initializer required')
    source, source_volume, prep, target, packages = map(q.checked_directory,
        map(Path, (source, source_volume, prep, target, packages)))
    if len({source, source_volume, prep, target, packages}) != 5:
        raise ValueError('distinct source, package and generation paths required')
    require_empty_volumes((source_volume, prep, target))
    for path in (source, packages):
        if not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise ValueError('read-only source and verified package mounts required')
    before = copy_source(source, source_volume / 'repo')
    destination = source_volume / 'repo'
    projected = {}
    for label in ('read-file', 'image-viewer'):
        incoming, outgoing = packages / label, destination / 'artifacts' / label
        expected = q.tree_inventory(incoming)
        if not expected:
            raise ValueError('verified package projection is empty: ' + label)
        shutil.copytree(incoming, outgoing)
        if q.tree_inventory(incoming) != expected or q.tree_inventory(outgoing) != expected:
            raise ValueError('package projection bytes differ: ' + label)
        projected[label] = expected
    q.verify_source(source, before)
    q.verify_source(destination, before)
    target_empty = list(target.iterdir()) == []
    if not target_empty or list(prep.iterdir()):
        raise ValueError('generation target/prep changed during source snapshot')
    for path in (prep, target):
        initialize_volume(path)
    space = os.statvfs(prep)
    result = {'schemaVersion': 1, 'files': before, 'sourceFiles': len(before),
        'sourceBytes': sum(value['bytes'] for value in before.values()),
        'linuxAvailableBytes': space.f_bavail * space.f_frsize,
        'literalEmptyTarget': target_empty, 'emptyDirectoriesPreserved': True,
        'packageProjections': ['read-file', 'image-viewer'], 'packageProjectionFiles': projected,
        'formalQualification': False, 'finalSystemAccepted': False}
    q.write_json(source_volume / 'snapshot.json', result)
    q.sync_directory(destination)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/checkout'))
    parser.add_argument('--source-volume', type=Path, default=Path('/source'))
    parser.add_argument('--prep', type=Path, default=Path('/prep'))
    parser.add_argument('--target', type=Path, default=Path('/target'))
    parser.add_argument('--packages', type=Path, default=Path('/packages'))
    args = parser.parse_args()
    result = snapshot(args.source, args.source_volume, args.prep, args.target, args.packages)
    print('source-snapshot-created', result['sourceFiles'])


if __name__ == '__main__':
    main()
