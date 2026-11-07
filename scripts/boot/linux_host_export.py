#!/usr/bin/env python3
"""Seal and independently replay Linux-host evidence; replay never starts a guest.

The bootstrap imports only the standard library until every exported byte has
been checked. Historical freezes and checker source are copied verbatim.
"""
import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys

MANIFEST = 'linux-export-manifest.json'
PROFILE = 'persistent-update-linux-portable-export'
DRIVER = 'source/scripts/boot/linux_host_export.py'
CHECKER_BASE = 'source/artifacts/persistent-update-20261104-prep'
MAX_JSON = 64 * 1024 * 1024
HEX = re.compile('[0-9a-f]{64}\\Z')
ROOTS = {'runtime': 'runtime', 'source': 'source', 'coldSource': 'cold-source',
         'coldBuild': 'cold-build', 'target': 'target', 'packages': 'packages',
         'assets': 'assets', 'tools': 'tools', 'workspace': 'pair-work'}


def require(condition, message):
    if not condition:
        raise ValueError('Linux portable export: ' + message)


def relative(name):
    require(isinstance(name, str) and 0 < len(name) <= 2048 and '\\' not in name
            and ':' not in name and all(ord(c) >= 32 for c in name), 'unsafe relative path')
    value = PurePosixPath(name)
    require(not value.is_absolute() and value.parts and value.as_posix() == name
            and all(part not in ('.', '..') for part in value.parts), 'noncanonical relative path')
    return name


def absolute(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'canonical absolute path required')
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'link/reparse point in path')
    return path


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def read_file(path, limit=None, data=False):
    path = Path(path)
    absolute(path.parent)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
            and not getattr(before, 'st_file_attributes', 0) & 0x400, 'single-link regular file required')
    require(limit is None or before.st_size <= limit, 'file exceeds byte limit')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    digest, chunks, size = hashlib.sha256(), [], 0
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        require(_stamp(os.fstat(stream.fileno())) == _stamp(before), 'file changed before read')
        for chunk in iter(lambda: stream.read(1024 ** 2), b''):
            size += len(chunk)
            require(size <= before.st_size and (limit is None or size <= limit), 'file grew while reading')
            digest.update(chunk)
            if data: chunks.append(chunk)
        require(size == before.st_size and _stamp(os.fstat(stream.fileno())) == _stamp(before)
                and _stamp(path.lstat()) == _stamp(before), 'file changed while reading')
    result = {'sha256': digest.hexdigest(), 'bytes': size, 'mode': stat.S_IMODE(before.st_mode)}
    return (result, b''.join(chunks)) if data else result


def json_bytes(raw):
    def pairs(items):
        value = {}
        for name, item in items:
            require(name not in value, 'duplicate JSON key')
            value[name] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('non-finite JSON value')))


def read_json(path):
    return json_bytes(read_file(path, MAX_JSON, True)[1])


def content(record):
    return {name: record[name] for name in ('sha256', 'bytes')}


def file_inventory(root):
    root = absolute(root)
    require(root.is_dir(), 'directory required')
    files, directories, work = {}, [], [(root, 0)]
    while work:
        directory, depth = work.pop()
        require(depth <= 40 and len(directories) < 65536, 'directory bound exceeded')
        before = directory.lstat()
        with os.scandir(directory) as entries:
            entries = sorted(entries, key=lambda entry: entry.name)
        for entry in entries:
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            name = relative(path.relative_to(root).as_posix())
            require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
                    'symlink/reparse member forbidden')
            if stat.S_ISDIR(info.st_mode):
                directories.append(name)
                work.append((path, depth + 1))
            else:
                files[name] = read_file(path)
                require(len(files) <= 65536, 'file bound exceeded')
        require(_stamp(directory.lstat()) == _stamp(before), 'directory changed while scanning')
    return dict(sorted(files.items())), sorted(directories)


def verify_manifest(root):
    root = absolute(root)
    observed, raw = read_file(root / MANIFEST, MAX_JSON, True)
    value = json_bytes(raw)
    require(type(value) is dict and set(value) == {'schemaVersion', 'profile', 'algorithm', 'files',
            'directories', 'bindings', 'replayDriver', 'formalQualification', 'finalSystemAccepted'}
            and type(value['schemaVersion']) is int and value['schemaVersion'] == 1
            and value['profile'] == PROFILE and value['algorithm'] == 'sha256'
            and value['formalQualification'] is False and value['finalSystemAccepted'] is False,
            'manifest schema/profile/flags differ')
    require(type(value['files']) is dict and 1 <= len(value['files']) <= 65535
            and type(value['directories']) is list and value['replayDriver'] == DRIVER
            and DRIVER in value['files'] and type(value['bindings']) is dict, 'manifest members differ')
    for name, record in value['files'].items():
        relative(name)
        require(name != MANIFEST and type(record) is dict and set(record) == {'sha256', 'bytes', 'mode'}
                and isinstance(record['sha256'], str) and HEX.fullmatch(record['sha256'])
                and type(record['bytes']) is int and record['bytes'] >= 0
                and type(record['mode']) is int and 0 <= record['mode'] <= 0o777,
                'manifest file record differs')
    require(value['directories'] == sorted(set(value['directories'])), 'directory set differs')
    for name in value['directories']: relative(name)
    files, directories = file_inventory(root)
    require(files.pop(MANIFEST, None) == observed and files == value['files']
            and directories == value['directories'], 'manifest file/directory set or bytes differ')
    require(read_file(root / MANIFEST, MAX_JSON, True)[1] == raw, 'manifest changed during verification')
    return {'manifest': value, 'manifestSha256': observed['sha256'], 'fileCount': len(files)}


def check_output(root, output):
    root, output = Path(root).resolve(), Path(output)
    require(output.is_absolute(), 'absolute replay output required')
    canonical = output.resolve()
    require(not canonical.is_relative_to(root), 'replay output must be outside sealed export')
    absolute(output.parent)
    require(not output.exists() and not output.is_symlink(), 'new replay output required')
    return output


def verify_bootstrap(root, verified):
    """Bind every importable source byte before the sealed driver is executed."""
    manifest = verified['manifest']
    binding = manifest['bindings']
    require('runtimeFreezeSha256' in binding, 'runtime freeze binding missing before import')
    frozen = Path(root) / 'runtime/runtime-freeze.json'
    require(read_file(frozen)['sha256'] == binding['runtimeFreezeSha256'], 'runtime freeze changed before import')
    runtime = read_json(frozen)
    source = runtime.get('runtimeSource')
    require(runtime.get('profile') == 'persistent-update-linux-runtime' and type(source) is dict and source,
            'runtime source inventory required before import')
    expected = {}
    for name, record in source.items():
        relative(name)
        require(type(record) is dict and set(record) == {'sha256', 'bytes'}, 'runtime source record differs')
        expected['source/' + name] = record
    actual = {name: content(record) for name, record in manifest['files'].items()
              if name.startswith('source/') and not name.startswith('source/artifacts/')}
    require(DRIVER in expected and actual == expected, 'importable source differs before import')
    return runtime


def map_original(root, mappings, name):
    require(isinstance(name, str), 'original absolute path required')
    path = PurePosixPath(name)
    require(path.is_absolute() and path.as_posix() == name and '..' not in path.parts
            and '\\' not in name, 'noncanonical original absolute path')
    choices = []
    for original, destination in mappings.items():
        base = PurePosixPath(original)
        require(base.is_absolute() and base.as_posix() == original and '..' not in base.parts,
                'original mapping root differs')
        relative(destination)
        if path.is_relative_to(base):
            choices.append((len(base.parts), base, destination))
    require(choices, 'unmapped original path: ' + name)
    _, base, destination = max(choices)
    return Path(root) / destination / path.relative_to(base)


def _modules(repo=None):
    if repo is not None:
        directory = absolute(Path(repo) / 'scripts/boot')
        sys.path.insert(0, str(directory))
    result = {}
    for name in ('linux_host_qualification', 'linux_host_build', 'linux_host_freeze', 'linux_host_runtime'):
        module = importlib.import_module(name)
        if repo is not None:
            require(Path(module.__file__).resolve() == directory / (name + '.py'), 'ambient replay module: ' + name)
        result[name] = module
    return tuple(result.values())


def verify_cold_build(root, freeze, target, packages, cold_repo, *, original_repo=None,
                      original_target=None, original_packages=None):
    q, builder, freezer, _ = _modules()
    root, freeze, target, packages, cold_repo = map(Path, (root, freeze, target, packages, cold_repo))
    digest = q.stable_hash(freeze)['sha256']
    value = q.read_json(freeze)
    require(type(value.get('schemaVersion')) is int and value['schemaVersion'] == 1
            and value.get('profile') == 'persistent-update-linux-host', 'cold freeze schema/profile differs')
    require(q.source_inventory(cold_repo) == value['source']['files']
            and q.stable_hash(cold_repo / 'Cargo.lock') == value['cargoLock'], 'original cold source differs')
    q.verify_stage(root, 'prepare', digest)
    prepared = q.read_json(root / 'prepare-result.json')
    require(prepared.get('status') == 'passed' and prepared.get('freezeSha256') == digest
            and prepared.get('formalQualification') is False and prepared.get('finalSystemAccepted') is False,
            'cold prepare failed or differs')
    admission = q.read_json(root / 'stages/prepare/admission.json')
    require(admission == {'freezeSha256': digest, 'packageReplayFiles': value['packageReplayFiles'],
                          'storage': value['storage'], 'formalQualification': False}, 'cold prepare admission differs')
    require(q.tree_inventory(root / 'replay') == value['packageReplayFiles'], 'cold package replay bytes differ')
    freezer.validate_reopen(value['storage']['probe'], value['storage']['reopen'])
    require(value['storage']['budget'] == freezer.capacity_budget(), 'cold capacity budget differs')
    freezer.validate_capacity(value['storage']['capacityObserved'])
    require(set(value['packages']) == set(builder.PACKAGES), 'six cold packages required')
    for label, record in value['packages'].items():
        path = packages / builder.relative_path(record['path'])
        require(q.tree_inventory(path) == record['files'], 'cold package bytes differ: ' + label)
        builder.verify_package(path, label, value['source']['files'])
    for label in ('read-file', 'image-viewer'):
        require(q.tree_inventory(cold_repo / 'artifacts' / label) == value['packages'][label]['files'],
                'original Rust package projection differs')
    stage = q.verify_stage(root, 'build', digest)
    detail = stage.get('detail', {})
    require(detail.get('coldBuildCommands') == 10 and detail.get('formalQualification') is False
            and detail.get('finalSystemAccepted') is False, 'complete ten-command cold build required')
    directory = root / 'stages/build'
    q.validate_environment(q.read_json(directory / 'environment.json'))
    cold = q.read_json(directory / 'cold-target.json')
    original_target = original_target or str(target)
    original_repo = original_repo or str(cold_repo)
    original_packages = original_packages or str(packages)
    require(cold.get('literalEmpty') is True and cold.get('entries') == []
            and cold.get('path') == original_target and detail.get('target') == original_target, 'cold target admission differs')
    environments = q.read_json(directory / 'build-environment.json')
    explicit = environments.get('explicit', {})
    home = PurePosixPath(explicit.get('HOME', ''))
    require(home.is_absolute() and str(home).endswith('/stages/build/home'), 'original build environment home differs')
    expected = builder.build_environment(Path(explicit.get('CARGO_HOME', '')), Path(original_target),
        Path(str(home.parent)), Path(original_packages), {label: row['path'] for label, row in value['packages'].items()})
    require(environments == {'initial': {key: val for key, val in expected.items() if not key.startswith('MING_')},
                             'explicit': expected}, 'cold Cargo environment allowlist differs')
    require(sorted(path.name for path in (directory / 'commands').iterdir()) == ['%02d' % n for n in range(1, 11)],
            'exact ten cold command directories required')
    totals = []
    for number, entry in enumerate(q.build_commands(), 1):
        command_root = directory / 'commands' / ('%02d' % number)
        command = q.read_json(command_root / 'command.json')
        result = q.read_json(command_root / 'result.json')
        require(command.get('command') == entry['command'] and command.get('cwd') == original_repo
                and command.get('timeoutSeconds') == entry['timeoutSeconds']
                and result.get('command') == entry['command'] and type(result.get('exitCode')) is int
                and result['exitCode'] == 0 and type(result.get('leaderExitCode')) is int
                and result['leaderExitCode'] == 0 and result.get('timedOut') is False
                and result.get('cleanupComplete') is True and result.get('orphanedChildren') == []
                and result.get('error') is None, 'cold command failure/argv/containment differs: ' + str(number))
        for label in ('stdout', 'stderr'):
            require(q.stable_hash(command_root / (label + '.log')) == result[label], 'cold command log hash differs')
        if number == 1 or entry['expectedSummary'] is not None:
            summary = builder.summarize((command_root / 'stdout.log').read_text(), entry['expectedSummary'])
            require(q.read_json(command_root / 'summary.json') == summary, 'cold exact summary receipt differs')
            totals.append(summary)
    artifacts = builder.built_artifacts(target)
    require(q.read_json(directory / 'artifacts.json') == artifacts, 'cold current executable bytes differ')
    pre_artifacts = q.read_json(directory / 'pre-explicit-artifacts.json')
    require(type(pre_artifacts) is dict and set(pre_artifacts) == set(artifacts), 'pre-explicit artifact set differs')
    for record in pre_artifacts.values():
        require(type(record) is dict and set(record) == {'sha256', 'bytes'}
                and isinstance(record['sha256'], str) and HEX.fullmatch(record['sha256'])
                and type(record['bytes']) is int and 4 <= record['bytes'] <= builder.ARTIFACT_LIMIT,
                'pre-explicit artifact record differs')
    # Explicit integration tests may make Cargo relink the host. Each snapshot
    # must bind its own seal; only the final snapshot binds current target bytes.
    for name, expected_artifacts in (('pre-explicit-artifact-seal.json', pre_artifacts),
                                     ('final-artifact-seal.json', artifacts)):
        seals = q.read_json(directory / name)
        require(set(seals) == set(expected_artifacts), 'fixed output seal set differs')
        for key, seal in seals.items():
            require(set(seal) == {'detached', 'sourceIdentity', 'sourceBefore', 'sourceAfter', 'artifact', 'artifactIdentity'}
                    and type(seal['detached']) is bool
                    and seal['sourceBefore'] == seal['sourceAfter'] == seal['artifact'] == expected_artifacts[key],
                    'fixed output seal bytes differ')
            source, final = seal['sourceIdentity'], seal['artifactIdentity']
            fields = {'dev', 'ino', 'mode', 'links', 'uid', 'gid', 'bytes', 'mtimeNs', 'ctimeNs'}
            require(set(source) == set(final) == fields
                    and all(type(value) is int for identity in (source, final) for value in identity.values())
                    and source['links'] >= 1 and final['links'] == 1
                    and seal['detached'] == (source['links'] > 1)
                    and all(identity['uid'] == identity['gid'] == 65534
                        and identity['bytes'] == expected_artifacts[key]['bytes']
                        and stat.S_ISREG(identity['mode']) and identity['mode'] & 0o111
                        and not identity['mode'] & 0o7000 for identity in (source, final))
                    and source['mode'] == final['mode']
                    and ((source['dev'], source['ino']) != (final['dev'], final['ino'])) == seal['detached'],
                    'fixed output seal identity differs')
    return {'commands': 10, 'freezeSha256': digest, 'buildResult': q.stable_hash(directory / 'result.json'),
            'artifacts': artifacts, 'summaries': totals, 'formalQualification': False, 'finalSystemAccepted': False}


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_geometry(root, runtime, cold, mappings):
    """Bind the one-test geometry observation to the original cold Cargo output."""
    base, target = root / 'runtime', root / 'target'
    execution = read_json(base / 'geometry-execution.json')
    require(execution == runtime['geometryExecution'], 'geometry execution differs from runtime freeze')
    binary = PurePosixPath(execution['binaryPath'])
    require(binary.parent == PurePosixPath(runtime['paths']['target']) / 'release/deps'
            and re.fullmatch('ming_app_host-[0-9a-f]+', binary.name), 'geometry unit ELF path differs')
    path = map_original(root, mappings, str(binary))
    identity, raw = read_file(path, data=True)
    require(raw[:4] == b'\x7fELF' and identity['mode'] & 0o111
            and execution['binarySha256'] == identity['sha256'], 'geometry unit ELF bytes differ')
    expected = [str(binary), '--exact', 'desktop_native::input_tests::persistent_update_two_app_input_geometry', '--nocapture']
    first = root / 'cold-build/stages/build/commands/01'
    stderr = read_file(first / 'stderr.log', data=True)[1].decode()
    units = re.findall(r'Running unittests src/lib\.rs \(([^\n()]+/ming_app_host-[0-9a-f]+)\)', stderr)
    require(units == [str(binary)] and type(execution.get('schemaVersion')) is int
            and execution['schemaVersion'] == 1 and execution.get('argv') == expected
            and type(execution.get('exitCode')) is int and execution['exitCode'] == 0
            and execution.get('stdoutSha256') == read_file(base / 'geometry.stdout.log')['sha256']
            and execution.get('coldBuildResultSha256') == cold['buildResult']['sha256']
            and execution.get('coldBuildFreezeSha256') == cold['freezeSha256']
            and execution.get('firstCommandResult') == content(read_file(first / 'result.json'))
            and execution.get('firstCommandStderr') == content(read_file(first / 'stderr.log'))
            and execution.get('cargoLock') == content(read_file(root / 'cold-source/Cargo.lock'))
            and execution.get('rustToolchain') == content(read_file(root / 'cold-source/rust-toolchain.toml'))
            and execution.get('formalQualification') is False and execution.get('finalSystemAccepted') is False,
            'geometry original execution provenance differs')
    driver = _load(root / CHECKER_BASE / 'run-stage.py', 'linux_export_geometry_driver')
    driver.validate_geometry(read_json(base / 'input-geometry.json'), root / 'source', base / 'geometry.stdout.log')
    q, _, _, _ = _modules()
    q.exact_summary(read_file(base / 'geometry.stdout.log', data=True)[1].decode(), (1, 0, 0, 0))
    return str(path.relative_to(root))


def verify_bindings(root, verified=None):
    """Pure relocated checks; never consult original absolute paths or a daemon."""
    root = absolute(root)
    verified = verified or verify_manifest(root)
    verify_bootstrap(root, verified)
    binding = verified['manifest']['bindings']
    require(set(binding) == {'roots', 'receiptRoots', 'runtimeFreezeSha256', 'coldFreezeSha256',
                            'coldOriginalRepo', 'geometryBinary', 'targetFiles'}
            and set(binding['roots']) == set(ROOTS) and type(binding['receiptRoots']) is dict,
            'binding schema differs')
    mappings = {}
    for role, original in binding['roots'].items():
        # The two /repo snapshots are deliberately distinct; cold commands retain
        # their original cwd and are checked explicitly, never path-rewritten.
        if role == 'coldSource':
            continue
        require(original not in mappings, 'duplicate original root role')
        mappings[original] = ROOTS[role]
    for original, destination in binding['receiptRoots'].items():
        require(original not in mappings and re.fullmatch(r'receipts/[0-9]+', destination), 'receipt root mapping differs')
        mappings[original] = destination
    for original in (*mappings, binding['roots']['coldSource'], binding['coldOriginalRepo']):
        require(isinstance(original, str) and PurePosixPath(original).is_absolute()
                and PurePosixPath(original).as_posix() == original and '..' not in PurePosixPath(original).parts
                and '\\' not in original, 'original root path differs')
    q, builder, freezer, runtime_module = _modules(root / 'source')
    base = root / 'runtime'
    value = read_json(base / 'runtime-freeze.json')
    digest = read_file(base / 'runtime-freeze.json')['sha256']
    require(type(value.get('schemaVersion')) is int and value['schemaVersion'] == 1
            and value.get('profile') == 'persistent-update-linux-runtime'
            and value.get('formalQualification') is False and value.get('finalSystemAccepted') is False
            and digest == binding['runtimeFreezeSha256'], 'runtime freeze profile/hash/flags differ')
    paths = value['paths']
    for key, role in (('repo', 'source'), ('build_root', 'coldBuild'), ('target', 'target'), ('packages_root', 'packages')):
        require(paths[key] == binding['roots'][role], 'original runtime root differs: ' + key)
    require(binding['roots']['runtime'] == '/prep', 'original runtime root must remain /prep')
    require(q.source_inventory(root / 'source') == value['runtimeSource'], 'complete runtime source differs')
    frozen = map_original(root, mappings, paths['build_freeze'])
    original = read_json(frozen)
    require(read_file(frozen)['sha256'] == binding['coldFreezeSha256'] == value['coldBuild']['freezeSha256'],
            'cold freeze digest differs')
    cold = verify_cold_build(root / 'cold-build', frozen, root / 'target', root / 'packages', root / 'cold-source',
        original_repo=binding['coldOriginalRepo'], original_target=paths['target'], original_packages=paths['packages_root'])
    require(value['coldBuild'] == {'root': paths['build_root'], 'freeze': paths['build_freeze'],
            'freezeSha256': cold['freezeSha256'], 'buildResult': cold['buildResult'], 'target': paths['target'],
            'artifacts': cold['artifacts'], 'sourceExtension': runtime_module.source_extension(original['source']['files'], value['runtimeSource'])},
            'runtime complete cold-build binding differs')
    prepare = read_json(base / 'runtime-prepare-result.json')
    require(prepare.get('status') == 'passed' and prepare.get('runtimeFreezeSha256') == digest
            and prepare.get('formalQualification') is False and prepare.get('finalSystemAccepted') is False,
            'runtime prepare result differs')
    for stage in ('prepare', 'build', 'bundle', 'admission', 'pair'):
        q.verify_stage(base, stage, digest)
    require(read_json(base / 'stages/prepare/admission.json') == {'runtimeFreezeSha256': digest,
            'coldBuild': value['coldBuild'], 'guestStarted': False}, 'runtime prepare admission differs')
    runtime_module.verify_bundle_products(base)
    runtime_module.verify_admission_products(base)
    review = runtime_module.verify_review(base)
    relocated = dict(value, paths=dict(paths, repo=str(root / 'source')))
    require(read_json(base / 'build/review-base.json') == runtime_module.review_base(base, relocated),
            'review base differs from actual frozen runtime payload and source')
    freezer.validate_reopen(value['storage']['probe'], value['storage']['reopen'])
    require(set(value['packages']) == set(builder.PACKAGES), 'six runtime packages required')
    for label, record in value['packages'].items():
        require(record['path'] == str(PurePosixPath(paths['packages_root']) / original['packages'][label]['path'])
                and q.tree_inventory(map_original(root, mappings, record['path'])) == record['files'] == original['packages'][label]['files'],
                'runtime package binding differs: ' + label)
    for payload_name, artifact_name in (('ming-app-host', 'release/ming-app-host'),
            ('prepare-persistent-update-fixture', 'release/examples/prepare_persistent_update_fixture')):
        require(content(read_file(base / 'inputs/payload' / payload_name)) == cold['artifacts'][artifact_name],
                'runtime payload differs from cold executable: ' + payload_name)
    for label, package in (('bootstrap', 'text-entry-demo'), ('old', 'old'), ('new', 'new')):
        require(content(read_file(base / 'inputs/payload/packages' / label / 'main.wasm')) == value['packages'][package]['files']['main.wasm'],
                'runtime payload differs from cold Kotlin package: ' + label)
    require(set(value['checker']) == set(runtime_module.CHECKER_FILES), 'complete bound checker files required')
    for name, expected in value['checker'].items():
        for path in (base / name, root / CHECKER_BASE / name, map_original(root, mappings, paths['checker_dir']) / name):
            require(content(read_file(path)) == expected, 'unchanged bound checker differs: ' + name)
    for number, expected_hash in runtime_module.INPUT_HASHES.items():
        record = value['inputs'][str(number)]
        copies = [base / ('input-boot%d.json' % number), base / 'inputs' / ('input-boot%d.json' % number),
                  root / CHECKER_BASE / ('input-boot%d.json' % number), map_original(root, mappings, record['path'])]
        require(record['path'] == paths['input%d' % number] and record['sha256'] == expected_hash
                and record['steps'] == (5 if number == 1 else 2)
                and record['minimumSeconds'] == (670 if number == 1 else 520), 'native input record differs')
        for path in copies:
            require(content(read_file(path)) == {key: record[key] for key in ('sha256', 'bytes')}, 'native input copy differs')
    for name, expected in value['toolReceipts'].items():
        require(content(read_file(map_original(root, mappings, name))) == expected, 'tool receipt changed')
    freezer.validate_image_receipt(read_json(map_original(root, mappings, paths['tools_receipt'])), runtime_module.TOOLS_IMAGE)
    require(value['tools']['image'] == runtime_module.TOOLS_IMAGE
            and read_json(map_original(root, mappings, paths['tools_manifest']))['files'] == value['tools']['files']
            and value['tools']['aptClosureQualification'] == 'development image observations only; no full offline APT closure claim',
            'tools image/manifest/scope differs')
    rust = original['environment']['rustImage']
    inspection = map_original(root, mappings, rust['inspectionReceipt'])
    require(read_file(inspection)['sha256'] == rust['inspectionReceiptSha256'] and rust['id'] == freezer.RUST_IMAGE,
            'original Rust inspection receipt differs')
    freezer.validate_image_receipt(read_json(inspection), freezer.RUST_IMAGE)
    node = original['environment']['replayNode']
    require(read_file(map_original(root, mappings, node['path']))['sha256'] == node['sha256'], 'original replay Node bytes differ')
    if 'toolchain' in original['environment']:
        toolchain = original['environment']['toolchain']
        require(read_file(map_original(root, mappings, toolchain['receipt']))['sha256'] == toolchain['sha256'], 'original toolchain receipt differs')
    capacity = original['storage']
    require(read_file(map_original(root, mappings, capacity['capacityReceipt']))['sha256'] == capacity['capacityReceiptSha256'],
            'original capacity receipt differs')
    probes = map_original(root, mappings, paths['probe_dir'])
    require(set(value['probes']) == {'contract', 'files', 'build'}
            and q.tree_inventory(probes) == value['probes']['files'], 'probe artifact/build binding differs')
    from linux_host_probe import verify_probe_build
    require(verify_probe_build(probes, root / 'source', cold['freezeSha256']) == value['probes']['build'],
            'independent source-v2 probe build provenance differs')
    from isolation_profiles import verify_review_and_files
    require(verify_review_and_files('source-v2', {'strictProbeProfile': 'source-v2',
        'strictProbeSha256': value['probes']['contract']['artifactSha256']}, probes) == value['probes']['contract'], 'probe profile binding differs')
    geometry = verify_geometry(root, value, cold, mappings)
    require(binding['geometryBinary'] == geometry, 'geometry target mapping differs')
    target_files = {name: content(record) for name, record in file_inventory(root / 'target')[0].items()}
    require(binding['targetFiles'] == target_files and set(target_files) == set(cold['artifacts']) | {str(Path(geometry).relative_to('target'))},
            'exact host/helper/geometry target set required')
    pair_products = read_json(base / 'stages/pair/external-products.json')
    require(set(pair_products) == {'pairRoot', 'pairFiles', 'workspaceRoot', 'workspaceFiles', 'disk', 'runtimeFreezeSha256'}
            and pair_products['workspaceRoot'] == binding['roots']['workspace']
            and pair_products['pairRoot'] == str(PurePosixPath(pair_products['workspaceRoot']) / 'cold-pair')
            and pair_products['runtimeFreezeSha256'] == digest
            and pair_products['workspaceFiles'] == q.tree_inventory(root / 'pair-work')
            and pair_products['pairFiles'] == q.tree_inventory(root / 'pair-work/cold-pair'), 'pair external product binding differs')
    disk = pair_products['disk']
    require(disk.get('schemaVersion') == 1 and disk.get('format') == 'raw' and disk.get('filesystem') == 'ext4'
            and disk.get('sizeMiB') == 128 and disk.get('image') == 'disks/linux-runtime-128.raw'
            and disk.get('imagePath') == str(PurePosixPath(pair_products['workspaceRoot']) / disk['image'])
            and map_original(root, mappings, disk['imagePath']).stat().st_size == 128 * 1024 ** 2,
            'same raw ext4 disk binding differs')
    disk_manifest = read_json(map_original(root, mappings, disk['manifestPath']))
    require({key: disk[key] for key in disk_manifest} == disk_manifest, 'original disk manifest differs')
    pair_stage = q.verify_stage(base, 'pair', digest)
    detail = pair_stage.get('detail', {})
    require(detail.get('twoBootsStrongRawVerified') is True and detail.get('formalQualification') is False
            and detail.get('finalSystemAccepted') is False
            and detail.get('bundleIdentity') == read_file(base / 'bundle/bundle.json')['sha256']
            and detail.get('diskUuid') == disk['uuid'], 'pair stage strong gate differs')
    return {'runtime': value, 'coldBuild': cold, 'mappings': mappings, 'pairDetail': detail,
            'manifestSha256': verified['manifestSha256'], 'fileCount': verified['fileCount']}


def replay_raw(root):
    """Run unchanged signature and raw-audit APIs under their normal repo layout."""
    root, base = Path(root), Path(root) / 'runtime'
    checker = _load(root / CHECKER_BASE / 'verify-persistent-update-evidence.py', 'linux_export_bound_checker')
    require(checker.PORTABLE is False and checker.REPO == root / 'source', 'checker normal source layout differs')
    from bootlib import checked_session, persistent_update_session_contract, validate_bundle, validate_reviewed_input
    from bundle import verify_payload
    from infra import validate_cold_pair
    from linux_host_pair import validate_strong_pair
    review = read_json(base / 'review.json')
    checked_session(review, base / 'inputs/payload')
    persistent_update_session_contract(review)
    identity = read_file(base / 'bundle/bundle.json')['sha256']
    bundle = validate_bundle(base / 'bundle', read_file(base / 'review.json')['sha256'])
    require(review.get('profile') == 'persistent-update' and bundle.get('payloadProfile') == 'persistent-update'
            and bundle.get('sessionReview') == review and bundle.get('instrumentedQualification') is True
            and bundle.get('diagnosticOnly') is False and bundle.get('diagnosticProfile') == 'input-pointer-ready-v1',
            'exact independent pointer-ready bundle required')
    verify_payload(base / 'inputs/payload', review)
    for name, expected in bundle['payloadSha256'].items():
        require(read_file(base / 'inputs/payload' / relative(name))['sha256'] == expected, 'bundle payload differs')
    signed = checker.verify_fixture_directory(base / 'build/fixture', base / 'inputs', review)
    require(signed == read_json(base / 'fixture-verification.json'), 'replayed fixture verification differs')
    runs = root / 'pair-work/cold-pair'
    audited = []
    for number in (1, 2):
        raw, _ = validate_reviewed_input(review, base / 'inputs' / ('input-boot%d.json' % number), number)
        require(hashlib.sha256(raw).hexdigest() == checker.INPUT_HASHES[number]
                and read_file(runs / ('boot-%d.input.json' % number), data=True)[1] == raw, 'immutable pair native input differs')
        audited.append(checker.verify_boot(runs / ('boot-%d' % number), number, review, json_bytes(raw), signed, identity, root / 'source'))
    validate_cold_pair(audited[0]['report'], audited[1]['report'])
    validate_strong_pair(*audited)
    require(read_json(runs / 'result.json').get('status') == 'same-bundle-disk-cold-boot-verified', 'pair runner success missing')
    return {'status': 'linux-portable-raw-normal-reboot-verified', 'boots': audited,
            'diskUuid': audited[0]['report']['diskUuid'], 'bundleIdentity': identity,
            'persistentUpdateNormalRebootVerified': True, 'formalQualification': False, 'finalSystemAccepted': False}


def _write(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def replay_export(root, output, *, semantic_only=False):
    root, output = absolute(root), check_output(root, output)
    result = {'status': 'failed', 'formalQualification': False, 'finalSystemAccepted': False}
    try:
        for name in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'PYTHONHOME', 'OPENSSL_CONF', 'OPENSSL_MODULES'):
            require(not os.environ.get(name), 'ambient replay injection forbidden: ' + name)
        verified = verify_manifest(root)
        binding = verify_bindings(root, verified)
        result = ({'status': 'linux-portable-bindings-verified', 'formalQualification': False,
                   'finalSystemAccepted': False} if semantic_only else replay_raw(root))
        if not semantic_only:
            require(binding['pairDetail'].get('bootIds') == [boot['bootId'] for boot in result['boots']]
                    and binding['pairDetail']['bundleIdentity'] == result['bundleIdentity']
                    and binding['pairDetail']['diskUuid'] == result['diskUuid'], 'replayed raw pair differs from pair stage')
        require(verify_manifest(root) == verified, 'export changed during semantic/raw replay')
        result.update(manifestSha256=binding['manifestSha256'], fileCount=binding['fileCount'])
        _write(output, result)
        return result
    except Exception as error:
        result.update(status='failed', error=str(error), formalQualification=False, finalSystemAccepted=False)
        if not output.exists(): _write(output, result)
        raise


def copy_file(source, destination, expected=None):
    before = read_file(source)
    require(expected is None or content(before) == expected, 'copy source differs from frozen bytes')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Path(source).open('rb') as original, destination.open('xb') as output:
        shutil.copyfileobj(original, output, 1024 ** 2)
        output.flush()
        os.fsync(output.fileno())
    destination.chmod(before['mode'])
    require(read_file(source) == before and read_file(destination) == before, 'copy changed bytes/mode')


def copy_tree(source, destination):
    before, directories = file_inventory(source)
    destination.mkdir(mode=0o700)
    for name in directories:
        (destination / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, record in before.items():
        copy_file(Path(source) / name, destination / name, content(record))
    require(file_inventory(source) == (before, directories) and file_inventory(destination) == (before, directories),
            'tree changed while copying')


def copy_source(source, destination, expected):
    q, _, _, _ = _modules()
    require(q.source_inventory(source) == expected, 'full source differs before copy')
    destination.mkdir(mode=0o700)
    for name, record in expected.items():
        copy_file(source / relative(name), destination / name, record)
    for label in ('read-file', 'image-viewer'):
        (destination / 'artifacts').mkdir(exist_ok=True, mode=0o700)
        copy_tree(source / 'artifacts' / label, destination / 'artifacts' / label)
    require(q.source_inventory(source) == expected and q.source_inventory(destination) == expected,
            'full source changed during copy')


def create_export(*, root, repo, cold_repo, assets_root, tools_root, output, receipt_roots=()):
    """Admit actual completed evidence before creating a new generation."""
    root, repo, cold_repo, assets_root, tools_root = map(absolute, (root, repo, cold_repo, assets_root, tools_root))
    q, builder, _, runtime = _modules(repo)
    runtime._runtime_root(root)
    value = runtime.load_runtime(root, repo)
    digest = read_file(root / 'runtime-freeze.json')['sha256']
    runtime.verify_bundle_products(root)
    runtime.verify_admission_products(root)
    runtime.verify_review(root)
    stage = q.verify_stage(root, 'pair', digest)
    require(stage.get('detail', {}).get('twoBootsStrongRawVerified') is True, 'actual strong pair required before export')
    external = read_json(root / 'stages/pair/external-products.json')
    work = absolute(external['workspaceRoot'])
    require(external['workspaceFiles'] == q.tree_inventory(work)
            and external['pairFiles'] == q.tree_inventory(absolute(external['pairRoot'])), 'actual pair changed before export')
    paths = value['paths']
    build_root, target, packages = map(absolute, (paths['build_root'], paths['target'], paths['packages_root']))
    freeze = absolute(paths['build_freeze'])
    cold_value = read_json(freeze)
    command = read_json(build_root / 'stages/build/commands/01/command.json')
    verify_cold_build(build_root, freeze, target, packages, cold_repo, original_repo=command['cwd'])
    receipts = {}
    for number, entry in enumerate(receipt_roots):
        original, actual = entry.split('=', 1)
        require(original not in receipts, 'duplicate receipt root')
        receipts[original] = (absolute(actual), 'receipts/' + str(number))
    inputs = [root, repo, cold_repo, assets_root, tools_root, build_root, target, packages, work]
    inputs.extend(actual for actual, _ in receipts.values())
    output = check_output(root, output)
    for source in inputs:
        require(not output.resolve().is_relative_to(source.resolve()) and not source.resolve().is_relative_to(output.resolve()),
                'export output must be separate from every input root')
    roots = {'runtime': root, 'source': repo, 'coldSource': cold_repo, 'coldBuild': build_root,
             'target': target, 'packages': packages, 'assets': assets_root, 'tools': tools_root, 'workspace': work}
    geometry = value['geometryExecution']
    unit = Path(geometry['binaryPath'])
    require(unit.is_relative_to(target), 'geometry ELF must be inside target')
    output.mkdir(mode=0o700)
    # No retry/reset on failure: the partly copied generation remains inspectable.
    with runtime._runtime_session(root, value):
        for role in ('runtime', 'coldBuild', 'packages', 'assets', 'tools', 'workspace'):
            copy_tree(roots[role], output / ROOTS[role])
        copy_source(repo, output / 'source', value['runtimeSource'])
        copy_source(cold_repo, output / 'cold-source', cold_value['source']['files'])
        (output / 'target').mkdir(mode=0o700)
        for name in (*builder.ARTIFACT_PATHS, str(unit.relative_to(target))):
            copy_file(target / name, output / 'target' / name)
        for original, (actual, destination) in receipts.items():
            (output / 'receipts').mkdir(exist_ok=True, mode=0o700)
            copy_tree(actual, output / destination)
        checker = output / CHECKER_BASE
        checker.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, expected in value['checker'].items():
            copy_file(root / name, checker / name, expected)
        for number in (1, 2):
            copy_file(root / ('input-boot%d.json' % number), checker / ('input-boot%d.json' % number))
        require(runtime.load_runtime(root, repo) == value and external['workspaceFiles'] == q.tree_inventory(work),
                'actual immutable inputs changed during export')
        files, directories = file_inventory(output)
        binding = {'roots': {role: str(path) for role, path in roots.items()},
            'receiptRoots': {original: destination for original, (_, destination) in receipts.items()},
            'runtimeFreezeSha256': digest, 'coldFreezeSha256': read_file(freeze)['sha256'],
            'coldOriginalRepo': command['cwd'], 'geometryBinary': 'target/' + unit.relative_to(target).as_posix(),
            'targetFiles': {name: content(record) for name, record in file_inventory(output / 'target')[0].items()}}
        _write(output / MANIFEST, {'schemaVersion': 1, 'profile': PROFILE, 'algorithm': 'sha256', 'files': files,
            'directories': directories, 'bindings': binding, 'replayDriver': DRIVER,
            'formalQualification': False, 'finalSystemAccepted': False})
    return verify_manifest(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('create', 'verify', 'replay'))
    parser.add_argument('--export', dest='export_root', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--cold-repo', type=Path)
    parser.add_argument('--assets-root', type=Path)
    parser.add_argument('--tools-root', type=Path)
    parser.add_argument('--receipt-root', action='append', default=[])
    parser.add_argument('--sealed-child', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.action == 'create':
        if args.output is not None or args.sealed_child or not all((args.root, args.repo, args.cold_repo, args.assets_root, args.tools_root)):
            parser.error('create requires --root --repo --cold-repo --assets-root --tools-root and new --export')
        value = create_export(root=args.root, repo=args.repo, cold_repo=args.cold_repo,
            assets_root=args.assets_root, tools_root=args.tools_root, output=args.export_root, receipt_roots=args.receipt_root)
        print(json.dumps({'manifestSha256': value['manifestSha256'], 'fileCount': value['fileCount'],
                          'formalQualification': False, 'finalSystemAccepted': False}, sort_keys=True))
        return 0
    if args.output is None:
        parser.error('verify/replay requires new --output outside export')
    root = absolute(args.export_root)
    output = check_output(root, args.output)
    try:
        verified = verify_manifest(root)  # Every exported byte checked before exporting code can import.
        verify_bootstrap(root, verified)
    except Exception as error:
        _write(output, {'status': 'failed', 'error': str(error), 'formalQualification': False, 'finalSystemAccepted': False})
        raise
    driver = root / DRIVER
    if not args.sealed_child:
        command = [sys.executable, '-I', '-B', str(driver), args.action, '--export', str(root), '--output', str(output), '--sealed-child']
        completed = subprocess.run(command, stdin=subprocess.DEVNULL, env={key: value for key, value in os.environ.items()
            if key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}, timeout=300)
        return completed.returncode
    require(Path(__file__).resolve() == driver, 'replay must run export own sealed driver')
    result = replay_export(root, output, semantic_only=args.action == 'verify')
    print(json.dumps({'status': result['status'], 'manifestSha256': result['manifestSha256'],
                      'formalQualification': False, 'finalSystemAccepted': False}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
