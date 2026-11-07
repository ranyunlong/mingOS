"""No-guest controls: synthetic processes never create a disk or invoke QEMU."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

try:
    import linux_host_pair as pair
except ModuleNotFoundError:
    pair = None

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL_SUPERVISOR = pair.supervise_boot if pair else None


def historical_driver():
    path = ROOT / 'artifacts/persistent-update-20261104-prep/run-stage.py'
    spec = importlib.util.spec_from_file_location('pair_control_historical', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(number=1):
    return {'status': 'normal-desktop-poweroff', 'bootAttempted': True,
        'kernelPowerdown': True, 'guestExitCode': 0, 'inputComplete': True,
        'bootNumber': number, 'bundleIdentity': 'a' * 64, 'diskUuid': 'disk',
        'inputScriptSha256': ('b' if number == 1 else 'c') * 64,
        'bootId': 'boot%d' % number, 'marker': {'counter': number, 'bootId': 'boot%d' % number},
        'previousMarker': None if number == 1 else {'counter': 1, 'bootId': 'boot1'}}


class PairControls(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(pair, 'pair controller is not implemented')
        self.driver = historical_driver()

    def test_abnormal_poweroff_identity_and_marker_chain_reject(self):
        first = report()
        pair.validate_boot_report(first, 1, 'a' * 64, 'disk', 'b' * 64, [])
        pair.validate_boot_report(report(2), 2, 'a' * 64, 'disk', 'c' * 64, [first])
        for field, value in [('inputComplete', False), ('bootAttempted', 1),
                ('kernelPowerdown', False), ('guestExitCode', False), ('bootNumber', True),
                ('bundleIdentity', 'x'), ('diskUuid', 'other'), ('inputScriptSha256', 'wrong'),
                ('marker', {'counter': True, 'bootId': 'boot1'}), ('previousMarker', {})]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                pair.validate_boot_report(dict(first, **{field: value}), 1, 'a' * 64, 'disk', 'b' * 64, [])
        with self.assertRaises(ValueError):
            pair.validate_boot_report(dict(report(2), previousMarker={}), 2, 'a' * 64, 'disk', 'c' * 64, [first])

    def test_strong_gate_failure_blocks_second_boot_and_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seen = []
            def run(number):
                seen.append(number)
                return report(number)
            def gate(number, value):
                raise ValueError('synthetic strong raw rejection')
            result = self.driver.run_two_boots(root, run, gate)
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(seen, [1])
            self.assertEqual(json.loads((root / 'first-failure.json').read_text())['bootNumber'], 1)
            with self.assertRaises(FileExistsError):
                self.driver.run_two_boots(root, run, gate)
            self.assertEqual(seen, [1])

    def test_live_raw_failure_stops_child_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / 'guest.jsonl'
            code = 'import pathlib,time; pathlib.Path(%r).write_text(%r); time.sleep(30)' % (
                str(raw), '{"type":"guest-error"}\n')
            started = time.monotonic()
            result = pair.supervise_boot(root / 'transport', [sys.executable, '-c', code],
                self.driver.RawGuard(raw, 1), timeout=3, pair_deadline=time.monotonic() + 4)
            self.assertNotEqual(result['exitCode'], 0)
            self.assertIn('raw guest failure', result['error'])
            self.assertTrue(result['cleanupComplete'])
            self.assertLess(time.monotonic() - started, 8)
            self.assertEqual(pair.q.direct_children(), set())

    def test_timeout_is_red_even_when_child_cleanup_succeeds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = pair.supervise_boot(root / 'transport', [sys.executable, '-c', 'import time;time.sleep(30)'],
                self.driver.RawGuard(root / 'guest.jsonl', 1), timeout=.15,
                pair_deadline=time.monotonic() + 4)
            self.assertTrue(result['timedOut'])
            self.assertTrue(result['cleanupComplete'])
            self.assertNotEqual(result['exitCode'], 0)

    def test_successful_leader_with_escaped_descendant_is_red_and_reaped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / 'guest.jsonl'
            code = ('import os,time,pathlib; pathlib.Path(%r).write_text(%r); '
                    'pid=os.fork(); '
                    'exec("if pid == 0:\\n os.setsid()\\n time.sleep(30)\\nelse:\\n time.sleep(.1)")') % (
                        str(raw), '{"type":"guest-result","status":"normal","desktopExitCode":0,"dataSynced":true,"poweroffRequested":true}\n')
            result = pair.supervise_boot(root / 'transport', [sys.executable, '-c', code],
                self.driver.RawGuard(raw, 1), timeout=3, pair_deadline=time.monotonic() + 4)
            self.assertEqual(result['leaderExitCode'], 0)
            self.assertNotEqual(result['exitCode'], 0)
            self.assertTrue(result['orphanedChildren'])
            self.assertTrue(result['cleanupComplete'])
            self.assertEqual(pair.q.direct_children(), set())

    def test_start_failure_is_sealed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = pair.supervise_boot(root / 'transport', ['/missing-mingos-pair-command'],
                self.driver.RawGuard(root / 'guest.jsonl', 1), timeout=1,
                pair_deadline=time.monotonic() + 4)
            self.assertNotEqual(result['exitCode'], 0)
            self.assertTrue(result['error'])
            self.assertEqual(json.loads((root / 'transport/result.json').read_text()), result)

    def test_success_requires_complete_raw_stream(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / 'guest.jsonl'
            code = 'import pathlib;pathlib.Path(%r).write_text(%r)' % (str(raw), '{"type":"guest-result"')
            result = pair.supervise_boot(root / 'transport', [sys.executable, '-c', code],
                self.driver.RawGuard(raw, 1), timeout=2, pair_deadline=time.monotonic() + 4)
            self.assertEqual(result['leaderExitCode'], 0)
            self.assertNotEqual(result['exitCode'], 0)
            self.assertIn('truncated raw guest stream', result['error'])

    def test_child_does_not_inherit_ambient_runtime_injection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / 'guest.jsonl'
            code = ('import os,pathlib;print(os.getenv("MINGOS_PAIR_AMBIENT", "clean"));'
                    'pathlib.Path(%r).write_text(%r)') % (
                        str(raw), '{"type":"guest-result","status":"normal","desktopExitCode":0,"dataSynced":true,"poweroffRequested":true}\n')
            with mock.patch.dict(os.environ, {'MINGOS_PAIR_AMBIENT': 'injected'}):
                result = pair.supervise_boot(root / 'transport', [sys.executable, '-c', code],
                    self.driver.RawGuard(raw, 1), timeout=2, pair_deadline=time.monotonic() + 4)
            self.assertEqual(result['exitCode'], 0)
            self.assertEqual((root / 'transport/stdout.log').read_text(), 'clean\n')

    def test_oversize_transport_log_stops_child(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = pair.supervise_boot(root / 'transport',
                [sys.executable, '-c', 'import os,time;os.write(2,b"x"*(8388608+1));time.sleep(30)'],
                self.driver.RawGuard(root / 'guest.jsonl', 1), timeout=3,
                pair_deadline=time.monotonic() + 4)
            self.assertNotEqual(result['exitCode'], 0)
            self.assertIn('log budget', result['error'])
            self.assertTrue(result['cleanupComplete'])

    def test_bound_driver_bytes_checked_before_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'run-stage.py'
            source.write_text('raise AssertionError("must not import changed bytes")\n')
            with self.assertRaises(ValueError):
                pair.load_bound(root, 'run-stage.py', {'sha256': '0' * 64, 'bytes': 1})
            source.write_text('value = 42\n')
            module = pair.load_bound(root, 'run-stage.py', pair.q.stable_hash(source))
            self.assertEqual(module.value, 42)

    def test_missing_or_changed_admission_prevents_pair_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pair.q.write_json(root / 'runtime-freeze.json', {'synthetic': True})
            digest = pair.q.stable_hash(root / 'runtime-freeze.json')['sha256']
            with mock.patch.object(pair.runtime, 'load_runtime', return_value={}), \
                 mock.patch.object(pair.runtime, 'verify_bundle_products', create=True), \
                 mock.patch.object(pair.runtime, 'verify_admission_products', create=True), \
                 mock.patch.object(pair.runtime, 'verify_review', return_value={}):
                for name in ('prepare', 'build', 'bundle'):
                    pair.q.run_stage(root, name, digest, lambda directory: {'synthetic': True})
                with self.assertRaises((OSError, ValueError)):
                    pair.verify_boundary(root, root, digest)
                pair.q.run_stage(root, 'admission', digest, lambda directory: {'guestStarted': False})
                pair.verify_boundary(root, root, digest)
                (root / 'stages/admission/extra.json').write_text('{}')
                with self.assertRaises(ValueError):
                    pair.verify_boundary(root, root, digest)

    def test_changed_freeze_between_boots_blocks_next_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pair.q.write_json(root / 'runtime-freeze.json', {'synthetic': True})
            digest = pair.q.stable_hash(root / 'runtime-freeze.json')['sha256']
            (root / 'runtime-freeze.json').write_text('{}')
            with self.assertRaises(ValueError):
                pair.verify_boundary(root, root, digest)

    def test_vm_arguments_retain_budgets_and_native_input_diagnostics(self):
        argv = pair.vm_command(Path('/repo'), Path('/prep'), WORK := Path('/work/mingos-boot-infra-20261025'),
                               Path('/work/mingos-boot-infra-20261025/cold-pair'), 2,
                               'disks/linux-runtime-128.json')
        self.assertEqual(argv[argv.index('--timeout') + 1], '900')
        self.assertEqual(argv[argv.index('--boot-number') + 1], '2')
        self.assertEqual(argv[argv.index('--runtime-root') + 1], '/run/mingos-runtime')
        self.assertEqual(argv[argv.index('--work-root') + 1], str(WORK))
        self.assertIn('--diagnostic-split-input', argv)
        self.assertIn('--diagnostic-pointer-handshake', argv)

    def test_successful_child_returns_sealed_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / 'guest.jsonl'
            code = 'import pathlib;pathlib.Path(%r).write_text(%r)' % (
                str(raw), '{"type":"guest-result","status":"normal","desktopExitCode":0,"dataSynced":true,"poweroffRequested":true}\n')
            result = pair.supervise_boot(root / 'transport', [sys.executable, '-c', code],
                self.driver.RawGuard(raw, 1), timeout=2, pair_deadline=time.monotonic() + 4)
            self.assertEqual(result['exitCode'], 0)
            self.assertFalse(result['orphanedChildren'])
            self.assertTrue(result['cleanupComplete'])

    def test_real_pair_orchestration_strong_gate_precedes_second_launch(self):
        self._orchestration(reject_first=True)

    def test_real_pair_orchestration_same_disk_bundle_and_marker_chain(self):
        self._orchestration(reject_first=False)

    def test_admission_intercepts_exact_new_controller_launch(self):
        self._orchestration(reject_first=False, probe=True)

    def test_failed_pair_stage_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pair.q.write_json(root / 'runtime-freeze.json', {'synthetic': True})
            value = {'storage': {'probe': {}}}
            with mock.patch.object(pair.runtime, '_runtime_root'), \
                 mock.patch.object(pair, 'verify_boundary', return_value=value), \
                 mock.patch.object(pair.storage, 'storage_session', return_value=__import__('contextlib').nullcontext()), \
                 mock.patch.object(pair, 'execute_pair', side_effect=RuntimeError('synthetic child failure')):
                with self.assertRaises(RuntimeError):
                    pair.run_pair(root, root)
                self.assertEqual(pair.q.read_json(root / 'stages/pair/result.json')['status'], 'failed')
                with self.assertRaises(FileExistsError):
                    pair.run_pair(root, root)

    def test_passed_pair_stage_binds_runtime_freeze(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pair.q.write_json(root / 'runtime-freeze.json', {'synthetic': True})
            digest = pair.q.stable_hash(root / 'runtime-freeze.json')['sha256']
            value = {'storage': {'probe': {}}}
            with mock.patch.object(pair.runtime, '_runtime_root'), \
                 mock.patch.object(pair, 'verify_boundary', return_value=value), \
                 mock.patch.object(pair.storage, 'storage_session', return_value=__import__('contextlib').nullcontext()), \
                 mock.patch.object(pair, 'execute_pair', return_value={'syntheticNoGuest': True}):
                result = pair.run_pair(root, root)
                self.assertEqual(result['freezeSha256'], digest)
                self.assertEqual(result['status'], 'passed')
                self.assertFalse(result['finalSystemAccepted'])

    def test_strong_pair_rejects_reboot_content_or_private_data_drift(self):
        one = {'bootId': 'boot1', 'sessionId': 'session1', 'after': {
            'envelope': {'diskUuid': 'disk', 'marker': {'counter': 1}},
            'content': {'files': 'committed'}, 'privateContent': {'files': 'private'}}}
        two = {'bootId': 'boot2', 'sessionId': 'session2', 'before': {
            'envelope': {'diskUuid': 'disk', 'previousMarker': {'counter': 1}},
            'content': {'files': 'committed'}, 'privateContent': {'files': 'private'}}}
        pair.validate_strong_pair(one, two)
        for key in ('content', 'privateContent', 'envelope'):
            changed = json.loads(json.dumps(two)); changed['before'][key] = {}
            with self.subTest(key=key), self.assertRaises(ValueError):
                pair.validate_strong_pair(one, changed)

    def _orchestration(self, reject_first, probe=False):
        # The disk utility and VM boundary are replaced: no format or guest is possible.
        import data_disk
        import bootlib
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            work = root / 'work'
            stage = root / 'stage'
            stage.mkdir()
            (root / 'bundle').mkdir()
            (root / 'bundle/bundle.json').write_text('{}')
            identity = pair.q.stable_hash(root / 'bundle/bundle.json')['sha256']
            review = {'inputScripts': {'boot1Sha256': 'b' * 64, 'boot2Sha256': 'c' * 64}}
            pair.q.write_json(root / 'review.json', review)
            pair.q.write_json(root / 'fixture-verification.json', {})
            pair.q.write_json(root / 'runtime-freeze.json', {'synthetic': True})
            (root / 'inputs').mkdir()
            for number in (1, 2):
                pair.q.write_json(root / ('inputs/input-boot%d.json' % number), {'steps': list(range(5 if number == 1 else 2))})
            for number in (1, 2):
                review['inputScripts']['boot%dSha256' % number] = pair.q.stable_hash(root / ('inputs/input-boot%d.json' % number))['sha256']
            (root / 'review.json').unlink()
            pair.q.write_json(root / 'review.json', review)
            value = {'checker': {'run-stage.py': {}, 'verify-persistent-update-evidence.py': {}},
                'inputs': {str(n): {'sha256': review['inputScripts']['boot%dSha256' % n]} for n in (1, 2)}}
            def workspace(path, create=False):
                path.mkdir(mode=0o700)
                return path
            def create_disk(path, name, size):
                (path / 'disk.raw').write_bytes(b'preserved-disk')
                return {'imagePath': str(path / 'disk.raw'), 'uuid': 'disk', 'image': 'disk.raw',
                        'manifestPath': str(path / 'disk.json')}
            seen = []
            def launch(directory, argv, guard, **limits):
                number = int(argv[argv.index('--boot-number') + 1])
                self.assertEqual(seen, [] if number == 1 else ['launch1', 'gate1'])
                seen.append('launch%d' % number)
                boot = Path(argv[argv.index('--output') + 1]); boot.mkdir()
                value = dict(report(number), bundleIdentity=identity,
                             inputScriptSha256=review['inputScripts']['boot%dSha256' % number])
                pair.q.write_json(boot / 'result.json', value)
                return {'exitCode': 0, 'cleanupComplete': True, 'timedOut': False}
            def checker_gate(run, number, *args):
                seen.append('gate%d' % number)
                if reject_first:
                    raise ValueError('strong checker rejected boot1')
                return {'bootId': 'boot%d' % number, 'sessionId': 'session%d' % number,
                    'before': {'envelope': {'diskUuid': 'disk', 'previousMarker': {'counter': 1}},
                        'content': {'committed': True}, 'privateContent': {}},
                    'after': {'envelope': {'diskUuid': 'disk', 'marker': {'counter': 1}},
                        'content': {'committed': True}, 'privateContent': {}}}
            checker = types.SimpleNamespace(verify_boot=checker_gate)
            def bound(root, name, expected):
                return self.driver if name == 'run-stage.py' else checker
            def reviewed(review, path, number):
                return path.read_bytes(), review['inputScripts']['boot%dSha256' % number]
            with mock.patch.object(pair, 'WORK', work), \
                 mock.patch.object(pair, 'verify_boundary', return_value=value), \
                 mock.patch.object(pair, 'load_bound', side_effect=bound), \
                 mock.patch.object(pair, 'supervise_boot', side_effect=launch), \
                 mock.patch.object(data_disk, 'runtime_directory'), \
                 mock.patch.object(data_disk, 'workspace', side_effect=workspace), \
                 mock.patch.object(data_disk, 'create_disk', side_effect=create_disk), \
                 mock.patch.object(data_disk, 'load_disk', side_effect=lambda *args: {
                     'imagePath': str(work / 'disk.raw'), 'uuid': 'disk', 'image': 'disk.raw',
                     'manifestPath': str(work / 'disk.json')}), \
                 mock.patch.object(bootlib, 'validate_bundle', return_value={'sessionReview': review}), \
                 mock.patch.object(bootlib, 'validate_reviewed_input', side_effect=reviewed):
                if probe:
                    # Exercise the real supervisor, intercepting only its VM launch.
                    with mock.patch.object(pair, 'supervise_boot', wraps=ORIGINAL_SUPERVISOR), \
                         mock.patch.object(pair.runtime, 'load_runtime', return_value=value), \
                         mock.patch.object(pair.runtime, 'verify_bundle_products', return_value={'sessionReview': review}), \
                         mock.patch.object(pair, 'RUNTIME', root):
                        result = pair.probe_pair_boundary(root, root)
                    self.assertTrue(result['vmLaunchIntercepted'])
                    self.assertTrue(result['secondBootAfterFailureBlocked'])
                    self.assertEqual(result['vmLaunchCount'], 1)
                    self.assertFalse(result['guestStarted'])
                    return
                if reject_first:
                    with self.assertRaises(RuntimeError):
                        pair.execute_pair(root, root, stage, value, 'd' * 64)
                    self.assertEqual(seen, ['launch1', 'gate1'])
                    self.assertFalse((work / 'cold-pair/boot-2').exists())
                    self.assertEqual(pair.q.read_json(work / 'cold-pair/result.json')['status'], 'failed')
                else:
                    result = pair.execute_pair(root, root, stage, value, 'd' * 64)
                    self.assertEqual(seen, ['launch1', 'gate1', 'launch2', 'gate2'])
                    self.assertEqual(result['diskUuid'], 'disk')
                    self.assertFalse(result['finalSystemAccepted'])
                    self.assertEqual(pair.q.read_json(stage / 'external-products.json')['pairRoot'], str(work / 'cold-pair'))
                self.assertEqual((work / 'disk.raw').read_bytes(), b'preserved-disk')


if __name__ == '__main__':
    unittest.main()
