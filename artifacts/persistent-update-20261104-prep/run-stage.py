#!/usr/bin/env python3
"""Source-frozen update qualification stages. Import and --help never run a VM."""
import argparse
import contextlib
import fcntl
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
import uuid

HERE = Path(__file__).resolve().parent
REPO = Path('/repo') if HERE == Path('/prep') else HERE.parents[1]
RUST_IMAGE = 'sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130'
OPENSSL_SOURCE_IMAGE = 'sha256:3646133f4b4d2e007dd3eee28d9db6336c29f6ccb31b9f8a49b88bc0a70051be'
TOOLS_IMAGE = 'sha256:2440560d810a834637cfeeb1057f7f471c40caa8e532645e00e063cbc8ec639a'
PROFILE = 'persistent-update'
STAGES = ('prepare', 'build', 'bundle', 'admission', 'pair', 'export', 'verify')
HEX = re.compile(r'[0-9a-f]{64}\Z')
REPLAY_MODULES = ('bootlib.py', 'infra.py', 'persistent_update_receipt.py', 'wayland_receipt.py',
                  'bundle.py', 'isolation_profiles.py', 'payload_profiles.py')
HOST_TEST_SAMPLES = {'image-viewer': 2, 'read-file': 1}
HOST_TEST_FILES = {'compiler.wasm', 'main.wasm', 'app.json', 'build-info.json', 'build-receipt.json'}
HOST_TEST_EVIDENCE = 'artifacts/persistent-update-regression-fixtures-20261105/'
HOST_TEST_FILE_LIMIT = 8 * 1024 * 1024
HOST_TEST_TOTAL_LIMIT = 32 * 1024 * 1024


def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for raw in iter(lambda: stream.read(1024 * 1024), b''): digest.update(raw)
    return digest.hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate JSON field: ' + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON: ' + value)))


def read(path): return strict_json(Path(path).read_bytes())


def write(path, value, *, exclusive=True):
    with Path(path).open('x' if exclusive else 'w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


def checked_root(value):
    raw = os.fspath(value)
    path = Path(raw)
    if not path.is_absolute() or any(part in ('.', '..', '') for part in raw.split('/')[1:]):
        raise ValueError('absolute nonaliased prep root required')
    if path.resolve() != path: raise ValueError('symlink prep root forbidden')
    return path


def relative(root, name):
    if not isinstance(name, str) or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError('unsafe relative artifact path')
    path = root / name
    if path.resolve() != path or not path.resolve().is_relative_to(root):
        raise ValueError('symlink or escaping artifact path')
    return path


def file_hashes(root):
    root = Path(root)
    def walk(directory):
        for child in sorted(directory.iterdir()):
            yield child
            if stat.S_ISDIR(child.lstat().st_mode): yield from walk(child)
    paths = walk(root) if stat.S_ISDIR(root.lstat().st_mode) else [root]
    result = {}
    for path in paths:
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode): continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('only single-link regular artifact files: ' + str(path))
        result[str(path.relative_to(root)) if root.is_dir() else path.name] = sha(path)
    return result


def verify_file_hashes(root, expected):
    if file_hashes(root) != expected: raise ValueError('artifact file set or bytes changed: ' + str(root))


def verify_source_files(freeze, repo, root=None):
    root = root or repo / 'artifacts/persistent-update-20261104-prep'
    actual = capture_source(repo, root)
    if set(actual) != set(freeze['files']): raise ValueError('frozen source file set changed')
    if actual != freeze['files']:
        changed = sorted(name for name in actual if actual[name] != freeze['files'][name])
        raise ValueError('frozen source changed: ' + ', '.join(changed))

def input_contracts():
    def click(name, x, y, expect, required=None):
        value = {'name': name, 'events': [{'kind': 'pointer', 'x': x, 'y': y},
                  {'kind': 'button', 'button': 'left', 'down': True},
                  {'kind': 'button', 'button': 'left', 'down': False}], 'expect': expect}
        if required is not None: value['requireApps'] = required
        return value
    first = {'schemaVersion': 1, 'requireApps': [1], 'steps': [
        click('native-install', 1148, 12, {'storagePrompt': {'mode': 17, 'packageId': 'dev.ming.persistent-update-demo', 'version': '0.2.0', 'hasAllow': True}, 'rootAdvance': True, 'storagePromptPresented': True}),
        click('confirm-update', 880, 478, {'storagePrompt': None, 'rootAdvance': True}),
        click('dock-launch-app2', 676, 766, {'apps': [{'app': 2, 'generation': 1, 'state': 'running'}]}),
        click('close-app2-after-ready', 96, 104, {'apps': [{'app': 2, 'generation': 1, 'state': 'closed'}],
              'persistentUpdate': {'transactionId': '$trial', 'app': 2, 'packageId': 'dev.ming.persistent-update-demo', 'newVersion': '0.2.0', 'status': 'committed'}}, [1, 2]),
        click('power-off-after-commit', 1238, 12, {'phase': 'stopped'})]}
    second = {'schemaVersion': 1, 'requireApps': [1], 'steps': [
        click('dock-launch-app2', 676, 766, {'apps': [{'app': 2, 'generation': 1, 'state': 'running'}]}),
        click('power-off-reopened', 1238, 12, {'phase': 'stopped'}, [1, 2])]}
    return first, second

def boot_modules(repo):
    directory = str(repo / 'scripts/boot')
    if directory not in sys.path: sys.path.insert(0, directory)


def validate_inputs(first, second, output):
    boot_modules(REPO)
    from infra import InputPlan
    for number, plan in enumerate((first, second), 1):
        if plan != input_contracts()[number - 1]: raise ValueError('changed qualification pointer plan')
        InputPlan(plan, output / ('input-%d.jsonl' % number), 900, split_transactions=True, pointer_handshake=True, persistent_update_gate=True)


def execution_budget(first, second):
    steps = [len(plan['steps']) for plan in (first, second)]
    # Per boot: startup 300 s, each UI step 45 s plus the retained 5 s pointer
    # handshake, normal shutdown/sync/unmount 120 s. Guest/worker limits do not change.
    minimum = [300 + count * (45 + 5) + 120 for count in steps]
    if any(value > 900 for value in minimum): raise ValueError('input plan exceeds retained boot budget')
    return {'bootSteps': steps, 'minimumBootSeconds': minimum, 'startupSeconds': 300,
            'uiStepSeconds': 45, 'normalShutdownSeconds': 120, 'pointerWaitMs': 5000,
            'timeoutSecondsEach': 900, 'vmSupervisorSlackSeconds': 45,
            'pairTimeoutSeconds': 2 * 900 + 120, 'containerTimeoutSeconds': 2 * 900 + 150,
            'guestMemoryMiB': 2048, 'guestVcpus': 2, 'progressTimeoutMs': 300000,
            'stderrLimitBytes': 8388608, 'observerMaxEvents': 4096, 'observerMaxSeconds': 900,
            'retainedWorkerBudgetsChanged': False, 'staticCalculationOnly': True,
            'finalSystemAccepted': False}


def environment_contract(identifier, host_root=None):
    host_root = host_root or HERE
    if not re.fullmatch(r'persistent-update-20261104-qual1-[a-z0-9-]+', identifier):
        raise ValueError('new update qualification identifier required')
    return {'qualificationId': identifier, 'rustImage': RUST_IMAGE, 'toolsImage': TOOLS_IMAGE,
            'hostBackingRoot': str(host_root / 'apfs-backing' / identifier), 'buildVolume': identifier + '-build',
            'uid': 65534, 'gid': 65534, 'cpus': 2, 'memoryBytes': 4294967296,
            'pidsLimit': 256, 'network': 'none', 'readOnlyRoot': True,
            'cacheStrategy': 'copy-readonly-cargo-and-release-cache-to-new-owned-volume',
            'buildCacheSource': '/Users/ranyunlong/projects/mingOS/.cache/linux-rust',
            'runtimeRoot': '/run/mingos-runtime', 'minimumHostAvailableBytes': 2147483648,
            'workRoot': '/var/tmp/mingos-boot-infra-20261025',
            'diskManifest': 'disks/' + identifier + '-128.json', 'diskSizeMiB': 128,
            'reuseHistoricalDisk': False}


def execute(directory, argv, timeout, *, cwd=None):
    directory.mkdir(parents=True, exist_ok=False)
    record = {'argv': [str(x) for x in argv], 'timeoutSeconds': timeout, 'startedAt': now(),
              'exitCode': None, 'timedOut': False}
    write(directory / 'command.json', record)
    try:
        with (directory / 'stdout.log').open('xb') as stdout, (directory / 'stderr.log').open('xb') as stderr:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                    timeout=timeout, check=False, cwd=cwd)
        record['exitCode'] = result.returncode
    except subprocess.TimeoutExpired as error:
        record.update(exitCode=-1, timedOut=True, error=repr(error))
    except Exception as error:
        record.update(exitCode=-1, error=repr(error))
    record.update(completedAt=now(), stdoutSha256=sha(directory / 'stdout.log'), stderrSha256=sha(directory / 'stderr.log'))
    write(directory / 'command.json', record, exclusive=False)
    return record


def load_checker(root):
    path = root / 'verify-persistent-update-evidence.py'
    spec = importlib.util.spec_from_file_location('persistent_update_evidence', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_two_boots(pair, run_one, gate):
    # Creating this latch before any child launch makes a failed generation non-retryable.
    latch = pair / 'attempt.json'
    write(latch, {'schemaVersion': 1, 'startedAt': now(), 'retryAllowed': False})
    reports, failure, number = [], None, None
    try:
        for number in (1, 2):
            report = run_one(number)
            checked = gate(number, report)
            write(pair / ('boot-%d-gate.json' % number), checked)
            reports.append(report)
        result = {'status': 'same-bundle-disk-cold-boot-verified', 'bootIds': [r['bootId'] for r in reports],
                  'bundleIdentity': reports[0]['bundleIdentity'], 'diskUuid': reports[0]['diskUuid'],
                  'markers': [r['marker'] for r in reports], 'firstFailure': None, 'finalSystemAccepted': False}
    except Exception as error:
        failure = {'bootNumber': number, 'reason': repr(error), 'at': now()}
        write(pair / 'first-failure.json', failure)
        result = {'status': 'failed', 'firstFailure': failure, 'finalSystemAccepted': False}
    write(pair / 'result.json', result)
    return result


class RawGuard:
    def __init__(self, path, number):
        self.path, self.number, self.offset, self.pending = path, number, 0, b''
        self.boot_id, self.session_id = None, None

    def poll(self, final=False):
        if not self.path.exists():
            if final: raise ValueError('missing raw guest stream')
            return
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            data = stream.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024: raise ValueError('raw guest incremental budget exceeded')
            self.offset = stream.tell()
        lines = (self.pending + data).split(b'\n')
        self.pending = lines.pop()
        # A valid 256 KiB stderr receipt expands through base64 in the guest
        # envelope. The relay enforces its own raw limit; bound this transport
        # independently without shrinking that accepted receipt size.
        if len(self.pending) > 1024 * 1024: raise ValueError('raw guest envelope line limit')
        for line in lines:
            event = strict_json(line)
            if not isinstance(event, dict): raise ValueError('invalid raw guest record')
            kind = event.get('type')
            if kind in ('guest-error', 'data-sync-error', 'forced-process-stop', 'desktop-error'):
                raise ValueError('raw guest failure: ' + kind)
            if kind == 'desktop-state':
                state = event.get('state') or {}
                if state.get('error') or state.get('phase') == 'failed': raise ValueError('raw desktop failure')
                for app in state.get('apps', []):
                    if app.get('state') == 'failed': raise ValueError('raw worker failure')
                    if self.number == 2 and app.get('app') not in (1, 2): raise ValueError('boot2 unexpected app/worker')
            if kind == 'guest-result' and (event.get('status') != 'normal' or event.get('desktopExitCode') != 0
                    or event.get('dataSynced') is not True or event.get('poweroffRequested') is not True):
                raise ValueError('abnormal raw terminal result')
        if final and self.pending: raise ValueError('truncated raw guest stream')


def docker_command(stage, repo, root, environment):
    name = environment['qualificationId'] + '-' + stage
    image = environment['rustImage'] if stage == 'build' else environment['toolsImage']
    command = ['docker', 'run', '--name', name, '--cidfile', str(root / 'stages' / stage / 'container.cid'),
               '--label', 'mingos.qualification-id=' + environment['qualificationId'], '--pull', 'never',
               '--network', 'none', '--read-only', '--init', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--user', '65534:65534',
               '--pids-limit', '256', '--memory', '4g', '--cpus', '2',
               '--tmpfs', '/tmp:rw,exec,size=512m,mode=1777',
               '--tmpfs', '/run/mingos-runtime:rw,size=16m,mode=0700,uid=65534,gid=65534',
               '--mount', 'type=bind,source=' + str(repo) + ',target=/repo,readonly',
               '--mount', 'type=bind,source=' + str(root) + ',target=/prep,readonly', '--workdir', '/repo']
    for name in {'build': ('build', 'inputs/payload'), 'bundle': ('bundle-stage',),
                 'admission': ('admission',), 'pair': (), 'export': ('export-stage',)}[stage]:
        command += ['--mount', 'type=bind,source=' + str(root / name) + ',target=/prep/' + name]
    if stage == 'build':
        command += ['--mount', 'type=bind,source=' + environment['buildCacheSource'] + ',target=/cache-ro,readonly',
                    '--mount', 'type=volume,source=' + environment['buildVolume'] + ',target=/var/tmp',
                    '--env', 'RUSTUP_HOME=/usr/local/rustup', '--env', 'RUSTUP_AUTO_INSTALL=0']
    if stage == 'admission':
        command += ['--mount','type=bind,source='+str(root/'admission-backing')+',target=/var/tmp']
    if stage in ('pair', 'export'):
        command += ['--mount', 'type=bind,source=' + environment['hostBackingRoot'] + ',target=/var/tmp']
    if stage != 'build':
        command += ['--mount', 'type=bind,source=' + str(root / 'external-inputs/openssl') + ',target=/usr/bin/openssl,readonly']
    return command + [image, 'python3', '-B', '/prep/run-stage.py', '--stage', stage, '--root', '/prep', '--inside']


def inspect_image(identifier):
    result = subprocess.run(['docker', 'image', 'inspect', identifier], capture_output=True, text=True, timeout=15, check=True)
    entries = strict_json(result.stdout)
    if len(entries) != 1 or entries[0].get('Id') != identifier or entries[0].get('Os') != 'linux' or entries[0].get('Architecture') != 'amd64':
        raise ValueError('fixed Linux/amd64 image identity mismatch')
    return {key: entries[0].get(key) for key in ('Id', 'Architecture', 'Os', 'Config', 'RootFS')}


def absent(kind, name):
    result = subprocess.run(['docker', kind, 'inspect', name], capture_output=True, text=True, timeout=15)
    if result.returncode == 0: raise FileExistsError('task resource already exists: ' + name)
    if 'no such' not in result.stderr.lower(): raise RuntimeError('cannot prove resource absence: ' + result.stderr)


def preparation_source_root(repo, root, names):
    if root.is_relative_to(repo): return root
    candidates = {relative(repo, name).parent for name in names
                  if Path(name).name == 'run-stage.py' and relative(repo, name).parent.samefile(root)}
    if len(candidates) != 1: raise ValueError('preparation source mount does not match inventory')
    return candidates.pop()


def source_names(repo, root):
    if not root.is_relative_to(repo):
        inventory = read(root / 'source-inventory.json')
        if not isinstance(inventory,list) or any(not isinstance(name,str) for name in inventory): raise ValueError('host source inventory required')
        names = set(inventory)
    else:
        result = subprocess.run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                cwd=repo, capture_output=True, check=True)
        names = {os.fsdecode(name) for name in result.stdout.split(b'\0') if name}
    # Both host and tools image independently enumerate source files so new,
    # previously untracked source additions cannot hide behind the inventory.
    excluded = {'.git','.cache','artifacts','target','build','builds','.gradle','.kotlin','__pycache__','node_modules','dist'}
    extensions = {'.py','.rs','.kt','.kts','.mjs','.js','.json','.toml','.lock','.md','.yml','.yaml','.sh','.c','.h','.properties','.txt'}
    for directory, children, files in os.walk(repo,followlinks=False):
        children[:] = [name for name in children if name not in excluded and not name.startswith('build-')]
        for filename in files:
            path=Path(directory)/filename
            if path.suffix in extensions:names.add(str(path.relative_to(repo)))
    for name in ('mingos-isolation-probe','vm-prepare.sh'):
        names.add('probes/linux-isolation-source-v2/build-current/'+name)
    # Build receipts include ignored Kotlin runtime and adapter inputs too.
    for receipt in (repo / 'artifacts/persistent-update-demo').glob('*/build-receipt.json'):
        names.update(read(receipt).get('inputSha256', {}))
    # These real Kotlin packages are hardcoded inputs of unignored Host tests.
    # Keep them outside the guest payload, but inside the source byte freeze.
    for sample in HOST_TEST_SAMPLES:
        directory = repo / 'artifacts' / sample
        if not directory.exists(): continue
        names.update(str((directory / name).relative_to(repo)) for name in file_hashes(directory))
        receipt = read(directory / 'build-receipt.json')
        for field in ('inputSha256', 'evidenceSha256'): names.update(receipt.get(field, {}))
    for directory in ('.cargo', 'crates', 'scripts', 'samples', 'ming-sdk', 'ming-compose', 'third_party', 'gradle'):
        base = repo / directory
        if not base.exists(): continue
        for path in base.rglob('*'):
            rel = path.relative_to(base)
            if any(part in ('build', 'builds', '.gradle', '.kotlin', '__pycache__', 'node_modules') for part in rel.parts): continue
            if path.is_file() or path.is_symlink(): names.add(str(path.relative_to(repo)))
    logical_root = preparation_source_root(repo, root, names)
    for pattern in ('*.py', 'input-boot*.json', 'input-geometry.json', 'geometry.stdout.log', 'session-review.draft.json', 'runtime-inputs.json', 'bootstrap-external-input.json'):
        names.update(str(path.relative_to(repo)) for path in logical_root.glob(pattern))
    return names


def capture_source(repo, root, frozen_bytes=None):
    captured = frozen_bytes or {}
    result = {}
    for name in sorted(source_names(repo, root)):
        path = relative(repo, name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise ValueError('unsafe source file: ' + name)
        if name in captured: raw = captured[name]
        else:
            with path.open('rb') as stream: raw = stream.read()
        after = path.lstat()
        stamp = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns, row.st_mode, row.st_nlink)
        if stamp(info) != stamp(after) or len(raw) != info.st_size: raise ValueError('source changed during capture: ' + name)
        result[name] = hashlib.sha256(raw).hexdigest()
    return result

def validate_geometry(receipt, repo, stdout_path=None):
    if set(receipt) != {'schemaVersion','geometry','sourceSha256','test','stdoutSha256'} or type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1:
        raise ValueError('actual geometry evidence schema')
    test = receipt['test']
    if test != {'matched':1,'passed':1,'failed':0,'ignored':0}: raise ValueError('geometry test must match and pass exactly one test')
    sources = {'crates/ming-app-host/src/graphics/desktop_shell.rs','crates/ming-app-host/src/desktop_model.rs','crates/ming-app-host/src/desktop_native.rs'}
    if set(receipt['sourceSha256']) != sources: raise ValueError('geometry actual source set')
    for name, expected in receipt['sourceSha256'].items():
        if sha(relative(repo, name)) != expected: raise ValueError('geometry actual source changed')
    geometry = receipt['geometry']
    if geometry.get('schemaVersion') != 1 or geometry.get('width') != 1280 or geometry.get('height') != 800:
        raise ValueError('geometry viewport')
    points = [[1148,12],[880,478],[676,766],[96,104],[1238,12]]
    if geometry.get('boot1Points') != points or geometry.get('boot2Points') != [points[2],points[4]]:
        raise ValueError('geometry fixed actual pointers')
    if geometry.get('app2Dock',{}).get('index') != 1 or geometry['app2Dock'].get('count') != 2:
        raise ValueError('geometry actual two-app dock')
    if not isinstance(receipt['stdoutSha256'],str) or not HEX.fullmatch(receipt['stdoutSha256']): raise ValueError('geometry output hash')
    if stdout_path is not None:
        raw = Path(stdout_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt['stdoutSha256']: raise ValueError('geometry raw output changed')
        pattern = rb'(?:test desktop_native::input_tests::persistent_update_two_app_input_geometry \.\.\. )?MING_PERSISTENT_UPDATE_GEOMETRY (.+)'
        lines = [matched.group(1) for line in raw.splitlines() if (matched := re.fullmatch(pattern,line))]
        if len(lines) != 1 or strict_json(lines[0]) != geometry or b'1 passed; 0 failed; 0 ignored' not in raw:
            raise ValueError('geometry raw test output mismatch')


def host_test_source_names(repo, sample):
    names = {'settings.gradle.kts', 'build.gradle.kts', 'gradle.properties',
             'gradle/wrapper/gradle-wrapper.jar', 'gradle/wrapper/gradle-wrapper.properties',
             'scripts/wasm-sdk-adapter.mjs', 'probes/compose-wasi/port/wasm-imports.mjs'}
    excluded = {'build', 'builds', '.gradle', '.kotlin', '.git', '.cache', '__pycache__', 'node_modules'}
    for name in ('samples/' + sample, 'ming-sdk', 'ming-compose', 'probes/compose-wasi/port',
                 'probes/compose-frame/coroutines-port'):
        directory = relative(repo, name)
        if not directory.is_dir(): raise ValueError('host test fixture source directory missing: ' + name)
        for parent, children, files in os.walk(directory, followlinks=False):
            children[:] = [child for child in children if child not in excluded]
            names.update(str((Path(parent) / child).relative_to(repo)) for child in files)
    return names


def host_test_inputs(repo):
    """Verify the two real Kotlin packages before allocating/copying a build cache."""
    result, retained = {}, {}
    for sample, sdk in HOST_TEST_SAMPLES.items():
        directory = relative(repo, 'artifacts/' + sample)
        if not directory.is_dir(): raise ValueError('missing real host test fixture: ' + sample)
        files = file_hashes(directory)
        if set(files) != HOST_TEST_FILES: raise ValueError('host test fixture file set: ' + sample)
        retained.update({str((directory / name).relative_to(repo)): (directory / name).stat().st_size for name in files})
        if any(size > HOST_TEST_FILE_LIMIT for size in retained.values()): raise ValueError('host test fixture file budget')
        receipt, info, manifest = (read(directory / name) for name in ('build-receipt.json', 'build-info.json', 'app.json'))
        if any(not isinstance(value, dict) for value in (receipt, info, manifest)): raise ValueError('host test fixture JSON objects required')
        if (type(receipt.get('schemaVersion')) is not int or receipt['schemaVersion'] != 1
                or receipt.get('status') != 'passed' or receipt.get('sample') != sample
                or receipt.get('offline') is not True or type(receipt.get('compilerExitCode')) is not int
                or receipt['compilerExitCode'] != 0 or receipt.get('rebuiltFromFrozenSource') is not True
                or receipt.get('rawToPackageReplayVerified') is not True or receipt.get('abiValidatedAgainstCurrentEngine') is not True
                or receipt.get('rebuiltThisQualification') is not False):
            raise ValueError('host test fixture real offline compiler receipt: ' + sample)
        command = receipt.get('command')
        if (not isinstance(command, list) or any(not isinstance(arg, str) for arg in command)
                or not {'--offline', '--rerun-tasks', '--no-build-cache', ':' + sample + ':compileProductionExecutableKotlinWasmWasi'} <= set(command)):
            raise ValueError('host test fixture compiler command: ' + sample)
        inputs, evidence = receipt.get('inputSha256'), receipt.get('evidenceSha256')
        if (not isinstance(inputs, dict) or not host_test_source_names(repo, sample) <= set(inputs)
                or not isinstance(evidence, dict) or not evidence
                or any(not name.startswith(HOST_TEST_EVIDENCE) for name in evidence) or set(inputs) & set(evidence)):
            raise ValueError('host test fixture source/evidence set: ' + sample)
        for name, expected in {**inputs, **evidence}.items():
            if not isinstance(expected, str) or not HEX.fullmatch(expected): raise ValueError('host test fixture input hash')
            path = relative(repo, name)
            observed = path.lstat()
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1 or sha(path) != expected:
                raise ValueError('host test fixture input changed: ' + name)
            if name in evidence: retained[name] = observed.st_size
        if any(size > HOST_TEST_FILE_LIMIT for size in retained.values()) or sum(retained.values()) > HOST_TEST_TOTAL_LIMIT:
            raise ValueError('host test fixture evidence budget')
        for field in ('sourceFreeze', 'stdout', 'stderr'):
            path, expected = receipt.get(field + 'Path'), receipt.get(field + 'Sha256')
            if not isinstance(path, str) or evidence.get(path) != expected or not HEX.fullmatch(expected or ''):
                raise ValueError('host test fixture compiler evidence binding: ' + field)
        source_freeze = read(relative(repo, receipt['sourceFreezePath']))
        if (not isinstance(source_freeze, dict) or not isinstance(source_freeze.get('files'), dict)
                or not isinstance(receipt.get('sourceCommit'), str) or not re.fullmatch(r'[0-9a-f]{40}', receipt['sourceCommit'])
                or receipt['sourceCommit'] != source_freeze.get('sourceCommit')
                or any(source_freeze['files'].get(name) != expected for name, expected in inputs.items())):
            raise ValueError('host test fixture frozen compiler source mismatch: ' + sample)
        toolchain, network = receipt.get('toolchainEvidence', {}), receipt.get('network', {})
        if (not isinstance(toolchain, dict) or set(toolchain) != {'path', 'sha256'}
                or evidence.get(toolchain['path']) != toolchain['sha256'] or not isinstance(network, dict)
                or network.get('platform') != 'darwin' or network.get('nonLoopbackDenied') is not True
                or network.get('loopbackAllowed') is not True or network.get('profileSha256') not in evidence.values()
                or any(type(network.get(key)) is not int or network[key] != 0 for key in ('pythonProbeExitCode', 'javaProbeExitCode'))):
            raise ValueError('host test fixture toolchain/network evidence: ' + sample)
        if (type(manifest.get('schemaVersion')) is not int or manifest['schemaVersion'] != 1 or manifest.get('id') != 'dev.ming.' + sample
                or manifest.get('version') != '0.1.0' or type(manifest.get('sdkVersion')) is not int
                or manifest['sdkVersion'] != sdk or manifest.get('entry') != 'main.wasm'
                or files['app.json'] != receipt.get('manifestSha256')
                or files['app.json'] != inputs.get('samples/' + sample + '/app.json')):
            raise ValueError('host test fixture manifest identity/bytes: ' + sample)
        package = receipt.get('package', {})
        keys = ('rawSha256', 'packagedSha256', 'adapterVersion', 'rewrites', 'imports', 'exports')
        if (not isinstance(package, dict) or any(key not in info or package.get(key) != info[key] for key in keys)
                or info['rawSha256'] != files['compiler.wasm'] or info['packagedSha256'] != files['main.wasm']
                or type(info['adapterVersion']) is not int or info['adapterVersion'] != 1
                or info['rewrites'] != ([{'from':'wasi_snapshot_preview1.random_get', 'to':'ming_v2.random_fill'}] if sdk == 2 else [])
                or not isinstance(info['imports'], list) or not isinstance(info['exports'], list)
                or (sdk == 1 and files['compiler.wasm'] != files['main.wasm'])):
            raise ValueError('host test fixture raw/package provenance: ' + sample)
        if any(not isinstance(row, dict) or row.get('module') not in ('ming_v1', 'ming_v2') for row in info['imports']):
            raise ValueError('host test fixture forbidden non-SDK import: ' + sample)
        for name in ('compiler.wasm', 'main.wasm'):
            if (directory / name).read_bytes()[:8] != b'\x00asm\x01\x00\x00\x00': raise ValueError('host test fixture Wasm bytes required')
        result[sample] = {'path': 'artifacts/' + sample, 'files': files,
                          'buildProvenance': 'current-worktree-offline-kotlin', 'rebuiltThisQualification': False}
    return result


def export_host_test_inputs(repo, freeze, output, excluded):
    fixtures = host_test_inputs(repo)
    if fixtures != freeze['hostTestFixtures']: raise ValueError('host test fixture bytes/provenance changed')
    names = set()
    for sample in HOST_TEST_SAMPLES:
        directory = 'artifacts/' + sample
        names.update(directory + '/' + filename for filename in HOST_TEST_FILES)
        names.update(read(repo / directory / 'build-receipt.json')['evidenceSha256'])
    files = {}
    for name in sorted(names):
        expected = freeze['files'].get(name)
        if expected is None or sha(relative(repo, name)) != expected: raise ValueError('frozen host test input missing/changed: ' + name)
        files[name] = expected
    target = output / 'host-test-inputs'
    target.mkdir()
    for name in files:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_regular(relative(repo, name), destination, excluded)
        if sha(destination) != files[name]: raise ValueError('frozen host test input changed during export: ' + name)
    write(target / 'manifest.json', {'schemaVersion': 1, 'fixtures': fixtures, 'files': files})


def fixture_inputs(repo, root=None):
    root = root or HERE
    result = {}
    for name in ('bootstrap','old','new','absent-write'):
        directory = root / 'external-inputs/bootstrap' if name == 'bootstrap' else repo / 'artifacts/persistent-update-demo' / name
        for filename in ('compiler.wasm','main.wasm','app.json','build-info.json','build-receipt.json'):
            if not (directory / filename).is_file(): raise ValueError('missing real fixture input: ' + str(directory / filename))
        info, receipt = read(directory/'build-info.json'), read(directory/'build-receipt.json')
        if receipt.get('status') != 'passed': raise ValueError('fixture compile receipt is not passed')
        raw, packaged = sha(directory/'compiler.wasm'), sha(directory/'main.wasm')
        if info.get('rawSha256') != raw or info.get('packagedSha256') != packaged:
            raise ValueError('fixture raw/package provenance mismatch')
        package = receipt.get('package', receipt)
        if package.get('rawSha256') != raw or package.get('packagedSha256') != packaged:
            raise ValueError('fixture receipt package mismatch')
        manifest = read(directory/'app.json')
        if manifest.get('id') != ('dev.ming.text-entry-demo' if name == 'bootstrap' else 'dev.ming.persistent-update-demo'):
            raise ValueError('fixture package identity')
        if receipt.get('manifestSha256') != sha(directory/'app.json'): raise ValueError('fixture manifest changed')
        for source, expected in (receipt.get('inputSha256',{}).items() if name != 'bootstrap' else []):
            if sha(relative(repo, source)) != expected: raise ValueError('Kotlin fixture source changed: ' + source)
        if (directory/'compiler.wasm').read_bytes()[:8] != b'\x00asm\x01\x00\x00\x00' or (directory/'main.wasm').read_bytes()[:8] != b'\x00asm\x01\x00\x00\x00':
            raise ValueError('real fixture Wasm required')
        result[name] = {'rawSha256':raw,'packagedSha256':packaged,
                        'files':file_hashes(directory), 'version':manifest['version'],
                        'buildProvenance':'external-existing-20261026' if name == 'bootstrap' else 'current-worktree-offline-kotlin',
                        'rebuiltThisQualification':False}
    if result['old']['packagedSha256'] == result['new']['packagedSha256']: raise ValueError('old and new Wasm must differ')
    network=repo/'artifacts/persistent-update-demo/network-isolation'
    isolated=read(network/'receipt.json')
    if isolated.get('status')!='passed' or isolated.get('platform')!='darwin' or isolated.get('loopbackAllowed') is not True:
        raise ValueError('real Kotlin network boundary evidence missing')
    result['networkIsolation']={'path':'artifacts/persistent-update-demo/network-isolation/receipt.json',
        'sha256':sha(network/'receipt.json'),'files':file_hashes(network),
        'networkBoundary':isolated['networkBoundary'],'platform':'darwin','linuxNetworkNoneClaimed':False}
    return result


def validate_runtime_inputs(runtime, repo, root=None, container=False):
    root = root or HERE
    if set(runtime) != {'schemaVersion','sourceImage','toolsImage','executable','libraries','sourceEvidence'} or type(runtime['schemaVersion']) is not int or runtime['schemaVersion'] != 1:
        raise ValueError('runtime input schema')
    if runtime['sourceImage'] != OPENSSL_SOURCE_IMAGE or runtime['toolsImage'] != TOOLS_IMAGE: raise ValueError('runtime image provenance')
    executable = runtime['executable']
    if set(executable) != {'path','sha256'} or executable['path'] != 'external-inputs/openssl': raise ValueError('runtime executable path')
    binary = root / executable['path']
    if not binary.is_file() or sha(binary) != executable['sha256'] or not binary.stat().st_mode & 0o111: raise ValueError('runtime executable changed')
    evidence = runtime['sourceEvidence']
    if set(evidence) != {'path','sha256'} or sha(relative(root,evidence['path'])) != evidence['sha256']: raise ValueError('runtime source evidence changed')
    historical = read(relative(root,evidence['path']))
    if (historical.get('images') != {'rust':OPENSSL_SOURCE_IMAGE,'tools':TOOLS_IMAGE}
            or historical.get('opensslSha256') != executable['sha256']
            or historical.get('values',{}).get('tools',{}).get('libraries') != runtime['libraries']
            or historical.get('values',{}).get('rust',{}).get('libraries') != runtime['libraries']):
        raise ValueError('runtime extraction evidence does not bind exact executable/libraries')
    if not runtime['libraries'] or any(not name.startswith('/lib') or not HEX.fullmatch(value) for name,value in runtime['libraries'].items()): raise ValueError('runtime dynamic-library hashes')
    if container:
        if shutil.which('openssl') != '/usr/bin/openssl' or sha('/usr/bin/openssl') != executable['sha256']: raise ValueError('actual Linux OpenSSL changed')
        for name, expected in runtime['libraries'].items():
            if sha(name) != expected: raise ValueError('actual Linux dependency changed: '+name)
    return runtime


def require_authorization(root, stage, freeze):
    if stage != 'pair': return
    authorization = read(root / 'authorization.json')
    if authorization.get('formalRunAuthorized') is not True: raise ValueError('concrete pair/new-disk authorization required')
    expected = {'formalRunAuthorized':True,'qualificationId':freeze['environment']['qualificationId'],
                'freezeSha256':sha(root/'freeze.json'),'newDiskSizeMiB':128,
                'hostBackingRoot':freeze['environment']['hostBackingRoot'],'bootCount':2,'retryAllowed':False}
    if json.dumps(authorization,sort_keys=True,separators=(',',':')) != json.dumps(expected,sort_keys=True,separators=(',',':')):
        raise ValueError('concrete pair/new-disk authorization required')
    verify_admission(root,freeze)


def host_apfs_evidence(path):
    path=checked_root(path)
    mount=subprocess.check_output(['mount'],text=True)
    device=path.stat().st_dev
    rows=[]
    for line in mount.splitlines():
        if ' (apfs,' not in line or 'read-only' in line:continue
        point=line.split(' on ',1)[1].rsplit(' (',1)[0]
        try:
            if Path(point).stat().st_dev==device:rows.append(line)
        except OSError:pass
    if sys.platform!='darwin' or not rows:raise ValueError('actual backing device must be writable host APFS')
    available=shutil.disk_usage(path).free
    if available<2147483648:raise ValueError('actual host APFS has less than 2 GiB free')
    return {'path':str(path),'device':device,'matchingWritableApfsMounts':rows,'freeBytes':available}


def admit_host_storage(root, environment):
    backing=checked_root(environment['hostBackingRoot'])
    if backing.exists():raise FileExistsError('permanent new generation cannot reuse backing root')
    backing.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    observed=host_apfs_evidence(backing.parent)
    backing.mkdir(mode=0o777);backing.chmod(0o777)
    write(root/'host-storage.json',dict(observed,path=str(backing),createdAt=now()))


def prepare(root, repo):
    if (root / 'freeze.json').exists() or (root / 'inputs').exists(): raise FileExistsError('preparation generation already exists')
    required = ('run-stage.py', 'raw_boot_audit.py', 'verify-persistent-update-evidence.py',
                'test_persistent_update_evidence.py', 'test_run_stage.py', 'input-geometry.json', 'geometry.stdout.log', 'runtime-inputs.json')
    for name in required:
        if not (root / name).is_file(): raise ValueError('preparation source/evidence incomplete: ' + name)
    geometry = read(root / 'input-geometry.json'); validate_geometry(geometry, repo, root / "geometry.stdout.log")
    first, second = input_contracts()
    input_raw = {}
    for number, expected in enumerate((first, second), 1):
        raw = (root / ('input-boot%d.json' % number)).read_bytes()
        if len(raw) > 1048576 or strict_json(raw) != expected: raise ValueError('changed fixed input plan')
        input_raw[number] = raw
    validate_inputs(first, second, root)
    fixtures = fixture_inputs(repo, root)
    host_tests = host_test_inputs(repo)
    runtime = read(root / 'runtime-inputs.json'); validate_runtime_inputs(runtime, repo, root)
    write(root/'source-inventory.json',sorted(source_names(repo,root)),exclusive=False)
    identifier = 'persistent-update-20261104-qual1-' + uuid.uuid4().hex[:12]
    environment = environment_contract(identifier)
    cache=checked_root(environment['buildCacheSource'])
    if not (cache/'cargo').is_dir() or not (cache/'target/release').is_dir():raise ValueError('explicit historical read-only build cache source missing')
    inspection = {'rust': inspect_image(RUST_IMAGE), 'tools': inspect_image(TOOLS_IMAGE)}
    absent('volume', environment['buildVolume'])
    if Path(environment['hostBackingRoot']).exists(): raise FileExistsError('new backing root already exists')
    for stage in ('build', 'bundle', 'pair', 'export', 'admission'): absent('container', identifier + '-' + stage)
    inputs = root / 'inputs'; inputs.mkdir()
    for number, raw in input_raw.items():
        path = inputs / ('input-boot%d.json' % number); path.write_bytes(raw); path.chmod(0o444)
    budget = execution_budget(first, second)
    write(root / 'execution-budget.json', budget); write(root / 'environment-inspection.json', inspection)
    freeze = {'schemaVersion': 1, 'profile': PROFILE, 'repo': str(repo), 'preparedAt': now(),
              'sourceCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
              'files': capture_source(repo, root), 'sourceInventorySha256':sha(root/'source-inventory.json'), 'environment': environment, 'runtime': runtime, 'budget': budget,
              'inputs': {'boot%dSha256' % n: hashlib.sha256(input_raw[n]).hexdigest() for n in (1, 2)},
              'checker': {'path': str((root / 'verify-persistent-update-evidence.py').relative_to(repo)),
                          'sha256': sha(root / 'verify-persistent-update-evidence.py')},
              'fixture': fixtures, 'hostTestFixtures': host_tests, 'finalSystemAccepted': False}
    write(root / 'freeze.json', freeze)
    verify_source_files(freeze, repo, root)
    seal_stage(root, 'prepare', ['freeze.json', 'source-inventory.json', 'inputs/input-boot1.json', 'inputs/input-boot2.json', 'execution-budget.json', 'environment-inspection.json'])

def verify_freeze(root, repo):
    freeze = read(root / 'freeze.json')
    fields = {'schemaVersion', 'profile', 'repo', 'preparedAt', 'sourceCommit', 'files', 'sourceInventorySha256', 'environment', 'runtime', 'budget', 'inputs', 'checker', 'fixture', 'hostTestFixtures', 'finalSystemAccepted'}
    if set(freeze) != fields or type(freeze['schemaVersion']) is not int or freeze['schemaVersion'] != 1 or freeze['profile'] != PROFILE or freeze['finalSystemAccepted'] is not False:
        raise ValueError('update freeze schema/profile')
    if sha(root/'source-inventory.json') != freeze['sourceInventorySha256']:raise ValueError('source inventory changed')
    verify_source_files(freeze, repo, root)
    if fixture_inputs(repo, root) != freeze['fixture']: raise ValueError('fixture bytes/provenance changed')
    if host_test_inputs(repo) != freeze['hostTestFixtures']: raise ValueError('host test fixture bytes/provenance changed')
    validate_runtime_inputs(freeze['runtime'], repo, root)
    specs = []
    for number in (1, 2):
        raw = (root / 'inputs' / ('input-boot%d.json' % number)).read_bytes()
        if len(raw) > 1048576 or hashlib.sha256(raw).hexdigest() != freeze['inputs']['boot%dSha256' % number]: raise ValueError('frozen input changed')
        specs.append(strict_json(raw))
    validate_inputs(*specs, root)
    logical_root = preparation_source_root(repo, root, freeze['files'])
    host_root = Path(freeze['repo']) / logical_root.relative_to(repo)
    if freeze['budget'] != execution_budget(*input_contracts()) or freeze['environment'] != environment_contract(freeze['environment']['qualificationId'], host_root): raise ValueError('frozen limits changed')
    return freeze

def product_hashes(root, names):
    result = {}
    for name in names:
        path = relative(root, name)
        if path.is_dir():
            result.update({str(Path(name) / key): value for key, value in file_hashes(path).items()})
        else:
            if not path.is_file(): raise ValueError('stage product missing: ' + name)
            result[name] = sha(path)
    return result


def seal_stage(root, stage, products):
    directory = root / 'stages' / stage
    directory.mkdir(parents=True, exist_ok=True)
    previous = STAGES[STAGES.index(stage) - 1] if stage != 'prepare' else None
    record = {'schemaVersion': 1, 'stage': stage, 'status': 'passed', 'finishedAt': now(),
              'freezeSha256': sha(root / 'freeze.json'), 'productRoots': products,
              'files': product_hashes(root, products), 'previousResultSha256': sha(root / 'stages' / previous / 'result.json') if previous else None}
    write(directory / 'result.json', record)


def verify_previous_chain(root, stage):
    freeze_hash = sha(root / 'freeze.json')
    for index in range(STAGES.index(stage) - 1, -1, -1):
        name = STAGES[index]
        ancestor = read(root / 'stages' / name / 'result.json')
        expected = sha(root / 'stages' / STAGES[index - 1] / 'result.json') if index else None
        if (ancestor.get('status') != 'passed' or ancestor.get('stage') != name
                or ancestor.get('freezeSha256') != freeze_hash or ancestor.get('previousResultSha256') != expected):
            raise ValueError('previous stage chain not successful or frozen')
        if product_hashes(root, ancestor['productRoots']) != ancestor['files']:
            raise ValueError('previous stage products changed: ' + name)


def verify_previous(root, stage):
    verify_previous_chain(root, stage)
    if (root / 'stages' / stage).exists(): raise FileExistsError('stage already attempted; preserve generation')


def review_from_payload(root, repo, freeze):
    boot_modules(repo)
    from bootlib import PERSISTENT_UPDATE_ARGS, PERSISTENT_UPDATE_FIXTURE, PERSISTENT_UPDATE_PROFILE
    from wayland_receipt import POINTER_SYNC_POLICY
    payload = root / 'inputs/payload'; probes = repo / 'probes/linux-isolation-source-v2/build-current'
    return {'reviewed': False, 'reviewer': None, 'profile': PROFILE, 'sessionMode': 'long-lived',
            'extraArgs': PERSISTENT_UPDATE_ARGS, 'persistentInstallState': '/data/mingos/install-root',
            'catalogPath': '/data/mingos/install-root/catalog.json', 'installSource': '/opt/mingos/install-source/app.json',
            'persistentUpdateFixture': PERSISTENT_UPDATE_FIXTURE,
            'persistentUpdateFixtureSha256': {key: sha(payload / PERSISTENT_UPDATE_FIXTURE[key].removeprefix('/opt/mingos/'))
                for key in ('helper', 'bootstrapWasm', 'oldWasm', 'newWasm')},
            'hostSha256': sha(payload / 'ming-app-host'), 'catalogSha256': sha(payload / 'catalog.json'),
            'inputScripts': freeze['inputs'], 'strictProbeProfile': 'source-v2',
            'strictProbeSha256': {name: sha(probes / name) for name in ('mingos-isolation-probe', 'vm-prepare.sh')},
            'instrumentedQualification': True, 'instrumentedProfile': PERSISTENT_UPDATE_PROFILE,
            'inputSynchronization': POINTER_SYNC_POLICY,
            'instrumentation': {'desktopEnv': {'WAYLAND_DEBUG': 'client'}, 'stderrLimitBytes': 8388608,
                'observerSha256': sha(repo / 'scripts/boot/evdev_observer.py'), 'observerMaxEvents': 4096,
                'observerMaxSeconds': 900, 'exclusiveGrab': False, 'readyCaptureSeconds': None},
            'budgetEvidence': 'execution-budget.json', 'budgetEvidenceSha256': sha(root / 'execution-budget.json'),
            'sourceCommit': freeze['sourceCommit'], 'sourceFreezeSha256': sha(root / 'freeze.json'),
            'fixtureEvidence': freeze['fixture'], 'finalSystemAccepted': False}

def verify_review(root, repo, freeze):
    review, base = read(root / 'review.json'), read(root / 'build/review-base.json')
    normalized = dict(review, reviewed=False, reviewer=None)
    expected = review_from_payload(root, repo, freeze)
    original = {key: value for key, value in expected.items() if key not in ('fixtureVerificationEvidence', 'fixtureVerificationSha256')}
    if review.get('reviewed') is not True or not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip() or normalized != expected or base != original:
        raise ValueError('primary review absent or payload review changed')
    boot_modules(repo)
    from bundle import verify_payload
    from bootlib import validate_reviewed_input
    verify_payload(root / 'inputs/payload', review)
    for number in (1, 2): validate_reviewed_input(review, root / 'inputs' / ('input-boot%d.json' % number), number)
    return review


def copy_build_cache(source, cache, record_path):
    cache.parent.mkdir(mode=0o700, parents=True)
    # Copy into the new task volume. Never chown or mutate the shared source cache.
    shutil.copytree(source / 'cargo', cache / 'cargo')
    (cache / 'target').mkdir()
    shutil.copytree(source / 'target/release', cache / 'target/release')
    copied = ['cargo', 'target/release']
    for name in ('.rustc_info.json', 'CACHEDIR.TAG'):
        path = source / 'target' / name
        if path.is_file():
            shutil.copyfile(path, cache / 'target' / name)
            copied.append('target/' + name)
    write(record_path, {'sharedCacheReadOnly': True, 'sharedOwnershipChanged': False,
          'copied': copied,
          'excluded': ['target/debug'], 'target': str(cache)})


def inside_build(root, repo, freeze):
    if list((root / 'build').iterdir()) or list((root / 'inputs/payload').iterdir()):
        raise FileExistsError('fresh dedicated build outputs required')
    if host_test_inputs(repo) != freeze['hostTestFixtures']: raise ValueError('host test fixture bytes/provenance changed')
    cache = Path('/var/tmp') / freeze['environment']['qualificationId'] / 'cache'
    copy_build_cache(Path('/cache-ro'), cache, root / 'build/cache-copy.json')
    environment = os.environ.copy()
    environment.update(CARGO_HOME=str(cache / 'cargo'), CARGO_TARGET_DIR=str(cache / 'target'),
                       CARGO_BUILD_JOBS='1', CARGO_PROFILE_DEV_DEBUG='0', CARGO_PROFILE_TEST_DEBUG='0')
    commands = [
        ['test', '--all-targets'], ['check', '--all-features', '--all-targets'],
        ['check', '--no-default-features', '--all-targets'],
        ['build', '--bin', 'ming-app-host'], ['build', '--example', 'prepare_persistent_update_fixture']]
    os.environ.update(environment)
    for index, arguments in enumerate(commands, 1):
        command = ['cargo', '+1.99.1', arguments[0], '--offline', '--locked', '--release', '-p', 'ming-app-host', '-j1', *arguments[1:]]
        result = execute(root / 'build' / ('cargo-%d' % index), command, 1800, cwd=repo)
        if result['exitCode']: raise RuntimeError('Linux cargo stage failed; preserve logs')
    os.environ.update(MING_TEST_PRIVATE_WORKER=str(cache / 'target/release/ming-app-host'),
        MING_UPDATE_BOOTSTRAP_WASM=str(root / 'external-inputs/bootstrap/main.wasm'),
        MING_UPDATE_OLD_WASM=str(repo / 'artifacts/persistent-update-demo/old/main.wasm'),
        MING_UPDATE_NEW_WASM=str(repo / 'artifacts/persistent-update-demo/new/main.wasm'),
        MING_UPDATE_ABSENT_WASM=str(repo / 'artifacts/persistent-update-demo/absent-write/main.wasm'),
        MING_UPDATE_FIXTURE_HELPER=str(cache / 'target/release/examples/prepare_persistent_update_fixture'))
    tests = []
    for index, test in enumerate((
        'ming_desktop::persistent_update_runtime::migrated_and_absent_write_commit_only_after_real_ready_close_reap',
        'ming_desktop::persistent_update_runtime::absent_nonempty_runtime_failure_rolls_back_original_missing_namespace',
        'ming_desktop::persistent_update_runtime::killed_host_recovers_in_independent_process'), 1):
        tests.append((str(index), ['--lib', test, '-j1', '--', '--exact', '--nocapture', '--ignored'], (1, 0, 0, 0)))
    tests += [
        ('public', ['--test', 'persistent_update_runtime',
            'separate_process_roles_and_real_stable_host_refuse_active_update_without_writes',
            '-j1', '--', '--exact', '--nocapture', '--ignored', '--test-threads=1'], (1, 0, 0, 0)),
        # All 18 recovery parents run once; the child is invoked only by its
        # bounded SIGKILL parents and remains ignored by this outer runner.
        ('recovery', ['--lib', 'persistent_update::recovery_tests::', '-j1', '--', '--nocapture', '--test-threads=1'], (18, 0, 1, 0))]
    for name, arguments, expected in tests:
        command = ['cargo', '+1.99.1', 'test', '--offline', '--locked', '--release', '-p', 'ming-app-host',
                   *arguments]
        directory = root / 'build' / ('explicit-update-' + name)
        result = execute(directory, command, 900, cwd=repo)
        if result['exitCode']: raise RuntimeError('explicit real update/recovery test failed')
        summaries = [line for line in (directory / 'stdout.log').read_text().splitlines() if line.startswith('test result:')]
        summary = re.fullmatch(r'test result: ok\. ([0-9]+) passed; ([0-9]+) failed; ([0-9]+) ignored; ([0-9]+) measured; '
                               r'[0-9]+ filtered out; finished in [0-9.]+s', summaries[0]) if len(summaries) == 1 else None
        if summary is None or tuple(map(int, summary.groups())) != expected:
            raise ValueError('explicit update test counts changed: ' + name)
    payload = root / 'inputs/payload'
    for name, source in [('ming-app-host', cache / 'target/release/ming-app-host'),
                         ('prepare-persistent-update-fixture', cache / 'target/release/examples/prepare_persistent_update_fixture')]:
        shutil.copyfile(source, payload / name); (payload / name).chmod(0o755)
    for name in ('bootstrap', 'old', 'new'):
        directory = payload / 'packages' / name; directory.mkdir(parents=True)
        source = root / 'external-inputs/bootstrap/main.wasm' if name == 'bootstrap' else repo / 'artifacts/persistent-update-demo' / name / 'main.wasm'
        shutil.copyfile(source, directory / 'main.wasm')
        if sha(directory / 'main.wasm') != freeze['fixture'][name]['packagedSha256']: raise ValueError('fixture copy mismatch')
    write(payload / 'catalog.json', {'schemaVersion': 3, 'apps': [{'id': 1, 'title': 'Text Lab', 'packageId': 'dev.ming.text-entry-demo'}], 'autostart': [1]})
    review = review_from_payload(root, repo, freeze)
    write(root / 'build/review-base.json', review)
    fixture = root / 'build/fixture'
    fixture.mkdir(mode=0o700)
    (fixture / 'data').mkdir(mode=0o700)
    argv = [str(payload / 'prepare-persistent-update-fixture'), 'initialize-first', str(fixture / 'data/install-root'), str(fixture / 'data'),
            str(payload / 'packages/bootstrap/main.wasm'), str(payload / 'packages/old/main.wasm'), str(payload / 'packages/new/main.wasm'),
            str(fixture / 'install-source/app.json'), str(payload / 'catalog.json'), review['catalogSha256'], 'seed', str(fixture / 'fixture-receipt.json')]
    result = execute(fixture / 'transport', argv, 60)
    # Keep the fixture checker's established flat transport artifact interface.
    write(fixture / 'command.json', dict(result, stage='fixture', commandRoot='/prep/build/fixture', inputsRoot='/prep/inputs', executableSha256=sha(payload / 'prepare-persistent-update-fixture')))
    for name in ('stdout.log', 'stderr.log'): shutil.copyfile(fixture / 'transport' / name, fixture / name)
    if result['exitCode']: raise RuntimeError('real fixture helper failed')
    signed = load_checker(root).verify_fixture_directory(fixture, root / 'inputs', review)
    write(root / 'build/fixture-verification.inside.json', signed)
    # Preserve the original Linux ownership/modes in an archive before making
    # a host-owned, readable byte copy for the independent host verification.
    file_hashes(fixture)
    archive = root / 'build/fixture-linux.tar'
    with tarfile.open(archive, 'x') as target:
        target.add(fixture, arcname='fixture', recursive=True)
    shutil.rmtree(fixture)


def materialize_fixture(root):
    archive = root / 'build/fixture-linux.tar'
    destination = root / 'build'
    members = []
    with tarfile.open(archive) as source:
        for member in source:
            name = Path(member.name)
            if name.is_absolute() or '..' in name.parts or name.parts[0] != 'fixture' or not (member.isdir() or member.isfile()):
                raise ValueError('unsafe original fixture archive member')
            path = relative(destination, str(name))
            if member.isdir(): path.mkdir(mode=0o755)
            else:
                with source.extractfile(member) as raw, path.open('xb') as output: shutil.copyfileobj(raw, output)
                path.chmod(0o444)
            members.append({'path': member.name, 'uid': member.uid, 'gid': member.gid, 'mode': member.mode,
                            'size': member.size, 'sha256': sha(path) if member.isfile() else None})
    write(root / 'build/fixture-materialization.json', {'originalLinuxArchiveSha256': sha(archive),
          'originalLinuxMetadata': members, 'materializedOwner': os.geteuid(),
          'materializedDirectoryMode': '0755', 'materializedFileMode': '0444',
          'qualification': 'Readable byte copy of real helper output; original ownership/modes remain in fixture-linux.tar.'})


def inside_bundle(root, repo, freeze):
    verify_review(root, repo, freeze)
    command = [sys.executable, '-B', str(repo / 'scripts/boot/bundle.py'), '--payload', str(root / 'inputs/payload'),
               '--review', str(root / 'review.json'), '--output', str(root / 'bundle-stage/bundle'), '--isolation-profile', 'source-v2',
               '--isolation-probes', str(repo / 'probes/linux-isolation-source-v2/build-current'),
               '--input-diagnostics', '--wayland-client-diagnostics', '--pointer-handshake-diagnostics']
    result = execute(root / 'bundle-stage/transport', command, 300)
    if result['exitCode']: raise RuntimeError('bundle stage failed')
    # rootfs is a build intermediate containing intended runtime symlinks.
    # Its bytes are already frozen in initramfs.cpio.gz; the export is regular files.
    shutil.rmtree(root / 'bundle-stage/bundle/rootfs')


def inside_pair(root, repo, freeze):
    # The disk/VM boundary is guarded independently of the host CLI dispatcher.
    require_authorization(root, 'pair', freeze)
    verify_previous_chain(root, 'pair')
    boot_modules(repo)
    from bootlib import validate_bundle, validate_reviewed_input
    from infra import validate_cold_pair
    from data_disk import workspace, create_disk
    review = verify_review(root, repo, freeze)
    checker = load_checker(root)
    signed = read(root / 'fixture-verification.json')
    work = workspace(Path(freeze['environment']['workRoot']), create=True)
    identifier = freeze['environment']['qualificationId']
    disk = create_disk(work, identifier + '-128', 128)
    pair = work / 'cold-pair'
    pair.mkdir(mode=0o700)
    write(pair / 'disk-preparation.json', disk)
    bundle = root / 'bundle'
    identity = sha(bundle / 'bundle.json')
    manifest = validate_bundle(bundle, sha(root / 'review.json'))
    if manifest['sessionReview'] != review: raise ValueError('bundle review mismatch')
    reports = []
    deadline = time.monotonic() + freeze['budget']['pairTimeoutSeconds']
    raw_inputs = {n: validate_reviewed_input(review, root / 'inputs' / ('input-boot%d.json' % n), n)[0] for n in (1, 2)}
    for number, raw in raw_inputs.items():
        path = pair / ('boot-%d.input.json' % number)
        path.write_bytes(raw)
        path.chmod(0o400)
    def run_one(number):
        # Check again at the boundary of every boot so source drift during boot 1
        # cannot launch a changed runner for boot 2 before the final host check.
        verify_freeze(root, repo)
        require_authorization(root, 'pair', freeze)
        verify_previous_chain(root, 'pair')
        if deadline - time.monotonic() < 945: raise TimeoutError('insufficient complete boot budget')
        if sha(bundle / 'bundle.json') != identity: raise ValueError('bundle changed between boots')
        boot = pair / ('boot-%d' % number)
        guard = RawGuard(boot / 'guest.jsonl', number)
        argv = [sys.executable, '-B', str(repo / 'scripts/boot/vm.py'), '--bundle', str(bundle), '--review', str(root / 'review.json'),
                '--work-root', str(work), '--data-manifest', freeze['environment']['diskManifest'], '--output', str(boot),
                '--timeout', '900', '--boot-number', str(number), '--input-script', str(pair / ('boot-%d.input.json' % number)),
                '--runtime-root', '/run/mingos-runtime', '--diagnostic-split-input', '--diagnostic-pointer-handshake']
        write(pair / ('boot-%d.command.json' % number), {'argv': argv, 'timeoutSeconds': 945, 'diskSha256Before': sha(Path(disk['imagePath']))})
        with (pair / ('boot-%d.stdout.log' % number)).open('xb') as stdout, (pair / ('boot-%d.stderr.log' % number)).open('xb') as stderr:
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
            started = time.monotonic()
            try:
                while child.poll() is None:
                    guard.poll()
                    if time.monotonic() - started >= 945 or time.monotonic() >= deadline: raise TimeoutError('boot/pair deadline')
                    time.sleep(.1)
                guard.poll(final=True)
                if child.returncode: raise RuntimeError('VM nonzero; preserve disk and raw logs')
            except BaseException:
                # The session was created solely for this vm.py child; terminate only it
                # and its QEMU descendants. A failure remains RED even if shutdown succeeds.
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    try: child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
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
        spec = strict_json(raw_inputs[number])
        verified = checker.verify_boot(pair / ('boot-%d' % number), number, review, spec, signed, identity, repo)
        reports.append(report)
        if number == 2: validate_cold_pair(*reports)
        return {'passed': True, 'bootNumber': number, 'strongRawVerified': True,
                'diskSha256After': sha(Path(disk['imagePath'])), 'checkerResult': verified}
    result = run_two_boots(pair, run_one, gate)
    if result['status'] == 'failed': raise RuntimeError('pair failed permanently; no retry or format')


def copy_regular(source, target, excluded):
    if source.is_symlink(): raise ValueError('export symlink forbidden: ' + str(source))
    info = source.lstat()
    if stat.S_ISSOCK(info.st_mode) and source.name == 'qmp.sock':
        excluded.append(str(source))
    elif source.is_dir():
        target.mkdir()
        for path in sorted(source.iterdir()): copy_regular(path, target / path.name, excluded)
    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
        shutil.copyfile(source, target)
    else: raise ValueError('export nonregular member forbidden: ' + str(source))


def export_replay_dependencies(root, repo, freeze, output, excluded):
    expected = {}
    for name in REPLAY_MODULES:
        relative_name = 'scripts/boot/' + name
        digest = freeze['files'].get(relative_name)
        if not isinstance(digest, str) or not HEX.fullmatch(digest) or sha(repo / relative_name) != digest:
            raise ValueError('frozen replay dependency changed or missing: ' + relative_name)
        expected[name] = digest
    directory = output / 'replay/scripts/boot'
    directory.mkdir(parents=True)
    for name, digest in expected.items():
        copy_regular(repo / 'scripts/boot' / name, directory / name, excluded)
        if sha(directory / name) != digest: raise ValueError('frozen replay dependency changed during export: ' + name)
    for number in (1, 2):
        name = 'input-boot%d.json' % number
        copy_regular(root / 'inputs' / name, output / name, excluded)
        if sha(output / name) != freeze['inputs']['boot%dSha256' % number]:
            raise ValueError('frozen replay reference input changed: ' + name)


def inside_export(root, repo, freeze):
    work = Path(freeze['environment']['workRoot'])
    output = root / 'export-stage/export'
    output.mkdir()
    excluded = []
    for source, name in [(work / 'cold-pair', 'pair'), (work / 'disks', 'disks'),
                         (root / 'inputs', 'inputs'), (root / 'bundle', 'bundle'),
                         (root / 'build/fixture', 'fixture'), (root / 'build', 'build-evidence'),
                         (root / 'external-inputs', 'external-inputs')]:
        copy_regular(source, output / name, excluded)
    for name in ('review.json', 'freeze.json', 'fixture-verification.json', 'execution-budget.json',
                 'run-stage.py', 'verify-persistent-update-evidence.py', 'raw_boot_audit.py',
                 'source-inventory.json', 'runtime-inputs.json', 'input-geometry.json', 'geometry.stdout.log',
                 'bootstrap-external-input.json', 'admission-host-storage.json', 'authorization.json',
                 'host-storage.json', 'environment-inspection.json'):
        copy_regular(root / name, output / name, excluded)
    copy_regular(root / 'review.json', output / 'inputs/session-review.json', excluded)
    copy_regular(root / 'admission', output / 'admission', excluded)
    (output/'stage-evidence').mkdir()
    for stage in STAGES[:STAGES.index('export')]:
        copy_regular(root/'stages'/stage,output/'stage-evidence'/stage,excluded)
    export_replay_dependencies(root, repo, freeze, output, excluded)
    export_host_test_inputs(repo, freeze, output, excluded)
    write(output / 'excluded-runtime-sockets.json', {'excluded': excluded, 'reason': 'QMP sockets are not archive members'})


def inside(root, stage):
    if sys.platform != 'linux' or os.geteuid() != 65534 or root != Path('/prep'): raise ValueError('fixed Linux UID65534 /prep environment required')
    limits = {'cpu.max': '200000 100000', 'memory.max': '4294967296', 'pids.max': '256'}
    for name, expected in limits.items():
        if (Path('/sys/fs/cgroup') / name).read_text().strip() != expected: raise ValueError('container limit mismatch: ' + name)
    repo = Path('/repo')
    freeze = verify_freeze(root, repo)
    if stage != 'build': validate_runtime_inputs(freeze['runtime'], repo, root, container=True)
    {'build': inside_build, 'bundle': inside_bundle, 'admission': inside_admission, 'pair': inside_pair, 'export': inside_export}[stage](root, repo, freeze)


def stop_container(root, stage, environment):
    cid_path = root / 'stages' / stage / 'container.cid'
    if not cid_path.exists(): return None
    cid = cid_path.read_text().strip()
    if not HEX.fullmatch(cid): raise ValueError('invalid current container identity')
    result = subprocess.run(['docker', 'container', 'inspect', cid], capture_output=True, text=True, timeout=15, check=True)
    value = strict_json(result.stdout)[0]
    if value.get('Name') != '/' + environment['qualificationId'] + '-' + stage: raise ValueError('current container identity changed')
    if value['State']['Running']:
        subprocess.run(['docker', 'stop', '--time', '3', cid], capture_output=True, timeout=15, check=True)
        result = subprocess.run(['docker', 'container', 'inspect', cid], capture_output=True, text=True, timeout=15, check=True)
        value = strict_json(result.stdout)[0]
    write(root / 'stages' / stage / 'container-inspect.json', value)
    if not container_stopped(value): raise ValueError('task container not confirmed stopped with Pid 0')
    return value['State']


def container_stopped(value):
    return value is not None and (value.get('State',{}).get('Running') is False
        and value['State'].get('Pid') == 0 and value['State'].get('Restarting') is False)


def reject_overlapping_writers(containers, targets):
    for container in containers:
        if container.get('State',{}).get('Running') is not True: continue
        for mount in container.get('Mounts',[]):
            if mount.get('Type') != 'bind' or mount.get('RW') is not True: continue
            source=Path(mount['Source']).resolve()
            if any(source==target or source.is_relative_to(target) or target.is_relative_to(source) for target in targets):
                raise ValueError('running container has overlapping writable bind: '+str(source))


def inspect_named_container(name, missing_ok=False):
    result=subprocess.run(['docker','container','inspect',name],capture_output=True,text=True,timeout=15)
    if result.returncode:
        if missing_ok and 'no such' in result.stderr.lower():return None
        raise RuntimeError('cannot determine current container state: '+result.stderr)
    entries=strict_json(result.stdout)
    if len(entries)!=1 or entries[0].get('Name')!='/'+name:raise ValueError('container exact identity changed')
    return entries[0]


def running_containers():
    result=subprocess.run(['docker','ps','--quiet','--no-trunc'],capture_output=True,text=True,timeout=15,check=True)
    ids=result.stdout.split()
    if not ids:return []
    result=subprocess.run(['docker','container','inspect',*ids],capture_output=True,text=True,timeout=15,check=True)
    return strict_json(result.stdout)


def stop_named_container(name, evidence, required=False):
    # Keep the native writer descriptor while daemon state is unknown. Evidence
    # failures must not release a writer whose QEMU descendants may still live.
    while True:
        try:
            value=inspect_named_container(name,missing_ok=not required)
            if value is None:return None
            if not container_stopped(value):
                subprocess.run(['docker','stop','--time','3',name],capture_output=True,text=True,timeout=15,check=True)
                value=inspect_named_container(name)
            if not container_stopped(value):raise RuntimeError('container not confirmed stopped with Pid 0')
            write(evidence,value,exclusive=False)
            return value
        except BaseException as error:
            try:
                with evidence.with_suffix('.unknown.jsonl').open('a') as stream:
                    stream.write(json.dumps({'at':now(),'error':repr(error),'writerLockStillHeld':True})+'\n')
            except BaseException:pass
            time.sleep(1)


@contextlib.contextmanager
def host_writer(root, freeze, stage):
    coordinator=Path('/private/tmp')/('mingos-native-writer-'+str(os.geteuid()))
    coordinator.mkdir(mode=0o700,exist_ok=True)
    if coordinator.resolve()!=coordinator or coordinator.stat().st_uid!=os.geteuid() or stat.S_IMODE(coordinator.stat().st_mode)!=0o700:
        raise ValueError('owned native writer coordinator required')
    lock=coordinator/'writer.lock'
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1:raise ValueError('owned single-link native lock required')
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        active=coordinator/'active.json'
        if active.exists():
            previous=read(active)
            value=inspect_named_container(previous['containerName'],missing_ok=True)
            if value is not None and not container_stopped(value):raise ValueError('previous cooperative writer has not stopped')
        environment=freeze['environment'];name=environment['qualificationId']+'-'+stage
        targets=[root.resolve(),Path(environment['hostBackingRoot']).resolve(),REPO.resolve()]
        reject_overlapping_writers(running_containers(),targets)
        write(active,{'containerName':name,'qualificationId':environment['qualificationId'],
                     'freezeSha256':sha(root/'freeze.json'),'startedAt':now(),'status':'active'},exclusive=False)
        old_handlers={}
        def interrupted(signum, frame):raise InterruptedError('host signal '+str(signum))
        try:
            for signum in (signal.SIGINT,signal.SIGTERM):old_handlers[signum]=signal.signal(signum,interrupted)
            yield
        finally:
            # Ignore subsequent termination while confirming the daemon state.
            # SIGKILL leaves active.json for the next cooperative writer to audit.
            for signum in old_handlers:signal.signal(signum,signal.SIG_IGN)
            directory=root/'stages'/stage;directory.mkdir(parents=True,exist_ok=True)
            value=stop_named_container(name,directory/'writer-stop-inspect.json')
            write(active,{'containerName':name,'qualificationId':environment['qualificationId'],
                         'status':'stopped','stoppedConfirmedBeforeUnlock':True,'containerPresent':value is not None},exclusive=False)
            for signum,handler in old_handlers.items():signal.signal(signum,handler)
    finally:os.close(fd)


def remove_finished_container(root, stage, environment):
    name=environment['qualificationId']+'-'+stage
    value=inspect_named_container(name)
    if not container_stopped(value):raise ValueError('cannot cleanup container before stopped confirmation')
    directory=root/'stages'/stage
    write(directory/'cleanup-inspect.json',value)
    result=subprocess.run(['docker','container','rm',name],capture_output=True,text=True,timeout=15,check=True)
    write(directory/'cleanup.json',{'containerName':name,'containerId':value['Id'],'removed':True,
          'stdout':result.stdout,'stderr':result.stderr,'imagesRemoved':False,'volumesRemoved':False,'dataRemoved':False})



def _host_stage(root, repo, stage):
    if stage == 'prepare':
        prepare(root, repo)
        return
    freeze = verify_freeze(root, repo)
    verify_previous(root, stage)
    if stage in ('bundle', 'admission', 'pair'): verify_review(root, repo, freeze)
    if stage == 'verify':
        directory = root / 'stages/verify'
        directory.mkdir()
        command = [sys.executable, '-I', '-B', str(root / 'export/verify-persistent-update-evidence.py'),
                   '--pair', str(root / 'export'), '--output', str(directory / 'checker-result.json')]
        transport = execute(directory / 'transport', command, 300, cwd=Path('/tmp'))
        if transport['exitCode']: raise ValueError('independent exported checker failed')
        result = read(directory / 'checker-result.json')
        if result.get('persistentUpdateNormalRebootVerified') is not True or result.get('finalSystemAccepted') is not False:
            raise ValueError('checker did not prove update/data qualification')
        seal_stage(root, stage, ['stages/verify/checker-result.json', 'stages/verify/transport'])
        return
    environment = freeze['environment']
    require_authorization(root, stage, freeze)
    inspect_image(environment['rustImage'] if stage == 'build' else environment['toolsImage'])
    absent('container', environment['qualificationId'] + '-' + stage)
    if stage == 'admission':
        backing=root/'admission-backing';backing.mkdir(mode=0o777);backing.chmod(0o777)
        write(root/'admission-host-storage.json',host_apfs_evidence(backing))
    if stage == 'pair':
        admit_host_storage(root, environment)
    if stage == 'build':
        volume = environment['buildVolume']
        absent('volume', volume)
        subprocess.run(['docker', 'volume', 'create', '--label', 'mingos.qualification-id=' + environment['qualificationId'], volume], capture_output=True, text=True, timeout=15, check=True)
    for name in {'build': ('build', 'inputs/payload'), 'bundle': ('bundle-stage',),
                 'admission': ('admission',), 'pair': (), 'export': ('export-stage',)}[stage]:
        path = root / name
        path.mkdir(mode=0o777)
        path.chmod(0o777)
    command = docker_command(stage, repo, root, environment)
    timeout = {'build': 8400, 'bundle': 360, 'admission': 300, 'pair': freeze['budget']['containerTimeoutSeconds'], 'export': 300}[stage]
    record = execute(root / 'stages' / stage, command, timeout)
    state = stop_container(root, stage, environment)
    if state is not None: remove_finished_container(root, stage, environment)
    if record['exitCode'] or state is None or state.get('OOMKilled') is not False or state.get('ExitCode') != 0:
        write(root / 'stages' / stage / 'failure.json', {'status': 'failed', 'command': record, 'containerState': state, 'retryAllowed': False})
        raise RuntimeError('stage failed; keep volume/backing/evidence, stopped container cleaned, no retry')
    verify_freeze(root, repo)
    products = ['stages/' + stage]
    if stage == 'build':
        materialize_fixture(root)
        summary = load_checker(root).verify_fixture_directory(root / 'build/fixture', root / 'inputs', read(root / 'build/review-base.json'))
        inside_summary = read(root / 'build/fixture-verification.inside.json')
        def semantic_fixture(value):
            value = json.loads(json.dumps(value))
            for label in ('bootstrap','old','new'): value[label].pop('openssl',None)
            return value
        if semantic_fixture(summary) != semantic_fixture(inside_summary):
            raise ValueError('independent host fixture verification differs from Linux verification')
        write(root / 'fixture-verification.json', summary)
        base = review_from_payload(root, repo, freeze)
        write(root / 'review.json', base)
        products += ['build', 'inputs/payload', 'fixture-verification.json']
    elif stage == 'bundle':
        (root / 'bundle-stage/bundle').rename(root / 'bundle')
        products += ['bundle', 'bundle-stage/transport', 'review.json']
    elif stage == 'admission':
        verify_admission(root,freeze)
        products += ['admission','admission-host-storage.json']
    elif stage == 'pair':
        # Evidence stays on the task volume until the explicit export stage.
        products += []
    elif stage == 'export':
        (root / 'export-stage/export').rename(root / 'export')
        output = root / 'export'
        context = {'schemaVersion': 1, 'repo': 'replay', 'inputs': 'inputs', 'review': 'inputs/session-review.json',
                   'bundle': 'bundle', 'fixtureDirectory': 'fixture',
                   'fixtureVerification': 'fixture-verification.json', 'freeze': 'freeze.json'}
        write(output / 'pair/verification-context.json', context)
        write(output / 'file-sha256.json', {'schemaVersion': 1, 'algorithm': 'sha256', 'files': file_hashes(output)})
        archive = root / 'qualification-export.tar'
        with tarfile.open(archive, 'x') as target:
            for name in sorted(file_hashes(output)):
                path = output / name
                target.add(path, arcname=name, recursive=False)
        with tarfile.open(archive) as target:
            members = target.getmembers()
            if any(not member.isfile() for member in members) or {member.name for member in members} != set(file_hashes(output)):
                raise ValueError('export archive members differ from regular file export')
            for member in members:
                if hashlib.sha256(target.extractfile(member).read()).hexdigest() != sha(output / member.name): raise ValueError('export archive bytes mismatch')
        products += ['export', 'qualification-export.tar']
    # Avoid hashing this stage's own result into itself.
    products.remove('stages/' + stage)
    for path in sorted((root / 'stages' / stage).iterdir()):
        if path.is_file(): products.append(str(path.relative_to(root)))
    seal_stage(root, stage, products)


def host_stage(root, repo, stage):
    if stage in ('admission','pair','export'):
        freeze=verify_freeze(root,repo)
        if stage != 'admission':require_authorization(root,'pair',freeze)
        verify_previous(root,stage)
        if stage in ('admission','pair'):verify_review(root,repo,freeze)
        with host_writer(root,freeze,stage):return _host_stage(root,repo,stage)
    return _host_stage(root,repo,stage)


def admission_probe(root, repo, freeze=None):
    """Real no-guest preflight; disk creation and the VM child launch are intercepted."""
    import socket
    import tempfile
    from unittest import mock
    boot_modules(repo)
    from data_disk import runtime_directory, workspace, lease
    runtime = read(root / 'runtime-inputs.json')
    validate_runtime_inputs(runtime, repo, root, container=True)
    if sys.platform != 'linux' or os.geteuid() != 65534: raise ValueError('Linux UID65534 admission required')
    for name, expected in {'cpu.max':'200000 100000','memory.max':'4294967296','pids.max':'256'}.items():
        if (Path('/sys/fs/cgroup')/name).read_text().strip() != expected: raise ValueError('actual container resource limit mismatch')
    runtime_root = runtime_directory(Path('/run/mingos-runtime'))
    available = shutil.disk_usage('/var/tmp').free
    if available < 2147483648: raise ValueError('actual Linux backing has less than 2 GiB free')
    report = {'schemaVersion':1,'status':'passed','guestStarted':False,'dataDiskCreated':False,
              'finalSystemAccepted':False,'uid':os.geteuid(),'availableBytes':available,
              'runtimeRoot':str(runtime_root),'runtimeFileSystem':'tmpfs','runtimeMode':'0700',
              'runtimeInputs':runtime,'sourceSha256':capture_source(repo,root)}
    for name in ('python3','qemu-system-x86_64','mkfs.ext4','e2fsck','blkid','openssl','cpio','gzip','ldd'):
        path=shutil.which(name)
        if path is None: raise ValueError('required actual Linux tool missing: '+name)
        report.setdefault('tools',{})[name]={'path':path,'sha256':sha(path)}
    with tempfile.TemporaryDirectory(prefix='admission-',dir=runtime_root) as directory:
        path=Path(directory);sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try: sock.bind(str(path/'qmp.sock'));sock.listen(1);report['qmpUnixSocketBound']=True
        finally:sock.close()
        work=workspace(Path('/var/tmp/mingos-boot-infra-20261025'),create=True)
        probe=work/'probe.bytes';probe.write_bytes(b'lease');probe.chmod(0o600)
        with lease(work,{'image':'probe.bytes'},runtime_root=runtime_root):
            try:
                with lease(work,{'image':'probe.bytes'},runtime_root=runtime_root): raise AssertionError('lease overlap accepted')
            except BlockingIOError:report['exclusiveLeaseVerified']=True
        with lease(work,{'image':'probe.bytes'},runtime_root=runtime_root):report['leaseReleasedVerified']=True
        private=path/'key.der';private.write_bytes(bytes.fromhex('302e020100300506032b657004220420')+bytes([7])*32)
        public=path/'public.der';public.write_bytes(bytes.fromhex('302a300506032b6570032100ea4a6c63e29c520abef5507b132ec5f9954776aebebe7b92421eea691446d22c'))
        message=path/'message';message.write_bytes(b'mingos-persistent-update-admission-v1')
        signature=path/'signature'
        def crypto(arguments):return subprocess.run(['/usr/bin/openssl','pkeyutl',*arguments],stdin=subprocess.DEVNULL,capture_output=True,timeout=10)
        signed=crypto(['-sign','-inkey',str(private),'-keyform','DER','-rawin','-in',str(message),'-out',str(signature)])
        if signed.returncode:raise ValueError('real OpenSSL signing probe failed: '+signed.stderr.decode())
        args=['-verify','-pubin','-inkey',str(public),'-keyform','DER','-rawin','-in',str(message),'-sigfile',str(signature)]
        if crypto(args).returncode:raise ValueError('real positive Ed25519 verification failed')
        message.write_bytes(b'altered-mingos-persistent-update-admission-v1')
        if crypto(args).returncode!=1:raise ValueError('altered message did not reject normally with OpenSSL exit 1')
        report.update(ed25519PositiveVerified=True,ed25519NegativeRejected=True)
        # Execute the current runner until its exact child launch, with all disk
        # creation and bundle prerequisites replaced by local test doubles.
        disk=path/'intercept.bytes';disk.write_bytes(b'not-a-disk')
        pair_work=path/'boundary';pair_work.mkdir();identity='1'*64
        review={'inputScripts':{'boot1Sha256':'2'*64,'boot2Sha256':'3'*64}}
        boundary_freeze={'environment':{'workRoot':str(pair_work),'qualificationId':'probe','diskManifest':'no-real-disk'},
                'budget':{'pairTimeoutSeconds':1920}}
        with mock.patch(__name__+'.require_authorization'), \
             mock.patch(__name__+'.verify_previous_chain'), \
             mock.patch.object(sys.modules['data_disk'],'workspace',return_value=pair_work), \
             mock.patch.object(sys.modules['data_disk'],'create_disk',return_value={'imagePath':str(disk),'uuid':'probe'}), \
             mock.patch.object(sys.modules['bootlib'],'validate_bundle',return_value={'sessionReview':review}), \
             mock.patch.object(sys.modules['bootlib'],'validate_reviewed_input',return_value=(b'{}',{})), \
             mock.patch(__name__+'.verify_review',return_value=review), \
             mock.patch(__name__+'.load_checker',return_value=object()), \
             mock.patch(__name__+'.verify_freeze',return_value=boundary_freeze), \
             mock.patch(__name__+'.read',return_value={}), \
             mock.patch(__name__+'.sha',return_value=identity), \
             mock.patch.object(subprocess,'Popen',side_effect=RuntimeError('EXPECTED_VM_BOUNDARY_INTERCEPT')) as launch:
            try:inside_pair(root,repo,boundary_freeze)
            except RuntimeError as error:
                if 'pair failed permanently' not in str(error):raise
            else:raise AssertionError('VM launch boundary was not intercepted')
            if launch.call_count!=1:raise ValueError('VM boundary launched unexpected number of children')
            report['vmLaunchIntercepted']=True;report['secondBootAfterFailureBlocked']=True
    output=root/('admission' if freeze is not None else 'admission-probe')
    if not output.is_dir(): raise ValueError('dedicated writable admission output required')
    result=execute(output/'runtime-copy',[sys.executable,'-B',str(repo/'scripts/boot/check-runtime.py'),
                   '--profile','persistent-update','--output',str(output/'runtime-copy-output')],120)
    if result['exitCode'] or read(output/'runtime-copy-output/result.json').get('guestPythonImportPassed') is not True:
        raise ValueError('actual runtime copy/isolated guest import failed')
    report['runtimeCopyAndGuestImportPassed']=True
    checked=execute(output/'checker-controls',[sys.executable,'-B','-m','unittest','discover','-s',str(root),
                    '-p','test_persistent_update_evidence.py','-q'],120)
    if checked['exitCode']: raise ValueError('actual Linux checker positive/negative controls failed')
    report['checkerPositiveAndNegativeControlsPassed']=True
    checked=execute(output/'driver-controls',[sys.executable,'-B','-m','unittest','discover','-s',str(root),
                    '-p','test_run_stage.py','-q'],120)
    if checked['exitCode']: raise ValueError('actual Linux driver positive/negative controls failed')
    report['driverPositiveAndNegativeControlsPassed']=True
    if freeze is not None:
        verify_freeze(root,repo)
        if report['sourceSha256'] != freeze['files']:raise ValueError('admission did not observe exact frozen source')
        report['freezeSha256']=sha(root/'freeze.json')
    write(output/'result.json',report)
    print(json.dumps({key:value for key,value in report.items() if key not in ('sourceSha256','runtimeInputs')},sort_keys=True))


def inside_admission(root, repo, freeze):
    verify_review(root,repo,freeze)
    admission_probe(root,repo,freeze)


def verify_admission(root, freeze):
    admission=read(root/'admission/result.json')
    flags=('ed25519PositiveVerified','ed25519NegativeRejected','exclusiveLeaseVerified','leaseReleasedVerified',
           'qmpUnixSocketBound','vmLaunchIntercepted','secondBootAfterFailureBlocked',
           'runtimeCopyAndGuestImportPassed','checkerPositiveAndNegativeControlsPassed','driverPositiveAndNegativeControlsPassed')
    if (type(admission.get('schemaVersion')) is not int or admission['schemaVersion'] != 1 or admission.get('status') != 'passed'
        or admission.get('guestStarted') is not False or admission.get('dataDiskCreated') is not False
        or admission.get('finalSystemAccepted') is not False or type(admission.get('uid')) is not int or admission['uid'] != 65534
        or admission.get('freezeSha256') != sha(root/'freeze.json')
        or admission.get('sourceSha256') != freeze['files']
        or admission.get('runtimeInputs') != freeze['runtime']
        or admission.get('runtimeRoot') != '/run/mingos-runtime' or admission.get('runtimeFileSystem') != 'tmpfs'
        or admission.get('runtimeMode') != '0700' or type(admission.get('availableBytes')) is not int or admission['availableBytes']<2147483648
        or any(admission.get(key) is not True for key in flags)):
        raise ValueError('exact frozen real no-guest admission is incomplete')
    return admission


def run_admission_host(root, repo):
    identifier='persistent-update-admission-'+uuid.uuid4().hex[:12]
    write(root/'source-inventory.json',sorted(source_names(repo,root)),exclusive=False)
    output=root/('development-admission-'+identifier.rsplit('-',1)[-1]);output.mkdir(exist_ok=False)
    (root/'admission-probe').mkdir(exist_ok=True)
    inspect_image(TOOLS_IMAGE);absent('container',identifier)
    argv=['docker','run','--name',identifier,'--pull','never','--network','none','--read-only','--init',
          '--cap-drop','ALL','--security-opt','no-new-privileges','--user','65534:65534','--pids-limit','256',
          '--memory','4g','--cpus','2','--tmpfs','/tmp:rw,exec,size=512m,mode=1777',
          '--tmpfs','/run/mingos-runtime:rw,size=16m,mode=0700,uid=65534,gid=65534',
          '--mount','type=bind,source='+str(repo)+',target=/repo,readonly',
          '--mount','type=bind,source='+str(root)+',target=/prep,readonly',
          '--mount','type=bind,source='+str(root/'external-inputs/openssl')+',target=/usr/bin/openssl,readonly',
          '--mount','type=bind,source='+str(output)+',target=/prep/admission-probe',
          '--workdir','/repo',TOOLS_IMAGE,'python3','-B','/prep/run-stage.py','--root','/prep','--admission-probe','--inside']
    output.chmod(0o777)
    backing=output/'backing';backing.mkdir(mode=0o777);backing.chmod(0o777)
    write(output/'host-storage.json',host_apfs_evidence(backing))
    image_index=argv.index(TOOLS_IMAGE)
    argv[image_index:image_index]=['--mount','type=bind,source='+str(backing)+',target=/var/tmp']
    result=execute(output/'transport',argv,180)
    inspected=subprocess.run(['docker','container','inspect',identifier],capture_output=True,text=True,timeout=15,check=True)
    container=strict_json(inspected.stdout)[0];write(output/'container-inspect.json',container)
    if container['State']['Running'] or container['State']['Pid']!=0:raise ValueError('admission container is not confirmed stopped')
    cleanup=subprocess.run(['docker','container','rm',identifier],capture_output=True,text=True,timeout=15,check=True)
    write(output/'cleanup.json',{'containerId':container['Id'],'containerName':identifier,'removed':True,
          'stdout':cleanup.stdout,'stderr':cleanup.stderr,'imagesRemoved':False,'volumesRemoved':False})
    if result['exitCode'] or container['State'].get('OOMKilled') or container['State']['ExitCode']!=0:
        raise ValueError('development no-guest admission failed; logs retained and stopped container cleaned')


def preview(root, repo):
    """Read current preparation inputs only; never inspect Docker or create files."""
    geometry=read(root/'input-geometry.json');validate_geometry(geometry,repo,root/'geometry.stdout.log')
    first,second=input_contracts();validate_inputs(first,second,root)
    specs={}
    for number,expected in enumerate((first,second),1):
        raw=(root/('input-boot%d.json'%number)).read_bytes()
        if strict_json(raw)!=expected:raise ValueError('preview fixed input changed')
        specs['boot%d'%number]={'path':'input-boot%d.json'%number,'sha256':hashlib.sha256(raw).hexdigest(),'spec':expected}
    runtime=read(root/'runtime-inputs.json');validate_runtime_inputs(runtime,repo,root)
    sources=capture_source(repo,root)
    probes=[]
    for path in sorted(root.glob('development-admission-*/result.json')):
        value=read(path)
        if value.get('status')=='passed':probes.append({'path':str(path.relative_to(root)),'sha256':sha(path),
            'sourceMatchesCurrentDraft':value.get('sourceSha256')==sources,'guestStarted':value.get('guestStarted'),
            'formalAdmission':False})
    result={'schemaVersion':1,'status':'draft-unsealed','profile':PROFILE,
        'reviewed':False,'formalRunAuthorized':False,'finalSystemAccepted':False,
        'sourceCommit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
        'files':sources,'sourceCount':len(sources),'inputs':specs,'fixture':fixture_inputs(repo,root),'hostTestFixtures':host_test_inputs(repo),
        'geometry':{'path':'input-geometry.json','sha256':sha(root/'input-geometry.json'),
                    'stdoutSha256':sha(root/'geometry.stdout.log'),'test':geometry['test']},
        'runtime':runtime,'budget':execution_budget(first,second),
        'environmentDraft':environment_contract('persistent-update-20261104-qual1-draft',root),
        'developmentAdmissions':probes,'stageOrder':list(STAGES),
        'remaining':['final independent source review','prepare source freeze','fresh Linux build and real fixture',
                     'primary session review','bundle','freeze-bound no-guest admission',
                     'concrete new-disk/two-boot authorization before pair']}
    return result



def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=STAGES)
    parser.add_argument('--admission-probe', action='store_true')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--root', required=True)
    parser.add_argument('--inside', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = checked_root(args.root)
    if sum((args.stage is not None,args.admission_probe,args.preview)) != 1:parser.error('exactly one of --stage, --preview, --admission-probe required')
    if args.preview:
        if args.inside:parser.error('--preview is host-only')
        print(json.dumps(preview(root,REPO),indent=2,sort_keys=True));return 0
    if args.admission_probe:
        if args.inside: admission_probe(root, Path('/repo'))
        else: run_admission_host(root, REPO)
        return 0
    if args.stage is None: parser.error('--stage or --admission-probe required')
    if args.inside:
        if args.stage not in ('build', 'bundle', 'admission', 'pair', 'export'): raise ValueError('unsupported container stage')
        inside(root, args.stage)
    else:
        if root != HERE: raise ValueError('root must be this independently prepared driver directory')
        host_stage(root, REPO, args.stage)
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
        raise SystemExit(1)
