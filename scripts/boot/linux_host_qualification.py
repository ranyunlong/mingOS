#!/usr/bin/env python3
"""Independent Linux-host qualification controls; importing never starts a guest.

The historical APFS driver is unchanged. Only source-check is exposed until the
storage, fixture and freeze stages are integrated; later stages fail closed.
"""
import argparse
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

STAGES = ('source-check', 'storage-admission', 'prepare', 'build', 'bundle',
          'admission', 'pair', 'export', 'verify')
EXCLUDED = {'.git', '.cache', '.gradle', '.kotlin', '.codegraph', '.superpowers',
            'artifacts', 'node_modules', 'target', 'build', 'builds', '__pycache__', 'dist'}
DEFAULT_COLD_PROFILE = 'persistent-update-linux-host'
COLD_PROFILES = (DEFAULT_COLD_PROFILE, 'persistent-uninstall-linux-host')


def cold_profile(profile=DEFAULT_COLD_PROFILE):
    if not isinstance(profile, str) or profile not in COLD_PROFILES:
        raise ValueError('explicit supported Linux cold profile required')
    return profile


def package_contract(profile=DEFAULT_COLD_PROFILE):
    profile = cold_profile(profile)
    packages = {'read-file': ('read-file', None), 'image-viewer': ('image-viewer', None),
                'text-entry-demo': ('text-entry-demo', None)}
    if profile == 'persistent-uninstall-linux-host':
        packages['uninstall'] = ('persistent-uninstall-demo', None)
    else:
        packages.update({'old': ('persistent-update-demo', 'old'), 'new': ('persistent-update-demo', 'new'),
                         'absent-write': ('persistent-update-demo', 'absent-write')})
    return packages


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read_json(path):
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                raise ValueError('duplicate JSON field: ' + name)
            result[name] = value
        return result
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def checked_directory(root):
    root = Path(root)
    fd = open_directory(root)
    os.close(fd)
    return root


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_directory(root):
    root = Path(root)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('absolute directory without parent traversal required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in root.parts[1:]:
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                            | os.O_CLOEXEC, dir_fd=fd)
            try:
                if stamp(before) != stamp(os.fstat(child)):
                    raise ValueError('ancestor directory replaced during open')
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return fd
    except (OSError, ValueError) as error:
        os.close(fd)
        raise ValueError('unsafe or unreadable directory: ' + str(root)) from error


def hash_at(parent, name, before):
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('single-link regular file required: ' + str(name))
    digest = hashlib.sha256()
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
    with os.fdopen(fd, 'rb') as stream:
        if stamp(os.fstat(stream.fileno())) != stamp(before):
            raise ValueError('file replaced during open')
        for raw in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(raw)
        if stamp(os.fstat(stream.fileno())) != stamp(before):
            raise ValueError('file changed during hashing')
    if stamp(os.stat(name, dir_fd=parent, follow_symlinks=False)) != stamp(before):
        raise ValueError('file replaced during hashing')
    return {'sha256': digest.hexdigest(), 'bytes': before.st_size}


def stable_hash(path):
    path = Path(path)
    parent = open_directory(path.absolute().parent)
    try:
        return hash_at(parent, path.name, os.stat(path.name, dir_fd=parent, follow_symlinks=False))
    except OSError as error:
        raise ValueError('unsafe or unreadable file: ' + str(path)) from error
    finally:
        os.close(parent)


def tree_inventory(root, excluded=()):
    root = Path(root)
    fd = open_directory(root)
    original = stamp(os.fstat(fd))
    result = {}
    def visit(directory, relative):
        before_directory = stamp(os.fstat(directory))
        names = sorted(os.listdir(directory))
        for name in names:
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode) and name in excluded:
                continue
            key = relative + name
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                                | os.O_CLOEXEC, dir_fd=directory)
                try:
                    if stamp(os.fstat(child)) != stamp(before):
                        raise ValueError('inventory directory replaced during open')
                    visit(child, key + '/')
                    if stamp(os.stat(name, dir_fd=directory, follow_symlinks=False)) != stamp(before):
                        raise ValueError('inventory directory changed or replaced')
                finally:
                    os.close(child)
            else:
                result[key] = hash_at(directory, name, before)
        if names != sorted(os.listdir(directory)) or before_directory != stamp(os.fstat(directory)):
            raise ValueError('inventory directory changed during traversal')
    try:
        visit(fd, '')
        current = open_directory(root)
        try:
            if stamp(os.fstat(current)) != original:
                raise ValueError('inventory root replaced during traversal')
        finally:
            os.close(current)
    except OSError as error:
        raise ValueError('inventory path unreadable or replaced') from error
    finally:
        os.close(fd)
    return dict(sorted(result.items()))


def source_inventory(repo):
    return tree_inventory(repo, EXCLUDED)


def verify_source(repo, expected):
    if source_inventory(repo) != expected:
        raise ValueError('source file set or bytes changed')


def exact_summary(raw, expected):
    lines = [line for line in raw.splitlines() if line.startswith('test result:')]
    match = re.fullmatch(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; '
                         r'\d+ filtered out; finished in [0-9.]+s', lines[0]) if len(lines) == 1 else None
    if match is None or tuple(map(int, match.groups())) != expected:
        raise ValueError('unique exact test summary required: ' + str(expected))
    return tuple(map(int, match.groups()))


def validate_environment(observed):
    expected = {'uid': 65534, 'gid': 65534, 'interfaces': ['lo'],
                'capEff': 0, 'capPrm': 0, 'noNewPrivs': 1, 'seccomp': 2,
                'rootReadOnly': True, 'sourceReadOnly': True,
                'cpuMax': [200000, 100000], 'memoryMax': 4294967296, 'pidsMax': 256}
    if observed != expected:
        raise ValueError('Linux isolation/resources differ: ' + ', '.join(
            key for key, value in expected.items() if observed.get(key) != value))


def observe_environment(repo):
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    cgroup = Path('/sys/fs/cgroup')
    return {'uid': os.geteuid(), 'gid': os.getegid(),
            'interfaces': sorted(path.name for path in Path('/sys/class/net').iterdir()),
            'capEff': int(status['CapEff'], 16), 'capPrm': int(status['CapPrm'], 16),
            'noNewPrivs': int(status['NoNewPrivs']), 'seccomp': int(status['Seccomp']),
            'rootReadOnly': bool(os.statvfs('/').f_flag & os.ST_RDONLY),
            'sourceReadOnly': bool(os.statvfs(repo).f_flag & os.ST_RDONLY),
            'cpuMax': list(map(int, (cgroup / 'cpu.max').read_text().split())),
            'memoryMax': int((cgroup / 'memory.max').read_text()),
            'pidsMax': int((cgroup / 'pids.max').read_text())}


def direct_children():
    # A caller must serialize execute: unrelated children are never adopted as
    # this command's processes or killed by its cleanup.
    children = set()
    for task in Path('/proc/self/task').iterdir():
        try:
            children.update(map(int, (task / 'children').read_text().split()))
        except FileNotFoundError:
            continue
    return children


def become_subreaper():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'PR_SET_CHILD_SUBREAPER failed')


def reap_descendants(deadline=None):
    deadline = time.monotonic() + 5 if deadline is None else deadline
    observed = set()
    reaped = {}
    while True:
        children = direct_children()
        observed.update(children)
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            observed.add(pid)
            reaped[pid] = os.waitstatus_to_exitcode(status)
        if not direct_children():
            return [{'pid': pid, 'exitCode': reaped.get(pid)} for pid in sorted(observed)], True
        if time.monotonic() >= deadline:
            return [{'pid': pid, 'exitCode': reaped.get(pid)} for pid in sorted(observed)], False
        time.sleep(0.01)


def sync_directory(path):
    fd = open_directory(path)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def execute(directory, command, timeout, *, cwd=None, env=None):
    become_subreaper()
    if direct_children():
        raise ValueError('execute refuses preexisting children; serialize commands')
    if not isinstance(timeout, (int, float)) or not 0 < timeout <= 1800:
        raise ValueError('command timeout must be in (0,1800] seconds')
    directory.mkdir(mode=0o700)
    sync_directory(directory.parent)
    started = now()
    write_json(directory / 'command.json', {'command': command, 'cwd': str(cwd) if cwd else None,
                                           'timeoutSeconds': timeout, 'startedAt': started})
    timed_out = False
    process = None
    error = None
    orphans, cleanup_complete = [], False
    with (directory / 'stdout.log').open('xb') as out, (directory / 'stderr.log').open('xb') as err:
        try:
            process = subprocess.Popen(command, cwd=cwd, env=env, stdout=out, stderr=err, start_new_session=True)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception as failure:
            error = repr(failure)
        finally:
            cleanup_deadline = time.monotonic() + 5
            cleanup_errors = []
            if process is not None:
                # Kill the original group and then separately reap adopted
                # descendants, including those which escaped with setsid().
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as failure:
                    cleanup_errors.append(repr(failure))
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=max(0.001, cleanup_deadline - time.monotonic()))
                except (OSError, subprocess.TimeoutExpired) as failure:
                    cleanup_errors.append(repr(failure))
            try:
                orphans, cleanup_complete = reap_descendants(cleanup_deadline)
            except OSError as failure:
                cleanup_errors.append(repr(failure))
                cleanup_complete = False
            if process is not None:
                leader_observation = next((entry for entry in orphans if entry['pid'] == process.pid), None)
                if leader_observation is not None:
                    process.returncode = leader_observation['exitCode']
                    orphans.remove(leader_observation)
            if cleanup_errors:
                error = '; '.join(([error] if error else []) + cleanup_errors)
        out.flush()
        err.flush()
        os.fsync(out.fileno())
        os.fsync(err.fileno())
    leader_exit = process.returncode if process is not None else None
    exit_code = leader_exit if leader_exit is not None else 127
    if exit_code == 0 and (orphans or not cleanup_complete or error or timed_out):
        exit_code = 125
    result = {'command': command, 'startedAt': started, 'finishedAt': now(),
              'exitCode': exit_code, 'leaderExitCode': leader_exit, 'timedOut': timed_out,
              'orphanedChildren': orphans, 'cleanupComplete': cleanup_complete, 'error': error,
              'stdout': stable_hash(directory / 'stdout.log'), 'stderr': stable_hash(directory / 'stderr.log')}
    write_json(directory / 'result.json', result)
    return result


def run_stage(root, stage, freeze_sha256, action):
    if stage not in STAGES or not re.fullmatch('[0-9a-f]{64}', freeze_sha256):
        raise ValueError('stage/freeze identity required')
    root = checked_directory(root)
    stages = root / 'stages'
    stages.mkdir(mode=0o700, exist_ok=True)
    sync_directory(root)
    checked_directory(stages)
    directory = stages / stage
    if directory.exists() or directory.is_symlink():
        raise FileExistsError('stage already attempted: ' + stage)
    for previous in stages.iterdir():
        if previous.name not in STAGES:
            raise ValueError('unknown previous stage entry')
        try:
            verify_stage(root, previous.name, freeze_sha256)
        except (OSError, ValueError, KeyError) as error:
            raise ValueError('previous stage failed, incomplete or modified: ' + previous.name) from error
    directory.mkdir(mode=0o700)
    sync_directory(stages)
    write_json(directory / 'attempt.json', {'stage': stage, 'freezeSha256': freeze_sha256, 'startedAt': now()})
    try:
        detail = action(directory)
        products = tree_inventory(directory)
    except Exception as error:
        write_json(directory / 'result.json', {'stage': stage, 'status': 'failed',
                   'freezeSha256': freeze_sha256, 'finishedAt': now(), 'error': str(error),
                   'finalSystemAccepted': False})
        raise
    write_json(directory / 'result.json', {'stage': stage, 'status': 'passed',
               'freezeSha256': freeze_sha256, 'finishedAt': now(), 'products': products,
               'detail': detail, 'formalQualification': False, 'finalSystemAccepted': False})


def verify_stage(root, stage, freeze_sha256):
    directory = checked_directory(Path(root) / 'stages' / stage)
    record = read_json(directory / 'result.json')
    actual = tree_inventory(directory)
    del actual['result.json']
    if (record.get('stage') != stage or record.get('status') != 'passed'
            or record.get('freezeSha256') != freeze_sha256 or actual != record.get('products')):
        raise ValueError('stage failure, freeze or products changed: ' + stage)
    return record


def build_commands(profile=DEFAULT_COLD_PROFILE):
    profile = cold_profile(profile)
    prefix = ['cargo', '+1.99.1']
    common = ['--offline', '--locked', '--release', '-p', 'ming-app-host']
    initial = [('test', ['--all-targets']), ('check', ['--all-features', '--all-targets']),
               ('check', ['--no-default-features', '--all-targets']), ('build', ['--bin', 'ming-app-host']),
               ('build', ['--example', 'prepare_persistent_uninstall_fixture' if profile == 'persistent-uninstall-linux-host'
                          else 'prepare_persistent_update_fixture'])]
    result = [{'command': prefix + [verb] + common + ['-j1'] + args, 'timeoutSeconds': 1800,
               'expectedSummary': None} for verb, args in initial]
    if profile == 'persistent-uninstall-linux-host':
        for name in ('ming_desktop::input_backpressure_tests::persistent_uninstall_fixture_writes_private_data_and_closes',
                     'desktop_native::input_tests::persistent_uninstall_native_real_fixture_ready_close_reap_and_uninstall'):
            result.append({'command': prefix + ['test'] + common + ['--lib', name, '-j1',
                           '--', '--exact', '--nocapture', '--ignored'], 'timeoutSeconds': 900,
                           'expectedSummary': (1, 0, 0, 0)})
        return result
    namespace = 'ming_desktop::persistent_update_runtime::'
    for name in ('migrated_and_absent_write_commit_only_after_real_ready_close_reap',
                 'absent_nonempty_runtime_failure_rolls_back_original_missing_namespace',
                 'killed_host_recovers_in_independent_process'):
        result.append({'command': prefix + ['test'] + common + ['--lib', namespace + name, '-j1',
                       '--', '--exact', '--nocapture', '--ignored'], 'timeoutSeconds': 900,
                       'expectedSummary': (1, 0, 0, 0)})
    result.append({'command': prefix + ['test'] + common + ['--test', 'persistent_update_runtime',
                   'separate_process_roles_and_real_stable_host_refuse_active_update_without_writes',
                   '-j1', '--', '--exact', '--nocapture', '--ignored', '--test-threads=1'],
                   'timeoutSeconds': 900, 'expectedSummary': (1, 0, 0, 0)})
    result.append({'command': prefix + ['test'] + common + ['--lib', 'persistent_update::recovery_tests::',
                   '-j1', '--', '--nocapture', '--test-threads=1'],
                   'timeoutSeconds': 900, 'expectedSummary': (18, 0, 1, 0)})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=COLD_PROFILES, default=DEFAULT_COLD_PROFILE)
    parser.add_argument('--stage', choices=STAGES, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('Linux execution required; Windows only orchestrates containers')
    if args.stage != 'source-check':
        parser.error('stage not integrated; no build or guest started')
    repo, root = checked_directory(args.repo), checked_directory(args.root)
    environment = observe_environment(repo)
    validate_environment(environment)
    inventory = source_inventory(repo)
    write_json(root / 'source-check.json', {'schemaVersion': 1, 'environment': environment,
               'files': inventory, 'formalQualification': False, 'finalSystemAccepted': False})


if __name__ == '__main__':
    main()
