"""Controls for the new Linux qualification generation, never a guest test."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import linux_host_qualification as q


class ColdProfileControls(unittest.TestCase):
    def test_uninstall_has_seven_exact_commands_without_update_recovery(self):
        plan = q.build_commands('persistent-uninstall-linux-host')
        self.assertEqual(len(plan), 7)
        self.assertEqual(plan[4]['command'][-2:], ['--example', 'prepare_persistent_uninstall_fixture'])
        names = ('ming_desktop::input_backpressure_tests::persistent_uninstall_fixture_writes_private_data_and_closes',
                 'desktop_native::input_tests::persistent_uninstall_native_real_fixture_ready_close_reap_and_uninstall')
        for entry, name in zip(plan[5:], names):
            self.assertEqual(entry['command'], ['cargo', '+1.99.1', 'test', '--offline', '--locked', '--release',
                '-p', 'ming-app-host', '--lib', name, '-j1', '--', '--exact', '--nocapture', '--ignored'])
            self.assertEqual(entry['expectedSummary'], (1, 0, 0, 0))
            self.assertEqual(entry['timeoutSeconds'], 900)
        self.assertEqual(plan[:4], q.build_commands()[:4])

    def test_profile_selection_is_closed_and_update_default_stays_exact(self):
        self.assertEqual(q.build_commands(), q.build_commands('persistent-update-linux-host'))
        self.assertEqual(len(q.build_commands()), 10)
        self.assertEqual(q.package_contract('persistent-uninstall-linux-host'), {
            'read-file': ('read-file', None), 'image-viewer': ('image-viewer', None),
            'text-entry-demo': ('text-entry-demo', None), 'uninstall': ('persistent-uninstall-demo', None)})
        for profile in ('persistent-uninstall', '', None, 'persistent-install-linux-host'):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                q.build_commands(profile)


class QualificationControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_inventory_rejects_unreadable_directory(self):
        hidden = self.root / 'hidden'
        hidden.mkdir()
        (hidden / 'source.rs').write_bytes(b'source')
        hidden.chmod(0)
        try:
            with self.assertRaises(ValueError):
                q.source_inventory(self.root)
        finally:
            hidden.chmod(0o700)

    def test_inventory_rejects_ancestor_swapped_for_symlink_during_open(self):
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'source.rs').write_bytes(b'original')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'source.rs').write_bytes(b'replacement')
        original_open = os.open
        replaced = False

        def swap(path, flags, *args, **kwargs):
            nonlocal replaced
            candidate = Path(path)
            if not replaced and (candidate.name == 'nested' or candidate.parent == nested):
                replaced = True
                nested.rename(self.root / 'old-nested')
                nested.symlink_to(outside, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(q.os, 'open', side_effect=swap):
            with self.assertRaises(ValueError):
                q.source_inventory(self.root)
        self.assertTrue(replaced)

    def test_failed_or_incomplete_stage_blocks_later_stage(self):
        (self.root / 'stages').mkdir()
        (self.root / 'stages/build').mkdir()
        with self.assertRaises(ValueError):
            q.run_stage(self.root, 'bundle', 'a' * 64, lambda _: {})
        self.assertFalse((self.root / 'stages/bundle').exists())
        q.write_json(self.root / 'stages/build/result.json', {'status': 'failed'})
        with self.assertRaises(ValueError):
            q.run_stage(self.root, 'bundle', 'a' * 64, lambda _: {})

    def test_new_stage_directories_sync_parents_before_action(self):
        seen = []
        original_fsync = os.fsync

        def sync(fd):
            seen.append(os.fstat(fd).st_ino)
            return original_fsync(fd)

        def action(_):
            self.assertIn(self.root.stat().st_ino, seen)
            self.assertIn((self.root / 'stages').stat().st_ino, seen)
            return {}

        with mock.patch.object(q.os, 'fsync', side_effect=sync):
            q.run_stage(self.root, 'prepare', 'a' * 64, action)

    def test_execute_kills_and_reaps_escaped_descendant(self):
        import sys
        import signal
        command = [sys.executable, '-c', 'import subprocess,sys; '
                   'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
                   'start_new_session=True); print(p.pid,flush=True)']
        result = q.execute(self.root / 'escaped', command, 5)
        pid = int((self.root / 'escaped/stdout.log').read_text())
        try:
            self.assertNotEqual(result['exitCode'], 0)
            self.assertEqual(result['leaderExitCode'], 0)
            self.assertIn(pid, [item['pid'] for item in result['orphanedChildren']])
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass

    def test_execute_refuses_preexisting_children(self):
        import subprocess
        import sys
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            with self.assertRaises(ValueError):
                q.execute(self.root / 'existing-child', [sys.executable, '-c', 'pass'], 5)
        finally:
            child.kill()
            child.wait(timeout=5)

    def test_timeout_reaps_escaped_descendant_and_records_leader(self):
        import sys
        command = [sys.executable, '-c', 'import subprocess,sys,time; '
                   'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],'
                   'start_new_session=True); print(p.pid,flush=True); time.sleep(60)']
        result = q.execute(self.root / 'escaped-timeout', command, 0.2)
        pid = int((self.root / 'escaped-timeout/stdout.log').read_text())
        self.assertTrue(result['timedOut'])
        self.assertEqual(result['leaderExitCode'], -9)
        self.assertTrue(result['cleanupComplete'])
        self.assertIn(pid, [item['pid'] for item in result['orphanedChildren']])
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_spawn_error_preserves_result(self):
        result = q.execute(self.root / 'spawn-error', ['/no-such-mingos-command'], 5)
        self.assertEqual(result['exitCode'], 127)
        self.assertIsNone(result['leaderExitCode'])
        self.assertTrue(result['cleanupComplete'])
        self.assertIn('FileNotFoundError', result['error'])
        self.assertEqual(q.read_json(self.root / 'spawn-error/result.json'), result)

    def test_leader_cleanup_wait_exception_still_reaps_and_seals_failure(self):
        import sys
        original_wait = q.subprocess.Popen.wait
        count = 0

        def interrupted_wait(process, timeout=None):
            nonlocal count
            count += 1
            if count == 1:
                raise q.subprocess.TimeoutExpired(process.args, timeout)
            if count == 2:
                raise OSError('injected wait failure')
            return original_wait(process, timeout=timeout)

        with mock.patch.object(q.subprocess.Popen, 'wait', interrupted_wait):
            result = q.execute(self.root / 'wait-error',
                               [sys.executable, '-c', 'import time; time.sleep(60)'], 0.2)
        self.assertNotEqual(result['exitCode'], 0)
        self.assertIn('injected wait failure', result['error'])
        self.assertEqual(q.read_json(self.root / 'wait-error/result.json'), result)

    def test_exact_summary_rejects_zero_matches_duplicate_and_ignored(self):
        line = 'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 42 filtered out; finished in 0.12s\n'
        self.assertEqual(q.exact_summary(line, (1, 0, 0, 0)), (1, 0, 0, 0))
        for raw in (line + line, '', line.replace('1 passed', '0 passed'),
                    line.replace('0 ignored', '1 ignored'), line.replace('ok.', 'FAILED.')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                q.exact_summary(raw, (1, 0, 0, 0))

    def test_recovery_summary_keeps_ignored_child(self):
        q.exact_summary('test result: ok. 18 passed; 0 failed; 1 ignored; 0 measured; 99 filtered out; finished in 2.0s\n', (18, 0, 1, 0))

    def test_inventory_includes_untracked_assets_and_new_source(self):
        (self.root / 'crates').mkdir()
        (self.root / 'crates/new.rs').write_bytes(b'new')
        (self.root / 'assets').mkdir()
        (self.root / 'assets/font.bin').write_bytes(b'font')
        (self.root / '.cargo').mkdir()
        (self.root / '.cargo/config.toml').write_bytes(b'config')
        (self.root / '.cache').mkdir()
        (self.root / '.cache/ignored').write_bytes(b'cache')
        self.assertEqual(set(q.source_inventory(self.root)), {'crates/new.rs', 'assets/font.bin', '.cargo/config.toml'})
        before = q.source_inventory(self.root)
        (self.root / 'crates/new.rs').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source'):
            q.verify_source(self.root, before)
        (self.root / 'added.kt').write_bytes(b'addition')
        with self.assertRaisesRegex(ValueError, 'source'):
            q.verify_source(self.root, before)

    def test_inventory_rejects_symlink_and_hardlink(self):
        original = self.root / 'original'
        original.write_bytes(b'bytes')
        (self.root / 'alias').symlink_to(original)
        with self.assertRaises(ValueError):
            q.source_inventory(self.root)
        (self.root / 'alias').unlink()
        os.link(original, self.root / 'hardlink')
        with self.assertRaises(ValueError):
            q.source_inventory(self.root)

    def test_stage_failure_is_durable_and_cannot_be_retried(self):
        def fail(directory):
            (directory / 'partial.log').write_bytes(b'failure evidence')
            raise RuntimeError('first failure')
        with self.assertRaisesRegex(RuntimeError, 'first failure'):
            q.run_stage(self.root, 'build', 'a' * 64, fail)
        self.assertEqual(q.read_json(self.root / 'stages/build/result.json')['status'], 'failed')
        self.assertEqual((self.root / 'stages/build/partial.log').read_bytes(), b'failure evidence')
        with self.assertRaises(FileExistsError):
            q.run_stage(self.root, 'build', 'a' * 64, lambda _: {})

    def test_success_chain_detects_modified_products(self):
        def action(directory):
            (directory / 'output').write_bytes(b'product')
            return {'note': 'control only'}
        q.run_stage(self.root, 'prepare', 'a' * 64, action)
        q.verify_stage(self.root, 'prepare', 'a' * 64)
        (self.root / 'stages/prepare/output').write_bytes(b'tampered')
        with self.assertRaises(ValueError):
            q.verify_stage(self.root, 'prepare', 'a' * 64)

    def test_environment_rejects_network_owner_write_and_resource_changes(self):
        observed = {'uid': 65534, 'gid': 65534, 'interfaces': ['lo'],
                    'capEff': 0, 'capPrm': 0, 'noNewPrivs': 1, 'seccomp': 2,
                    'rootReadOnly': True, 'sourceReadOnly': True,
                    'cpuMax': [200000, 100000], 'memoryMax': 4294967296, 'pidsMax': 256}
        q.validate_environment(observed)
        for key, value in {'uid': 0, 'gid': 0, 'interfaces': ['eth0', 'lo'], 'capEff': 1,
                           'capPrm': 1, 'noNewPrivs': 0, 'seccomp': 0,
                           'rootReadOnly': False, 'sourceReadOnly': False,
                           'cpuMax': [300000, 100000], 'memoryMax': 8589934592, 'pidsMax': 512}.items():
            changed = copy.deepcopy(observed)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                q.validate_environment(changed)

    def test_command_timeout_and_nonzero_preserve_logs(self):
        import sys
        result = q.execute(self.root / 'nonzero', [sys.executable, '-c', 'import sys; print("kept", flush=True); sys.exit(7)'], 5)
        self.assertEqual(result['exitCode'], 7)
        self.assertEqual((self.root / 'nonzero/stdout.log').read_bytes(), b'kept\n')
        result = q.execute(self.root / 'timeout', [sys.executable, '-c', 'import time; print("started", flush=True); time.sleep(60)'], 0.1)
        self.assertTrue(result['timedOut'])
        self.assertNotEqual(result['exitCode'], 0)
        self.assertEqual((self.root / 'timeout/stdout.log').read_bytes(), b'started\n')

    def test_command_plan_preserves_task9_limits_and_exact_controllers(self):
        plan = q.build_commands()
        self.assertEqual(len(plan), 10)
        self.assertEqual([entry['timeoutSeconds'] for entry in plan], [1800] * 5 + [900] * 5)
        for entry in plan:
            self.assertTrue({'--offline', '--locked', '--release', '-j1'} <= set(entry['command']))
        self.assertEqual(plan[-1]['expectedSummary'], (18, 0, 1, 0))
        self.assertTrue(all(entry['expectedSummary'] == (1, 0, 0, 0) for entry in plan[5:9]))
        self.assertTrue(all('--ignored' in entry['command'] and '--exact' in entry['command'] for entry in plan[5:9]))


if __name__ == '__main__':
    unittest.main()
