"""Fail-closed Rust Task9 cold-build controller; importing never runs Cargo."""
import argparse
import fcntl
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import tomllib

import linux_host_qualification as q
import linux_host_freeze as freezer
import linux_host_storage as storage

RUST_IMAGE = 'sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80'
PACKAGES = {'read-file': ('read-file', None), 'image-viewer': ('image-viewer', None),
            'text-entry-demo': ('text-entry-demo', None),
            'old': ('persistent-update-demo', 'old'),
            'new': ('persistent-update-demo', 'new'),
            'absent-write': ('persistent-update-demo', 'absent-write')}
SUMMARY = re.compile(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; '
                     r'(\d+) measured; \d+ filtered out; finished in [0-9.]+s')
ARTIFACT_PATHS = ('release/ming-app-host', 'release/examples/prepare_persistent_update_fixture')
ARTIFACT_LIMIT = 512 * 1024 * 1024


def relative_path(value):
    if (not isinstance(value, str) or not value or '\\' in value
            or PurePosixPath(value).is_absolute() or any(p in ('', '.', '..') for p in value.split('/'))):
        raise ValueError('safe relative package/input path required')
    return value


def require_readonly(path):
    if not os.statvfs(path).f_flag & os.ST_RDONLY:
        raise ValueError('frozen input requires read-only mount: ' + str(path))


def validate_provenance(value):
    image = value['environment']['rustImage']
    if image.get('id') != RUST_IMAGE:
        raise ValueError('frozen Rust image differs')
    receipt = Path(image['inspectionReceipt'])
    if not receipt.is_absolute():
        raise ValueError('absolute image inspection receipt required')
    before = q.stable_hash(receipt)
    if before['sha256'] != image.get('inspectionReceiptSha256'):
        raise ValueError('image inspection receipt hash differs')
    require_readonly(receipt)
    inspection = q.read_json(receipt)
    if not isinstance(inspection, list) or len(inspection) != 1 or inspection[0].get('Id') != RUST_IMAGE:
        raise ValueError('unique fixed Rust image inspection required')
    if q.stable_hash(receipt) != before:
        raise ValueError('image inspection receipt changed while reading')
    toolchain = value['environment'].get('toolchain')
    if toolchain is not None:
        path = Path(toolchain['receipt'])
        if not path.is_absolute() or q.stable_hash(path)['sha256'] != toolchain['sha256']:
            raise ValueError('frozen toolchain receipt changed')
        require_readonly(path)


def validate_admission(root, value, freeze_sha256):
    try:
        result_hash = q.stable_hash(root / 'prepare-result.json')
        result = q.read_json(root / 'prepare-result.json')
    except OSError as error:
        raise ValueError('prepare result missing or unsafe') from error
    if (result.get('status') != 'passed' or result.get('freezeSha256') != freeze_sha256
            or result.get('formalQualification') is not False or result.get('finalSystemAccepted') is not False):
        raise ValueError('prepare result failed or binds a different freeze')
    if q.tree_inventory(root / 'replay') != value.get('packageReplayFiles'):
        raise ValueError('package replay evidence changed')
    admission = q.read_json(root / 'stages/prepare/admission.json')
    if any(admission.get(key) != expected for key, expected in (
            ('freezeSha256', freeze_sha256), ('packageReplayFiles', value['packageReplayFiles']),
            ('storage', value['storage']), ('formalQualification', False))):
        raise ValueError('prepare stage admission differs from frozen inputs')
    frozen_storage = value['storage']
    freezer.validate_reopen(frozen_storage['probe'], frozen_storage['reopen'])
    if frozen_storage['budget'] != freezer.capacity_budget():
        raise ValueError('frozen cold-build capacity budget changed')
    freezer.validate_capacity(frozen_storage['capacityObserved'])
    capacity_receipt = Path(frozen_storage['capacityReceipt'])
    if not capacity_receipt.is_absolute() or q.stable_hash(capacity_receipt)['sha256'] != frozen_storage['capacityReceiptSha256']:
        raise ValueError('capacity receipt changed')
    require_readonly(capacity_receipt)
    return result_hash


def validate_cargo_config(repo, cargo_home):
    for name in ('config', 'config.toml'):
        candidate = cargo_home / name
        if candidate.exists() or candidate.is_symlink():
            raise ValueError('ambient Cargo home config is forbidden')
    old = repo / '.cargo/config'
    if old.exists() or old.is_symlink():
        raise ValueError('legacy Cargo config is forbidden')
    config = repo / '.cargo/config.toml'
    if config.exists():
        q.stable_hash(config)
        settings = tomllib.loads(config.read_text())
        allowed = {'build': {'jobs', 'target-dir'}, 'net': {'retry'}, 'http': {'timeout'}}
        if any(key not in allowed or not isinstance(fields, dict)
               or set(fields) - allowed[key] for key, fields in settings.items()):
            raise ValueError('Cargo source config has unapproved compiler/runner/profile overrides')
    release = tomllib.loads((repo / 'Cargo.toml').read_text()).get('profile', {}).get('release', {})
    if release.get('opt-level', 3) != 3 or release.get('debug', False) not in (False, 0):
        raise ValueError('release profile must retain optimized non-debug defaults')


def verify_package(directory, label, source):
    sample, fixture = PACKAGES[label]
    receipt = q.read_json(directory / 'build-receipt.json')
    info = q.read_json(directory / 'build-info.json')
    manifest = q.read_json(directory / 'app.json')
    if (type(receipt.get('schemaVersion')) is not int or receipt['schemaVersion'] != 1
            or receipt.get('status') != 'passed' or receipt.get('sample') != sample
            or receipt.get('fixture') != fixture or receipt.get('offline') is not True
            or type(receipt.get('compilerExitCode')) is not int or receipt['compilerExitCode'] != 0
            or receipt.get('compilerSignal') is not None or receipt.get('kotlin') != '2.3.20'
            or receipt.get('formalQualification') is not False
            or receipt.get('finalSystemAccepted') is not False
            or any(receipt.get(key) is not True for key in ('rawToPackageReplayVerified',
                       'sourceInputsStable', 'dependencyInputsStable'))):
        raise ValueError('successful offline package receipt required: ' + label)
    command = receipt.get('command')
    required = ['--offline', '--rerun-tasks', '--no-build-cache', '--no-configuration-cache',
                ':' + sample + ':compileProductionExecutableKotlinWasmWasi']
    if fixture:
        required.append('-PpersistentUpdateFixture=' + fixture)
    if not isinstance(command, list) or not all(isinstance(arg, str) for arg in command) or any(arg not in command for arg in required):
        raise ValueError('package compiler flags differ: ' + label)
    inputs = receipt.get('inputSha256')
    if (not isinstance(inputs, dict) or not inputs or info.get('inputSha256') != inputs
            or any(source.get(relative_path(name), {}).get('sha256') != digest for name, digest in inputs.items())):
        raise ValueError('package source input hashes differ: ' + label)
    for name, key in (('compiler.wasm', 'rawSha256'), ('main.wasm', 'packagedSha256'), ('app.json', 'manifestSha256')):
        digest = q.stable_hash(directory / name)['sha256']
        if receipt.get(key) != digest or (name != 'app.json' and info.get(key) != digest):
            raise ValueError('package manifest/raw/main hash differs: ' + label)
    if receipt.get('package') != info:
        raise ValueError('package build-info differs: ' + label)
    sdk = 1 if label == 'read-file' else 2 if label == 'image-viewer' else 6
    if (manifest.get('schemaVersion') != 1 or manifest.get('id') != 'dev.ming.' + sample
            or manifest.get('sdkVersion') != sdk or manifest.get('entry') != 'main.wasm'
            or manifest.get('version') != ('0.2.0' if fixture in ('new', 'absent-write') else '0.1.0')):
        raise ValueError('package manifest identity differs: ' + label)


def input_snapshot(repo, cargo_home, packages_root, value):
    packages = {}
    paths = []
    if set(value['packages']) != set(PACKAGES):
        raise ValueError('exact six package labels required')
    for label, record in value['packages'].items():
        relative = relative_path(record['path'])
        directory = packages_root / relative
        paths.append(directory)
        packages[label] = q.tree_inventory(directory)
    if len(set(paths)) != len(PACKAGES) or any(a in b.parents or b in a.parents for n, a in enumerate(paths) for b in paths[n + 1:]):
        raise ValueError('package directories overlap')
    return {'source': q.source_inventory(repo), 'cargoLock': q.stable_hash(repo / 'Cargo.lock'),
            'registry': q.tree_inventory(cargo_home / 'registry'), 'packages': packages}


def verify_inputs(snapshot, value):
    if snapshot['source'] != value['source']['files'] or snapshot['cargoLock'] != value['cargoLock']:
        raise ValueError('frozen source files or Cargo.lock changed')
    if not snapshot['registry'] or snapshot['registry'] != value['cargo']['files']:
        raise ValueError('frozen Cargo registry file set or bytes changed')
    if any(files != value['packages'][label]['files'] for label, files in snapshot['packages'].items()):
        raise ValueError('frozen package tree or evidence changed')


def build_environment(cargo_home, target, stage, packages_root, paths):
    # Construct a fresh allowlist: no inherited RUSTFLAGS, Cargo profiles,
    # compiler/linker wrappers, CC/CFLAGS, preload variables or tool search path.
    return {'PATH': '/usr/local/cargo/bin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
            'HOME': str(stage / 'home'), 'TMPDIR': str(stage / 'tmp'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'RUSTUP_HOME': '/usr/local/rustup', 'RUSTUP_AUTO_INSTALL': '0',
            'CARGO_HOME': str(cargo_home), 'CARGO_TARGET_DIR': str(target),
            'CARGO_BUILD_JOBS': '1', 'CARGO_NET_OFFLINE': 'true',
            'MING_TEST_PRIVATE_WORKER': str(target / 'release/ming-app-host'),
            'MING_UPDATE_FIXTURE_HELPER': str(target / 'release/examples/prepare_persistent_update_fixture'),
            'MING_UPDATE_BOOTSTRAP_WASM': str(packages_root / paths['text-entry-demo'] / 'main.wasm'),
            'MING_UPDATE_OLD_WASM': str(packages_root / paths['old'] / 'main.wasm'),
            'MING_UPDATE_NEW_WASM': str(packages_root / paths['new'] / 'main.wasm'),
            'MING_UPDATE_ABSENT_WASM': str(packages_root / paths['absent-write'] / 'main.wasm')}


def summarize(raw, expected):
    if expected is not None:
        counts = q.exact_summary(raw, expected)
        return {'groups': 1, 'passed': counts[0], 'failed': counts[1], 'ignored': counts[2], 'measured': counts[3]}
    lines = [line for line in raw.splitlines() if line.startswith('test result:')]
    matches = [SUMMARY.fullmatch(line) for line in lines]
    if not matches or any(match is None for match in matches):
        raise ValueError('all-targets test summary missing or malformed')
    counts = [tuple(map(int, match.groups())) for match in matches]
    totals = [sum(row[n] for row in counts) for n in range(4)]
    if totals[0] <= 0 or totals[1] or totals[3]:
        raise ValueError('all-targets test summary has zero tests or failures')
    return dict(zip(('groups', 'passed', 'failed', 'ignored', 'measured'), (len(counts), *totals)))


def _artifact_identity(info):
    return dict(zip(('dev', 'ino', 'mode', 'links', 'uid', 'gid', 'bytes', 'mtimeNs', 'ctimeNs'), q.stamp(info)))


def _artifact_fd_hash(fd, expected):
    if q.stamp(os.fstat(fd)) != q.stamp(expected):
        raise ValueError('artifact source identity changed')
    os.lseek(fd, 0, os.SEEK_SET)
    digest, size, magic = hashlib.sha256(), 0, b''
    while True:
        raw = os.read(fd, 1024 * 1024)
        if not raw:
            break
        if not magic:
            magic = raw[:4]
        size += len(raw)
        if size > ARTIFACT_LIMIT:
            raise ValueError('artifact size budget exceeded')
        digest.update(raw)
    if magic != b'\x7fELF' or size != expected.st_size or q.stamp(os.fstat(fd)) != q.stamp(expected):
        raise ValueError('artifact ELF bytes or source identity changed during read')
    return {'sha256': digest.hexdigest(), 'bytes': size}


def seal_artifacts(target):
    """Detach only Cargo's two fixed executable aliases; global hashing stays strict."""
    target = q.checked_directory(Path(target))
    receipts = {}
    for name in ARTIFACT_PATHS:
        path = target / name
        parent = q.open_directory(path.parent)
        source, temporary, owned_temporary, temporary_object = None, None, False, None
        try:
            before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink < 1
                    or (before.st_uid, before.st_gid) != (65534, 65534)
                    or not stat.S_IMODE(before.st_mode) & 0o111 or before.st_mode & 0o7000
                    or not 4 <= before.st_size <= ARTIFACT_LIMIT):
                raise ValueError('bounded owned regular executable artifact required: ' + name)
            source = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            first = _artifact_fd_hash(source, before)
            detached = before.st_nlink > 1
            if detached:
                temporary = '.linux-host-seal-' + secrets.token_hex(16)
                output = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=parent)
                owned_temporary = True
                created = os.fstat(output)
                temporary_object = (created.st_dev, created.st_ino)
                try:
                    os.lseek(source, 0, os.SEEK_SET)
                    copied, copied_size = hashlib.sha256(), 0
                    while True:
                        raw = os.read(source, 1024 * 1024)
                        if not raw:
                            break
                        copied_size += len(raw)
                        if copied_size > ARTIFACT_LIMIT:
                            raise ValueError('artifact copy size budget exceeded')
                        copied.update(raw)
                        pending = memoryview(raw)
                        while pending:
                            written = os.write(output, pending)
                            if written <= 0:
                                raise OSError('artifact copy made no progress')
                            pending = pending[written:]
                    os.fchmod(output, stat.S_IMODE(before.st_mode))
                    os.fsync(output)
                    temporary_identity = os.fstat(output)
                    if ({'sha256': copied.hexdigest(), 'bytes': copied_size} != first
                            or q.hash_at(parent, temporary, temporary_identity) != first):
                        raise ValueError('artifact copy bytes differ from original')
                finally:
                    os.close(output)
            second = _artifact_fd_hash(source, before)
            if second != first or q.stamp(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != q.stamp(before):
                raise ValueError('artifact source changed before atomic replacement')
            current_parent = q.open_directory(path.parent)
            try:
                if (os.fstat(current_parent).st_dev, os.fstat(current_parent).st_ino) != (os.fstat(parent).st_dev, os.fstat(parent).st_ino):
                    raise ValueError('artifact parent directory replaced')
            finally:
                os.close(current_parent)
            if detached:
                if q.stamp(os.stat(temporary, dir_fd=parent, follow_symlinks=False)) != q.stamp(temporary_identity):
                    raise ValueError('owned artifact temporary replaced')
                os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
                owned_temporary = False
                os.fsync(parent)
            artifact = q.stable_hash(path)
            final = path.stat(follow_symlinks=False)
            if artifact != first or final.st_nlink != 1 or final.st_uid != 65534 or final.st_gid != 65534:
                raise ValueError('sealed artifact bytes or ownership changed')
            receipts[name] = {'detached': detached, 'sourceIdentity': _artifact_identity(before),
                'sourceBefore': first, 'sourceAfter': second, 'artifact': artifact,
                'artifactIdentity': _artifact_identity(final)}
        finally:
            if source is not None:
                os.close(source)
            if owned_temporary:
                try:
                    remaining = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    remaining = None
                if remaining is not None and (remaining.st_dev, remaining.st_ino) == temporary_object:
                    os.unlink(temporary, dir_fd=parent)
                    os.fsync(parent)
            os.close(parent)
    return receipts


def built_artifacts(target):
    result = {}
    for name in ARTIFACT_PATHS:
        path = target / name
        result[name] = q.stable_hash(path)
        with path.open('rb') as stream:
            magic = stream.read(4)
        if not path.stat().st_mode & 0o111 or magic != b'\x7fELF':
            raise ValueError('new executable ELF artifact required: ' + name)
    return result


def run_build(root, repo, freeze, cargo_home, target, packages_root):
    """Execute one admitted freeze exactly once, retaining every failed attempt."""
    root, repo, cargo_home, target, packages_root = map(q.checked_directory, map(Path, (root, repo, cargo_home, target, packages_root)))
    freeze = Path(freeze)
    if not freeze.is_absolute():
        raise ValueError('absolute build freeze path required')
    if (root / 'stages/build').exists() or (root / 'stages/build').is_symlink():
        raise FileExistsError('build stage already attempted; preserve generation')
    observed = q.observe_environment(repo)
    q.validate_environment(observed)
    for other in (repo, cargo_home, packages_root):
        if target == other or target in other.parents or other in target.parents:
            raise ValueError('target overlaps frozen input paths')
    descriptor = q.open_directory(target)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
        if info.st_uid != 65534 or info.st_gid != 65534 or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('target owner65534 and mode700 required')
        if os.listdir(descriptor):
            raise ValueError('literal empty new target required; metadata caches are not cold')
        frozen_hash = q.stable_hash(freeze)
        value = q.read_json(freeze)
        if (type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1
                or value.get('profile') != 'persistent-update-linux-host'):
            raise ValueError('new Linux build freeze schema/profile required')
        q.verify_stage(root, 'prepare', frozen_hash['sha256'])
        prepared_hash = validate_admission(root, value, frozen_hash['sha256'])
        validate_provenance(value)
        require_readonly(cargo_home / 'registry')
        require_readonly(packages_root)
        validate_cargo_config(repo, cargo_home)
        snapshot = input_snapshot(repo, cargo_home, packages_root, value)
        verify_inputs(snapshot, value)
        for label, record in value['packages'].items():
            verify_package(packages_root / record['path'], label, snapshot['source'])
        for label in ('read-file', 'image-viewer'):
            if q.tree_inventory(repo / 'artifacts' / label) != snapshot['packages'][label]:
                raise ValueError('Rust host-test package projection differs: ' + label)

        def action(directory):
            q.write_json(directory / 'environment.json', observed)
            q.write_json(directory / 'cold-target.json', {'path': str(target), 'identity': identity, 'entries': [], 'literalEmpty': True})
            env = build_environment(cargo_home, target, directory, packages_root,
                                    {label: record['path'] for label, record in value['packages'].items()})
            initial_env = {key: val for key, val in env.items() if not key.startswith('MING_')}
            for name in ('home', 'tmp', 'commands'):
                (directory / name).mkdir(mode=0o700)
            q.write_json(directory / 'build-environment.json', {'initial': initial_env, 'explicit': env})
            results = []
            try:
                for number, entry in enumerate(q.build_commands(), 1):
                    session.check_identity()
                    q.verify_source(repo, value['source']['files'])
                    current = target.stat(follow_symlinks=False)
                    if (current.st_dev, current.st_ino, current.st_uid, current.st_gid, stat.S_IMODE(current.st_mode)) != identity:
                        raise ValueError('cold target directory identity changed')
                    command_root = directory / 'commands' / ('%02d' % number)
                    if q.stable_hash(freeze) != frozen_hash:
                        raise ValueError('build freeze bytes changed')
                    validate_cargo_config(repo, cargo_home)
                    if number == 1 and os.listdir(descriptor):
                        raise ValueError('literal empty target required immediately before first Cargo command')
                    result = q.execute(command_root, entry['command'], entry['timeoutSeconds'], cwd=repo,
                                       env=initial_env if number <= 5 else env)
                    if result['exitCode'] != 0 or result.get('timedOut') or not result.get('cleanupComplete'):
                        raise RuntimeError('Cargo command failed; preserve generation: ' + str(number))
                    if number == 1 or entry['expectedSummary'] is not None:
                        summary = summarize((command_root / 'stdout.log').read_text(), entry['expectedSummary'])
                        q.write_json(command_root / 'summary.json', summary)
                    results.append({'number': number, 'result': result})
                    if number == 5:
                        q.write_json(directory / 'pre-explicit-artifact-seal.json', seal_artifacts(target))
                        q.write_json(directory / 'pre-explicit-artifacts.json', built_artifacts(target))
                q.write_json(directory / 'final-artifact-seal.json', seal_artifacts(target))
                q.write_json(directory / 'artifacts.json', built_artifacts(target))
                return {'coldBuildCommands': len(results), 'target': str(target),
                        'formalQualification': False, 'finalSystemAccepted': False}
            finally:
                after = input_snapshot(repo, cargo_home, packages_root, value)
                q.write_json(directory / 'inputs-after.json', after)
                verify_inputs(after, value)
                if q.stable_hash(freeze) != frozen_hash:
                    raise ValueError('build freeze bytes changed')
                validate_provenance(value)
                validate_cargo_config(repo, cargo_home)
                if validate_admission(root, value, frozen_hash['sha256']) != prepared_hash:
                    raise ValueError('prepare result changed during build')

        with storage.storage_session(root / 'storage-control', value['storage']['probe'],
                                     sum(freezer.capacity_budget().values())) as session:
            q.run_stage(root, 'build', frozen_hash['sha256'], action)
        return q.verify_stage(root, 'build', frozen_hash['sha256'])
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'repo', 'freeze', 'cargo-home', 'target', 'packages-root'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    run_build(args.root, args.repo, args.freeze, args.cargo_home, args.target, args.packages_root)


if __name__ == '__main__':
    main()
