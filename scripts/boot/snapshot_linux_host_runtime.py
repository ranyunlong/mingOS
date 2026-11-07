#!/usr/bin/env python3
"""Initialize runtime/output volumes or consume an explicitly reviewed bundle.

Import and --help do not modify files or start processes. Runtime assets must
already be independently verified. This helper never supplies review approval.
"""
import argparse
import os
from pathlib import Path
import shutil
import sys

import linux_host_qualification as q
from snapshot_linux_host_source import copy_source, initialize_volume, require_empty_volumes


def require_root():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Linux root initializer required')


def initialize_output(directory):
    require_root()
    directory = q.checked_directory(Path(directory))
    require_empty_volumes((directory,))
    initialize_volume(directory)


def initialize_runtime(source=Path('/checkout'), source_volume=Path('/source'),
                       prep=Path('/prep'), work=Path('/work'), packages=Path('/packages')):
    require_root()
    source, source_volume, prep, work, packages = map(q.checked_directory,
        map(Path, (source, source_volume, prep, work, packages)))
    if len({source, source_volume, prep, work, packages}) != 5:
        raise ValueError('distinct source, package and generation paths required')
    require_empty_volumes((source_volume, prep, work))
    for path in (source, packages):
        if not os.statvfs(path).f_flag & os.ST_RDONLY:
            raise ValueError('read-only source and verified packages required')
    files = copy_source(source, source_volume / 'repo')
    projections = {}
    for label in ('read-file', 'image-viewer'):
        incoming, outgoing = packages / label, source_volume / 'repo/artifacts' / label
        expected = q.tree_inventory(incoming)
        if not expected:
            raise ValueError('empty verified package projection: ' + label)
        shutil.copytree(incoming, outgoing)
        if q.tree_inventory(outgoing) != expected or q.tree_inventory(incoming) != expected:
            raise ValueError('runtime source package projection differs')
        projections[label] = expected
    q.verify_source(source, files)
    q.verify_source(source_volume / 'repo', files)
    require_empty_volumes((prep, work))
    for path in (prep, work):
        initialize_volume(path)
    result = {'schemaVersion': 1, 'files': files, 'sourceFiles': len(files),
        'sourceBytes': sum(record['bytes'] for record in files.values()),
        'packageProjectionFiles': projections, 'formalQualification': False,
        'finalSystemAccepted': False}
    q.write_json(source_volume / 'runtime-source-snapshot.json', result)
    return result


def validate_explicit_review(base, review):
    reviewer = review.get('reviewer')
    if (base.get('reviewed') is not False or base.get('reviewer') is not None
            or review.get('reviewed') is not True or not isinstance(reviewer, str)
            or not reviewer.strip() or review != dict(base, reviewed=True, reviewer=reviewer)):
        raise ValueError('explicit independent review must match every pending review field')
    return review


def bundle_reviewed(root, repo, review_file):
    import linux_host_runtime as runtime
    root, repo, review_file = map(Path, (root, repo, review_file))
    runtime._runtime_root(root)
    runtime.load_runtime(root, repo)
    # Copy approval exactly as supplied; never turn a pending review into one.
    before = q.stable_hash(review_file)
    validate_explicit_review(q.read_json(root / 'build/review-base.json'), q.read_json(review_file))
    runtime._copy(review_file, root / 'review.json', before)
    runtime.verify_review(root)
    return runtime.bundle_runtime(root, repo)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', required=True, choices=('initialize', 'initialize-output', 'bundle-reviewed'))
    parser.add_argument('--output-volume', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--review', type=Path)
    args = parser.parse_args()
    if args.action == 'initialize':
        if any((args.output_volume, args.root, args.repo, args.review)):
            parser.error('initialize takes only its fixed fresh volume mounts')
        value = initialize_runtime()
        print('runtime-source-snapshot-created', value['sourceFiles'])
    elif args.action == 'initialize-output':
        if args.output_volume is None or any((args.root, args.repo, args.review)):
            parser.error('initialize-output requires only --output-volume')
        initialize_output(args.output_volume)
        print('empty-output-volume-initialized')
    else:
        if not all((args.root, args.repo, args.review)) or args.output_volume is not None:
            parser.error('bundle-reviewed requires --root --repo --review')
        bundle_reviewed(args.root, args.repo, args.review)


if __name__ == '__main__':
    main()
