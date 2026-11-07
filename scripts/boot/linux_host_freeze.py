#!/usr/bin/env python3
"""Prepare a new Linux-host source freeze from actual admitted inputs.

This module cannot start a guest. Its prepare result admits the build input
set only; a release build and paired boot have their own acceptance stages.
"""
import argparse
import os
from pathlib import Path, PureWindowsPath
import stat
import sys

import linux_host_qualification as q
import linux_host_storage as storage

RUST_IMAGE = 'sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80'
LABELS = ('read-file', 'image-viewer', 'text-entry-demo', 'old', 'new', 'absent-write')


def capacity_budget():
    gib = 1024 ** 3
    # Additional available capacity required before a cold build. Existing
    # allocated images/caches do not consume the same free bytes a second time.
    return {'coldReleaseTargetBytes': 12 * gib, 'sourceAndKotlinOutputsBytes': 2 * gib,
            'dependencyGrowthBytes': 2 * gib, 'imageAndEvidenceExportsBytes': 8 * gib,
            'bundleBytes': 2 * gib, 'guestDiskAndLogsBytes': gib // 2,
            'failedGenerationPreservationBytes': 4 * gib, 'reserveBytes': 2 * gib}


def validate_capacity(value):
    required = sum(capacity_budget().values())
    for key in ('hostAvailableBytes', 'linuxAvailableBytes'):
        if type(value.get(key)) is not int or value[key] < required:
            raise ValueError('insufficient or unknown capacity: ' + key)
    disk = value.get('backingDiskPath')
    if (not isinstance(disk, str) or not PureWindowsPath(disk).is_absolute()
            or PureWindowsPath(disk).suffix.lower() != '.vhdx'
            or not isinstance(value.get('hostFilesystem'), str) or not value['hostFilesystem']):
        raise ValueError('actual Windows Docker backing disk/filesystem observation required')


def validate_image_receipt(value, expected):
    if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict)
            or value[0].get('Id') != expected or not value[0].get('RootFS', {}).get('Layers')):
        raise ValueError('one exact image inspection with immutable layers required')


def package_path(root, name):
    if (not isinstance(name, str) or name.startswith('/') or '\\' in name
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('canonical relative package path required')
    path = Path(root) / name
    q.checked_directory(path)
    if not path.is_relative_to(root):
        raise ValueError('package escapes root')
    return path


def validate_reopen(first, second):
    if (first.get('schemaVersion') != 1 or second.get('schemaVersion') != 1
            or any(first.get(key) != second.get(key) for key in ('root', 'marker', 'lease'))
            or any(value.get(flag) is not True for value in (first, second)
                   for flag in ('lockCompetitorBlocked', 'lockAvailableAfterRelease'))):
        raise ValueError('independent storage reopen does not bind original objects')


def readonly_input(path):
    path = Path(path)
    if path.resolve() != path or not path.is_absolute():
        raise ValueError('canonical read-only input path required')
    info = q.stable_hash(path)
    if not os.statvfs(path).f_flag & os.ST_RDONLY:
        raise ValueError('input receipt must be mounted read-only: ' + str(path))
    return info


def admit_storage(root, reopen=False):
    root = q.checked_directory(root)
    info = root.lstat()
    if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (65534, 65534, 0o700):
        raise ValueError('prep root UID65534/GID65534 mode0700 required')
    control = root / 'storage-control'
    required = sum(capacity_budget().values())
    if reopen:
        q.write_json(root / 'storage-reopen-attempt.json', {'startedAt': q.now()})
        first = q.read_json(root / 'storage-probe.json')
        second = storage.reopen(control, first, required)
        validate_reopen(first, second)
        q.write_json(root / 'storage-reopen.json', second)
        return second
    control.mkdir(mode=0o700)
    q.sync_directory(root)
    result = storage.probe(control, required)
    q.write_json(root / 'storage-probe.json', result)
    return result


def _prepare(root, repo, cargo_home, packages_root, image_receipt, capacity_receipt,
             node, node_sha256, package_paths=None, toolchain_receipt=None):
    root, repo, cargo_home, packages_root = map(q.checked_directory, (root, repo, cargo_home, packages_root))
    environment = q.observe_environment(repo)
    q.validate_environment(environment)
    paths = package_paths or {label: label for label in LABELS}
    if set(paths) != set(LABELS) or len(set(paths.values())) != len(LABELS):
        raise ValueError('six distinct real Kotlin packages required')
    first = q.read_json(root / 'storage-probe.json')
    second = q.read_json(root / 'storage-reopen.json')
    validate_reopen(first, second)
    capacity_hash = readonly_input(capacity_receipt)
    capacity = q.read_json(capacity_receipt)
    linux = os.statvfs(root)
    # Use the current Linux measurement, retaining the older one as evidence.
    admitted_capacity = dict(capacity, linuxAvailableBytes=linux.f_bavail * linux.f_frsize)
    validate_capacity(admitted_capacity)
    image_hash = readonly_input(image_receipt)
    validate_image_receipt(q.read_json(image_receipt), RUST_IMAGE)
    if readonly_input(node)['sha256'] != node_sha256:
        raise ValueError('pinned independent replay Node executable changed')
    toolchain = None
    if toolchain_receipt is not None:
        toolchain = {'receipt': str(toolchain_receipt),
                     'sha256': readonly_input(toolchain_receipt)['sha256']}
    if not os.statvfs(packages_root).f_flag & os.ST_RDONLY:
        raise ValueError('Kotlin packages must be mounted read-only')
    if not os.statvfs(cargo_home / 'registry').f_flag & os.ST_RDONLY:
        raise ValueError('Cargo registry must be mounted read-only')
    if (root / 'freeze.json').exists() or (root / 'replay').exists():
        raise FileExistsError('freeze already attempted; preserve generation')
    required = sum(capacity_budget().values())
    with storage.storage_session(root / 'storage-control', first, required):
        replay = root / 'replay'
        replay.mkdir(mode=0o700)
        q.sync_directory(root)
        before = q.source_inventory(repo)
        packages = {}
        for label in LABELS:
            directory = package_path(packages_root, paths[label])
            files = q.tree_inventory(directory)
            result = q.execute(replay / label,
                [str(node), str(repo / 'scripts/boot/build-linux-host-kotlin.mjs'), '--verify',
                 '--repo', str(repo), '--directory', str(directory)], 60, cwd=repo)
            if result['exitCode'] != 0:
                raise ValueError('real Kotlin source/package replay failed: ' + label)
            if q.tree_inventory(directory) != files:
                raise ValueError('Kotlin package changed during replay')
            packages[label] = {'path': paths[label], 'files': files}
        q.verify_source(repo, before)
        freeze = {'schemaVersion': 1, 'profile': 'persistent-update-linux-host',
                  'source': {'files': before}, 'packages': packages,
                  'cargo': {'files': q.tree_inventory(cargo_home / 'registry')},
                  'cargoLock': q.stable_hash(repo / 'Cargo.lock'),
                  'environment': {'observed': environment, 'rustImage': {
                      'id': RUST_IMAGE, 'inspectionReceipt': str(image_receipt),
                      'inspectionReceiptSha256': image_hash['sha256']},
                      'replayNode': {'path': str(node), 'sha256': node_sha256}},
                  'storage': {'probe': first, 'reopen': second,
                      'capacityReceipt': str(capacity_receipt), 'capacityReceiptSha256': capacity_hash['sha256'],
                      'capacityObserved': admitted_capacity, 'budget': capacity_budget()},
                  'packageReplayFiles': q.tree_inventory(replay),
                  'preparedAt': q.now(), 'finalSystemAccepted': False}
        if toolchain is not None:
            freeze['environment']['toolchain'] = toolchain
        q.write_json(root / 'freeze.json', freeze)
        digest = q.stable_hash(root / 'freeze.json')['sha256']
        def seal(directory):
            q.verify_source(repo, before)
            q.write_json(directory / 'admission.json', {'freezeSha256': digest,
                         'packageReplayFiles': freeze['packageReplayFiles'],
                         'storage': freeze['storage'], 'formalQualification': False})
            return {'sourceFileCount': len(before), 'realKotlinPackages': list(LABELS),
                    'buildAccepted': False, 'guestStarted': False}
        q.run_stage(root, 'prepare', digest, seal)
        return freeze


def prepare(root, repo, cargo_home, packages_root, image_receipt, capacity_receipt,
            node, node_sha256, package_paths=None, toolchain_receipt=None):
    root = q.checked_directory(root)
    q.write_json(root / 'prepare-attempt.json', {'startedAt': q.now(), 'finalSystemAccepted': False})
    try:
        freeze = _prepare(root, repo, cargo_home, packages_root, image_receipt, capacity_receipt,
                          node, node_sha256, package_paths, toolchain_receipt)
    except Exception as error:
        q.write_json(root / 'prepare-result.json', {'status': 'failed', 'error': str(error),
                     'finishedAt': q.now(), 'finalSystemAccepted': False})
        raise
    q.write_json(root / 'prepare-result.json', {'status': 'passed', 'finishedAt': q.now(),
                 'freezeSha256': q.stable_hash(root / 'freeze.json')['sha256'],
                 'formalQualification': False, 'finalSystemAccepted': False})
    return freeze


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('storage-probe', 'storage-reopen', 'prepare'), required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--cargo-home', type=Path)
    parser.add_argument('--packages-root', type=Path)
    parser.add_argument('--image-receipt', type=Path)
    parser.add_argument('--capacity-receipt', type=Path)
    parser.add_argument('--node', type=Path)
    parser.add_argument('--node-sha256')
    parser.add_argument('--toolchain-receipt', type=Path)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Linux required')
    if args.stage != 'prepare':
        admit_storage(args.root, reopen=args.stage == 'storage-reopen')
    else:
        if any(getattr(args, name) is None for name in ('repo', 'cargo_home', 'packages_root', 'image_receipt', 'capacity_receipt', 'node', 'node_sha256')):
            parser.error('prepare requires explicit frozen source, dependency, image, capacity and replay inputs')
        prepare(args.root, args.repo, args.cargo_home, args.packages_root,
                args.image_receipt, args.capacity_receipt, args.node, args.node_sha256,
                toolchain_receipt=args.toolchain_receipt)


if __name__ == '__main__':
    main()
