#!/usr/bin/env python3
"""Frozen Linux runtime two-boot controller. Import and --help never start a guest."""
import argparse
import importlib.util
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

import linux_host_qualification as q
import linux_host_freeze as freezer
import linux_host_uninstall_runtime as runtime
import linux_host_storage as storage

WORK = Path('/work/mingos-boot-infra-20261025')
RUNTIME = Path('/run/mingos-runtime')
LOG_LIMIT = 8 * 1024 * 1024


def validate_boot_report(report, number, identity, disk_uuid, input_sha256, previous):
    if (report.get('status') != 'normal-desktop-poweroff'
            or any(report.get(key) is not True for key in ('bootAttempted', 'kernelPowerdown', 'inputComplete'))
            or type(report.get('guestExitCode')) is not int or report['guestExitCode'] != 0
            or type(report.get('bootNumber')) is not int or report['bootNumber'] != number
            or report.get('bundleIdentity') != identity or report.get('diskUuid') != disk_uuid
            or report.get('inputScriptSha256') != input_sha256
            or not isinstance(report.get('bootId'), str) or not report['bootId']):
        raise ValueError('boot lacks exact reviewed normal complete poweroff')
    marker = report.get('marker')
    if (not isinstance(marker, dict) or type(marker.get('counter')) is not int
            or marker['counter'] != number or marker.get('bootId') != report['bootId']):
        raise ValueError('fresh disk marker identity/sequence differs')
    expected = None if number == 1 else previous[0]['marker']
    if report.get('previousMarker') != expected:
        raise ValueError('previous disk marker chain differs')
    if number == 2:
        from infra import validate_cold_pair
        validate_cold_pair(previous[0], report)


def validate_strong_pair(one, two):
    """Same content/private state survives an actual normal reboot, per checker."""
    try:
        okay = (one['bootId'] != two['bootId'] and one['sessionId'] != two['sessionId']
            and one['after']['envelope']['diskUuid'] == two['before']['envelope']['diskUuid']
            and two['before']['envelope']['previousMarker'] == one['after']['envelope']['marker']
            and one['after']['content'] == two['before']['content']
            and one['after']['privateContent'] == two['before']['privateContent'])
    except (KeyError, TypeError) as error:
        raise ValueError('strong pair snapshot missing') from error
    if not okay:
        raise ValueError('reboot changed identity/marker/content/private data')


def _log_budgets(directory, guard):
    for path in (directory / 'stdout.log', directory / 'stderr.log',
                 guard.path.parent / 'kernel-serial.log', guard.path):
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > LOG_LIMIT:
                raise ValueError('boot log budget or regular-file identity exceeded: ' + str(path))


def supervise_boot(directory, command, guard, *, timeout=945, pair_deadline):
    """Monitor raw guest failures live and reap this serialized child tree in 5s."""
    if not 0 < timeout <= 945 or pair_deadline <= time.monotonic():
        raise ValueError('bounded boot/pair deadline required')
    q.become_subreaper()
    if q.direct_children():
        raise ValueError('pair supervisor refuses unrelated existing children')
    directory = Path(directory)
    directory.mkdir(mode=0o700)
    q.sync_directory(directory.parent)
    q.write_json(directory / 'command.json', {'command': command, 'timeoutSeconds': timeout,
                 'startedAt': q.now(), 'cleanupBudgetSeconds': 5,
                 'environment': runtime.runtime_environment()})
    child, failure, timed_out = None, None, False
    cleanup_errors, orphans, cleanup_complete = [], [], False
    started = time.monotonic()
    with (directory / 'stdout.log').open('xb') as out, (directory / 'stderr.log').open('xb') as err:
        try:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                     start_new_session=True, env=runtime.runtime_environment())
            while child.poll() is None:
                _log_budgets(directory, guard)
                guard.poll()
                if time.monotonic() - started >= timeout or time.monotonic() >= pair_deadline:
                    timed_out = True
                    raise TimeoutError('boot/pair deadline exceeded')
                time.sleep(.05)
            _log_budgets(directory, guard)
            guard.poll(final=True)
            if child.returncode != 0:
                raise RuntimeError('VM child exited nonzero')
        except BaseException as error:
            failure = repr(error)
        finally:
            deadline = time.monotonic() + 5
            if child is not None:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as error:
                    cleanup_errors.append(repr(error))
                try:
                    if child.poll() is None:
                        child.kill()
                    child.wait(timeout=max(.001, deadline - time.monotonic()))
                except (OSError, subprocess.TimeoutExpired) as error:
                    cleanup_errors.append(repr(error))
            try:
                orphans, cleanup_complete = q.reap_descendants(deadline)
            except OSError as error:
                cleanup_errors.append(repr(error))
            if child is not None:
                adopted = next((item for item in orphans if item['pid'] == child.pid), None)
                if adopted:
                    child.returncode = adopted['exitCode']
                    orphans.remove(adopted)
            for stream in (out, err):
                stream.flush()
                os.fsync(stream.fileno())
    leader_exit = child.returncode if child else None
    code = leader_exit if leader_exit is not None else 127
    if code == 0 and (failure or timed_out or orphans or cleanup_errors or not cleanup_complete):
        code = 125
    result = {'exitCode': code, 'leaderExitCode': leader_exit, 'timedOut': timed_out,
        'error': failure, 'cleanupErrors': cleanup_errors, 'orphanedChildren': orphans,
        'cleanupComplete': cleanup_complete, 'finishedAt': q.now(),
        'stdout': q.stable_hash(directory / 'stdout.log'), 'stderr': q.stable_hash(directory / 'stderr.log')}
    q.write_json(directory / 'result.json', result)
    return result


def load_bound(root, name, expected):
    path = Path(root) / name
    if q.stable_hash(path) != expected:
        raise ValueError('frozen pair driver/checker changed before import: ' + name)
    spec = importlib.util.spec_from_file_location('linux_pair_bound_' + name.replace('-', '_').replace('.', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if q.stable_hash(path) != expected:
        raise ValueError('frozen pair driver/checker changed during import: ' + name)
    return module


def verify_boundary(root, repo, digest):
    if q.stable_hash(root / 'runtime-freeze.json')['sha256'] != digest:
        raise ValueError('runtime freeze changed before next boot')
    value = runtime.load_runtime(root, repo)
    for stage in ('prepare', 'build', 'bundle', 'admission'):
        q.verify_stage(root, stage, digest)
    runtime.verify_bundle_products(root)
    runtime.verify_admission_products(root)
    runtime.verify_review(root)
    if q.stable_hash(root / 'runtime-freeze.json')['sha256'] != digest:
        raise ValueError('runtime freeze changed at boot boundary')
    return value


def vm_command(repo, root, work, pair, number, disk_manifest):
    return [sys.executable, '-B', str(repo / 'scripts/boot/vm.py'),
        '--bundle', str(root / 'bundle'), '--review', str(root / 'review.json'),
        '--work-root', str(work), '--data-manifest', disk_manifest,
        '--output', str(pair / ('boot-%d' % number)), '--timeout', '900',
        '--boot-number', str(number), '--input-script', str(pair / ('boot-%d.input.json' % number)),
        '--runtime-root', str(RUNTIME), '--diagnostic-split-input', '--diagnostic-pointer-handshake']


def execute_pair(root, repo, stage, value, digest):
    """Create one new disk and run boot2 only after boot1's strong raw gate."""
    from bootlib import validate_bundle, validate_reviewed_input
    from data_disk import workspace, create_disk, load_disk, runtime_directory
    if verify_boundary(root, repo, digest) != value:
        raise ValueError('runtime snapshot differs before pair')
    driver = load_bound(root, 'driver.py', value['checker']['driver.py'])
    checker = load_bound(root, 'checker.py',
                         value['checker']['checker.py'])
    review = q.read_json(root / 'review.json')
    signed = q.read_json(root / 'fixture-verification.json')
    bundle = root / 'bundle'
    identity = q.stable_hash(bundle / 'bundle.json')['sha256']
    manifest = validate_bundle(bundle, q.stable_hash(root / 'review.json')['sha256'])
    if manifest['sessionReview'] != review:
        raise ValueError('review differs from exact bundle')
    raw_inputs = {}
    for number in (1, 2):
        raw, input_hash = validate_reviewed_input(review, root / 'inputs' / ('input-boot%d.json' % number), number)
        spec = driver.strict_json(raw)
        if (input_hash != value['inputs'][str(number)]['sha256']
                or len(spec.get('steps', [])) != (7 if number == 1 else 1)):
            raise ValueError('exact seven/one native input steps required')
        raw_inputs[number] = raw
    runtime_directory(RUNTIME)
    work = workspace(WORK, create=True)
    disk = create_disk(work, 'linux-runtime-128', 128)
    disk_manifest = str(Path(disk['manifestPath']).relative_to(work))
    pair = work / 'cold-pair'
    pair.mkdir(mode=0o700)
    q.sync_directory(work)
    q.write_json(pair / 'disk-preparation.json', disk)
    for number, raw in raw_inputs.items():
        with (pair / ('boot-%d.input.json' % number)).open('xb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        (pair / ('boot-%d.input.json' % number)).chmod(0o400)
    q.sync_directory(pair)
    reports, audited = [], []
    deadline = time.monotonic() + 1920
    def run_one(number):
        if verify_boundary(root, repo, digest) != value:
            raise ValueError('runtime source/provenance changed between boots')
        if deadline - time.monotonic() < 945:
            raise TimeoutError('insufficient complete boot supervisor budget')
        if q.stable_hash(bundle / 'bundle.json')['sha256'] != identity or load_disk(work, disk_manifest) != disk:
            raise ValueError('same bundle/disk identity changed')
        if q.stable_hash(pair / ('boot-%d.input.json' % number))['sha256'] != value['inputs'][str(number)]['sha256']:
            raise ValueError('reviewed input bytes changed before boot')
        boot = pair / ('boot-%d' % number)
        argv = vm_command(repo, root, work, pair, number, disk_manifest)
        q.write_json(pair / ('boot-%d.command.json' % number), {'argv': argv,
            'timeoutSeconds': 900, 'supervisorSeconds': 945, 'pairSeconds': 1920,
            'containerSeconds': 1950, 'guestVcpus': 2, 'guestMemoryMiB': 2048,
            'guestWatchdogSeconds': 300, 'pointerWaitSeconds': 5, 'stderrLimitBytes': LOG_LIMIT,
            'diskSha256Before': q.stable_hash(Path(disk['imagePath']))['sha256']})
        result = supervise_boot(pair / ('boot-%d.transport' % number), argv,
            driver.RawGuard(boot / 'guest.jsonl', number), pair_deadline=deadline)
        if result['exitCode'] != 0 or result.get('timedOut') or not result.get('cleanupComplete'):
            raise RuntimeError('VM/raw supervisor failed; preserve disk and first failure')
        return q.read_json(boot / 'result.json')
    def gate(number, report):
        validate_boot_report(report, number, identity, disk['uuid'],
                             value['inputs'][str(number)]['sha256'], reports)
        checked = checker.verify_boot(pair / ('boot-%d' % number), number, review,
            driver.strict_json(raw_inputs[number]), signed, identity, repo)
        if number == 2:
            validate_strong_pair(audited[0], checked)
        audited.append(checked)
        reports.append(report)
        return {'passed': True, 'bootNumber': number, 'strongRawVerified': True,
            'diskSha256After': q.stable_hash(Path(disk['imagePath']))['sha256'], 'checkerResult': checked}
    result = driver.run_two_boots(pair, run_one, gate)
    if result['status'] != 'same-bundle-disk-cold-boot-verified':
        raise RuntimeError('pair failed permanently; no retry, disk reset or format')
    if time.monotonic() > deadline:
        raise TimeoutError('pair verification exceeded retained 1920-second deadline')
    if verify_boundary(root, repo, digest) != value:
        raise ValueError('runtime changed during pair')
    if load_disk(work, disk_manifest) != disk:
        raise ValueError('disk identity changed after pair')
    q.write_json(stage / 'external-products.json', {'pairRoot': str(pair), 'pairFiles': q.tree_inventory(pair),
        'workspaceRoot': str(work), 'workspaceFiles': q.tree_inventory(work),
        'disk': disk, 'runtimeFreezeSha256': digest})
    return {'twoBootsStrongRawVerified': True, 'bundleIdentity': identity, 'diskUuid': disk['uuid'],
        'bootIds': [report['bootId'] for report in reports], 'formalQualification': False,
        'finalSystemAccepted': False}


def run_pair(root, repo):
    root, repo = map(q.checked_directory, map(Path, (root, repo)))
    runtime._runtime_root(root)
    if (root / 'stages/pair').exists() or (root / 'stages/pair').is_symlink():
        raise FileExistsError('pair stage already attempted; preserve original disk and failure')
    digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
    value = verify_boundary(root, repo, digest)
    with storage.storage_session(root / 'storage-control', value['storage']['probe'],
                                 sum(freezer.capacity_budget().values())):
        q.run_stage(root, 'pair', digest, lambda stage: execute_pair(root, repo, stage, value, digest))
    return q.verify_stage(root, 'pair', digest)


def probe_pair_boundary(root, repo):
    """No-guest admission: intercept this controller's first exact VM launch."""
    from unittest import mock
    import data_disk
    root, repo = Path(root), Path(repo)
    value = runtime.load_runtime(root, repo)
    runtime.verify_bundle_products(root)
    digest = q.stable_hash(root / 'runtime-freeze.json')['sha256']
    original_verify = q.verify_stage
    original_popen = subprocess.Popen
    launches, creations = [], []
    def admitted_prerequisites(stage_root, stage, freeze_sha256):
        # Admission invokes this probe while its own q stage is incomplete.
        if stage == 'admission' and Path(stage_root) == root and freeze_sha256 == digest:
            return {'probeInProgress': True, 'guestStarted': False}
        return original_verify(stage_root, stage, freeze_sha256)
    with tempfile.TemporaryDirectory(prefix='pair-boundary-', dir=RUNTIME) as temporary:
        directory = Path(temporary)
        work = directory / 'work'
        stage = directory / 'stage'
        stage.mkdir(mode=0o700)
        def workspace(path, create=False):
            if Path(path) != work or create is not True:
                raise ValueError('unexpected probe workspace request')
            work.mkdir(mode=0o700)
            return work
        def create_disk(path, name, size):
            if Path(path) != work or name != 'linux-runtime-128' or size != 128 or creations:
                raise ValueError('unexpected or repeated probe disk request')
            creations.append(name)
            (work / 'disks').mkdir(mode=0o700)
            image = work / 'disks/linux-runtime-128.raw'
            image.write_bytes(b'not-a-filesystem-no-format-probe')
            image.chmod(0o600)
            manifest = {'imagePath': str(image), 'manifestPath': str(work / 'disks/linux-runtime-128.json'),
                        'image': 'disks/linux-runtime-128.raw', 'uuid': 'no-real-disk', 'sizeMiB': 128}
            q.write_json(Path(manifest['manifestPath']), manifest)
            return manifest
        def launch(command, *args, **kwargs):
            if list(command) == vm_command(repo, root, work, work / 'cold-pair', 1,
                                           'disks/linux-runtime-128.json'):
                launches.append(list(command))
                raise RuntimeError('EXPECTED_LINUX_PAIR_VM_BOUNDARY_INTERCEPT')
            # runtime_directory performs only this real tmpfs observation.
            if list(command) == ['/usr/bin/stat', '-f', '-c', '%T', str(RUNTIME)]:
                return original_popen(command, *args, **kwargs)
            raise RuntimeError('unexpected subprocess in no-guest pair probe: ' + repr(command))
        with mock.patch.object(q, 'verify_stage', side_effect=admitted_prerequisites), \
             mock.patch.object(runtime, 'verify_admission_products', return_value={'probeInProgress': True}), \
             mock.patch.object(data_disk, 'workspace', side_effect=workspace), \
             mock.patch.object(data_disk, 'create_disk', side_effect=create_disk), \
             mock.patch.object(data_disk, 'load_disk', side_effect=lambda *args: q.read_json(work / 'disks/linux-runtime-128.json')), \
             mock.patch.object(sys.modules[__name__], 'WORK', work), \
             mock.patch.object(subprocess, 'Popen', side_effect=launch):
            try:
                execute_pair(root, repo, stage, value, digest)
            except RuntimeError as error:
                if 'pair failed permanently' not in str(error):
                    raise
            else:
                raise ValueError('pair probe unexpectedly passed launch')
        result = q.read_json(work / 'cold-pair/result.json')
        transport = q.read_json(work / 'cold-pair/boot-1.transport/result.json')
        if (len(launches) != 1 or len(creations) != 1 or result.get('status') != 'failed'
                or result.get('firstFailure', {}).get('bootNumber') != 1
                or (work / 'cold-pair/boot-2').exists()
                or 'EXPECTED_LINUX_PAIR_VM_BOUNDARY_INTERCEPT' not in (transport.get('error') or '')
                or not transport.get('cleanupComplete')):
            raise ValueError('exact first-boot launch failure containment was not proven')
        return {'schemaVersion': 1, 'vmLaunchIntercepted': True, 'vmLaunchCount': 1,
            'secondBootAfterFailureBlocked': True, 'guestStarted': False, 'dataDiskCreated': False,
            'runtimeFreezeSha256': digest, 'command': launches[0],
            'budget': q.read_json(work / 'cold-pair/boot-1.command.json'),
            'supervisorResult': transport, 'firstFailure': result['firstFailure'],
            'formalQualification': False, 'finalSystemAccepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--probe-boundary', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.probe_boundary:
        if args.output is None:
            parser.error('--probe-boundary requires new --output')
        q.write_json(args.output, probe_pair_boundary(args.root, args.repo))
    else:
        if args.output is not None:
            parser.error('--output requires --probe-boundary')
        run_pair(args.root, args.repo)


if __name__ == '__main__':
    main()
