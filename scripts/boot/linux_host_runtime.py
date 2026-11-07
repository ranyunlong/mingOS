"""Independent Linux runtime extension. Importing never creates disks or guests."""
import importlib.util
import gzip
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

import linux_host_qualification as q
import linux_host_build as builder
import linux_host_freeze as freezer
import linux_host_storage as storage

TOOLS_IMAGE = 'sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091'
INPUT_HASHES = {1: '823ccec8dd15a9b6a4e639f8448649af3b5d5251672970ba8c10aa4117562e10',
                2: '7bd20ab7f393496d5c21682070622dbbe5cc400aca35a64a0550b42c52f692ce'}
CHECKER_FILES = ('verify-persistent-update-evidence.py', 'raw_boot_audit.py', 'run-stage.py',
                 'input-geometry.json', 'geometry.stdout.log', 'geometry-execution.json',
                 'test_persistent_update_evidence.py', 'test_run_stage.py')
SOURCE_EXTENSIONS = {'probes/linux-isolation-source-v2/profile.json', 'scripts/boot/README.md',
    'scripts/boot/new-linux-host-build.ps1', 'scripts/boot/snapshot_linux_host_source.py',
    'scripts/boot/test_snapshot_linux_host_source.py', 'scripts/boot/test-new-linux-host-build.ps1',
    'scripts/boot/qualification_image_archive.py', 'scripts/boot/test_qualification_image_archive.py',
    'scripts/boot/restore-qualification-assets.ps1', 'scripts/test-restore-qualification-assets.ps1',
    'scripts/boot/test-restore-qualification-assets.ps1'}
REQUIRED_TOOLS = ('/usr/bin/python3', '/usr/bin/openssl', '/usr/bin/qemu-system-x86_64',
                  '/usr/bin/weston', '/usr/bin/cpio', '/usr/bin/ldd')
SOURCE_NORMALIZATIONS = {
    'scripts/boot/init': (
        {'sha256': '9380e1854e585031577639fe76e34d9a7fcdb75d42a632f697a7e047c3f31e2a', 'bytes': 751},
        {'sha256': '4fb1c7ff87a1d1b99cd3fbf175d347f67b2d8e86c643ef8d02430cb7ee5e0c05', 'bytes': 734}),
    '.gitattributes': (
        {'sha256': 'c4bdbfe6a1ec1421ed59531e5026a53570a54427f65bd73fbad3f6932de4fac3', 'bytes': 1023},
        {'sha256': '1484e582bc789fa0ac31a59e275907b745269cf20e7ea47e2809c9ece379b5a2', 'bytes': 1053}),
}


def source_extension(original, current):
    """Permit explicitly inventoried driver/docs changes, never production drift."""
    changes = {}
    for name in sorted(set(original) | set(current)):
        if original.get(name) != current.get(name):
            if name in SOURCE_NORMALIZATIONS:
                if (original.get(name), current.get(name)) != SOURCE_NORMALIZATIONS[name]:
                    raise ValueError('unreviewed source normalization: ' + name)
                changes[name] = {'before': original.get(name), 'after': current.get(name)}
                continue
            if not (name.startswith('docs/') or name.startswith('scripts/boot/linux_host_')
                    or name.startswith('scripts/boot/test_linux_host_')
                    or name in SOURCE_EXTENSIONS):
                raise ValueError('cold-build production source differs: ' + name)
            changes[name] = {'before': original.get(name), 'after': current.get(name)}
    return changes


def native_inputs(first, second):
    result = {}
    for number, path in ((1, Path(first)), (2, Path(second))):
        observed = q.stable_hash(path)
        if observed['sha256'] != INPUT_HASHES[number]:
            raise ValueError('exact frozen native input bytes required')
        spec = q.read_json(path)
        count = 5 if number == 1 else 2
        if len(spec['steps']) != count:
            raise ValueError('native input step count differs')
        result[str(number)] = {'path': str(path), **observed, 'steps': count,
                               'minimumSeconds': 300 + count * (45 + 5) + 120}
    return result


def cold_build_binding(build_root, build_freeze, target, repo):
    build_root, build_freeze, target, repo = map(Path, (build_root, build_freeze, target, repo))
    frozen = q.stable_hash(build_freeze)
    value = q.read_json(build_freeze)
    if value.get('profile') != 'persistent-update-linux-host':
        raise ValueError('independent Linux cold-build freeze required')
    result = q.verify_stage(build_root, 'build', frozen['sha256'])
    if result.get('detail', {}).get('coldBuildCommands') != 10:
        raise ValueError('complete ten-command Task9 build required')
    artifacts = builder.built_artifacts(target)
    if artifacts != q.read_json(build_root / 'stages/build/artifacts.json'):
        raise ValueError('cold-build artifact hashes differ')
    extension = source_extension(value['source']['files'], q.source_inventory(repo))
    if q.stable_hash(build_freeze) != frozen:
        raise ValueError('cold-build freeze changed')
    return {'root': str(build_root), 'freeze': str(build_freeze), 'freezeSha256': frozen['sha256'],
            'buildResult': q.stable_hash(build_root / 'stages/build/result.json'),
            'target': str(target), 'artifacts': artifacts, 'sourceExtension': extension}


def tools_binding(inspection, manifest):
    try:
        freezer.validate_image_receipt(inspection, TOOLS_IMAGE)
    except ValueError as error:
        raise ValueError('fixed tools image required') from error
    if (manifest.get('schemaVersion') != 1 or not isinstance(manifest.get('files'), dict)
            or not manifest['files']):
        raise ValueError('nonempty tools file manifest required')
    for name, expected in manifest['files'].items():
        path = Path(name)
        if not path.is_absolute() or path.resolve() != path or q.stable_hash(path) != expected:
            raise ValueError('frozen tool file changed: ' + name)
    return {'image': TOOLS_IMAGE, 'files': manifest['files'],
            'aptClosureQualification': 'development image observations only; no full offline APT closure claim'}


def geometry_binding(checker_dir, repo, cold):
    checker_dir = Path(checker_dir)
    receipt = q.read_json(checker_dir / 'input-geometry.json')
    raw = checker_dir / 'geometry.stdout.log'
    _load_driver(checker_dir).validate_geometry(receipt, Path(repo), raw)
    summaries = re.findall(r'^test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured;',
                           raw.read_text(), re.MULTILINE)
    if summaries != [('1', '0', '0', '0')]:
        raise ValueError('geometry exact test summary required')
    execution = q.read_json(checker_dir / 'geometry-execution.json')
    binary = Path(execution['binaryPath'])
    if (not binary.is_absolute() or binary.parent != Path(cold['target']) / 'release/deps'
            or not re.fullmatch(r'ming_app_host-[0-9a-f]+', binary.name)
            or binary.read_bytes()[:4] != b'\x7fELF'):
        raise ValueError('geometry cold-build unit ELF required')
    expected = [str(binary), '--exact', 'desktop_native::input_tests::persistent_update_two_app_input_geometry', '--nocapture']
    if (execution.get('schemaVersion') != 1 or execution.get('argv') != expected
            or execution.get('exitCode') != 0 or execution.get('binarySha256') != q.stable_hash(binary)['sha256']
            or execution.get('stdoutSha256') != q.stable_hash(raw)['sha256']
            or execution.get('coldBuildResultSha256') != cold['buildResult']['sha256']):
        raise ValueError('geometry exact execution binding differs')
    cargo = Path(cold['root']) / 'stages/build/commands/01/stderr.log'
    if not re.search(r'Running unittests src/lib\.rs \(' + re.escape(str(binary)) + r'\)', cargo.read_text()):
        raise ValueError('original Cargo unit artifact provenance missing')
    return execution


def runtime_snapshot(*, repo, build_root, build_freeze, target, packages_root, checker_dir,
                     input1, input2, tools_receipt, tools_manifest, probe_dir):
    paths = {key: str(Path(value)) for key, value in locals().copy().items()}
    repo, build_root, target, packages_root, checker_dir, probe_dir = map(q.checked_directory,
        map(Path, (repo, build_root, target, packages_root, checker_dir, probe_dir)))
    q.validate_environment(q.observe_environment(repo))
    for path in (build_root, target, packages_root, checker_dir, probe_dir):
        builder.require_readonly(path)
    for path in (build_freeze, tools_receipt, tools_manifest, input1, input2):
        freezer.readonly_input(Path(path))
    cold = cold_build_binding(build_root, build_freeze, target, repo)
    geometry = geometry_binding(checker_dir, repo, cold)
    original = q.read_json(build_freeze)
    packages = {}
    for label in builder.PACKAGES:
        directory = packages_root / original['packages'][label]['path']
        if q.tree_inventory(directory) != original['packages'][label]['files']:
            raise ValueError('cold-build Kotlin package changed: ' + label)
        builder.verify_package(directory, label, q.source_inventory(repo))
        packages[label] = {'path': str(directory), 'files': q.tree_inventory(directory)}
    tools = tools_binding(q.read_json(tools_receipt), q.read_json(tools_manifest))
    for name in REQUIRED_TOOLS:
        if str(Path(name).resolve()) not in tools['files']:
            raise ValueError('required executable missing from tools manifest: ' + name)
    kernels = list(Path('/boot').glob('vmlinuz-*'))
    if len(kernels) != 1:
        raise ValueError('exactly one runtime kernel required')
    for path in (kernels[0], Path('/boot') / ('config-' + kernels[0].name.removeprefix('vmlinuz-'))):
        if str(path.resolve()) not in tools['files']:
            raise ValueError('kernel/config missing from tools manifest')
    from isolation_profiles import load_profile, verify_review_and_files
    profile = load_profile('source-v2')
    probes = verify_review_and_files('source-v2', {'strictProbeProfile': 'source-v2',
        'strictProbeSha256': profile['artifactSha256']}, probe_dir)
    first, second = (q.read_json(Path('/prep') / name) for name in ('storage-probe.json', 'storage-reopen.json'))
    freezer.validate_reopen(first, second)
    return {'schemaVersion': 1, 'profile': 'persistent-update-linux-runtime', 'paths': paths,
            'coldBuild': cold, 'geometryExecution': geometry, 'packages': packages, 'runtimeSource': q.source_inventory(repo),
            'checker': {name: q.stable_hash(checker_dir / name) for name in CHECKER_FILES},
            'inputs': native_inputs(input1, input2), 'tools': tools,
            'toolReceipts': {str(path): q.stable_hash(path) for path in map(Path, (tools_receipt, tools_manifest))},
            'probes': {'contract': probes, 'files': q.tree_inventory(probe_dir)},
            'storage': {'probe': first, 'reopen': second},
            'formalQualification': False, 'finalSystemAccepted': False}


def prepare_runtime(root, **paths):
    root = q.checked_directory(Path(root))
    q.write_json(root / 'runtime-prepare-attempt.json', {'startedAt': q.now(), 'finalSystemAccepted': False})
    try:
        value = runtime_snapshot(**paths)
        q.write_json(root / 'runtime-freeze.json', value)
        digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
        def seal(stage):
            q.write_json(stage / 'admission.json', {'runtimeFreezeSha256': digest,
                         'coldBuild': value['coldBuild'], 'guestStarted': False})
            return {'runtimeInputsFrozen': True, 'guestStarted': False}
        q.run_stage(root, 'prepare', digest, seal)
    except Exception as error:
        q.write_json(root / 'runtime-prepare-result.json', {'status': 'failed', 'error': str(error),
                     'formalQualification': False, 'finalSystemAccepted': False})
        raise
    q.write_json(root / 'runtime-prepare-result.json', {'status': 'passed', 'runtimeFreezeSha256': digest,
                 'formalQualification': False, 'finalSystemAccepted': False})
    return value


def load_runtime(root, repo):
    root = q.checked_directory(Path(root))
    frozen = q.stable_hash(root / 'runtime-freeze.json')
    value = q.read_json(root / 'runtime-freeze.json')
    result = q.read_json(root / 'runtime-prepare-result.json')
    if (value.get('profile') != 'persistent-update-linux-runtime' or result.get('status') != 'passed'
            or result.get('runtimeFreezeSha256') != frozen['sha256'] or value['paths']['repo'] != str(repo)):
        raise ValueError('runtime prepare/freeze binding differs')
    q.verify_stage(root, 'prepare', frozen['sha256'])
    if runtime_snapshot(**value['paths']) != value or q.stable_hash(root / 'runtime-freeze.json') != frozen:
        raise ValueError('runtime immutable inputs changed')
    return value


def _runtime_root(root):
    root = q.checked_directory(Path(root))
    info = root.stat()
    if root != Path('/prep') or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (65534, 65534, 0o700):
        raise ValueError('runtime root must be /prep UID65534/GID65534 mode0700')
    for name in ('LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONPATH', 'PYTHONHOME', 'OPENSSL_CONF', 'OPENSSL_MODULES'):
        if os.environ.get(name):
            raise ValueError('ambient runtime injection forbidden: ' + name)


def _runtime_session(root, value):
    return storage.storage_session(root / 'storage-control', value['storage']['probe'],
                                   sum(freezer.capacity_budget().values()))


def fixture_command(catalog_hash):
    payload, fixture = Path('/prep/inputs/payload'), Path('/prep/build/fixture')
    return [str(payload / 'prepare-persistent-update-fixture'), 'initialize-first',
            str(fixture / 'data/install-root'), str(fixture / 'data'),
            *[str(payload / 'packages' / name / 'main.wasm') for name in ('bootstrap', 'old', 'new')],
            str(fixture / 'install-source/app.json'), str(payload / 'catalog.json'), catalog_hash,
            'seed', str(fixture / 'fixture-receipt.json')]


def _copy(source, destination, expected):
    before = q.stable_hash(source)
    if before != expected:
        raise ValueError('copy source differs from freeze: ' + str(source))
    with Path(source).open('rb') as raw, Path(destination).open('xb') as output:
        shutil.copyfileobj(raw, output)
        output.flush()
        os.fsync(output.fileno())
    if q.stable_hash(source) != before or q.stable_hash(destination) != before:
        raise ValueError('copy bytes differ from freeze')
    q.sync_directory(Path(destination).parent)


def _load_checker(root):
    # The unchanged checker explicitly maps HERE=/prep to its readonly /repo.
    spec = importlib.util.spec_from_file_location('linux_runtime_bound_checker', root / CHECKER_FILES[0])
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return checker


def _load_driver(root):
    spec = importlib.util.spec_from_file_location('linux_runtime_bound_driver', root / 'run-stage.py')
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    return driver


def review_base(root, value):
    from bootlib import PERSISTENT_UPDATE_ARGS, PERSISTENT_UPDATE_FIXTURE, PERSISTENT_UPDATE_PROFILE
    from wayland_receipt import POINTER_SYNC_POLICY
    payload = root / 'inputs/payload'
    digest = lambda path: q.stable_hash(path)['sha256']
    return {'reviewed': False, 'reviewer': None, 'profile': 'persistent-update', 'sessionMode': 'long-lived',
        'extraArgs': PERSISTENT_UPDATE_ARGS, 'persistentInstallState': '/data/mingos/install-root',
        'catalogPath': '/data/mingos/install-root/catalog.json', 'installSource': '/opt/mingos/install-source/app.json',
        'persistentUpdateFixture': PERSISTENT_UPDATE_FIXTURE,
        'persistentUpdateFixtureSha256': {name: digest(payload / PERSISTENT_UPDATE_FIXTURE[name].removeprefix('/opt/mingos/'))
            for name in ('helper', 'bootstrapWasm', 'oldWasm', 'newWasm')},
        'hostSha256': digest(payload / 'ming-app-host'), 'catalogSha256': digest(payload / 'catalog.json'),
        'inputScripts': {'boot%dSha256' % n: value['inputs'][str(n)]['sha256'] for n in (1, 2)},
        'strictProbeProfile': 'source-v2', 'strictProbeSha256': value['probes']['contract']['artifactSha256'],
        'instrumentedQualification': True, 'instrumentedProfile': PERSISTENT_UPDATE_PROFILE,
        'inputSynchronization': POINTER_SYNC_POLICY,
        'instrumentation': {'desktopEnv': {'WAYLAND_DEBUG': 'client'}, 'stderrLimitBytes': 8388608,
            'observerSha256': digest(Path(value['paths']['repo']) / 'scripts/boot/evdev_observer.py'),
            'observerMaxEvents': 4096, 'observerMaxSeconds': 900, 'exclusiveGrab': False, 'readyCaptureSeconds': None},
        'budgetEvidence': 'execution-budget.json', 'budgetEvidenceSha256': digest(root / 'execution-budget.json'),
        'sourceFreezeSha256': digest(root / 'runtime-freeze.json'),
        'coldBuildFreezeSha256': value['coldBuild']['freezeSha256'], 'finalSystemAccepted': False}


def _fixture_products(root):
    return {'payload': q.tree_inventory(root / 'inputs/payload'), 'fixture': q.tree_inventory(root / 'build/fixture'),
            'reviewBase': q.stable_hash(root / 'build/review-base.json'),
            'signedFixture': q.stable_hash(root / 'fixture-verification.json'),
            'budget': q.stable_hash(root / 'execution-budget.json'),
            'references': {name: q.stable_hash(root / name) for name in (*CHECKER_FILES, 'input-boot1.json', 'input-boot2.json',
                'inputs/input-boot1.json', 'inputs/input-boot2.json')}}


def _materialize_fixture(root, repo, value, stage):
    payload = root / 'inputs/payload'
    payload.mkdir(parents=True, mode=0o700)
    fixture = root / 'build/fixture'
    fixture.mkdir(parents=True, mode=0o700)
    (fixture / 'data').mkdir(mode=0o700)
    for name, relative in (('ming-app-host', 'release/ming-app-host'),
                           ('prepare-persistent-update-fixture', 'release/examples/prepare_persistent_update_fixture')):
        _copy(Path(value['paths']['target']) / relative, payload / name, value['coldBuild']['artifacts'][relative])
        (payload / name).chmod(0o755)
    for label, package in (('bootstrap', 'text-entry-demo'), ('old', 'old'), ('new', 'new')):
        directory = payload / 'packages' / label
        directory.mkdir(parents=True, mode=0o700)
        record = value['packages'][package]
        _copy(Path(record['path']) / 'main.wasm', directory / 'main.wasm', record['files']['main.wasm'])
    q.write_json(payload / 'catalog.json', {'schemaVersion': 3, 'apps': [
        {'id': 1, 'title': 'Text Lab', 'packageId': 'dev.ming.text-entry-demo'}], 'autostart': [1]})
    for name in CHECKER_FILES:
        _copy(Path(value['paths']['checker_dir']) / name, root / name, value['checker'][name])
    for n in (1, 2):
        record = value['inputs'][str(n)]
        _copy(Path(record['path']), root / ('input-boot%d.json' % n), {key: record[key] for key in ('sha256', 'bytes')})
        _copy(Path(record['path']), root / 'inputs' / ('input-boot%d.json' % n), {key: record[key] for key in ('sha256', 'bytes')})
    _load_driver(root).validate_geometry(q.read_json(root / 'input-geometry.json'), repo, root / 'geometry.stdout.log')
    q.exact_summary((root / 'geometry.stdout.log').read_text(), (1, 0, 0, 0))
    q.write_json(root / 'execution-budget.json', {'guestWatchdogSeconds': 300, 'pointerWaitSeconds': 5,
        'bootTimeoutSeconds': 900, 'bootSupervisorSeconds': 945, 'pairTimeoutSeconds': 1920,
        'containerTimeoutSeconds': 1950, 'bootMinimumSeconds': {'1': 670, '2': 520},
        'finalSystemAccepted': False})
    base = review_base(root, value)
    q.write_json(root / 'build/review-base.json', base)
    command = fixture_command(base['catalogSha256'])
    result = q.execute(fixture / 'transport', command, 60, env=runtime_environment())
    q.write_json(fixture / 'command.json', dict(result, stage='fixture', argv=command,
        commandRoot='/prep/build/fixture', inputsRoot='/prep/inputs',
        executableSha256=base['persistentUpdateFixtureSha256']['helper']))
    for name in ('stdout.log', 'stderr.log'):
        _copy(fixture / 'transport' / name, fixture / name, q.stable_hash(fixture / 'transport' / name))
    if result['exitCode'] != 0 or result.get('timedOut') or not result.get('cleanupComplete'):
        raise RuntimeError('real fixture helper failed; preserve runtime generation')
    signed = _load_checker(root).verify_fixture_directory(fixture, root / 'inputs', base)
    q.write_json(root / 'fixture-verification.json', signed)
    q.write_json(stage / 'external-products.json', _fixture_products(root))
    load_runtime(root, repo)
    return {'realSignedFixture': True, 'guestStarted': False}


def materialize_fixture(root, repo):
    root, repo = Path(root), Path(repo)
    value = load_runtime(root, repo)
    _runtime_root(root)
    digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
    with _runtime_session(root, value):
        q.run_stage(root, 'build', digest, lambda stage: _materialize_fixture(root, repo, value, stage))
    return q.verify_stage(root, 'build', digest)


def verify_review(root):
    root = Path(root)
    review = q.read_json(root / 'review.json')
    base = q.read_json(root / 'build/review-base.json')
    if (review.get('reviewed') is not True or not isinstance(review.get('reviewer'), str)
            or not review['reviewer'].strip() or dict(review, reviewed=False, reviewer=None) != base):
        raise ValueError('exact primary review of actual runtime payload required')
    return review


def runtime_environment():
    return {'PATH': '/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/tmp',
            'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}


def bundle_command(root, repo, probe_dir):
    return [sys.executable, '-B', str(repo / 'scripts/boot/bundle.py'), '--payload', str(root / 'inputs/payload'),
            '--review', str(root / 'review.json'), '--output', str(root / 'bundle'),
            '--isolation-profile', 'source-v2', '--isolation-probes', str(probe_dir),
            '--input-diagnostics', '--wayland-client-diagnostics', '--pointer-handshake-diagnostics']


def inspect_initramfs(path, required):
    """Stream newc bytes without extracting files, following links, or executing init."""
    seen, found, total = set(), {}, 0
    with gzip.open(path, 'rb') as archive:
        while True:
            header = archive.read(110)
            if len(header) != 110 or header[:6] != b'070701':
                raise ValueError('truncated or unsupported newc archive')
            try:
                fields = [int(header[6 + 8 * n:14 + 8 * n], 16) for n in range(13)]
            except ValueError as error:
                raise ValueError('invalid newc fields') from error
            mode, uid, gid, size, namesize = fields[1], fields[2], fields[3], fields[6], fields[11]
            if not 1 <= namesize <= 4096 or size > 256 * 1024 ** 2:
                raise ValueError('newc member bound exceeded')
            rawname = archive.read(namesize)
            if len(rawname) != namesize or rawname[-1:] != b'\0':
                raise ValueError('truncated newc name')
            name = rawname[:-1].decode('utf-8')
            padding = -(110 + namesize) % 4
            if archive.read(padding) != b'\0' * padding:
                raise ValueError('invalid/truncated newc name padding')
            if name == 'TRAILER!!!':
                if size:
                    raise ValueError('invalid newc trailer')
                # Consume to gzip EOF so CRC/truncation is checked as well.
                tail = archive.read(1024 * 1024 + 1)
                if len(tail) > 1024 * 1024 or any(tail):
                    raise ValueError('unexpected bytes after newc trailer')
                break
            if (name in seen or name.startswith('/') or '\\' in name or '\0' in name
                    or (name != '.' and any(part in ('', '.', '..') for part in name.split('/')))
                    or uid != 0 or gid != 0 or stat.S_IFMT(mode) not in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK)):
                raise ValueError('unsafe/duplicate/non-root newc member')
            seen.add(name)
            if len(seen) > 65536:
                raise ValueError('newc file count bound exceeded')
            digest, remaining = hashlib.sha256(), size
            while remaining:
                raw = archive.read(min(1024 * 1024, remaining))
                if not raw:
                    raise ValueError('truncated newc member')
                digest.update(raw)
                remaining -= len(raw)
            padding = -size % 4
            if archive.read(padding) != b'\0' * padding:
                raise ValueError('invalid/truncated newc data padding')
            total += size
            if total > 2 * 1024 ** 3:
                raise ValueError('newc total byte bound exceeded')
            if name in required:
                if not stat.S_ISREG(mode) or digest.hexdigest() != required[name]:
                    raise ValueError('required initramfs file bytes differ: ' + name)
                found[name] = digest.hexdigest()
    if found != required:
        raise ValueError('required initramfs file set incomplete')
    return {'entries': len(seen), 'uncompressedFileBytes': total, 'requiredFiles': found}


def crypto_probe(directory):
    directory = Path(directory)
    private, public, message, signature = (directory / name for name in ('key.der', 'public.der', 'message', 'signature'))
    for path, raw in ((private, bytes.fromhex('302e020100300506032b657004220420') + bytes([7]) * 32),
                      (public, bytes.fromhex('302a300506032b6570032100ea4a6c63e29c520abef5507b132ec5f9954776aebebe7b92421eea691446d22c')),
                      (message, b'mingos-linux-runtime-admission-v1')):
        with path.open('xb') as output:
            output.write(raw)
        path.chmod(0o600)
    def command(label, args, expected):
        argv = ['/usr/bin/openssl', 'pkeyutl', *args]
        result = q.execute(directory / label, argv, 10, env=runtime_environment())
        if result['exitCode'] != expected or result.get('timedOut') or not result.get('cleanupComplete'):
            raise ValueError('actual OpenSSL probe failed: ' + label)
    command('sign', ['-sign', '-inkey', str(private), '-keyform', 'DER', '-rawin', '-in', str(message), '-out', str(signature)], 0)
    verify = ['-verify', '-pubin', '-inkey', str(public), '-keyform', 'DER', '-rawin', '-in', str(message), '-sigfile', str(signature)]
    command('positive', verify, 0)
    message.write_bytes(b'tampered-mingos-linux-runtime-admission-v1')
    command('negative', verify, 1)
    return {'ed25519PositiveVerified': True, 'ed25519NegativeRejected': True,
            'publicKeySha256': q.stable_hash(public)['sha256'], 'signature': q.stable_hash(signature)}


def _verify_fixture_stage(root):
    digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
    q.verify_stage(root, 'build', digest)
    if q.read_json(root / 'stages/build/external-products.json') != _fixture_products(root):
        raise ValueError('fixture/payload/reference products changed')


def verify_bundle_products(root):
    root = Path(root)
    _verify_fixture_stage(root)
    digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
    q.verify_stage(root, 'bundle', digest)
    if q.read_json(root / 'stages/bundle/external-products.json') != {
            'bundle': q.tree_inventory(root / 'bundle'), 'review': q.stable_hash(root / 'review.json')}:
        raise ValueError('bundle or primary review bytes changed')
    return q.read_json(root / 'bundle/bundle.json')


def _bundle_action(root, repo, value, stage):
    from bootlib import validate_bundle, validate_kernel_config, validate_reviewed_input
    from bundle import verify_payload
    review = verify_review(root)
    _verify_fixture_stage(root)
    verify_payload(root / 'inputs/payload', review)
    for number in (1, 2):
        validate_reviewed_input(review, root / 'inputs' / ('input-boot%d.json' % number), number)
    result = q.execute(stage / 'transport', bundle_command(root, repo, Path(value['paths']['probe_dir'])),
                       300, env=runtime_environment())
    if result['exitCode'] != 0 or result.get('timedOut') or not result.get('cleanupComplete'):
        raise RuntimeError('real bundle failed; preserve runtime generation')
    manifest = validate_bundle(root / 'bundle', q.stable_hash(root / 'review.json')['sha256'])
    validate_kernel_config((root / 'bundle/kernel.config').read_text())
    required = {'opt/mingos/' + name: record['sha256'] for name, record in q.tree_inventory(root / 'inputs/payload').items()}
    required['etc/mingos/session.json'] = q.stable_hash(root / 'review.json')['sha256']
    for name in ('init', 'bootlib.py', 'guest.py', 'guest_platform.py', 'infra.py', 'bounded_log.py',
                 'wayland_receipt.py', 'worker_resources.py', 'weston.ini', 'persistent_update_receipt.py', 'evdev_observer.py'):
        required['init' if name == 'init' else 'etc/mingos/' + name] = q.stable_hash(repo / 'scripts/boot' / name)['sha256']
    for source, destination in (('mingos-isolation-probe', 'usr/local/bin/mingos-isolation-probe'),
                                 ('vm-prepare.sh', 'usr/local/libexec/mingos-isolation-prepare.sh')):
        required[destination] = value['probes']['contract']['artifactSha256'][source]
    q.write_json(stage / 'initramfs-inspection.json', inspect_initramfs(root / 'bundle/initramfs.cpio.gz', required))
    if (manifest.get('sessionReview') != review or manifest.get('payloadProfile') != 'persistent-update'
            or manifest.get('instrumentedQualification') is not True or manifest.get('diagnosticOnly') is not False):
        raise ValueError('actual persistent-update qualification bundle required')
    # rootfs is a derived build intermediate with intended runtime symlinks.
    # Its complete archive was inspected above; all sealed outputs stay regular.
    shutil.rmtree(root / 'bundle/rootfs')
    load_runtime(root, repo)
    q.write_json(stage / 'external-products.json', {'bundle': q.tree_inventory(root / 'bundle'),
                                                  'review': q.stable_hash(root / 'review.json')})
    return {'actualBundleCreated': True, 'initramfsRequiredFilesVerified': len(required), 'guestStarted': False}


def bundle_runtime(root, repo):
    root, repo = Path(root), Path(repo)
    value = load_runtime(root, repo)
    _runtime_root(root)
    digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
    with _runtime_session(root, value):
        q.run_stage(root, 'bundle', digest, lambda stage: _bundle_action(root, repo, value, stage))
    return verify_bundle_products(root)


def qmp_probe(directory, runtime_root):
    """Run a paused machine without kernel, initrd, disk, or guest execution."""
    from bootlib import Qmp
    from data_disk import runtime_directory
    directory, runtime_root = Path(directory), runtime_directory(runtime_root)
    socket_path = runtime_root / 'admission-qmp.sock'
    if socket_path.exists() or socket_path.is_symlink():
        raise FileExistsError('admission QMP socket already attempted')
    command = ['/usr/bin/qemu-system-x86_64', '-machine', 'q35,accel=tcg', '-m', '64', '-smp', '1',
               '-display', 'none', '-nodefaults', '-S', '-qmp', 'unix:' + str(socket_path) + ',server=on,wait=off']
    with (directory / 'qemu.stdout.log').open('xb') as out, (directory / 'qemu.stderr.log').open('xb') as err:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                   env=runtime_environment())
        qmp = None
        try:
            qmp = Qmp(socket_path, timeout=10, transcript_path=directory / 'qmp.jsonl')
            qmp.execute('quit')
            process.wait(timeout=5)
            if process.returncode != 0:
                raise ValueError('paused QMP machine did not quit successfully')
        finally:
            if qmp is not None:
                qmp.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    return {'command': command, 'pausedNoKernelQmpQuitVerified': True, 'guestStarted': False}


def _ldd_probe(stage, binaries, tools):
    records = {}
    for n, binary in enumerate(binaries):
        if Path(binary).read_bytes()[:4] != b'\x7fELF':
            raise ValueError('ELF binary required for dynamic closure: ' + str(binary))
        destination = stage / ('ldd-%02d' % n)
        result = q.execute(destination, ['/usr/bin/ldd', str(binary)], 15, env=runtime_environment())
        raw = (destination / 'stdout.log').read_text() + (destination / 'stderr.log').read_text()
        if result['exitCode'] != 0 or 'not found' in raw:
            raise ValueError('unresolved ELF dynamic closure: ' + str(binary))
        dependencies = {}
        for name in re.findall(r'(?:=>\s+)?(/[^\s(]+)', raw):
            path = Path(name).resolve()
            observed = q.stable_hash(path)
            if tools['files'].get(str(path)) != observed:
                raise ValueError('ELF dependency outside frozen tools: ' + str(path))
            dependencies[str(path)] = observed
        records[str(binary)] = dependencies
    return records


def tmpfs_probe(directory):
    """Bounded tmpfs I/O and real child lock checks without disk reservation."""
    import fcntl
    from data_disk import runtime_directory
    directory = runtime_directory(Path(directory))
    lease = directory / 'lease'
    fd = os.open(lease, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, b'runtime admission\n')
        os.fsync(fd)
        q.sync_directory(directory)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code = ('import fcntl,os,sys; f=os.open(sys.argv[1],os.O_RDWR|os.O_NOFOLLOW);'
                '\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
                '\nexcept BlockingIOError: sys.exit(23)')
        for name, expected in (('held', 23), ('released', 0)):
            if name == 'released':
                fcntl.flock(fd, fcntl.LOCK_UN)
            result = q.execute(directory / name, [sys.executable, '-I', '-B', '-c', code, str(lease)],
                               10, env=runtime_environment())
            if result['exitCode'] != expected or result.get('timedOut') or not result.get('cleanupComplete'):
                raise ValueError('tmpfs real child lock check failed')
        renamed = directory / 'renamed'
        lease.rename(renamed)
        q.sync_directory(directory)
        renamed.unlink()
        q.sync_directory(directory)
        available = os.statvfs(directory)
        return {'lockCompetitorBlocked': True, 'lockAvailableAfterRelease': True,
                'fileAndDirectoryFsyncVerified': True,
                'observedAvailableBytes': available.f_bavail * available.f_frsize}
    finally:
        os.close(fd)


def _admit_action(root, repo, value, stage, runtime_root):
    from bootlib import validate_kernel_config
    from data_disk import runtime_directory
    import socket
    runtime_root = runtime_directory(Path(runtime_root))
    verify_bundle_products(root)
    validate_kernel_config((root / 'bundle/kernel.config').read_text())
    binaries = [root / 'inputs/payload/ming-app-host', root / 'inputs/payload/prepare-persistent-update-fixture',
                Path(value['paths']['probe_dir']) / 'mingos-isolation-probe',
                *map(Path, ('/usr/bin/python3', '/usr/bin/openssl', '/usr/bin/qemu-system-x86_64', '/usr/bin/weston'))]
    q.write_json(stage / 'elf-closure.json', _ldd_probe(stage, binaries, value['tools']))
    crypto = stage / 'crypto'
    crypto.mkdir(mode=0o700)
    q.write_json(stage / 'crypto-result.json', crypto_probe(crypto))
    probe = runtime_root / 'admission-storage'
    probe.mkdir(mode=0o700)
    observed = tmpfs_probe(probe)
    q.write_json(stage / 'tmpfs-storage.json', observed)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(runtime_root / 'admission-bound.sock'))
        sock.listen(1)
    finally:
        sock.close()
    # This explicit child entry permits process-tree containment by q.execute.
    qmp = q.execute(stage / 'qmp-transport', [sys.executable, '-B', str(repo / 'scripts/boot/linux_host_runtime.py'),
        '--probe-qmp', str(stage), '--runtime-root', str(runtime_root)], 30, env=runtime_environment())
    if qmp['exitCode'] != 0 or qmp.get('timedOut') or not qmp.get('cleanupComplete'):
        raise ValueError('bounded paused no-kernel QMP probe failed')
    commands = [
        ('runtime-copy', [sys.executable, '-B', str(repo / 'scripts/boot/check-runtime.py'),
                          '--profile', 'persistent-update', '--output', str(stage / 'runtime-copy-result')]),
        *[(name.removesuffix('.py'), [sys.executable, '-B', '-m', 'unittest', 'discover',
                                     '-s', str(root), '-p', name, '-v'])
          for name in ('test_persistent_update_evidence.py', 'test_run_stage.py')],
        ('pair-boundary', [sys.executable, '-B', str(repo / 'scripts/boot/linux_host_pair.py'),
                           '--probe-boundary', '--root', str(root), '--repo', str(repo),
                           '--output', str(stage / 'pair-boundary-result.json')])]
    for name, command in commands:
        result = q.execute(stage / name, command, 120, env=runtime_environment())
        if result['exitCode'] != 0 or result.get('timedOut') or not result.get('cleanupComplete'):
            raise ValueError('actual no-guest admission control failed: ' + name)
    boundary = q.read_json(stage / 'pair-boundary-result.json')
    if (boundary.get('vmLaunchIntercepted') is not True or type(boundary.get('vmLaunchCount')) is not int
            or boundary['vmLaunchCount'] != 1 or boundary.get('secondBootAfterFailureBlocked') is not True
            or any(boundary.get(name) is not False for name in ('guestStarted', 'dataDiskCreated', 'finalSystemAccepted'))
            or boundary.get('runtimeFreezeSha256') != q.stable_hash(root / 'runtime-freeze.json')['sha256']
            or boundary.get('supervisorResult', {}).get('exitCode') in (None, 0)
            or boundary.get('supervisorResult', {}).get('cleanupComplete') is not True):
        raise ValueError('actual pair launch interception binding differs')
    load_runtime(root, repo)
    q.write_json(stage / 'external-products.json', {'bundle': q.tree_inventory(root / 'bundle'),
        'review': q.stable_hash(root / 'review.json'), 'runtimeFreeze': q.stable_hash(root / 'runtime-freeze.json')})
    return {'noGuestRuntimeAdmission': True, 'guestStarted': False, 'dataDiskCreated': False,
            'elfClosureVerified': True, 'ed25519PositiveNegativeVerified': True,
            'tmpfsSocketStorageLockVerified': True, 'pausedNoKernelQmpQuitVerified': True,
            'guestPythonRuntimeCopyVerified': True, 'originalCheckerDriverControlsPassed': True,
            'pairLaunchBoundaryVerified': True,
            'guestUidNamespaceExecutionVerified': False}


def admit_runtime(root, repo, runtime_root=Path('/run/mingos-runtime')):
    root, repo = Path(root), Path(repo)
    value = load_runtime(root, repo)
    _runtime_root(root)
    verify_bundle_products(root)
    digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
    with _runtime_session(root, value):
        q.run_stage(root, 'admission', digest, lambda stage: _admit_action(root, repo, value, stage, runtime_root))
    return verify_admission_products(root)


def verify_admission_products(root):
    root = Path(root)
    verify_bundle_products(root)
    digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
    result = q.verify_stage(root, 'admission', digest)
    expected = {'bundle': q.tree_inventory(root / 'bundle'), 'review': q.stable_hash(root / 'review.json'),
                'runtimeFreeze': q.stable_hash(root / 'runtime-freeze.json')}
    if q.read_json(root / 'stages/admission/external-products.json') != expected:
        raise ValueError('admitted bundle/review/freeze products changed')
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--probe-qmp', type=Path)
    mode.add_argument('--stage', choices=('prepare', 'fixture', 'bundle', 'admission'))
    parser.add_argument('--root', type=Path)
    parser.add_argument('--runtime-root', type=Path, default=Path('/run/mingos-runtime'))
    names = ('repo', 'build_root', 'build_freeze', 'target', 'packages_root', 'checker_dir',
             'input1', 'input2', 'tools_receipt', 'tools_manifest', 'probe_dir')
    for name in names:
        parser.add_argument('--' + name.replace('_', '-'), type=Path)
    args = parser.parse_args(argv)
    if args.probe_qmp is not None:
        if args.root is not None or any(getattr(args, name) is not None for name in names):
            parser.error('QMP probe cannot be combined with runtime stage paths')
        q.write_json(args.probe_qmp / 'qmp-result.json', qmp_probe(args.probe_qmp, args.runtime_root))
        return
    required = names if args.stage == 'prepare' else ('repo',)
    if args.root is None or any(getattr(args, name) is None for name in required):
        parser.error('stage requires --root and ' + ', '.join('--' + name.replace('_', '-') for name in required))
    if args.stage != 'prepare' and any(getattr(args, name) is not None for name in names if name != 'repo'):
        parser.error('runtime input paths are accepted only by prepare')
    if args.stage == 'prepare':
        prepare_runtime(args.root, **{name: getattr(args, name) for name in names})
    elif args.stage == 'fixture':
        materialize_fixture(args.root, args.repo)
    elif args.stage == 'bundle':
        bundle_runtime(args.root, args.repo)
    else:
        admit_runtime(args.root, args.repo, args.runtime_root)


if __name__ == '__main__':
    main()
