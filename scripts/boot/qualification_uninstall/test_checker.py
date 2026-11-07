"""Synthetic controls for the independent uninstall auditor; no VM or Docker."""
import importlib.util
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
if not shutil.which('openssl') and Path('C:/Program Files/Git/usr/bin/openssl.exe').is_file():
    os.environ['PATH'] = 'C:/Program Files/Git/usr/bin' + os.pathsep + os.environ['PATH']
from fixtures import Fixture, RawBoot, load, put, encoded, portable_pair, seal, digest, controls


class InterfaceTests(unittest.TestCase):
    def test_fixed_inputs_construct_production_input_plan(self):
        from infra import InputPlan
        import io
        for number in (1, 2):
            spec = json.loads((HERE / f'input-boot{number}.json').read_bytes())
            InputPlan(spec, io.StringIO(), 60, split_transactions=True, pointer_handshake=True)

    def test_independent_checker_exposes_fail_closed_pair_interface(self):
        path = HERE / 'checker.py'
        self.assertTrue(path.is_file(), 'independent uninstall checker is missing')
        spec = importlib.util.spec_from_file_location('uninstall_checker_test', path)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        with self.assertRaises((ValueError, FileNotFoundError)):
            checker.pair(HERE / 'nonexistent-evidence')


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.checker = load()
        self.fixture = Fixture(self.root)
        self.signed = self.checker.verify_fixture_directory(self.fixture.fixture, self.fixture.inputs, self.fixture.review)

    def boot(self, number=1):
        return RawBoot(self.root, number, self.fixture, self.signed)

    def check(self, boot):
        boot.flush()
        self.assertTrue(callable(getattr(self.checker, 'verify_boot', None)), 'raw uninstall verify_boot missing')
        return self.checker.verify_boot(boot.run, boot.number, self.fixture.review, boot.spec,
            self.signed, 'b' * 64, self.checker.REPO)

    def test_complete_boot1_early_commit_and_boot2(self):
        for number in (1, 2):
            audited = self.check(self.boot(number))
            self.assertEqual(audited['bootNumber'], number)
            self.assertEqual(audited['after']['privateContent'], self.checker.PRIVATE_CONTENT)

    def pending_launch_boot(self, mutate=None):
        values = controls.chain(old=False)
        for record in values:
            value = record['receipt']
            if record['kind'] != 'native-input' or value['sequence'] not in (1, 2):
                continue
            for key in ('before', 'after'):
                anchor = value[key]
                for row in anchor['history']:
                    if row['app'] == 2 and row['status'] == 'starting':
                        row['workerPid'] = 0
                for app in anchor['state']['apps']:
                    if app['app'] == 2 and app['state'] == 'starting':
                        app['workerPid'] = 0
        original_line, changed = controls.line, False
        def line(record):
            nonlocal changed
            # Mutate after RawBoot normalizes generation/revision, before it seals transport bytes.
            if mutate and not changed:
                mutate(values)
                changed = True
            return original_line(record)
        with mock.patch.object(controls, 'chain', return_value=values), \
                mock.patch.object(controls, 'line', side_effect=line):
            return self.boot()

    def test_pending_native_launch_history_binds_to_later_worker(self):
        boot = self.pending_launch_boot()
        audited = self.check(boot)
        self.assertEqual(audited['bootNumber'], 1)

    def test_pending_native_launch_does_not_hide_invalid_history(self):
        def pending(values):
            return values[2]['receipt']['after']
        def completed(values):
            return values[-2]['receipt']['before']['history'][0]
        for label, mutate in (
                ('nonzero-pid', lambda v: pending(v)['history'][0].update(workerPid=999)),
                ('wrong-generation', lambda v: pending(v)['history'][0].update(generation=99)),
                ('wrong-window', lambda v: pending(v)['history'][0].update(window=99)),
                ('wrong-app', lambda v: pending(v)['history'][0].update(app=3)),
                ('completed-zero', lambda v: pending(v)['history'][0].update(completed=True)),
                ('running-zero', lambda v: pending(v)['history'][0].update(status='running')),
                ('state-pid', lambda v: pending(v)['state']['apps'][0].update(workerPid=999)),
                ('no-launch-trace', lambda v: v[2]['receipt']['trace'][0].update(kind='other')),
                ('duplicate', lambda v: pending(v)['history'].append(copy.deepcopy(pending(v)['history'][0]))),
                ('terminal-pid-zero', lambda v: completed(v).update(workerPid=0)),
                ('terminal-content', lambda v: completed(v).update(requests=999))):
            with self.subTest(change=label):
                boot = self.pending_launch_boot(mutate)
                try:
                    with self.assertRaises(ValueError):
                        self.check(boot)
                finally:
                    shutil.rmtree(boot.run)

    def test_private_and_removed_snapshots_fail_closed(self):
        for number, phase in ((1, 'pre-desktop'), (1, 'post-desktop'), (2, 'pre-desktop'), (2, 'post-desktop')):
            boot = self.boot(number)
            value = boot.snapshot_value(phase)
            for field in self.checker.ACTIONS:
                changed = copy.deepcopy(value)
                changed['envelope']['actions'][field] = 1 - int(number == 1 and phase == 'pre-desktop')
                with self.subTest(number=number, phase=phase, field=field), self.assertRaises(ValueError):
                    self.checker.validate_snapshot(changed, boot.boot, number, phase,
                        boot.session if phase == 'post-desktop' else None, 42 if phase == 'post-desktop' else None)
            if phase != 'pre-desktop' or number == 2:
                for change in ('removed', 'empty', 'wrong', 'extra', 'bool-size', 'fingerprint'):
                    changed = copy.deepcopy(value)
                    if change == 'removed': changed['removed'] = None
                    elif change == 'empty': changed['privateContent']['files'] = []
                    elif change == 'wrong': changed['privateContent']['files'][0]['sha256'] = '0' * 64
                    elif change == 'extra': changed['privateContent']['files'].append(copy.deepcopy(changed['privateContent']['files'][0]))
                    elif change == 'bool-size': changed['privateContent']['files'][0]['size'] = True
                    else: changed['removed']['fingerprint'] = '0' * 64
                    with self.subTest(number=number, phase=phase, change=change), self.assertRaises(ValueError):
                        self.checker.validate_snapshot(changed, boot.boot, number, phase,
                            boot.session if phase == 'post-desktop' else None, 42 if phase == 'post-desktop' else None)
            shutil.rmtree(boot.run)

    def test_raw_failures_truncation_offsets_qmp_and_reseed_reject(self):
        for change in ('forced-stop', 'qmp-error', 'qmp-extra', 'offset', 'digest', 'post-init', 'missing-sync', 'missing-power',
                'removed', 'private', 'source-argv', 'wrong-pid', 'relay-offset', 'result-missing', 'clock-regression'):
            boot = self.boot()
            if change == 'forced-stop': boot.events.insert(-2, dict(type='forced-process-stop', uptime=boot.events[-2]['uptime']))
            elif change == 'qmp-error':
                row = boot.qmp[-1]['message']; row['error'] = {}; row.pop('return')
            elif change == 'qmp-extra': boot.command('input-send-event', {'events': []})
            elif change == 'offset': next(e for e in boot.events if e.get('name') == 'desktop.log')['offset'] = 1
            elif change == 'digest': next(e for e in boot.events if e['type'] == 'desktop-diagnostic-stderr')['sha256'] = '0' * 64
            elif change in ('post-init', 'removed', 'private'):
                event = [e for e in boot.events if e['type'] == 'persistent-uninstall-state'][-1]
                if change == 'post-init': event['receipt']['envelope']['actions']['sourcesCreated'] = 1
                elif change == 'removed': event['receipt']['removed'] = None
                else: event['receipt']['privateContent']['files'] = []
            elif change == 'missing-sync': boot.events = [e for e in boot.events if e['type'] != 'data-synced']
            elif change == 'source-argv': next(e for e in boot.events if e['type'] == 'process-start')['argv'] += ['--events', '/tmp/events.json']
            elif change == 'wrong-pid': next(e for e in boot.events if e['type'] == 'desktop-persistent-receipt')['desktopPid'] = 999
            elif change == 'relay-offset': next(e for e in boot.events if e['type'] == 'desktop-persistent-receipt')['logStartOffset'] += 1
            elif change == 'result-missing':
                boot.events = [e for e in boot.events if not(e['type'] == 'desktop-persistent-receipt' and e['record']['kind'] == 'persistent-uninstall-commit')]
            elif change == 'clock-regression': boot.progress[-1]['monotonic'] = 0
            boot.flush()
            if change == 'missing-power': (boot.run / 'kernel-serial.log').write_text('no power down')
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.checker.verify_boot(boot.run, 1, self.fixture.review, boot.spec, self.signed, 'b' * 64, self.checker.REPO)
            shutil.rmtree(boot.run)

    def test_complete_worker_result_reap_close_and_strict_telemetry_required(self):
        for change in ('reap', 'result', 'close', 'native-exit-error', 'pending-inconsistent', 'isolation', 'dropped-trace', 'ready-empty', 'ready-future'):
            boot = self.boot()
            outcome = copy.deepcopy(boot.outcome)
            if change in ('reap', 'result', 'close'):
                kind = {'reap': 'worker-reaped', 'result': 'result-accepted', 'close': 'terminal-reply-written'}[change]
                outcome['trace'] = [e for e in outcome['trace'] if e['app'] != 2 or e['kind'] != kind]
            elif change == 'native-exit-error': outcome['instances'][-1]['error'] = 'WORKER_EXIT_STATUS'
            elif change == 'pending-inconsistent': outcome['instances'][-1]['frameClock']['pending'] = 5
            elif change == 'isolation': outcome['instances'][-1]['isolation']['cleaned'] = False
            elif change.startswith('ready-'):
                next(e for e in outcome['trace'] if e['kind'] == 'ready' and e['app'] == 2)['detail'] = '' if change == 'ready-empty' else 'source=999999'
            else: outcome['droppedTrace'] = 1
            next(e for e in boot.events if e.get('name') == 'desktop.log')['data'] = base64.b64encode(encoded(outcome) + b'\n').decode()
            with self.subTest(change=change), self.assertRaises(ValueError): self.check(boot)
            shutil.rmtree(boot.run)

    def test_boot2_no_app2_anywhere_in_full_history_trace_or_process_table(self):
        for change in ('state', 'trace', 'instance', 'process', 'source', 'helper'):
            boot = self.boot(2)
            if change == 'state': next(e for e in boot.events if e['type'] == 'desktop-state')['state']['apps'].append(copy.deepcopy(controls.history()))
            elif change in ('trace', 'instance'):
                outcome = copy.deepcopy(boot.outcome)
                if change == 'trace': outcome['trace'].append(dict(outcome['trace'][0], app=2))
                else: outcome['instances'].append(copy.deepcopy(controls.history()))
                next(e for e in boot.events if e.get('name') == 'desktop.log')['data'] = base64.b64encode(encoded(outcome)+b'\n').decode()
            elif change == 'process': next(e for e in boot.events if e['type'] == 'process-table')['processes'].append({'pid': 888, 'argv': 'ming-app-host __worker', 'status': ''})
            elif change == 'source': next(e for e in boot.events if e['type'] == 'process-start')['argv'] += ['--install-source', '/opt/mingos/install-source/app.json']
            else: next(e for e in boot.events if e['type'] == 'persistent-uninstall-state')['argv'][1] = 'initialize-first'
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)): self.check(boot)
            shutil.rmtree(boot.run)

    def test_signed_source_and_stored_manifest_tampering_and_locks_reject(self):
        for change in ('signature', 'sdk', 'extra', 'migration', 'module', 'lock1', 'lock2', 'directory'):
            source = self.fixture.fixture / 'install-source/app.json'
            backup = source.read_bytes()
            modified = json.loads(backup)
            if change == 'signature': modified['publisherSignature'] = base64.b64encode(bytes(64)).decode()
            elif change == 'sdk': modified['sdkVersion'] = True
            elif change == 'extra': modified['extra'] = None
            elif change == 'migration': modified['dataMigration'] = {}
            if change in ('signature', 'sdk', 'extra', 'migration'): put(source, modified)
            elif change == 'module': (source.parent / 'main.wasm').write_bytes(b'bad')
            elif change.startswith('lock'):
                name = '.ming-installed-packages.lock' if change == 'lock1' else '.ming-publisher-keyring.lock'
                (self.fixture.fixture / 'data/install-root' / name).unlink()
            else: (self.fixture.fixture / 'data/install-root/extra-empty').mkdir()
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.checker.verify_fixture_directory(self.fixture.fixture, self.fixture.inputs, self.fixture.review)
            source.write_bytes(backup)
            if change == 'module': (source.parent / 'main.wasm').write_bytes(b'\0asm\x01\0\0\0new')
            elif change.startswith('lock'): (self.fixture.fixture / 'data/install-root' / name).write_bytes(b'')
            elif change == 'directory': (self.fixture.fixture / 'data/install-root/extra-empty').rmdir()

    def test_duplicate_nonfinite_json_and_fixed_input_shape_reject(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b'{"a":1e999}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): self.checker.bounded_json(raw)
        spec = json.loads((HERE / 'input-boot1.json').read_bytes())
        self.assertEqual(len(spec['steps']), 7)
        for key in ('schemaVersion',):
            changed = copy.deepcopy(spec); changed[key] = True
            with self.assertRaises(ValueError): self.checker.validate_input(changed, 1)



class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = portable_pair(self.base / 'export')
        self.checker = load()

    def replay(self, root=None, repository=False):
        root = root or self.root
        output = self.base / ('receipt-' + str(len(list(self.base.glob('receipt-*')))) + '.json')
        checker = HERE / 'checker.py' if repository else root / 'checker.py'
        result = subprocess.run([sys.executable, '-I', '-B', str(checker), '--pair', str(root),
            '--output', str(output)], capture_output=True, text=True, cwd=self.base, timeout=60)
        return result, json.loads(output.read_bytes()) if output.exists() else None

    def test_full_pair_relocated_independent_cli_without_repository(self):
        moved = self.base / 'relocated sealed evidence'
        self.root.rename(moved)
        result, receipt = self.replay(moved)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(receipt['persistentUninstallNormalRebootVerified'])
        self.assertTrue(receipt['privateDataPreserved'])
        self.assertFalse(receipt['formalQualification'])
        self.assertFalse(receipt['finalSystemAccepted'])
        self.assertEqual(receipt['boots'][1]['before']['envelope']['previousMarker'],
                         receipt['boots'][0]['after']['envelope']['marker'])

    def test_repository_cli_verifies_and_dispatches_sealed_checker(self):
        result, receipt = self.replay(repository=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertTrue(receipt['privateDataPreserved'])

    def test_manifest_file_set_hash_and_source_freeze_tampering_reject(self):
        self.checker.verify_export_manifest(self.root)
        for change in ('member', 'missing', 'unlisted', 'freeze', 'duplicate', 'traversal'):
            target = self.base / change
            shutil.copytree(self.root, target)
            if change == 'member': (target / 'pair/result.json').write_bytes(b'{}')
            elif change == 'missing': (target / 'pair/result.json').unlink()
            elif change == 'unlisted': (target / 'unlisted').write_bytes(b'extra')
            elif change == 'freeze':
                value = json.loads((target / 'freeze.json').read_bytes())
                value['files']['scripts/boot/infra.py'] = '0' * 64
                put(target / 'freeze.json', value); seal(target)
            elif change == 'duplicate':
                (target / 'file-sha256.json').write_bytes(b'{"schemaVersion":1,"schemaVersion":1}')
            else:
                value = json.loads((target / 'file-sha256.json').read_bytes())
                value['files']['../escape'] = '0' * 64
                put(target / 'file-sha256.json', value)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.checker.verify_export_manifest(target)

    def test_resealed_individually_valid_boot2_cannot_change_cross_boot_registry(self):
        path = self.root / 'pair/boot-2/guest.jsonl'
        rows = [json.loads(line) for line in path.read_bytes().splitlines()]
        for row in rows:
            if row['type'] == 'persistent-uninstall-state':
                content = row['receipt']['content']
                content['registrySha256'] = digest(b'changed registry after reboot')
                for member in content['files']:
                    if member['path'] == 'installed-packages-v1': member['sha256'] = content['registrySha256']
                stdout = json.loads(row['stdout']); stdout['content'] = content
                row['stdout'] = json.dumps(stdout)
        path.write_bytes(b''.join(encoded(row) + b'\n' for row in rows))
        seal(self.root)
        review = json.loads((self.root / 'inputs/session-review.json').read_bytes())
        signed = self.checker.verify_fixture_directory(self.root / 'fixture', self.root / 'inputs', review)
        self.checker.verify_boot(self.root / 'pair/boot-2', 2, review,
            json.loads((HERE / 'input-boot2.json').read_bytes()), signed,
            digest((self.root / 'bundle/bundle.json').read_bytes()), self.checker.REPO)
        result, receipt = self.replay()
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        self.assertIsNotNone(receipt, result.stderr)
        self.assertIn('reboot identity/marker/content/removal/private chain differs', receipt['error'])
        self.assertFalse(receipt['privateDataPreserved'])


from fixtures import controls


if __name__ == '__main__':
    unittest.main()
