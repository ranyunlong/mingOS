"""Offline synthetic controls; passing them is never VM qualification evidence."""
import importlib.util
import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
REPO = Path('/repo') if HERE == Path('/prep') else HERE.parents[1]
sys.path.insert(0, str(REPO / 'scripts/boot'))
import test_persistent_update_receipt as controls
from bootlib import PERSISTENT_UPDATE_ARGS, PERSISTENT_UPDATE_FIXTURE
from wayland_receipt import POINTER_SYNC_POLICY

REPLAY_MODULES = ('bootlib.py', 'infra.py', 'persistent_update_receipt.py', 'wayland_receipt.py',
                  'bundle.py', 'isolation_profiles.py', 'payload_profiles.py')


def load_checker():
    spec = importlib.util.spec_from_file_location('persistent_update_evidence', HERE / 'verify-persistent-update-evidence.py')
    checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
    return checker


def hash_bytes(value):
    return hashlib.sha256(value).hexdigest()


def put_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value) + '\n')


class SignedFixture:
    """Create signed synthetic controls without executing the fixture helper."""
    def __init__(self, root, *, omit_absent_migration=False):
        self.root = root
        self.inputs, self.fixture = root / 'inputs', root / 'fixture'
        payload, data = self.inputs / 'payload', self.fixture / 'data'
        self.install = data / 'install-root'; self.install.mkdir(parents=True)
        helper = payload / 'prepare-persistent-update-fixture'; helper.parent.mkdir(parents=True)
        helper.write_bytes(b'synthetic helper, never executed')
        self.key = root / 'synthetic-seed.der'
        self.key.write_bytes(bytes.fromhex('302e020100300506032b657004220420') + bytes([7]) * 32)
        self.packages = {}
        for label, package, version, migration in [('bootstrap', 'dev.ming.text-entry-demo', '0.1.0', None),
                ('old', controls.PACKAGE, '0.1.0', None), ('new', controls.PACKAGE, '0.2.0',
                 {'fromVersion': '0.1.0', 'steps': [{'op': 'ensureDir', 'path': 'state'},
                  {'op': 'renameFile', 'from': 'note.txt', 'to': 'state/note.txt'}]})]:
            module = b'\0asm\x01\0\0\0' + label.encode()
            directory = payload / 'packages' / label; directory.mkdir(parents=True)
            (directory / 'main.wasm').write_bytes(module)
            def text(value):
                value = value.encode(); return struct.pack('>Q', len(value)) + value
            migration_bytes = controls.encoded(migration)
            message = (b'ming-package-signature-v1\0' + text('mingos-dev-2026') + struct.pack('>I', 1)
                + text(package) + text(version) + struct.pack('>I', 6) + text('main.wasm') + b'\0\0'
                + struct.pack('>Q', len(migration_bytes)) + migration_bytes + struct.pack('>Q', len(module)) + module)
            message_path = root / 'message'; signature_path = root / 'signature'; message_path.write_bytes(message)
            subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(self.key), '-keyform', 'DER',
                '-rawin', '-in', str(message_path), '-out', str(signature_path)], check=True, capture_output=True)
            manifest = {'schemaVersion': 1, 'id': package, 'version': version, 'sdkVersion': 6,
                'entry': 'main.wasm', 'privateStorage': False, 'publicStorage': False, 'dataMigration': migration,
                'publisherKeyId': 'mingos-dev-2026', 'publisherSignature': base64.b64encode(signature_path.read_bytes()).decode()}
            if omit_absent_migration and migration is None: manifest.pop('dataMigration')
            fingerprint = (b'ming-private-storage-package-v1\0' + struct.pack('>I', 1) + text(package)
                + text(version) + text('main.wasm') + struct.pack('>I', 6) + struct.pack('>Q', len(module)) + module)
            if migration is not None:
                fingerprint += b'\0data-migration-v1' + struct.pack('>Q', len(migration_bytes)) + migration_bytes
            fingerprint += b'\0publisher-key-id-v1' + text('mingos-dev-2026')
            self.packages[label] = {'manifest': manifest, 'wasm': module, 'fingerprint': hash_bytes(fingerprint)}
            if label == 'new': destination = self.fixture / 'install-source'
            else: destination = self.install / f'packages/{package.encode().hex()}/{version}-{hash_bytes(fingerprint)}'
            destination.mkdir(parents=True)
            put_json(destination / 'app.json', manifest); (destination / 'main.wasm').write_bytes(module)
        public = bytes.fromhex('ea4a6c63e29c520abef5507b132ec5f9954776aebebe7b92421eea691446d22c')
        (self.install / 'trusted-publishers-v1').write_bytes(b'ming-trusted-publishers-v1\nmingos-dev-2026\tactive\t' + base64.b64encode(public) + b'\n')
        (payload / 'catalog.json').write_text('{"schemaVersion":3,"apps":[],"autostart":[1]}\n')
        (self.install / 'catalog.json').write_text('synthetic two-app catalog bytes\n')
        (self.install / 'installed-packages-v1').write_text('synthetic two active records\n')
        (self.install / 'desktop-install-state-v1.json').write_text('{}\n')
        namespace = data / ('app-' + base64.b32encode(controls.PACKAGE.encode()).decode().lower().rstrip('=')) / 'files'
        namespace.mkdir(parents=True); (namespace / 'note.txt').write_bytes(b'alpha')
        apps = []
        for number, label in [(2, 'old'), (1, 'bootstrap')]:
            package = self.packages[label]; manifest = package['manifest']
            directory = f"packages/{manifest['id'].encode().hex()}/{manifest['version']}-{package['fingerprint']}"
            apps.append({'app': number, 'packageId': manifest['id'], 'version': manifest['version'], 'fingerprint': package['fingerprint'],
                'manifestPath': directory + '/app.json', 'manifestSha256': hash_bytes((self.install / directory / 'app.json').read_bytes()),
                'wasmPath': directory + '/main.wasm', 'wasmSha256': hash_bytes(package['wasm'])})
        names = {'catalogSha256': 'catalog.json', 'registrySha256': 'installed-packages-v1',
                 'trustSha256': 'trusted-publishers-v1', 'bootstrapSha256': 'desktop-install-state-v1.json'}
        self.content = {key: hash_bytes((self.install / name).read_bytes()) for key, name in names.items()}
        self.content.update(apps=apps, files=[{'path': str(path.relative_to(self.install)), 'sha256': hash_bytes(path.read_bytes())}
                                           for path in sorted(self.install.rglob('*')) if path.is_file()])
        self.value = {'schemaVersion': 1, 'status': 'initialized-first', 'actions': controls.snapshot()['envelope']['actions'],
                      'content': self.content, 'privateContent': controls.private()}
        put_json(self.fixture / 'fixture-receipt.json', self.value); put_json(self.fixture / 'stdout.log', self.value)
        (self.fixture / 'stderr.log').write_text('')
        self.review = {'profile': 'persistent-update', 'instrumentedProfile': 'persistent-update-pointer-ready-v1',
            'persistentInstallState': '/data/mingos/install-root', 'catalogPath': '/data/mingos/install-root/catalog.json',
            'installSource': '/opt/mingos/install-source/app.json', 'extraArgs': PERSISTENT_UPDATE_ARGS,
            'persistentUpdateFixture': PERSISTENT_UPDATE_FIXTURE, 'catalogSha256': hash_bytes((payload / 'catalog.json').read_bytes()),
            'persistentUpdateFixtureSha256': {'helper': hash_bytes(helper.read_bytes()),
                **{label + 'Wasm': hash_bytes(package['wasm']) for label, package in self.packages.items()}},
            'inputScripts': {'boot1Sha256': hash_bytes((HERE / 'input-boot1.json').read_bytes()),
                             'boot2Sha256': hash_bytes((HERE / 'input-boot2.json').read_bytes())}}
        original, staged = Path('/prep/build/fixture'), Path('/prep/inputs/payload')
        argv = [str(staged / 'prepare-persistent-update-fixture'), 'initialize-first', str(original / 'data/install-root'), str(original / 'data'),
            str(staged / 'packages/bootstrap/main.wasm'), str(staged / 'packages/old/main.wasm'), str(staged / 'packages/new/main.wasm'),
            str(original / 'install-source/app.json'), str(staged / 'catalog.json'), self.review['catalogSha256'], 'seed', str(original / 'fixture-receipt.json')]
        put_json(self.fixture / 'command.json', {'stage': 'fixture', 'argv': argv, 'exitCode': 0,
                 'commandRoot': str(original), 'inputsRoot': '/prep/inputs',
                 'executableSha256': self.review['persistentUpdateFixtureSha256']['helper']})


class FixtureSignatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.fixture = SignedFixture(Path(self.temp.name)); self.checker = load_checker()

    def check(self):
        return self.checker.verify_fixture_directory(self.fixture.fixture, self.fixture.inputs, self.fixture.review)

    def test_actual_openssl_verifies_old_new_and_bootstrap_and_rejects_tampered_message(self):
        result = self.check()
        self.assertEqual(result['initialContent'], self.fixture.content)
        for label in ('bootstrap', 'old', 'new'):
            self.assertIs(result[label]['signatureVerified'], True)
            self.assertIs(result[label]['tamperedMessageRejected'], True)
            self.assertEqual(len(result[label]['openssl']['sha256']), 64)

    def check_manifest(self, label, manifest):
        directory = Path(self.temp.name) / ('candidate-' + label); directory.mkdir(exist_ok=True)
        put_json(directory / 'app.json', manifest)
        package = self.fixture.packages[label]
        (directory / 'main.wasm').write_bytes(package['wasm'])
        return self.checker.signed_package(directory / 'app.json', package['manifest']['id'],
            package['manifest']['version'], self.fixture.inputs / 'payload/packages' / label / 'main.wasm')

    def test_signed_fixture_accepts_rust_omitted_and_explicit_null_absent_migration(self):
        for omitted in (True, False):
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory() as work:
                fixture = SignedFixture(Path(work), omit_absent_migration=omitted)
                result = self.checker.verify_fixture_directory(fixture.fixture, fixture.inputs, fixture.review)
                self.assertEqual(result['initialContent'], fixture.content)
                for label in ('bootstrap', 'old'):
                    manifest = fixture.packages[label]['manifest']
                    if omitted: self.assertNotIn('dataMigration', manifest)
                    else: self.assertIsNone(manifest['dataMigration'])
                    self.assertIs(result[label]['signatureVerified'], True)
                    self.assertIs(result[label]['tamperedMessageRejected'], True)
                    self.assertEqual(result[label]['migrationSha256'], hash_bytes(b'null'))
                self.assertEqual(fixture.packages['new']['manifest']['dataMigration'], self.checker.MIGRATION)
                self.assertIs(result['new']['signatureVerified'], True)

    def test_signed_manifest_rejects_extra_fields_and_missing_required_signature_fields(self):
        for label in ('bootstrap', 'old', 'new'):
            for omitted in ((True, False) if label != 'new' else (False,)):
                original = copy.deepcopy(self.fixture.packages[label]['manifest'])
                if omitted: original.pop('dataMigration')
                for field in ('publisherKeyId', 'publisherSignature', 'privateStorage'):
                    with self.subTest(label=label, omitted=omitted, missing=field):
                        manifest = copy.deepcopy(original); del manifest[field]
                        with self.assertRaises(ValueError): self.check_manifest(label, manifest)
                with self.subTest(label=label, omitted=omitted, extra=True):
                    manifest = copy.deepcopy(original); manifest['unexpected'] = None
                    with self.assertRaises(ValueError): self.check_manifest(label, manifest)

    def test_signed_manifest_rejects_migration_on_bootstrap_and_old(self):
        for label in ('bootstrap', 'old'):
            for migration in ({}, False, [], 'null', self.checker.MIGRATION):
                with self.subTest(label=label, migration=migration):
                    manifest = copy.deepcopy(self.fixture.packages[label]['manifest']); manifest['dataMigration'] = migration
                    with self.assertRaises(ValueError): self.check_manifest(label, manifest)

    def test_signed_new_manifest_requires_exact_nonnull_migration(self):
        valid = self.fixture.packages['new']['manifest']
        missing = copy.deepcopy(valid); del missing['dataMigration']
        cases = [missing]
        for migration in (None, {}, False, [], {'fromVersion': '0.1.0', 'steps': []}):
            manifest = copy.deepcopy(valid); manifest['dataMigration'] = migration; cases.append(manifest)
        for change in ('extra-migration-field', 'extra-step-field', 'wrong-version', 'wrong-destination'):
            manifest = copy.deepcopy(valid)
            if change == 'extra-migration-field': manifest['dataMigration']['unexpected'] = None
            elif change == 'extra-step-field': manifest['dataMigration']['steps'][0]['unexpected'] = None
            elif change == 'wrong-version': manifest['dataMigration']['fromVersion'] = '0.0.0'
            else: manifest['dataMigration']['steps'][1]['to'] = 'wrong/note.txt'
            cases.append(manifest)
        for index, manifest in enumerate(cases):
            with self.subTest(case=index), self.assertRaises(ValueError): self.check_manifest('new', manifest)

    def test_original_container_command_survives_read_only_export_path_relocation(self):
        original = self.check()
        exported = Path(self.temp.name) / 'export'; exported.mkdir()
        shutil.copytree(self.fixture.fixture, exported / 'fixture')
        shutil.copytree(self.fixture.inputs, exported / 'inputs')
        relocated = self.checker.verify_fixture_directory(exported / 'fixture', exported / 'inputs', self.fixture.review)
        self.assertEqual(relocated, original)

    def test_signature_manifest_wasm_helper_and_private_tampering_are_rejected(self):
        for change in ('signature', 'migration', 'bool-sdk', 'wasm', 'private', 'command', 'extra-directory', 'stdout-schema'):
            with self.subTest(change=change):
                self.setUp(); source = self.fixture.fixture / 'install-source/app.json'
                if change in ('signature', 'migration', 'bool-sdk'):
                    value = json.loads(source.read_text())
                    if change == 'signature': value['publisherSignature'] = base64.b64encode(bytes(64)).decode()
                    elif change == 'migration': value['dataMigration']['steps'][1]['to'] = 'wrong/note.txt'
                    else: value['sdkVersion'] = True
                    put_json(source, value)
                elif change == 'wasm': (source.parent / 'main.wasm').write_bytes(b'\0asm\x01\0\0\0tamper')
                elif change == 'private':
                    next((self.fixture.fixture / 'data').glob('app-*/files/note.txt')).write_bytes(b'other')
                elif change == 'extra-directory': (self.fixture.install / 'unexpected-empty-directory').mkdir()
                elif change == 'stdout-schema':
                    value = json.loads((self.fixture.fixture / 'stdout.log').read_text())
                    value['schemaVersion'] = True
                    put_json(self.fixture.fixture / 'stdout.log', value)
                else:
                    path = self.fixture.fixture / 'command.json'; value = json.loads(path.read_text()); value['exitCode'] = 1; put_json(path, value)
                with self.assertRaises(ValueError): self.check()


class RawBoot:
    """Complete synthetic QMP, pointer, native, helper, and poweroff streams."""
    def __init__(self, root, number, fixture, signed):
        self.run = root / f'boot-{number}'; self.run.mkdir()
        self.number, self.fixture, self.signed = number, fixture, signed
        self.spec = json.loads((HERE / f'input-boot{number}.json').read_text())
        self.events, self.progress, self.qmp = [], [], []
        self.stderr = bytearray(b'client get_pointer(new id wl_pointer@36)\n')
        self.clock, self.request_id = 1.0, 0
        self.new_content = copy.deepcopy(fixture.content)
        package = signed['new']; directory = f"packages/{controls.PACKAGE.encode().hex()}/0.2.0-{package['fingerprint']}"
        new_app = {'app': 2, 'packageId': controls.PACKAGE, 'version': '0.2.0', 'fingerprint': package['fingerprint'],
                   'manifestPath': directory + '/app.json', 'manifestSha256': package['manifestSha256'],
                   'wasmPath': directory + '/main.wasm', 'wasmSha256': package['wasmSha256']}
        self.new_content['apps'][0] = new_app
        self.new_content['registrySha256'] = hash_bytes(b'new actual registry control')
        files = {row['path']: row['sha256'] for row in fixture.content['files']}
        files.update({directory + '/app.json': package['manifestSha256'], directory + '/main.wasm': package['wasmSha256'],
                      'installed-packages-v1': self.new_content['registrySha256']})
        self.new_content['files'] = [{'path': path, 'sha256': digest} for path, digest in sorted(files.items())]
        self.values = controls.chain(number, early_commit=True)
        replaced = set()
        def replace(value):
            if isinstance(value, dict):
                if id(value) in replaced: return
                replaced.add(id(value))
                if set(controls.update_record()) == set(value):
                    value.update(old={key: signed['old'][key] for key in value['old']},
                                 new={key: signed['new'][key] for key in value['new']})
                    for key in ('catalogSha256', 'bootstrapSha256', 'trustSha256'): value[key] = fixture.content[key]
                elif 'catalogSha256' in value and 'apps' in value:
                    initial = any(app.get('app') == 2 and app.get('version') == '0.1.0' for app in value['apps'])
                    value.clear(); value.update(copy.deepcopy(fixture.content if initial else self.new_content)); return
                elif set(value) == set(controls.package_identity(2, '0.2.0')):
                    value.update({key: signed['new' if value['version'] == '0.2.0' else 'old'][key] for key in value})
                elif 'fingerprint' in value and value.get('packageId') == controls.PACKAGE and value.get('version') == '0.2.0':
                    value['fingerprint'] = signed['new']['fingerprint']
                if 'bootId' in value and 'revision' in value and 'apps' in value:
                    revision = value['revision']; root_state = value['root']
                    root_state['presentedSequence'] = 1 if revision <= 3 else 2 if revision <= 7 else 3
                    if root_state['storagePrompt'] is not None: root_state['storagePromptPresentedSequence'] = 2
                    for app in value['apps']:
                        if app['state'] == 'ready': app['state'] = 'running'
                    value['apps'].append(self.app1())
                for item in list(value.values()): replace(item)
            elif isinstance(value, list):
                for item in value: replace(item)
        replace(self.values)
        if number == 1:
            trial = next(v['receipt'] for v in self.values if v['kind'] == 'trial-published')
            inner = {'packageId': controls.PACKAGE, 'oldVersion': '0.1.0', 'oldFingerprint': signed['old']['fingerprint'],
                'newVersion': '0.2.0', 'newFingerprint': signed['new']['fingerprint'],
                'backupName': '.ming-update-backup-trial-123', 'migrationDeclared': True, 'phase': 'recordPublished'}
            for label, value in [('outerRecord', trial['record']), ('innerJournal', inner)]:
                data = controls.encoded(value)
                trial['trialObservation'][label + 'Sha256'] = hash_bytes(data)
                trial['trialObservation'][label + 'Base64'] = base64.b64encode(data).decode()
        for value in self.values:
            if value['kind'] == 'native-input':
                index = (value['receipt']['sequence'] - 1) // 2
                value['receipt'].update({axis: self.spec['steps'][index]['events'][0][axis] for axis in ('x', 'y')})
        self.before, self.after = controls.snapshot(number), controls.snapshot(number, 'post-desktop')
        self.before['content'] = copy.deepcopy(fixture.content if number == 1 else self.new_content)
        self.after['content'] = copy.deepcopy(self.new_content)
        self.emit('guest-start', bootId=controls.BOOT)
        self.emit('data-marker', uuid=self.before['envelope']['diskUuid'], marker=self.before['envelope']['marker'],
                  previousMarker=self.before['envelope']['previousMarker'], readbackVerified=True)
        self.helper(self.before, 'pre-desktop')
        argv = ['/opt/mingos/ming-app-host', 'desktop', '/data/mingos/install-root/catalog.json', '--renderer', 'cpu']
        argv += PERSISTENT_UPDATE_ARGS + ['--persistent-install-state', '/data/mingos/install-root']
        if number == 1: argv += ['--install-source', '/opt/mingos/install-source/app.json']
        self.emit('process-start', name='desktop', pid=controls.PID, argv=argv)
        self.qmp.append({'monotonic': self.tick(), 'direction': 'receive', 'message': {'QMP': {}}})
        self.command('qmp_capabilities'); mice = [{'name': 'tablet', 'absolute': True, 'current': True}]
        self.command('query-mice', result=mice)
        current = copy.deepcopy(self.values[0]['receipt']['before']['state'])
        self.snapshot(current); self.record('input-routing', mice=mice, state=current)
        if number == 1:
            groups = [self.values[:2], self.values[2:6], self.values[6:10], self.values[10:14], self.values[14:]]
        else: groups = [self.values[:4], self.values[4:]]
        for index, (step, group) in enumerate(zip(self.spec['steps'], groups)):
            from infra import input_events
            events = input_events(step['events'], current); transactions = [events[:2]] + [[item] for item in events[2:]]
            self.command('input-send-event', {'events': transactions[0]})
            point = step['events'][0]; after_offset = len(self.stderr)
            self.record('input-pointer-dispatched', step=step['name'], point=point, desktopPid=controls.PID,
                        afterLogOffset=after_offset, waitSeconds=5, state=current)
            line = ('client wl_pointer@36.enter(1, wl_surface@15, %s, %s)\n' if index == 0 else
                    'client wl_pointer@36.motion(1, %s, %s)\n') % (point['x'], point['y'])
            self.stderr.extend((line + 'client wl_pointer@36.frame()\n').encode())
            pointer = {'pointer': 36, 'surface': 15, 'entered': True, 'x': float(point['x']), 'y': float(point['y']),
                       'sequence': index + 1, 'logOffset': len(self.stderr)}
            self.emit('desktop-pointer-receipt', bootId=controls.BOOT, sessionId=controls.SESSION, desktopPid=controls.PID, receipt=pointer)
            self.record('input-pointer-ready', step=step['name'], receipt=dict(pointer, bootId=controls.BOOT,
                        sessionId=controls.SESSION, desktopPid=controls.PID), afterLogOffset=after_offset, state=current)
            for transaction in transactions[1:]: self.command('input-send-event', {'events': transaction})
            self.record('input-dispatched', step=step['name'], events=events, transactions=transactions,
                        diagnosticSplit=True, pointerHandshake=True, state=current)
            for value in group:
                data = controls.line(value); start = len(self.stderr); self.stderr.extend(data)
                self.emit('desktop-persistent-receipt', bootId=controls.BOOT, sessionId=controls.SESSION, desktopPid=controls.PID,
                    logStartOffset=start, logEndOffset=len(self.stderr), rawBase64=base64.b64encode(data).decode(), record=value)
            if index == 2 and number == 1 or index == 0 and number == 2:
                current = copy.deepcopy(group[-1]['receipt']['anchor']['state'])
            else:
                current = copy.deepcopy(next(v['receipt']['after']['state'] for v in reversed(group) if v['kind'] == 'native-input'))
            if index == len(groups) - 1:
                current = controls.state(100, 'closed', updated='committed' if number == 1 else None, phase='stopped')
                current['root']['presentedSequence'] = 3; current['apps'].append(self.app1(stopped=True))
            self.snapshot(current); self.record('input-expectation', step=step['name'], state=current)
        self.final_state = current
        self.emit('desktop-exit', code=0); self.emit('desktop-workers-stopped', active=[])
        self.helper(self.after, 'post-desktop')
        self.emit('data-synced', uuid=self.before['envelope']['diskUuid'], marker=self.before['envelope']['marker'], unmounted=True)
        self.emit('desktop-diagnostic-stderr', bytes=len(self.stderr), sha256=hash_bytes(self.stderr), complete=True, limitBytes=8388608)
        target = controls.history('closed')
        initial = dict(target, app=1, window=1, workerPid=52, packageId='dev.ming.text-entry-demo')
        outcome = {'activeWorkersAfterShutdown': 0, 'droppedTrace': 0, 'trace': [], 'instances': [initial, target]}
        for name, data in [('desktop-wayland.stderr.log', bytes(self.stderr)), ('desktop.log', controls.encoded(outcome) + b'\n')]:
            self.emit('log', name=name, offset=0, encoding='base64', data=base64.b64encode(data).decode())
        self.emit('guest-result', status='normal', desktopExitCode=0, error=None, dataSynced=True,
                  poweroffRequested=True, bootId=controls.BOOT, diskUuid=self.before['envelope']['diskUuid'],
                  previousMarker=self.before['envelope']['previousMarker'], marker=self.before['envelope']['marker'])
        self.emit('guest-stop')
        self.report = {'status': 'normal-desktop-poweroff', 'bootId': controls.BOOT,
            'diskUuid': self.before['envelope']['diskUuid'], 'previousMarker': self.before['envelope']['previousMarker'],
            'marker': self.before['envelope']['marker'], 'lastState': current, 'inputComplete': True,
            'kernelPowerdown': True, 'guestExitCode': 0, 'bootNumber': number, 'bundleIdentity': 'b' * 64,
            'inputScriptSha256': fixture.review['inputScripts'][f'boot{number}Sha256']}
        self.flush()

    def app1(self, stopped=False):
        return {'app': 1, 'generation': 1, 'window': 1, 'workerPid': 52, 'state': 'closed' if stopped else 'running',
            'sourceSequence': 2, 'presentedSourceSequence': 2, 'firstReadyMs': 11,
            'inputReady': not stopped, 'minimized': False, 'outer': None}

    def tick(self):
        self.clock += 0.05; return self.clock

    def emit(self, kind, **fields):
        self.events.append(dict(type=kind, uptime=float(len(self.events)), **copy.deepcopy(fields)))

    def record(self, kind, **fields):
        self.progress.append(dict(type=kind, monotonic=self.tick(), **copy.deepcopy(fields)))

    def command(self, name, arguments=None, result=None):
        self.request_id += 1; message = {'execute': name, 'id': self.request_id}
        if arguments is not None: message['arguments'] = arguments
        self.qmp += [{'monotonic': self.tick(), 'direction': 'send', 'message': message},
                     {'monotonic': self.tick(), 'direction': 'receive',
                      'message': {'id': self.request_id, 'return': {} if result is None else result}}]

    def snapshot(self, state):
        self.emit('desktop-state', state=state, desktopPid=controls.PID, diagnosticStderrOffset=len(self.stderr))

    def helper(self, value, phase):
        checker = load_checker(); env = value['envelope']
        helper_value = {'schemaVersion': 1, 'status': env['status'], 'actions': env['actions'],
                        'content': value['content'], 'privateContent': value['privateContent']}
        self.emit('persistent-update-state', code=0, argv=checker.helper_argv(self.number, phase, self.fixture.review),
                  stdout=json.dumps(helper_value), stderr='', receipt=value)

    def flush(self):
        for name, values in [('guest.jsonl', self.events), ('inputs.jsonl', self.progress), ('qmp.jsonl', self.qmp)]:
            (self.run / name).write_text(''.join(json.dumps(value) + '\n' for value in values))
        put_json(self.run / 'qmp.json', [row['message'] for row in self.qmp])
        put_json(self.run / 'result.json', self.report)
        (self.run / 'kernel-serial.log').write_text('reboot: Power down\n')


class RawBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name); self.fixture = SignedFixture(root)
        self.checker = load_checker()
        self.signed = self.checker.verify_fixture_directory(self.fixture.fixture, self.fixture.inputs, self.fixture.review)
        self.boot = RawBoot(root, 1, self.fixture, self.signed)

    def check(self):
        self.boot.flush()
        return self.checker.verify_boot(self.boot.run, self.boot.number, self.fixture.review,
                self.boot.spec, self.signed, 'b' * 64, REPO)

    def test_full_raw_native_pointer_helper_controls_accept_early_commit(self):
        result = self.check()
        self.assertEqual(result['pointerHandshakes'], 5)
        self.assertEqual(result['trialRecord']['transactionId'], 'trial-123')
        self.assertEqual(result['after']['privateContent'], controls.private(False))

    def test_boot2_full_raw_reopen_control_accepts_without_mutation_receipts(self):
        self.boot = RawBoot(Path(self.temp.name), 2, self.fixture, self.signed)
        result = self.check()
        self.assertEqual(result['pointerHandshakes'], 2)
        self.assertIsNone(result['trialRecord'])

    def test_raw_exit_codes_and_zero_counters_require_integers(self):
        for field in ('desktopExitCode', 'desktop-exit.code', 'guestExitCode',
                      'activeWorkersAfterShutdown', 'droppedTrace'):
            for invalid in (False, 0.0):
                with self.subTest(field=field, invalid=invalid):
                    self.setUp()
                    if field == 'desktopExitCode':
                        next(e for e in self.boot.events if e['type'] == 'guest-result')[field] = invalid
                    elif field == 'desktop-exit.code':
                        next(e for e in self.boot.events if e['type'] == 'desktop-exit')['code'] = invalid
                    elif field == 'guestExitCode':
                        self.boot.report[field] = invalid
                    else:
                        event = next(e for e in self.boot.events if e['type'] == 'log' and e['name'] == 'desktop.log')
                        outcome = json.loads(base64.b64decode(event['data']))
                        outcome[field] = invalid
                        event['data'] = base64.b64encode(controls.encoded(outcome) + b'\n').decode()
                    with self.assertRaises(ValueError): self.check()

    def test_outcome_identity_and_clock_require_integers(self):
        for instance, field, invalid in ((0, 'app', True), (0, 'generation', True),
                (0, 'window', True), (1, 'generation', True), (1, 'closes', True),
                (1, 'cancels', False), (1, 'closes', 1.0), (1, 'cancels', 0.0)):
            with self.subTest(instance=instance, field=field, invalid=invalid):
                self.setUp()
                event = next(e for e in self.boot.events if e['type'] == 'log' and e['name'] == 'desktop.log')
                outcome = json.loads(base64.b64decode(event['data']))
                row = outcome['instances'][instance]
                (row['frameClock'] if field in ('closes', 'cancels') else row)[field] = invalid
                event['data'] = base64.b64encode(controls.encoded(outcome) + b'\n').decode()
                with self.assertRaises(ValueError): self.check()

    def test_helper_stdout_and_native_relay_preserve_json_types(self):
        for change in ('helper-schema', 'helper-counter', 'relay-schema'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'relay-schema':
                    event = next(e for e in self.boot.events if e['type'] == 'desktop-persistent-receipt')
                    event['record']['receipt']['schemaVersion'] = True
                else:
                    event = next(e for e in self.boot.events if e['type'] == 'persistent-update-state')
                    value = json.loads(event['stdout'])
                    if change == 'helper-schema': value['schemaVersion'] = True
                    else: value['actions']['recoveryInvocations'] = False
                    event['stdout'] = json.dumps(value)
                with self.assertRaises(ValueError): self.check()

    def test_raw_offset_identity_clock_and_pointer_tampering_are_rejected(self):
        for change in ('relay-offset', 'relay-bytes', 'relay-pid', 'guest-clock', 'qmp-id', 'pointer-offset'):
            with self.subTest(change=change):
                self.setUp()
                relay = next(e for e in self.boot.events if e['type'] == 'desktop-persistent-receipt')
                if change == 'relay-offset': relay['logStartOffset'] += 1
                elif change == 'relay-bytes': relay['rawBase64'] = base64.b64encode(b'forged\n').decode()
                elif change == 'relay-pid': relay['desktopPid'] += 1
                elif change == 'guest-clock': self.boot.events[-1]['uptime'] = -1
                elif change == 'qmp-id': self.boot.qmp[-1]['message']['id'] = 999
                else: self.boot.progress[2]['receipt']['logOffset'] = 0
                with self.assertRaises(ValueError): self.check()

    def test_missing_unmount_powerdown_reopen_or_legacy_source_fails(self):
        for change in ('sync', 'unmount', 'powerdown', 'source', 'post-private', 'post-journal', 'old-profile', 'changed-plan',
                       'forced-stop', 'truncated-jsonl', 'truncated-stderr', 'bundle', 'disk', 'marker', 'boot2-initialize', 'boot2-recovery'):
            with self.subTest(change=change):
                self.setUp()
                if change == 'sync': self.boot.events = [e for e in self.boot.events if e['type'] != 'data-synced']
                elif change == 'unmount': next(e for e in self.boot.events if e['type'] == 'data-synced')['unmounted'] = False
                elif change == 'powerdown': self.boot.report['kernelPowerdown'] = False
                elif change == 'source': next(e for e in self.boot.events if e['type'] == 'process-start')['argv'] += ['--events', '/script.json']
                elif change == 'post-private': self.boot.events[-7]['receipt']['privateContent']['files']['entries'] = 3
                elif change == 'post-journal':
                    event = [e for e in self.boot.events if e['type'] == 'persistent-update-state'][-1]
                    event['receipt']['content']['files'].append({'path': 'update-journal-v1', 'sha256': 'a' * 64})
                elif change == 'old-profile': self.fixture.review['profile'] = 'data-text-update'
                elif change == 'changed-plan': self.boot.spec['steps'][0]['events'][0]['x'] += 1
                elif change == 'forced-stop': self.boot.events.insert(-2, dict(type='forced-process-stop', uptime=self.boot.events[-2]['uptime']))
                elif change == 'truncated-jsonl':
                    self.boot.flush()
                    path = self.boot.run / 'guest.jsonl'; path.write_bytes(path.read_bytes()[:-1])
                    with self.assertRaises(ValueError):
                        self.checker.verify_boot(self.boot.run, 1, self.fixture.review, self.boot.spec, self.signed, 'b' * 64, REPO)
                    continue
                elif change == 'truncated-stderr':
                    event = next(e for e in self.boot.events if e['type'] == 'log' and e['name'] == 'desktop-wayland.stderr.log')
                    event['data'] = base64.b64encode(base64.b64decode(event['data'])[:-1]).decode()
                elif change == 'bundle': self.boot.report['bundleIdentity'] = 'c' * 64
                elif change == 'disk': self.boot.report['diskUuid'] = '22222222-2222-4222-8222-222222222222'
                elif change == 'marker': self.boot.report['marker'] = {'bootId': controls.BOOT, 'counter': 2}
                else:
                    self.boot = RawBoot(Path(self.temp.name), 2, self.fixture, self.signed)
                    helper = next(e for e in self.boot.events if e['type'] == 'persistent-update-state')
                    helper['receipt']['envelope']['actions']['initializedRoots' if change == 'boot2-initialize' else 'recoveryInvocations'] = 1
                with self.assertRaises(ValueError): self.check()


class CheckerInterfaceTests(unittest.TestCase):
    def test_pair_requires_actual_raw_artifacts(self):
        source = HERE / 'verify-persistent-update-evidence.py'
        self.assertTrue(source.is_file(), 'persistent update offline checker is not implemented')
        spec = importlib.util.spec_from_file_location('persistent_update_evidence', source)
        checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker)
        self.assertTrue(callable(checker.verify_boot))
        self.assertTrue(callable(checker.verify_fixture_directory))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises((ValueError, FileNotFoundError)):
                checker.pair(Path(directory))


class PortableCheckerTests(unittest.TestCase):
    """These synthetic bytes exercise offline replay; no helper/VM is executed."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.export = self.root / 'export'; self.export.mkdir()
        self.other = self.root / 'unrelated-cwd'; self.other.mkdir()
        for name in ('verify-persistent-update-evidence.py', 'raw_boot_audit.py', 'input-boot1.json', 'input-boot2.json'):
            shutil.copyfile(HERE / name, self.export / name)
        scripts = self.export / 'replay/scripts/boot'; scripts.mkdir(parents=True)
        for name in REPLAY_MODULES: shutil.copyfile(REPO / 'scripts/boot' / name, scripts / name)
        prefix = 'artifacts/persistent-update-20261104-prep/'
        self.freeze = {'schemaVersion': 1, 'profile': 'persistent-update',
            'files': {'scripts/boot/' + name: hash_bytes((scripts / name).read_bytes()) for name in REPLAY_MODULES},
            'checker': {'path': prefix + 'verify-persistent-update-evidence.py',
                        'sha256': hash_bytes((self.export / 'verify-persistent-update-evidence.py').read_bytes())},
            'inputs': {'boot%dSha256' % n: hash_bytes((self.export / ('input-boot%d.json' % n)).read_bytes()) for n in (1, 2)}}
        for name in ('verify-persistent-update-evidence.py', 'raw_boot_audit.py'):
            self.freeze['files'][prefix + name] = hash_bytes((self.export / name).read_bytes())
        put_json(self.export / 'freeze.json', self.freeze)
        self.seal()

    def seal(self):
        files = {p.relative_to(self.export).as_posix(): hash_bytes(p.read_bytes())
                 for p in sorted(self.export.rglob('*')) if p.is_file() and p != self.export / 'file-sha256.json'}
        put_json(self.export / 'file-sha256.json', {'schemaVersion': 1, 'algorithm': 'sha256', 'files': files})

    def command(self, *arguments):
        return subprocess.run([sys.executable, '-I', '-B', str(self.export / 'verify-persistent-update-evidence.py'), *arguments],
                              cwd=self.other, capture_output=True, text=True, timeout=60)

    def test_help_from_unrelated_cwd_uses_only_sealed_export(self):
        result = self.command('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--pair', result.stdout)
        self.assertFalse(list(self.export.rglob('__pycache__')))

    def test_export_manifest_rejects_missing_added_and_changed_files_before_import(self):
        for change in ('missing', 'added', 'tampered', 'missing-manifest', 'self-reference', 'bool-schema',
                       'duplicate-key', 'unsafe-path', 'dot-path', 'bad-digest', 'symlink', 'unfrozen-module', 'extra-module'):
            with self.subTest(change=change):
                self.setUp()
                module = self.export / 'replay/scripts/boot/infra.py'
                manifest = self.export / 'file-sha256.json'; value = json.loads(manifest.read_text())
                if change == 'missing': module.unlink()
                elif change == 'added': (self.export / 'unlisted.txt').write_text('extra')
                elif change == 'tampered': module.write_text("raise RuntimeError('UNVERIFIED_IMPORT_EXECUTED')\n")
                elif change == 'missing-manifest': manifest.unlink()
                elif change == 'self-reference': value['files']['file-sha256.json'] = 'a' * 64
                elif change == 'bool-schema': value['schemaVersion'] = True
                elif change == 'duplicate-key': manifest.write_text('{"schemaVersion":1,"schemaVersion":1,"algorithm":"sha256","files":{}}')
                elif change == 'unsafe-path': value['files']['../escape'] = 'a' * 64
                elif change == 'dot-path': value['files']['.'] = 'a' * 64
                elif change == 'bad-digest': value['files']['freeze.json'] = False
                elif change == 'symlink': module.unlink(); module.symlink_to(REPO / 'scripts/boot/infra.py')
                elif change == 'unfrozen-module': module.write_text(module.read_text() + '\n# changed after source freeze\n'); self.seal()
                else: (module.parent / 'unfrozen.py').write_text('raise RuntimeError()\n'); self.seal()
                if change in ('self-reference', 'bool-schema', 'unsafe-path', 'dot-path', 'bad-digest'): put_json(manifest, value)
                result = self.command('--help')
                self.assertNotEqual(result.returncode, 0, change)
                self.assertNotIn('UNVERIFIED_IMPORT_EXECUTED', result.stderr)
                if change == 'missing-manifest': self.assertIn("FileNotFoundError", result.stderr)
                else: self.assertIn('ValueError: portable export:', result.stderr)

    def test_pair_revalidates_manifest_after_module_import(self):
        script = self.export / 'verify-persistent-update-evidence.py'
        code = ("import importlib.util, pathlib; "
                "spec = importlib.util.spec_from_file_location('exported_checker', %r); "
                "checker = importlib.util.module_from_spec(spec); spec.loader.exec_module(checker); "
                "root = pathlib.Path(%r); (root / 'added-after-import').write_text('changed'); "
                "checker.pair(root)") % (str(script), str(self.export))
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=self.other,
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ValueError: portable export: manifest file set differs', result.stderr)

    def test_portable_pair_cannot_mix_another_exports_manifest_and_dependencies(self):
        other_export = self.root / 'other-export'; shutil.copytree(self.export, other_export)
        output = self.other / 'wrong-export-result.json'
        result = self.command('--pair', str(other_export), '--output', str(output))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('checker must replay its own sealed export', json.loads(output.read_text())['error'])

    def test_output_inside_export_is_rejected_including_symlinked_parent(self):
        alias = self.other / 'export-alias'; alias.symlink_to(self.export, target_is_directory=True)
        original = {p.relative_to(self.export).as_posix() for p in self.export.rglob('*')}
        for directory in (self.export, alias):
            with self.subTest(directory=directory):
                result = self.command('--pair', str(self.export), '--output', str(directory / 'result.json'))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('ValueError: portable export: checker output must be outside', result.stderr)
                self.assertFalse((self.export / 'result.json').exists())
        self.assertEqual(original, {p.relative_to(self.export).as_posix() for p in self.export.rglob('*')})

    def test_source_checker_cannot_write_into_complete_sealed_pair(self):
        self.add_pair()
        alias = self.other / 'export-alias'; alias.symlink_to(self.export, target_is_directory=True)
        original = {p.relative_to(self.export).as_posix(): hash_bytes(p.read_bytes())
                    for p in self.export.rglob('*') if p.is_file()}
        for directory in (self.export, alias):
            with self.subTest(directory=directory):
                result = subprocess.run([sys.executable, '-I', '-B', str(HERE / 'verify-persistent-update-evidence.py'),
                    '--pair', str(self.export), '--output', str(directory / 'unsealed-result.json')],
                    cwd=self.other, capture_output=True, text=True, timeout=60)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('ValueError: portable export: checker output must be outside', result.stderr)
                self.assertFalse((self.export / 'unsealed-result.json').exists())
        self.assertEqual(original, {p.relative_to(self.export).as_posix(): hash_bytes(p.read_bytes())
                                  for p in self.export.rglob('*') if p.is_file()})

    def test_source_cli_replays_another_generation_using_its_own_frozen_checker(self):
        self.add_pair()
        script = self.export / 'verify-persistent-update-evidence.py'
        script.write_text(script.read_text() + '\n# distinct sealed checker generation\n')
        exported_hash = hash_bytes(script.read_bytes())
        self.freeze['checker']['sha256'] = exported_hash
        self.freeze['files'][self.freeze['checker']['path']] = exported_hash
        put_json(self.export / 'freeze.json', self.freeze); self.seal()
        output = self.other / 'source-replay-result.json'
        result = subprocess.run([sys.executable, '-I', '-B', str(HERE / 'verify-persistent-update-evidence.py'),
            '--pair', str(self.export), '--output', str(output)],
            cwd=self.other, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        value = json.loads(output.read_text())
        self.assertEqual(value['checkerSha256'], exported_hash)
        self.assertNotEqual(value['checkerSha256'], hash_bytes((HERE / 'verify-persistent-update-evidence.py').read_bytes()))
        with self.assertRaisesRegex(ValueError, 'own exported checker'):
            load_checker().pair(self.export)
        self.assertFalse(list(self.export.rglob('__pycache__')))

    def test_source_cli_propagates_exported_checker_failure(self):
        self.add_pair(); put_json(self.export / 'pair/result.json', {'status': 'failed'}); self.seal()
        output = self.other / 'failed-source-replay-result.json'
        result = subprocess.run([sys.executable, '-I', '-B', str(HERE / 'verify-persistent-update-evidence.py'),
            '--pair', str(self.export), '--output', str(output)],
            cwd=self.other, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 1, result.stderr + result.stdout)
        value = json.loads(output.read_text())
        self.assertEqual(value['status'], 'failed')
        self.assertIn('actual cold-pair runner success missing', value['error'])
        self.assertIs(value['finalSystemAccepted'], False)

    def add_pair(self):
        source = self.root / 'synthetic-source'; source.mkdir(); fixture = SignedFixture(source)
        payload = fixture.inputs / 'payload'
        (payload / 'ming-app-host').write_bytes(b'\x7fELFsynthetic never executed')
        (payload / 'prepare-persistent-update-fixture').write_bytes(b'\x7fELFsynthetic helper never executed')
        put_json(payload / 'catalog.json', {'schemaVersion': 3, 'apps': [
            {'id': 1, 'title': 'Text Lab', 'packageId': 'dev.ming.text-entry-demo'}], 'autostart': [1]})
        fixture.review.update(reviewed=True, reviewer='synthetic offline control', sessionMode='long-lived',
            budgetEvidence='synthetic no execution', hostSha256=hash_bytes((payload / 'ming-app-host').read_bytes()),
            catalogSha256=hash_bytes((payload / 'catalog.json').read_bytes()),
            instrumentedQualification=True, inputSynchronization=POINTER_SYNC_POLICY)
        helper_sha = hash_bytes((payload / 'prepare-persistent-update-fixture').read_bytes())
        fixture.review['persistentUpdateFixtureSha256']['helper'] = helper_sha
        command = json.loads((fixture.fixture / 'command.json').read_text())
        command['executableSha256'] = helper_sha; command['argv'][9] = fixture.review['catalogSha256']
        put_json(fixture.fixture / 'command.json', command)
        for number in (1, 2): shutil.copyfile(HERE / ('input-boot%d.json' % number), fixture.inputs / ('input-boot%d.json' % number))
        put_json(fixture.inputs / 'session-review.json', fixture.review)
        checker = load_checker(); signed = checker.verify_fixture_directory(fixture.fixture, fixture.inputs, fixture.review)
        bundle = source / 'bundle'; bundle.mkdir()
        (bundle / 'vmlinuz').write_bytes(b'synthetic kernel never booted')
        (bundle / 'initramfs.cpio.gz').write_bytes(b'synthetic initramfs never booted')
        manifest = {'reviewSha256': hash_bytes((fixture.inputs / 'session-review.json').read_bytes()),
            'sha256': {name: hash_bytes((bundle / name).read_bytes()) for name in ('vmlinuz', 'initramfs.cpio.gz')},
            'payloadSha256': {p.relative_to(payload).as_posix(): hash_bytes(p.read_bytes()) for p in payload.rglob('*') if p.is_file()},
            'payloadProfile': 'persistent-update', 'sessionReview': fixture.review, 'instrumentedQualification': True,
            'diagnosticOnly': False, 'diagnosticProfile': 'input-pointer-ready-v1'}
        put_json(bundle / 'bundle.json', manifest); identity = hash_bytes((bundle / 'bundle.json').read_bytes())
        pair = source / 'pair'; pair.mkdir()
        for number in (1, 2):
            boot = RawBoot(source, number, fixture, signed); boot.report['bundleIdentity'] = identity
            if number == 2:
                def change(value):
                    if isinstance(value, dict):
                        for key in list(value):
                            if key == 'previousMarker': value[key] = {'bootId': 'boot', 'counter': 1}; continue
                            if key == 'bootId' and value[key] == 'boot': value[key] = 'cold'
                            if key == 'sessionId' and value[key] == 'session': value[key] = 'restart'
                            change(value[key])
                    elif isinstance(value, list):
                        for item in value: change(item)
                change(boot.events); change(boot.progress); change(boot.report)
                def replace(data): return data.replace(b'"bootId":"boot"', b'"bootId":"cold"').replace(b'"sessionId":"session"', b'"sessionId":"restart"')
                stderr = replace(bytes(boot.stderr))
                for event in boot.events:
                    if event['type'] == 'desktop-persistent-receipt': event['rawBase64'] = base64.b64encode(replace(base64.b64decode(event['rawBase64']))).decode()
                    elif event['type'] == 'log' and event['name'] == 'desktop-wayland.stderr.log': event['data'] = base64.b64encode(stderr).decode()
                    elif event['type'] == 'desktop-diagnostic-stderr': event['sha256'] = hash_bytes(stderr)
            boot.flush(); boot.run.rename(pair / ('boot-%d' % number))
            shutil.copyfile(HERE / ('input-boot%d.json' % number), pair / ('boot-%d.input.json' % number))
        put_json(pair / 'result.json', {'status': 'same-bundle-disk-cold-boot-verified'})
        for name in ('fixture', 'inputs', 'bundle', 'pair'): shutil.copytree(source / name, self.export / name)
        self.seal()

    def test_complete_synthetic_pair_replays_without_original_checkout_or_cwd(self):
        self.add_pair(); result_path = self.other / 'result.json'
        result = self.command('--pair', str(self.export), '--output', str(result_path))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        value = json.loads(result_path.read_text())
        self.assertEqual(value['status'], 'actual-persistent-update-cold-pair-evidence-verified')
        self.assertEqual([boot['pointerHandshakes'] for boot in value['boots']], [5, 2])
        self.assertIs(value['finalSystemAccepted'], False)
        self.assertEqual(value['exportManifestSha256'], hash_bytes((self.export / 'file-sha256.json').read_bytes()))
        self.assertFalse(list(self.export.rglob('__pycache__')))


if __name__ == '__main__':
    unittest.main()
