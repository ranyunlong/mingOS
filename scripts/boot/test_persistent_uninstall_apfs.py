"""Real file/process controls for APFS pair supervision. Never Docker or QEMU."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('apfs_runner_test', HERE / 'persistent_uninstall_apfs.py')
runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)


def process_script(body):
    return 'import sys;sys.path.insert(0,' + repr(str(HERE)) + ');import persistent_uninstall_apfs as r\n' + body


class HostWriterTests(unittest.TestCase):
    def test_help_and_import_do_not_launch_external_processes(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('spawn')):
            spec.loader.exec_module(runner)
        result = subprocess.run([sys.executable, '-B', str(HERE / 'persistent_uninstall_apfs.py'), '--help'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'prepare', result.stdout)

    def test_canonical_rejects_aliases_and_symlinks(self):
        for path in ('relative', '/tmp/../tmp/path', '/tmp/./path', '/tmp//path'):
            with self.subTest(path=path), self.assertRaises(ValueError): runner.canonical(path)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); (root / 'real').mkdir(); (root / 'alias').symlink_to(root / 'real')
            with self.assertRaises(ValueError): runner.canonical(root / 'alias/file')

    def test_real_concurrent_process_is_rejected_until_first_writer_exits(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); lock = root / 'writer.lock'; ready = root / 'ready'
            script = process_script('from pathlib import Path\nimport time\nwith r.writer_lock(Path(sys.argv[1])):\n Path(sys.argv[2]).write_text("ready")\n time.sleep(30)\n')
            child = subprocess.Popen([sys.executable, '-B', '-c', script, str(lock), str(ready)])
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(ready.exists())
                contender = process_script('from pathlib import Path\ntry:\n with r.writer_lock(Path(sys.argv[1])):sys.exit(8)\nexcept BlockingIOError:sys.exit(0)\n')
                result = subprocess.run([sys.executable, '-B', '-c', contender, str(lock)], capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
            finally:
                child.terminate(); child.wait(timeout=5)
            with runner.writer_lock(lock): pass

    def test_lock_rejects_symlink_and_hardlink(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); source = root / 'source'; source.touch()
            (root / 'link').symlink_to(source)
            with self.assertRaises(ValueError):
                with runner.writer_lock(root / 'link'): pass
            os.link(source, root / 'hard')
            with self.assertRaises(ValueError):
                with runner.writer_lock(root / 'hard'): pass

    def test_overlap_rejects_parent_equal_child_and_checks_all_targets(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); target = root / 'backing'
            for source in (root, target, target / 'child', root / 'attempt'):
                entry = {'State': {'Running': True}, 'Mounts': [{'Type': 'bind', 'RW': True, 'Source': str(source)}]}
                with self.subTest(source=source), self.assertRaises(ValueError):
                    runner.reject_overlapping_writers([entry], [target, root / 'attempt'])
            for rw, running in ((False, True), (True, False)):
                entry = {'State': {'Running': running}, 'Mounts': [{'Type': 'bind', 'RW': rw, 'Source': str(root)}]}
                runner.reject_overlapping_writers([entry], [target])

    def test_attempt_latch_survives_failed_process_and_rejects_reuse(self):
        with tempfile.TemporaryDirectory() as name:
            attempt = Path(name).resolve(); latch = attempt / 'attempt.json'
            runner.write(latch, {'retryAllowed': False})
            state = {'State': {'Running': False, 'Pid': 0, 'Restarting': False}}
            with self.assertRaises(RuntimeError):
                runner.guarded_process([sys.executable, '-c', 'print("original failure");raise SystemExit(7)'],
                                       'test', attempt, 5, inspect=lambda _: state)
            with self.assertRaises(FileExistsError): runner.write(latch, {'retryAllowed': False})
            self.assertIn('original failure', (attempt / 'stdout.log').read_text())
            self.assertEqual(runner.read(attempt / 'command-result.json')['exitCode'], 7)
            self.assertTrue(latch.exists())

    def test_stopped_requires_running_false_pid_zero_and_not_restarting(self):
        for state in ({'Running': True, 'Pid': 0, 'Restarting': False},
                      {'Running': False, 'Pid': 4, 'Restarting': False},
                      {'Running': False, 'Pid': 0, 'Restarting': True}, {'Running': False}):
            self.assertFalse(runner.stopped({'State': state}))
        self.assertTrue(runner.stopped({'State': {'Running': False, 'Pid': 0, 'Restarting': False}}))

    def test_unknown_state_retries_with_native_lock_held_until_pid_zero(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); lock = root / 'writer.lock'; observations = []
            states = [RuntimeError('daemon unavailable'), {'State': {'Running': True, 'Pid': 99, 'Restarting': False}},
                      {'State': {'Running': False, 'Pid': 0, 'Restarting': False}}]
            def inspect(_):
                try:
                    with runner.writer_lock(lock): observations.append('unlocked')
                except BlockingIOError: observations.append('locked')
                value = states.pop(0)
                if isinstance(value, Exception): raise value
                return value
            with runner.writer_lock(lock), patch.object(time, 'sleep'):
                state = runner.ensure_stopped('test', root / 'lifecycle.jsonl', inspect, lambda _: None)
                self.assertEqual(state['State']['Pid'], 0)
            self.assertEqual(observations, ['locked'] * 3)
            records = [json.loads(line) for line in (root / 'lifecycle.jsonl').read_text().splitlines()]
            self.assertEqual([record['event'] for record in records], ['stop-state-unknown', 'container-stopped'])
            with runner.writer_lock(lock): pass

    def test_sigint_stops_process_before_unlock_and_preserves_raw_logs(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); attempt = root / 'attempt'; attempt.mkdir(); lock = root / 'writer.lock'
            child_ready = root / 'child-ready'; stopped_file = root / 'stopped'
            sleeper = 'from pathlib import Path;import time;print("raw-before-signal",flush=True);Path(' + repr(str(child_ready)) + ').touch();time.sleep(30)'
            body = ('from pathlib import Path\nimport json\na=Path(sys.argv[1]);lock=Path(sys.argv[2]);stopped=Path(sys.argv[3])\n'
                    'def inspect(_):\n return {"State":{"Running":not stopped.exists(),"Pid":0 if stopped.exists() else 99,"Restarting":False}}\n'
                    'def stop(_):\n'
                    ' try:\n  with r.writer_lock(lock):raise AssertionError("unlocked before stopped")\n'
                    ' except BlockingIOError:pass\n stopped.write_text("stopped")\n'
                    'try:\n with r.writer_lock(lock):\n  r.guarded_process(' + repr([sys.executable, '-c', sleeper]) + ',"fake",a,30,inspect,stop)\n'
                    'except InterruptedError:sys.exit(0)\n')
            child = subprocess.Popen([sys.executable, '-B', '-c', process_script(body), str(attempt), str(lock), str(stopped_file)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not child_ready.exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(child_ready.exists()); child.send_signal(signal.SIGINT)
                stdout, stderr = child.communicate(timeout=10)
                self.assertEqual(child.returncode, 0, stderr)
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=5)
            self.assertTrue(stopped_file.exists())
            self.assertIn('raw-before-signal', (attempt / 'stdout.log').read_text())
            self.assertTrue(runner.read(attempt / 'command-result.json')['stoppedConfirmedBeforeUnlock'])
            with runner.writer_lock(lock): pass

    def test_sigkill_releases_native_lock_but_new_generation_rejects_live_or_unknown_writer(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); lock = root / 'writer.lock'; active = root / 'active.json'
            body = ('from pathlib import Path\nimport time\nwith r.writer_lock(Path(sys.argv[1])):\n'
                    ' r.replace_record(Path(sys.argv[2]),{"identifier":"old-generation","containerLaunchIssued":True})\n'
                    ' time.sleep(30)\n')
            child = subprocess.Popen([sys.executable, '-B', '-c', process_script(body), str(lock), str(active)])
            try:
                deadline = time.monotonic() + 5
                while not active.exists() and time.monotonic() < deadline: time.sleep(.01)
                self.assertTrue(active.exists()); child.kill(); child.wait(timeout=5)
            finally:
                if child.poll() is None: child.kill(); child.wait(timeout=5)
            with runner.writer_lock(lock):
                for value in (None, {'State': {'Running': True, 'Pid': 99, 'Restarting': False}}):
                    with self.subTest(value=value), self.assertRaises(RuntimeError):
                        runner.check_previous_writer(active, inspect=lambda _: value)
                def unknown(_): raise RuntimeError('daemon unavailable')
                with self.assertRaisesRegex(RuntimeError, 'unavailable'): runner.check_previous_writer(active, unknown)
                runner.check_previous_writer(active, inspect=lambda _: {'State': {'Running': False, 'Pid': 0, 'Restarting': False}})

    def test_postlaunch_absence_and_log_failure_keep_lock_until_explicit_stopped_state(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); lock = root / 'writer.lock'; calls = []
            values = [None, RuntimeError('daemon unavailable'), {'State': {'Running': False, 'Pid': 0, 'Restarting': False}}]
            def inspect(_):
                with self.assertRaises(BlockingIOError):
                    with runner.writer_lock(lock): pass
                calls.append('locked'); value = values.pop(0)
                if isinstance(value, Exception): raise value
                return value
            with runner.writer_lock(lock), patch.object(time, 'sleep'), patch.object(runner, 'append_event', side_effect=OSError('disk full')), patch.object(sys, 'stderr', io.StringIO()):
                value = runner.ensure_stopped('test', root / 'events.jsonl', inspect, require_present=True)
            self.assertEqual(value['State']['Pid'], 0)
            self.assertEqual(calls, ['locked'] * 3)
            with runner.writer_lock(lock): pass

    def test_coordinator_requires_owned_private_directory(self):
        # Reproduce the actual filesystem invariants without modifying shared
        # /private/tmp coordination state used by other runner invocations.
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); directory = root / 'coordinator'; directory.mkdir(mode=0o700)
            with patch.object(runner, 'canonical', return_value=directory): self.assertEqual(runner.host_coordinator(), directory)
            directory.chmod(0o777)
            with patch.object(runner, 'canonical', return_value=directory), self.assertRaises(ValueError): runner.host_coordinator()

    def test_seal_covers_all_raw_and_disk_bytes_while_writer_is_held(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); attempt = root / 'attempt'; backing = root / 'backing'
            attempt.mkdir(); backing.mkdir(); lock = root / 'writer.lock'
            (attempt / 'origin.json').write_text('original provenance')
            (backing / 'raw.log').write_text('raw guest bytes')
            (backing / 'disk.raw').write_bytes(b'unchanged disk')
            original_sha = runner.sha
            def hash_while_locked(path):
                with self.assertRaises(BlockingIOError):
                    with runner.writer_lock(lock): pass
                return original_sha(path)
            with runner.writer_lock(lock), patch.object(runner, 'sha', side_effect=hash_while_locked):
                sealed = runner.seal_attempt(attempt, backing, 'passed')
            self.assertEqual(set(sealed['backingFiles']), {'raw.log', 'disk.raw'})
            self.assertEqual(set(sealed['attemptFiles']), {'origin.json'})
            (backing / 'raw.log').write_text('changed')
            self.assertNotEqual(runner.file_hashes(backing), sealed['backingFiles'])

    def test_seal_rejects_socket_aliases_and_multiple_hardlinks(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); (root / 'raw').write_text('data')
            (root / 'alias').symlink_to(root / 'raw')
            with self.assertRaises(ValueError): runner.file_hashes(root)
            (root / 'alias').unlink(); os.link(root / 'raw', root / 'hardlink')
            with self.assertRaises(ValueError): runner.file_hashes(root)

    def test_docker_command_has_one_container_two_rw_binds_and_tmpfs_runtime(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); prep = root / 'prep'; prep.mkdir()
            runner.write(prep / 'freeze.json', {'environment': {'toolsImage': 'sha256:fixed', 'rustImage': runner.OPENSSL_SOURCE_IMAGE}})
            executable = root / 'openssl'; executable.write_bytes(b'fixed runtime executable'); executable.chmod(0o555)
            origin = {'repo': str(root), 'prep': str(prep), 'identifier': 'unique-attempt', 'storageRoot': str(root / 'storage'), 'hostUid': os.geteuid()}
            with patch.object(runner, 'OPENSSL_SHA256', hashlib.sha256(executable.read_bytes()).hexdigest()):
                origin['executionEnvironment'] = runner.execution_contract(origin['identifier'], root / 'storage', runner.read(prep / 'freeze.json'), os.geteuid(), executable)
                command = runner.docker_command(origin, root / 'attempt')
            self.assertEqual(command[:2], ['docker', 'run'])
            self.assertIn('/run/mingos-runtime:rw,noexec,size=16m,uid=65534,gid=65534,mode=700', command)
            binds = [command[index + 1] for index, arg in enumerate(command[:-1]) if arg == '--mount']
            self.assertEqual(len(binds), 4)
            self.assertTrue(binds[0].endswith(',readonly'))
            self.assertIn('target=/var/tmp', binds[2])
            self.assertEqual(binds[3], 'type=bind,source=' + str(executable) + ',target=/usr/bin/openssl,readonly')
            self.assertNotIn('--rm', command)


class OpenSslRuntimeTests(unittest.TestCase):
    def test_prepare_rejects_runtime_source_outside_readonly_repo_before_creating_attempt(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); repo = root / 'repo'; repo.mkdir()
            prep = root / 'prep'; prep.mkdir(); attempt = root / 'attempt'; storage = root / 'storage'
            runner.write(prep / 'freeze.json', {'environment': {'rustImage': runner.OPENSSL_SOURCE_IMAGE}})
            executable = root / 'openssl'; executable.write_bytes(b'fixed runtime executable'); executable.chmod(0o555)
            with patch.object(runner, 'OPENSSL_SHA256', hashlib.sha256(executable.read_bytes()).hexdigest()), \
                 patch.object(runner, 'verify_original', side_effect=AssertionError('outside source reached original verification')):
                with self.assertRaisesRegex(ValueError, 'read-only repository'):
                    runner.prepare(prep, attempt, storage, executable, repo=repo)
            self.assertFalse(attempt.exists())

    def test_frozen_runtime_contract_rejects_changed_bytes_source_and_writable_alias(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); executable = root / 'openssl'
            executable.write_bytes(b'fixed runtime executable'); executable.chmod(0o555)
            expected = hashlib.sha256(executable.read_bytes()).hexdigest()
            with patch.object(runner, 'OPENSSL_SHA256', expected):
                contract = runner.verify_openssl_runtime(executable, runner.OPENSSL_SOURCE_IMAGE, repo=root)
                self.assertEqual(contract['sha256'], expected)
                self.assertEqual(contract['containerPath'], '/usr/bin/openssl')
                self.assertEqual(contract['linuxDependencies'], runner.OPENSSL_LIBRARIES)
                with self.assertRaisesRegex(ValueError, 'source image'):
                    runner.verify_openssl_runtime(executable, 'sha256:other', repo=root)
                with self.assertRaisesRegex(ValueError, 'writable'):
                    runner.verify_openssl_runtime(executable, runner.OPENSSL_SOURCE_IMAGE, (root,), repo=root)
                executable.chmod(0o755); executable.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'hash'):
                    runner.verify_openssl_runtime(executable, runner.OPENSSL_SOURCE_IMAGE, repo=root)

    def test_runtime_input_rejects_alias_hardlink_and_writable_permissions(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); executable = root / 'openssl'
            executable.write_bytes(b'fixed runtime executable'); executable.chmod(0o555)
            with patch.object(runner, 'OPENSSL_SHA256', hashlib.sha256(executable.read_bytes()).hexdigest()):
                alias = root / 'alias'; alias.symlink_to(executable)
                with self.assertRaises(ValueError): runner.verify_openssl_runtime(alias, runner.OPENSSL_SOURCE_IMAGE, repo=root)
                os.link(executable, root / 'hard')
                with self.assertRaisesRegex(ValueError, 'single-link'):
                    runner.verify_openssl_runtime(executable, runner.OPENSSL_SOURCE_IMAGE, repo=root)
                (root / 'hard').unlink(); executable.chmod(0o777)
                with self.assertRaisesRegex(ValueError, 'permissions'):
                    runner.verify_openssl_runtime(executable, runner.OPENSSL_SOURCE_IMAGE, repo=root)

    def test_prepare_requires_explicit_runtime_input_before_any_external_process(self):
        with patch.object(subprocess, 'run', side_effect=AssertionError('spawn')), patch.object(sys, 'stderr', io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                runner.main(['prepare', '--attempt-root', '/private/tmp/new-attempt', '--prep', '/private/tmp/prep', '--storage-root', '/private/tmp/storage'])
        self.assertEqual(error.exception.code, 2)


if __name__ == '__main__': unittest.main()
