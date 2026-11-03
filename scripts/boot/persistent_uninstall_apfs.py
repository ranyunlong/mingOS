#!/usr/bin/env python3
"""Independent APFS-backed uninstall pair. `prepare` and --help never boot a VM.

The host flock covers cooperative writers using this repository's global lock.
It is not a security boundary against arbitrary host or Docker processes.
Original preparation, build, bundle, review and checker bytes remain read-only.
"""
import argparse
import contextlib
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
RUNTIME = Path('/run/mingos-runtime')
OPENSSL_SOURCE_IMAGE = 'sha256:3646133f4b4d2e007dd3eee28d9db6336c29f6ccb31b9f8a49b88bc0a70051be'
OPENSSL_SHA256 = 'acc10176d038aeb7b2a77d9ec972ac2985857a7191f7aecb7c54ef0e2c7e9549'
# Extracted from the frozen Rust image. Its required libraries were compared
# byte-for-byte with the frozen tools image; no checker or image is replaced.
OPENSSL_LIBRARIES = {
    '/lib/x86_64-linux-gnu/libc.so.6': '9792e3cbb541c8f44c7acf5f14f4022ea62998ecc787d326bed4d8b6547dfd92',
    '/lib/x86_64-linux-gnu/libcrypto.so.3': '8ae56f0a9481280a68c19f59809cd242a07a91ad7142944d21a878221992244b',
    '/lib/x86_64-linux-gnu/libssl.so.3': '9592038e92d1f41affcbb625334d0b6e788714c889104fa6d5b107cf9fd657f2',
    '/lib/x86_64-linux-gnu/libz.so.1': '85590dd58edf5445e18bc7193e5ebc01ac5841f1ae187e97705a662e90c6421e',
    '/lib/x86_64-linux-gnu/libzstd.so.1': '27f07c9a49c2c956bcfb64cd4712976586a66facbf15fc7f09bc37413b5f2b21',
    '/lib64/ld-linux-x86-64.so.2': 'c8438e4fde1934e61c88311633f00949ff645d5c04cdb8671fa3d78164d2f307',
}
HOST_CHANGES = {'scripts/boot/data_disk.py', 'scripts/boot/vm.py'}
EXECUTION_SOURCES = {'scripts/boot/persistent_uninstall_apfs.py', 'scripts/boot/test_persistent_uninstall_apfs.py',
                     'scripts/boot/test_runtime_storage.py'}


def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def read(path):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result: raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def write(path, value, exclusive=True):
    with Path(path).open('x' if exclusive else 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    sync_directory(Path(path).parent)


def replace_record(path, value):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    write(temporary, value)
    os.replace(temporary, path)
    sync_directory(path.parent)


def canonical(value):
    raw = os.fspath(value)
    path = Path(raw)
    if not path.is_absolute() or any(p in ('', '.', '..') for p in raw.split('/')[1:]) or path.resolve() != path:
        raise ValueError('canonical absolute path without symlinks required')
    return path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def overlaps(first, second):
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def reject_overlapping_writers(containers, targets):
    for container in containers:
        if container.get('State', {}).get('Running') is not True: continue
        for mount in container['Mounts']:
            if mount.get('RW') is True and mount.get('Type') == 'bind':
                source = Path(mount['Source']).resolve()
                if any(overlaps(source, canonical(target)) for target in targets):
                    raise ValueError('running container has overlapping writable bind: ' + str(source))


@contextlib.contextmanager
def writer_lock(path):
    path = canonical(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise ValueError('owned single-link native writer lock required')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def docker_json(argv):
    result = subprocess.run(['docker', *argv], capture_output=True, text=True, timeout=15)
    if result.returncode:
        if argv[:2] == ['container', 'inspect'] and 'no such' in result.stderr.lower(): return None
        raise RuntimeError('cannot determine Docker state: ' + result.stderr)
    return read_json_text(result.stdout)


def read_json_text(raw):
    return json.loads(raw)


def inspect_container(name):
    values = docker_json(['container', 'inspect', name])
    if values is None: return None
    if len(values) != 1 or values[0].get('Name') != '/' + name:
        raise ValueError('container identity differs')
    return values[0]


def stopped(value):
    return value is None or (value.get('State', {}).get('Running') is False
                            and value['State'].get('Pid') == 0 and value['State'].get('Restarting') is False)


def append_event(path, value):
    with path.open('a') as stream:
        stream.write(json.dumps(dict(at=now(), **value)) + '\n'); stream.flush(); os.fsync(stream.fileno())


def safety_event(path, value):
    # Storage exhaustion must never release a writer while Docker is unknown.
    try: append_event(path, value)
    except BaseException as error:
        try: print('supervisor evidence unavailable: ' + repr(error), file=sys.stderr, flush=True)
        except BaseException: pass


def ensure_stopped(name, evidence, inspect=inspect_container, stop=None, require_present=False):
    if stop is None:
        def stop(identifier):
            subprocess.run(['docker', 'stop', '--time', '3', identifier], capture_output=True, timeout=15, check=True)
    # Unknown daemon state retains the host descriptor. SIGKILL still leaves the
    # durable active record, so a later cooperative writer fails closed.
    while True:
        try:
            value = inspect(name)
            if require_present and value is None: raise RuntimeError('launched container absence is ambiguous')
            if not stopped(value):
                stop(name); value = inspect(name)
            if require_present and value is None: raise RuntimeError('launched container disappeared')
            if not stopped(value): raise RuntimeError('container not stopped with Pid 0')
            safety_event(evidence, {'event': 'container-stopped', 'container': value})
            return value
        except BaseException as error:
            safety_event(evidence, {'event': 'stop-state-unknown', 'error': repr(error), 'writerLockStillHeld': True})
            time.sleep(1)


def guarded_process(command, name, attempt, timeout, inspect=inspect_container, stop=None):
    child = None
    record = {'argv': command, 'timeoutSeconds': timeout, 'startedAt': now(), 'exitCode': None}
    write(attempt / 'command.json', record)
    old = {}
    def interrupt(signum, frame): raise InterruptedError('host signal ' + str(signum))
    try:
        for sig in (signal.SIGINT, signal.SIGTERM): old[sig] = signal.signal(sig, interrupt)
        with (attempt / 'stdout.log').open('xb') as stdout, (attempt / 'stderr.log').open('xb') as stderr:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
            record['exitCode'] = child.wait(timeout=timeout)
        if record['exitCode']: raise RuntimeError('pair container client failed')
        return record
    except BaseException as error:
        record['error'] = repr(error)
        raise
    finally:
        # Ignore further termination requests during the safety boundary. The
        # container, including its init/QEMU descendants, must be stopped first.
        for sig in old: signal.signal(sig, signal.SIG_IGN)
        while child is not None and child.poll() is None:
            try:
                try: os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try: os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                    child.wait(timeout=5)
            except BaseException as error:
                safety_event(attempt / 'lifecycle.jsonl', {'event': 'client-stop-unknown', 'error': repr(error), 'writerLockStillHeld': True})
                time.sleep(1)
        value = ensure_stopped(name, attempt / 'lifecycle.jsonl', inspect, stop, require_present=child is not None)
        record.update(completedAt=now(), containerState=None if value is None else value['State'],
                      stoppedConfirmedBeforeUnlock=True)
        write(attempt / 'command-result.json', record)
        for sig, handler in old.items(): signal.signal(sig, handler)


def verify_original(prep, repo=REPO):
    """Recheck old sealed bytes, allowing only the two explicit host additions."""
    stage = load(prep / 'run-stage.py', 'apfs_original_stage')
    original = read(prep / 'freeze.json')
    prior = None; sealed_inputs = {}
    for name in ('prepare', 'build', 'bundle'):
        path = prep / 'stages' / name / 'result.json'; record = read(path)
        if (record.get('stage') != name or record.get('status') != 'passed'
                or record.get('freezeSha256') != sha(prep / 'freeze.json')
                or record.get('previousResultSha256') != prior):
            raise ValueError('original stage seal chain differs')
        # Earlier directory roots can acquire later sealed products; every
        # originally sealed member must still have its original bytes.
        sealed_inputs.update({key: value for key, value in record['files'].items() if key.startswith('inputs/')})
        for relative, expected in record['files'].items():
            if sha(stage.relative(prep, relative)) != expected: raise ValueError('original sealed product changed: ' + relative)
        if name != 'prepare' and stage.product_hashes(prep, record['productRoots']) != record['files']:
            raise ValueError('original build/bundle product set differs')
        prior = sha(path)
    if stage.product_hashes(prep, ['inputs']) != sealed_inputs:
        raise ValueError('original input file set differs from sealed prepare/build union')
    for relative, expected in original['files'].items():
        actual = sha(stage.relative(repo, relative))
        if actual != expected and relative not in HOST_CHANGES:
            raise ValueError('original frozen source changed: ' + relative)
    review = stage.verify_review(prep, repo, original)
    sys.path.insert(0, str(repo / 'scripts/boot'))
    from bootlib import validate_bundle
    manifest = validate_bundle(prep / 'bundle', sha(prep / 'review.json'))
    if manifest['sessionReview'] != review: raise ValueError('original bundled review differs')
    for name, expected in manifest['scriptSha256'].items():
        if sha(repo / 'scripts/boot' / name) != expected: raise ValueError('original bundled guest script changed')
    checker = stage.load_checker(prep)
    fixture = checker.verify_fixture_directory(prep / 'build/fixture', prep / 'inputs', review)
    if fixture != read(prep / 'fixture-verification.json'): raise ValueError('original fixture verification differs')
    return stage, original, checker, review


def openssl_contract(path):
    return {'sourceImage': OPENSSL_SOURCE_IMAGE, 'hostPath': str(path), 'containerPath': '/usr/bin/openssl',
            'sha256': OPENSSL_SHA256, 'linuxDependencies': dict(OPENSSL_LIBRARIES)}


def verify_openssl_runtime(path, source_image, writable_roots=(), container=False, repo=REPO):
    path = canonical(path)
    if not path.is_relative_to(canonical(repo)):
        raise ValueError('OpenSSL input must be inside the read-only repository bind')
    if source_image != OPENSSL_SOURCE_IMAGE: raise ValueError('OpenSSL source image differs')
    if any(character in str(path) for character in '\\:,=\0'): raise ValueError('unsafe OpenSSL bind path')
    if any(overlaps(path, canonical(root)) for root in writable_roots):
        raise ValueError('OpenSSL input overlaps a writable runtime tree')
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError('single-link regular OpenSSL input required')
    if info.st_mode & 0o022 or not info.st_mode & 0o111: raise ValueError('OpenSSL input permissions differ')
    if sha(path) != OPENSSL_SHA256: raise ValueError('OpenSSL input hash differs')
    if container:
        if shutil.which('openssl') != '/usr/bin/openssl' or sha('/usr/bin/openssl') != OPENSSL_SHA256:
            raise ValueError('mounted OpenSSL executable differs')
        for library, expected in OPENSSL_LIBRARIES.items():
            if sha(library) != expected: raise ValueError('OpenSSL dependency hash differs: ' + library)
    return openssl_contract(path)


def execution_contract(identifier, storage, original, host_uid, openssl):
    return {'schemaVersion': 1, 'profile': 'persistent-uninstall-apfs-pair-v1',
            'containerName': identifier, 'toolsImage': original['environment']['toolsImage'],
            'hostUid': host_uid, 'hostCoordinator': '/private/tmp/mingos-persistent-uninstall-apfs-' + str(host_uid),
            'hostStorageRoot': str(storage), 'hostBackingRoot': str(storage / identifier),
            'containerBackingRoot': '/var/tmp', 'workRoot': '/var/tmp/mingos-boot-infra-20261025',
            'diskManifest': 'disks/' + identifier + '-128.json', 'diskSizeMiB': 128,
            'runtimeRoot': str(RUNTIME), 'runtimeFileSystem': 'tmpfs', 'runtimeBytes': 16777216,
            'runtimeMode': '0700', 'runtimeUid': 65534, 'runtimeGid': 65534,
            'opensslRuntime': openssl_contract(openssl),
            'uid': 65534, 'gid': 65534, 'cpus': 2, 'memoryBytes': 4294967296,
            'pidsLimit': 256, 'network': 'none', 'readOnlyRoot': True,
            'pairPolicy': 'one-container-two-fresh-sequential-qemu-strict-gate-after-each',
            'hostWriterScope': 'cooperative-current-uid-across-repositories',
            'coordinatorPersistence': 'temporary-host-coordinator-may-be-cleared-on-reboot',
            'recoveryPolicy': 'permanent-attempt-no-retry-and-fresh-docker-rw-overlap-admission',
            'minimumHostAvailableBytes': 2147483648, 'retainedWorkerBudgetsChanged': False}


def prepare(prep, attempt, storage, openssl, repo=REPO):
    prep, attempt, storage = map(canonical, (prep, attempt, storage))
    if overlaps(prep, attempt) or overlaps(prep, storage) or overlaps(storage, attempt):
        raise ValueError('original preparation, attempt and backing roots must be disjoint')
    openssl = canonical(openssl)
    verify_openssl_runtime(openssl, read(prep / 'freeze.json')['environment']['rustImage'], (attempt, storage), repo=repo)
    stage, original, checker, review = verify_original(prep, repo)
    attempt.mkdir(mode=0o700); sync_directory(attempt.parent)
    identifier = 'persistent-uninstall-apfs-' + uuid.uuid4().hex
    execution = json.loads(json.dumps(original))
    for name in HOST_CHANGES | EXECUTION_SOURCES: execution['files'][name] = sha(repo / name)
    write(attempt / 'execution-freeze.json', execution)
    origin = {'schemaVersion': 1, 'preparedAt': now(), 'prep': str(prep), 'repo': str(repo),
              'attempt': str(attempt), 'storageRoot': str(storage), 'identifier': identifier,
              'hostUid': os.geteuid(), 'executionEnvironment': execution_contract(identifier, storage, original, os.geteuid(), openssl),
              'originalFreezeSha256': sha(prep / 'freeze.json'), 'reviewSha256': sha(prep / 'review.json'),
              'bundleIdentity': sha(prep / 'bundle/bundle.json'), 'executionFreezeSha256': sha(attempt / 'execution-freeze.json'),
              'originalStageSeals': {name: sha(prep / 'stages' / name / 'result.json') for name in ('prepare', 'build', 'bundle')},
              'hostChanges': {name: {'originalSha256': original['files'][name], 'executionSha256': sha(repo / name)} for name in sorted(HOST_CHANGES)},
              'freezeMeaning': 'Execution source hashes only. Its environment is retained historical build metadata; executionEnvironment in this origin is the actual new runtime contract. Original build freeze, review and bundle remain unchanged.',
              'cooperativeWritersOnly': True, 'retryAllowed': False, 'finalSystemAccepted': False}
    write(attempt / 'execution-origin.json', origin)
    return origin


def verify_execution(attempt, repo=REPO):
    origin = read(attempt / 'execution-origin.json'); prep = canonical(origin['prep'])
    if not re.fullmatch(r'persistent-uninstall-apfs-[0-9a-f]{32}', origin['identifier']): raise ValueError('invalid unique attempt identifier')
    storage = canonical(origin['storageRoot'])
    openssl = canonical(origin['executionEnvironment']['opensslRuntime']['hostPath'])
    if any(overlaps(first, second) for first, second in ((prep, attempt), (prep, storage), (attempt, storage))):
        raise ValueError('preparation, attempt and backing roots must be disjoint')
    verify_openssl_runtime(openssl, read(prep / 'freeze.json')['environment']['rustImage'],
                           (attempt, storage), container=sys.platform == 'linux', repo=repo)
    stage, original, checker, review = verify_original(prep, repo)
    if origin['repo'] != str(repo) or origin['attempt'] != str(attempt): raise ValueError('execution path binding differs')
    for path, expected in [(prep / 'freeze.json', origin['originalFreezeSha256']), (prep / 'review.json', origin['reviewSha256']),
                           (prep / 'bundle/bundle.json', origin['bundleIdentity']), (attempt / 'execution-freeze.json', origin['executionFreezeSha256'])]:
        if sha(path) != expected: raise ValueError('execution provenance changed')
    if set(origin['originalStageSeals']) != {'prepare', 'build', 'bundle'}: raise ValueError('complete original stage seals required')
    if type(origin['hostUid']) is not int or origin['hostUid'] < 0: raise ValueError('host uid binding required')
    if sys.platform == 'darwin' and origin['hostUid'] != os.geteuid(): raise ValueError('host uid changed')
    if origin['executionEnvironment'] != execution_contract(origin['identifier'], storage, original, origin['hostUid'], openssl):
        raise ValueError('new execution environment differs from exact runtime contract')
    for name, expected in origin['originalStageSeals'].items():
        if sha(prep / 'stages' / name / 'result.json') != expected: raise ValueError('original stage seal changed')
    execution = read(attempt / 'execution-freeze.json')
    expected = json.loads(json.dumps(original))
    for name in HOST_CHANGES | EXECUTION_SOURCES: expected['files'][name] = sha(repo / name)
    if execution != expected: raise ValueError('execution freeze differs from exact host-only derivation')
    changes = {name: {'originalSha256': original['files'][name], 'executionSha256': sha(repo / name)} for name in sorted(HOST_CHANGES)}
    if origin['hostChanges'] != changes: raise ValueError('explicit host/test source delta changed')
    checker.verify_freeze(execution, repo, prep / 'inputs')
    return origin, stage, execution, checker, review


def docker_command(origin, attempt):
    repo = Path(origin['repo']); prep = Path(origin['prep'])
    original = read(prep / 'freeze.json')
    environment = origin['executionEnvironment']
    openssl = canonical(environment['opensslRuntime']['hostPath'])
    verify_openssl_runtime(openssl, original['environment']['rustImage'], (attempt, Path(origin['storageRoot'])), repo=repo)
    expected = execution_contract(origin['identifier'], Path(origin['storageRoot']), original, origin['hostUid'], openssl)
    if environment != expected: raise ValueError('Docker command runtime contract differs')
    backing = Path(environment['hostBackingRoot'])
    return ['docker', 'run', '--name', origin['identifier'], '--cidfile', str(attempt / 'container.cid'),
            '--label', 'mingos.qualification-id=' + origin['identifier'], '--pull', 'never', '--network', 'none',
            '--read-only', '--init', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--user', '65534:65534', '--pids-limit', '256', '--memory', '4g', '--cpus', '2',
            '--tmpfs', '/tmp:rw,exec,size=512m,mode=1777',
            '--tmpfs', str(RUNTIME) + ':rw,noexec,size=16m,uid=65534,gid=65534,mode=700',
            '--mount', 'type=bind,source=' + str(repo) + ',target=' + str(repo) + ',readonly',
            '--mount', 'type=bind,source=' + str(attempt) + ',target=' + str(attempt),
            '--mount', 'type=bind,source=' + str(backing) + ',target=/var/tmp',
            '--mount', 'type=bind,source=' + str(openssl) + ',target=/usr/bin/openssl,readonly', '--workdir', str(repo),
            environment['toolsImage'], 'python3', '-B', str(repo / 'scripts/boot/persistent_uninstall_apfs.py'),
            'inside', '--attempt-root', str(attempt)]


def host_coordinator():
    # One native namespace for this UID across repositories and backing roots.
    # /private/tmp may be cleared at reboot: permanent attempt latches and fresh
    # Docker RW overlap inspection remain necessary recovery checks.
    path = canonical(Path('/private/tmp') / ('mingos-persistent-uninstall-apfs-' + str(os.geteuid())))
    try: path.mkdir(mode=0o700)
    except FileExistsError: pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('owned 0700 native host coordinator required')
    return path


def check_previous_writer(active, inspect=inspect_container):
    if active.exists():
        canonical(active)
        previous = read(active)
        value = inspect(previous['identifier'])
        if (value is None and previous.get('containerLaunchIssued') is True) or not stopped(value):
            raise RuntimeError('previous cooperative writer container has not stopped')


def file_hashes(root, excluded=()):
    if not root.exists(): return {}
    values = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode): continue
        name = str(path.relative_to(root))
        if name in excluded: continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('evidence must contain only single-link regular files: ' + str(path))
        values[name] = sha(path)
    return values


def seal_attempt(attempt, backing, status):
    record = {'schemaVersion': 1, 'status': status, 'sealedAt': now(), 'attemptRoot': str(attempt),
              'backingRoot': str(backing), 'attemptFiles': file_hashes(attempt, ('evidence-seal.json',)),
              'backingFiles': file_hashes(backing), 'retryAllowed': False, 'finalSystemAccepted': False}
    write(attempt / 'evidence-seal.json', record)
    # Re-read bytes while the host writer descriptor is still held.
    if file_hashes(attempt, ('evidence-seal.json',)) != record['attemptFiles'] or file_hashes(backing) != record['backingFiles']:
        raise ValueError('evidence changed during final seal')
    return record


def host_run(attempt, repo=REPO):
    origin, stage, execution, checker, review = verify_execution(attempt, repo)
    storage = canonical(origin['storageRoot'])
    lock_dir = host_coordinator()
    lock_path = lock_dir / 'writer.lock'
    if overlaps(storage, lock_path) or overlaps(attempt, lock_path): raise ValueError('writable mounts must not expose host lock')
    with writer_lock(lock_path):
        active = lock_dir / 'active.json'
        check_previous_writer(active)
        stage.inspect_image(execution['environment']['toolsImage'])
        reject_overlapping_writers(stage.running_containers(),
                                   (storage, attempt, lock_dir, Path(origin['executionEnvironment']['opensslRuntime']['hostPath'])))
        if inspect_container(origin['identifier']) is not None: raise FileExistsError('attempt container already exists')
        # O_EXCL plus fsync is permanent, even after SIGKILL or host failure.
        write(attempt / 'attempt.json', {'startedAt': now(), 'retryAllowed': False, 'identifier': origin['identifier']})
        active_record = {'identifier': origin['identifier'], 'attempt': str(attempt), 'storage': str(storage), 'containerLaunchIssued': False}
        replace_record(active, active_record)
        try:
            if storage.exists() and not storage.is_dir(): raise ValueError('storage root must be directory')
            storage.mkdir(mode=0o755, exist_ok=True)
            disk = subprocess.run(['df', '-P', str(storage)], capture_output=True, text=True, timeout=15, check=True)
            mounts = subprocess.run(['/sbin/mount'], capture_output=True, text=True, timeout=15, check=True)
            device = disk.stdout.splitlines()[-1].split()[0]
            rows = [line for line in mounts.stdout.splitlines() if line.startswith(device + ' on ')]
            if sys.platform != 'darwin' or len(rows) != 1 or ' (apfs,' not in rows[0] or 'read-only' in rows[0]:
                raise ValueError('writable native host APFS backing required')
            available = os.statvfs(storage).f_bavail * os.statvfs(storage).f_frsize
            write(attempt / 'storage-inspection.json', {'df': disk.stdout, 'mount': rows[0], 'canonicalStorageRoot': str(storage),
                    'availableBytes': available, 'minimumAvailableBytes': 2147483648})
            if available < 2147483648: raise ValueError('host APFS available bytes below retained 2 GiB admission')
            backing = storage / origin['identifier']; backing.mkdir(mode=0o777); backing.chmod(0o777)
            attempt.chmod(0o777)
            command = docker_command(origin, attempt)
            active_record['containerLaunchIssued'] = True; replace_record(active, active_record)
            try:
                record = guarded_process(command, origin['identifier'], attempt,
                                         execution['budget']['containerTimeoutSeconds'])
            finally:
                # guarded_process proves the complete container has stopped before
                # returning or raising. The descriptor remains held through here.
                result_path = attempt / 'command-result.json'
                if result_path.exists() and read(result_path).get('stoppedConfirmedBeforeUnlock') is True:
                    append_event(attempt / 'lifecycle.jsonl', {'event': 'container-stopped-lock-retained', 'stoppedConfirmedBeforeUnlock': True})
            state = record['containerState']
            if state is None or state.get('ExitCode') != 0 or state.get('OOMKilled') is not False:
                raise RuntimeError('pair container failed; attempt and disk are preserved')
            verify_execution(attempt, repo)
            result = checker.pair(backing / 'mingos-boot-infra-20261025/cold-pair')
            write(attempt / 'host-checker-result.json', result)
            append_event(attempt / 'lifecycle.jsonl', {'event': 'writer-release-ready', 'stoppedConfirmedBeforeUnlock': True})
            write(attempt / 'result.json', dict(result, retryAllowed=False))
            seal_attempt(attempt, storage / origin['identifier'], 'passed')
        except BaseException as error:
            # Unknown container state cannot reach here: guarded_process keeps
            # the descriptor until its complete container is stopped.
            try:
                write(attempt / 'failure.json', {'status': 'failed', 'at': now(), 'error': repr(error),
                                                'retryAllowed': False, 'finalSystemAccepted': False})
                seal_attempt(attempt, storage / origin['identifier'], 'failed')
            except BaseException as evidence_error:
                try: print('failed attempt evidence could not be sealed: ' + repr(evidence_error), file=sys.stderr)
                except BaseException: pass
            raise
    return result


def inside_pair(attempt, repo=REPO):
    origin, stage, execution, checker, review = verify_execution(attempt, repo)
    sys.path.insert(0, str(repo / 'scripts/boot'))
    from data_disk import workspace, create_disk, runtime_directory
    from bootlib import validate_reviewed_input
    from infra import validate_cold_pair
    if sys.platform != 'linux' or os.geteuid() != 65534: raise ValueError('Linux uid65534 pair required')
    for name, value in {'memory.max': '4294967296', 'pids.max': '256', 'cpu.max': '200000 100000'}.items():
        if (Path('/sys/fs/cgroup') / name).read_text().strip() != value: raise ValueError('container limits changed')
    runtime_directory(RUNTIME)
    write(attempt / 'linux-runtime.json', {'root': str(RUNTIME), 'fileSystem': 'tmpfs', 'uid': os.geteuid(), 'mode': oct(RUNTIME.stat().st_mode & 0o777)})
    prep = Path(origin['prep']); environment = origin['executionEnvironment']
    work = workspace(Path(environment['workRoot']), create=True)
    disk = create_disk(work, origin['identifier'] + '-128', environment['diskSizeMiB'])
    if str(Path(disk['manifestPath']).relative_to(work)) != environment['diskManifest']:
        raise ValueError('created disk manifest differs from runtime contract')
    pair = work / 'cold-pair'; pair.mkdir(mode=0o700)
    write(pair / 'disk-preparation.json', disk)
    identity = origin['bundleIdentity']; reports = []; deadline = time.monotonic() + execution['budget']['pairTimeoutSeconds']
    raw_inputs = {n: validate_reviewed_input(review, prep / 'inputs' / ('input-boot%d.json' % n), n)[0] for n in (1, 2)}
    for n, raw in raw_inputs.items():
        path = pair / ('boot-%d.input.json' % n); path.write_bytes(raw); path.chmod(0o400)
    def run_one(number):
        verify_execution(attempt, repo)
        if deadline - time.monotonic() < 945: raise TimeoutError('insufficient complete boot budget')
        boot = pair / ('boot-%d' % number); guard = stage.RawGuard(boot / 'guest.jsonl', number)
        argv = [sys.executable, '-B', str(repo / 'scripts/boot/vm.py'), '--bundle', str(prep / 'bundle'), '--review', str(prep / 'review.json'),
                '--work-root', str(work), '--data-manifest', str(Path(disk['manifestPath']).relative_to(work)), '--output', str(boot),
                '--timeout', '900', '--boot-number', str(number), '--input-script', str(pair / ('boot-%d.input.json' % number)),
                '--runtime-root', str(RUNTIME), '--diagnostic-split-input', '--diagnostic-pointer-handshake']
        write(pair / ('boot-%d.command.json' % number), {'argv': argv, 'timeoutSeconds': 945, 'diskSha256Before': sha(disk['imagePath'])})
        with (pair / ('boot-%d.stdout.log' % number)).open('xb') as stdout, (pair / ('boot-%d.stderr.log' % number)).open('xb') as stderr:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True); started = time.monotonic()
            try:
                while child.poll() is None:
                    guard.poll()
                    if time.monotonic() - started >= 945 or time.monotonic() >= deadline: raise TimeoutError('boot/pair deadline')
                    time.sleep(.1)
                guard.poll(final=True)
                if child.returncode: raise RuntimeError('VM failed; preserve original disk/logs')
            except BaseException:
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired: os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=5)
                raise
        return read(boot / 'result.json')
    def gate(number, report):
        if not (report.get('status') == 'normal-desktop-poweroff' and report.get('bootAttempted') is True
                and report.get('kernelPowerdown') is True and report.get('guestExitCode') == 0 and report.get('inputComplete') is True
                and report.get('bootNumber') == number and report.get('bundleIdentity') == identity and report.get('diskUuid') == disk['uuid']
                and report.get('inputScriptSha256') == review['inputScripts']['boot%dSha256' % number]):
            raise ValueError('boot did not prove reviewed normal complete poweroff')
        if report.get('marker', {}).get('counter') != number or report.get('marker', {}).get('bootId') != report.get('bootId'):
            raise ValueError('fresh disk marker identity/sequence mismatch')
        if (number == 1 and report.get('previousMarker') is not None) or (number == 2 and report.get('previousMarker') != reports[0]['marker']):
            raise ValueError('previous marker chain mismatch')
        verified = checker.verify_boot(pair / ('boot-%d' % number), number, review, read_json_text(raw_inputs[number]),
                                       read(prep / 'fixture-verification.json'), identity, repo)
        reports.append(report)
        if number == 2: validate_cold_pair(*reports)
        return {'passed': True, 'bootNumber': number, 'strongRawVerified': True, 'diskSha256After': sha(disk['imagePath']), 'checkerResult': verified}
    result = stage.run_two_boots(pair, run_one, gate)
    if result['status'] == 'failed': raise RuntimeError('pair failed permanently; no retry or format')
    write(pair / 'verification-context.json', {'schemaVersion': 1, 'repo': str(repo), 'inputs': str(prep / 'inputs'),
          'review': str(prep / 'review.json'), 'bundle': str(prep / 'bundle'), 'fixtureDirectory': str(prep / 'build/fixture'),
          'fixtureVerification': str(prep / 'fixture-verification.json'), 'freeze': str(attempt / 'execution-freeze.json')})
    write(pair / 'inside-checker-result.json', checker.pair(pair))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'run', 'inside'))
    parser.add_argument('--attempt-root', required=True, type=canonical)
    parser.add_argument('--prep', type=canonical)
    parser.add_argument('--storage-root', type=canonical)
    parser.add_argument('--openssl-runtime', type=canonical)
    args = parser.parse_args(argv)
    if sys.version_info < (3, 11): parser.error('Python 3.11+ required for original frozen boot tooling')
    if args.operation == 'prepare':
        if args.prep is None or args.storage_root is None: parser.error('prepare requires --prep and --storage-root')
        if args.openssl_runtime is None: parser.error('prepare requires --openssl-runtime from the frozen Rust image')
        prepare(args.prep, args.attempt_root, args.storage_root, args.openssl_runtime)
    elif args.operation == 'run': host_run(args.attempt_root)
    else: inside_pair(args.attempt_root)
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr); raise SystemExit(1)
