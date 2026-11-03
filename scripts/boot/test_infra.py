import copy
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock
from pathlib import Path
from infra import contained, regular_file, validate_state, state_ready, state_input_ready, input_events, classify_guest, validate_cold_pair, InputPlan

class InfraTests(unittest.TestCase):
    def persistent_helper_receipt(self, initialize=False):
        apps = [{'app': 1, 'packageId': 'dev.ming.text-entry-demo', 'version': '0.1.0', 'fingerprint': 'a' * 64,
                 'manifestPath': 'packages/initial/app.json', 'manifestSha256': 'b' * 64,
                 'wasmPath': 'packages/initial/main.wasm', 'wasmSha256': 'c' * 64}]
        if not initialize:
            apps.insert(0, {'app': 2, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'fingerprint': 'd' * 64,
                            'manifestPath': 'packages/new/app.json', 'manifestSha256': 'e' * 64,
                            'wasmPath': 'packages/new/main.wasm', 'wasmSha256': 'f' * 64})
        for app in apps:
            directory = 'packages/' + app['packageId'].encode().hex() + '/' + app['version'] + '-' + app['fingerprint']
            app['manifestPath'], app['wasmPath'] = directory + '/app.json', directory + '/main.wasm'
        content = {'catalogSha256': '1' * 64, 'registrySha256': '2' * 64, 'trustSha256': '3' * 64,
                   'bootstrapSha256': '4' * 64, 'apps': apps, 'files': sorted([
                       {'path': path, 'sha256': digest} for path, digest in (
                           ('catalog.json', '1' * 64), ('installed-packages-v1', '2' * 64),
                           ('trusted-publishers-v1', '3' * 64), ('desktop-install-state-v1.json', '4' * 64))] + [
                       {'path': app[field + 'Path'], 'sha256': app[field + 'Sha256']}
                       for app in apps for field in ('manifest', 'wasm')], key=lambda row: row['path'])}
        return {'schemaVersion': 1, 'status': 'initialized-first' if initialize else 'opened-existing', 'content': content,
                'actions': {name: int(initialize) for name in ('initializedRoots', 'packagesInstalled',
                    'trustStoresProvisioned', 'catalogsCreated', 'sourcesCreated')}}

    def test_persistent_helper_snapshot_strict_schema_counts_paths_and_content(self):
        import guest
        for initialize in (True, False):
            receipt = self.persistent_helper_receipt(initialize)
            context = {'schemaVersion': 1, 'bootId': 'boot-one', 'bootNumber': 1, 'role': 'initialize-first',
                       'marker': {'bootId': 'boot-one', 'counter': 1}, 'previousMarker': None, 'diskUuid': 'disk',
                       'registryRoot': '/data/mingos/install-root', 'catalogPath': '/data/mingos/install-root/catalog.json'}
            phase = 'pre-desktop' if initialize else 'post-desktop'
            snapshot = guest.persistent_snapshot(receipt, context, phase, None if initialize else 'session-one')
            self.assertEqual(snapshot['content'], receipt['content'])
            self.assertEqual(snapshot['envelope']['status'], receipt['status'])
            self.assertEqual(snapshot['envelope']['phase'], phase)
            self.assertEqual(snapshot['envelope']['actions']['packagesInstalled'], int(initialize))
        original = self.persistent_helper_receipt()
        changes = [lambda r: r.update(extra=True), lambda r: r['content'].update(extra=True),
                   lambda r: r['actions'].update(packagesInstalled=1), lambda r: r['actions'].update(catalogsCreated=False),
                   lambda r: r['content']['apps'][0].update(extra=1),
                   lambda r: r['content']['apps'][0].update(manifestPath='../outside/app.json'),
                   lambda r: r['content']['apps'][0].update(fingerprint='G' * 64),
                   lambda r: r['content']['apps'][0].update(app=True),
                   lambda r: r['content']['apps'].reverse(), lambda r: r['content']['files'].reverse(),
                   lambda r: r['content']['files'].pop(),
                   lambda r: r['content']['files'].append({'path': 'desktop-install-pending-v1.json', 'sha256': 'a' * 64})]
        for change in changes:
            changed = copy.deepcopy(original); change(changed)
            with self.subTest(change=change), self.assertRaises(ValueError):
                guest.persistent_snapshot(changed, context, 'post-desktop', 'session-one')

    def test_persistent_snapshot_rejects_noncanonical_unknown_and_unbounded_files(self):
        import guest
        context = {'bootNumber': 2, 'role': 'open-existing'}
        original = self.persistent_helper_receipt()
        drift = copy.deepcopy(original)
        app = drift['content']['apps'][0]
        old_path = app['manifestPath']; app['manifestPath'] = 'packages/initial/app.json'
        for item in drift['content']['files']:
            if item['path'] == old_path: item['path'] = app['manifestPath']
        drift['content']['files'].sort(key=lambda row: row['path'])
        unknown = copy.deepcopy(original)
        unknown['content']['files'].append({'path': 'unapproved.txt', 'sha256': 'a' * 64})
        unknown['content']['files'].sort(key=lambda row: row['path'])
        history = copy.deepcopy(original)
        for number in range(124):
            directory = 'packages/' + b'dev.ming.history'.hex() + '/2.0.' + str(number) + '-' + 'e' * 64
            for name in ('app.json', 'main.wasm'):
                history['content']['files'].append({'path': directory + '/' + name, 'sha256': 'a' * 64})
        history['content']['files'].sort(key=lambda row: row['path'])
        self.assertEqual(len(history['content']['files']), 256)
        guest.persistent_snapshot(history, context, 'pre-desktop', None)
        oversized = copy.deepcopy(history)
        directory = 'packages/' + b'dev.ming.history'.hex() + '/3.0.0-' + 'e' * 64
        oversized['content']['files'] += [{'path': directory + '/' + name, 'sha256': 'a' * 64}
                                         for name in ('app.json', 'main.wasm')]
        oversized['content']['files'].sort(key=lambda row: row['path'])
        incomplete = copy.deepcopy(history)
        missing = 'packages/' + b'dev.ming.history'.hex() + '/2.0.0-' + 'e' * 64 + '/main.wasm'
        incomplete['content']['files'] = [item for item in incomplete['content']['files'] if item['path'] != missing]
        for name, receipt in (('active-path', drift), ('unknown-file', unknown),
                              ('over-256', oversized), ('incomplete-history', incomplete)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                guest.persistent_snapshot(receipt, context, 'pre-desktop', None)

    def test_persistent_receipt_read_rejects_duplicates_oversize_and_unsafe_metadata(self):
        import guest
        import os
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / 'receipt.json'; path.write_text(json.dumps(self.persistent_helper_receipt()))
            path.chmod(0o600)
            self.assertEqual(guest.read_persistent_receipt(path)['status'], 'opened-existing')
            original = path.read_bytes()
            for raw in (b'{"schemaVersion":1,"schemaVersion":1}', b' ' * (262144 + 1)):
                path.write_bytes(raw)
                with self.assertRaises(ValueError): guest.read_persistent_receipt(path)
            path.write_bytes(original); path.chmod(0o644)
            with self.assertRaises(ValueError): guest.read_persistent_receipt(path)
            path.chmod(0o600)
            alias = Path(work) / 'alias'; os.link(path, alias)
            with self.assertRaises(ValueError): guest.read_persistent_receipt(path)
            alias.unlink(); alias.symlink_to(path)
            with self.assertRaises((ValueError, OSError)): guest.read_persistent_receipt(alias)

    def test_persistent_helper_replaces_old_evidence_and_binds_stdout_to_file(self):
        import guest
        import contextlib
        import hashlib
        import io
        import subprocess
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); helper = root / 'helper'; helper.write_bytes(b'synthetic-helper')
            output = root / 'receipt.json'; output.write_text('stale receipt must be removed')
            session = self.persistent_session()
            session['persistentInstallFixtureSha256'] = {'helper': hashlib.sha256(helper.read_bytes()).hexdigest()}
            context = {'schemaVersion': 1, 'bootId': 'two', 'bootNumber': 2, 'role': 'open-existing',
                       'marker': {'bootId': 'two', 'counter': 2}, 'previousMarker': {'bootId': 'one', 'counter': 1},
                       'diskUuid': 'disk', 'registryRoot': '/data/mingos/install-root',
                       'catalogPath': '/data/mingos/install-root/catalog.json'}
            mapping = {'/opt/mingos/prepare-persistent-install-fixture': helper,
                       '/run/evidence/persistent-install-fixture.json': output}
            receipt = self.persistent_helper_receipt()
            for changed_stdout in (False, True):
                def invoke(argv, **kwargs):
                    self.assertEqual(argv, ['/opt/mingos/prepare-persistent-install-fixture', 'open-existing',
                                            '/data/mingos/install-root', '/run/evidence/persistent-install-fixture.json'])
                    self.assertFalse(output.exists(), 'stale receipt survived before helper execution')
                    output.write_text(json.dumps(receipt)); output.chmod(0o600)
                    stdout = copy.deepcopy(receipt)
                    if changed_stdout: stdout['actions']['packagesInstalled'] = 1
                    return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(stdout) + '\n', stderr='')
                lines = io.StringIO()
                with mock.patch.object(guest, 'Path', side_effect=lambda value: mapping[value]), \
                     mock.patch.object(guest.subprocess, 'run', side_effect=invoke), contextlib.redirect_stdout(lines):
                    if changed_stdout:
                        with self.assertRaises(ValueError): guest.prepare_persistent_snapshot(session, context, 'pre-desktop')
                    else:
                        snapshot = guest.prepare_persistent_snapshot(session, context, 'pre-desktop')
                        self.assertEqual(snapshot['envelope']['bootNumber'], 2)
                raw = json.loads(lines.getvalue().removeprefix('MINGOS_EVIDENCE ').strip())
                self.assertEqual(raw['type'], 'persistent-install-state')
                self.assertEqual(raw['receipt'] is None, changed_stdout)

    def persistent_session(self):
        return {'profile': 'persistent-install', 'persistentInstallState': '/data/mingos/install-root',
                'catalogPath': '/data/mingos/install-root/catalog.json',
                'catalogSha256': '9' * 64,
                'extraArgs': ['--duration-ms', '0', '--isolation', 'strict', '--data-root', '/data/mingos',
                              '--state-file', '/run/evidence/desktop-state.json'],
                'installSource': '/opt/mingos/install-source/app.json',
                'persistentInstallFixture': {
                    'helper': '/opt/mingos/prepare-persistent-install-fixture',
                    'installRoot': '/data/mingos/install-root',
                    'initialWasm': '/opt/mingos/packages/initial/main.wasm',
                    'sourceWasm': '/opt/mingos/packages/new/main.wasm',
                    'sourceManifest': '/opt/mingos/install-source/app.json',
                    'receipt': '/run/evidence/persistent-install-fixture.json'}}

    def test_persistent_guest_role_requires_mounted_identity_and_exact_marker_sequence(self):
        import guest
        session = self.persistent_session()
        first = SimpleNamespace(mounted=True, disk_uuid='disk-uuid', boot_id='boot-one', previous=None,
                                marker={'bootId': 'boot-one', 'counter': 1})
        first_line = 'rdinit=/init mingos.persistent_install_boot=1 mingos.persistent_install_role=initialize-first'
        context = guest.persistent_context(session, first, first_line)
        self.assertEqual((context['bootNumber'], context['role'], context['marker']['counter']), (1, 'initialize-first', 1))
        second = SimpleNamespace(mounted=True, disk_uuid='disk-uuid', boot_id='boot-two', previous=first.marker,
                                 marker={'bootId': 'boot-two', 'counter': 2})
        second_line = 'rdinit=/init mingos.persistent_install_boot=2 mingos.persistent_install_role=open-existing'
        self.assertEqual(guest.persistent_context(session, second, second_line)['previousMarker'], first.marker)
        for line in (first_line.replace('boot=1', 'boot=2'), first_line.replace('initialize-first', 'open-existing'),
                     first_line + ' mingos.persistent_install_boot=1', first_line.replace('boot=1', 'boot=01'), ''):
            with self.subTest(line=line), self.assertRaises(ValueError): guest.persistent_context(session, first, line)
        for change in ({'mounted': False}, {'disk_uuid': None}, {'previous': first.marker},
                       {'marker': {'bootId': 'other', 'counter': 1}}, {'marker': {'bootId': 'boot-one', 'counter': True}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                guest.persistent_context(session, SimpleNamespace(**dict(vars(first), **change)), first_line)

    def test_persistent_guest_second_boot_never_passes_seed_source_or_legacy_events(self):
        import guest
        session = self.persistent_session()
        first, second = {'bootNumber': 1, 'role': 'initialize-first'}, {'bootNumber': 2, 'role': 'open-existing'}
        self.assertEqual(guest.persistent_helper_argv(session, first, 'pre-desktop'), [
            '/opt/mingos/prepare-persistent-install-fixture', 'initialize-first', '/data/mingos/install-root',
            '/opt/mingos/packages/initial/main.wasm', '/opt/mingos/packages/new/main.wasm',
            '/opt/mingos/install-source/app.json', '/opt/mingos/catalog.json', '9' * 64,
            '/run/evidence/persistent-install-fixture.json'])
        for context, phase in ((first, 'post-desktop'), (second, 'pre-desktop'), (second, 'post-desktop')):
            self.assertEqual(guest.persistent_helper_argv(session, context, phase), [
                '/opt/mingos/prepare-persistent-install-fixture', 'open-existing', '/data/mingos/install-root',
                '/run/evidence/persistent-install-fixture.json'])
        first_argv = guest.desktop_argv(session, first)
        self.assertEqual(first_argv[:3], ['/opt/mingos/ming-app-host', 'desktop', '/data/mingos/install-root/catalog.json'])
        self.assertEqual(first_argv[-4:], ['--persistent-install-state', '/data/mingos/install-root',
                                         '--install-source', '/opt/mingos/install-source/app.json'])
        second_argv = guest.desktop_argv(session, second)
        self.assertEqual(second_argv[-2:], ['--persistent-install-state', '/data/mingos/install-root'])
        self.assertNotIn('--events', second_argv); self.assertNotIn('--install-root', second_argv)
        self.assertNotIn('--install-source', second_argv)
        for field in ('installFixture', 'updateFixture', 'installEvents', 'installRoot'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                guest.desktop_argv(dict(session, **{field: {}}), second)

    def test_paths_reject_escape_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("../disk", "/dev/sda", "C:/disk", "a\\b", "a,b"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    contained(root, name)
            (root / "plain").write_bytes(b"data")
            regular_file(root / "plain")
            try:
                (root / "link").symlink_to(root / "plain")
            except OSError:
                return
            with self.assertRaises(ValueError):
                regular_file(root / "link")

    def state(self):
        return {"schemaVersion": 1, "bootId": "boot-1", "sessionId": "session-1", "revision": 3,
                "phase": "ready", "root": {"width": 1280, "height": 800, "presentedSequence": 9},
                "apps": [{"app": 1, "generation": 1, "workerPid": 20, "state": "ready",
                          "sourceSequence": 5, "presentedSourceSequence": 5, "inputReady": True}], "error": None}

    def test_state_binds_boot_session_revision(self):
        s = self.state()
        validate_state(s, "boot-1", "session-1", 2)
        for boot, session, rev in (("old", "session-1", 2), ("boot-1", "old", 2), ("boot-1", "session-1", 4)):
            with self.subTest(boot=boot, session=session, rev=rev), self.assertRaises(ValueError):
                validate_state(s, boot, session, rev)

    def test_initial_null_root_is_valid_but_cannot_enable_input(self):
        initial=self.state();initial['phase']='starting';initial['root']=None
        try: validate_state(initial,'boot-1')
        except ValueError as error: self.fail('host initial null root must be accepted: '+str(error))
        self.assertFalse(state_ready(initial,[1]))
        self.assertFalse(state_input_ready(initial,[1]))
        initial['phase']='ready'
        with self.assertRaises(ValueError): validate_state(initial,'boot-1')

    def test_actual_host_running_closing_minimized_states_are_supported(self):
        state=self.state()
        for status in ('starting','running','closing','minimized','closed','failed'):
            state['apps'][0]['state']=status
            try: validate_state(state,'boot-1')
            except ValueError as error:self.fail('actual host status rejected '+status+': '+str(error))
            self.assertEqual(state_ready(state,[1]),status=='running')
        state['apps'][0]['state']='running';state['apps'][0]['minimized']=True
        self.assertFalse(state_ready(state,[1]))
        state['apps'][0]['state']='unknown'
        with self.assertRaises(ValueError):validate_state(state,'boot-1')

    def test_ready_requires_presented_root_and_source(self):
        s = self.state()
        self.assertTrue(state_ready(s, [1]))
        self.assertFalse(state_ready(s, [1, 2]))
        s["apps"][0]["presentedSourceSequence"] = 0
        self.assertFalse(state_ready(s, [1]))
        s = self.state(); s["apps"][0]["inputReady"] = False
        self.assertFalse(state_ready(s, [1]))
        s = self.state(); s["apps"][0]["minimized"] = True
        self.assertFalse(state_ready(s, [1]))
        s = self.state(); s["root"]["presentedSequence"] = 0
        self.assertFalse(state_ready(s, [1]))

    def test_state_input_ready_allows_prompt_phase_for_required_apps_only(self):
        state = self.state()
        state["phase"] = "starting"
        state["apps"][0].update(state="closed", inputReady=False, presentedSourceSequence=5)
        state["apps"].append({"app": 2, "generation": 1, "workerPid": 21, "state": "running",
                               "sourceSequence": 5, "presentedSourceSequence": 5, "inputReady": True})
        self.assertTrue(state_input_ready(state, [2]))
        self.assertFalse(state_input_ready(state, [1]))
        state["apps"][1]["inputReady"] = False
        self.assertFalse(state_input_ready(state, [2]))

    def test_state_input_ready_allows_active_prompt_without_closed_app(self):
        state = self.state()
        state["phase"] = "starting"
        state["root"]["storagePrompt"] = {
            "mode": 16, "packageId": "dev.ming.install-demo", "version": "1.0.0", "hasAllow": True,
        }
        self.assertTrue(state_input_ready(state, [1]))

    def test_state_input_ready_gates_current_prompt_only_when_new_field_is_present(self):
        for phase in ('starting', 'ready'):
            with self.subTest(phase=phase):
                state = self.state()
                state['phase'] = phase
                state['root']['storagePrompt'] = {
                    'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True,
                }
                self.assertTrue(state_input_ready(state, [1]), 'legacy prompt state remains compatible')
                for sequence in (None, 0, -1, 8, 10, True, False, '9', 9.0):
                    with self.subTest(sequence=sequence):
                        state['root']['storagePromptPresentedSequence'] = sequence
                        self.assertFalse(state_input_ready(state, [1]))
                state['root']['storagePromptPresentedSequence'] = 9
                self.assertTrue(state_input_ready(state, [1]))
        state = self.state()
        state['root'].update(storagePrompt=None, storagePromptPresentedSequence=None)
        self.assertTrue(state_input_ready(state, [1]), 'no prompt must not block ordinary input')

    def test_input_plan_defers_prompt_click_until_current_prompt_frame_is_presented(self):
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            plan = InputPlan({'schemaVersion': 1, 'requireApps': [1], 'steps': [{
                'name': 'confirm-install', 'events': [
                    {'kind': 'button', 'button': 'left', 'down': True},
                    {'kind': 'button', 'button': 'left', 'down': False},
                ], 'expect': {'storagePrompt': None},
            }]}, Path(work) / 'inputs.jsonl', 10)
            state = self.state()
            state['root']['storagePrompt'] = {
                'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True,
            }
            state['root']['storagePromptPresentedSequence'] = None
            qmp = Transport()
            plan.advance(state, qmp)
            self.assertEqual(qmp.calls, [], 'do not send confirm buttons before prompt presentation')
            state['root']['storagePromptPresentedSequence'] = 9
            plan.advance(state, qmp)
            self.assertEqual([command for command, _ in qmp.calls], ['input-send-event', 'input-send-event'])

    def test_state_prompt_presentation_sequence_is_optional_and_bound_to_current_root(self):
        state = self.state()
        state['root']['storagePrompt'] = {
            'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True,
        }
        # Legacy states remain valid; an unpresented current prompt uses null.
        validate_state(state, 'boot-1')
        state['root']['storagePromptPresentedSequence'] = None
        validate_state(state, 'boot-1')
        state['root']['storagePromptPresentedSequence'] = 9
        validate_state(state, 'boot-1')
        for value in (True, False, 0, -1, 8, 10, '9', 9.0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                invalid = self.state()
                invalid['root'].update(storagePrompt=state['root']['storagePrompt'],
                                       storagePromptPresentedSequence=value)
                validate_state(invalid, 'boot-1')
        state['root']['storagePrompt'] = None
        with self.assertRaises(ValueError):
            validate_state(state, 'boot-1')
        state['root']['storagePromptPresentedSequence'] = None
        validate_state(state, 'boot-1')

    def test_input_prompt_presentation_gate_waits_for_current_prompt_frame(self):
        class Transport:
            def execute(self, command, args): return {}
        prompt = {'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True}
        spec = {'schemaVersion': 1, 'requireApps': [1], 'steps': [{
            'name': 'native-install', 'events': [{'kind': 'key', 'qcode': 'a', 'down': False}],
            'expect': {'storagePrompt': prompt, 'storagePromptPresented': True},
        }]}
        with tempfile.TemporaryDirectory() as work:
            try:
                plan = InputPlan(spec, Path(work) / 'inputs.jsonl', 10)
            except ValueError as error:
                self.fail('explicit storagePromptPresented expectation must be supported: ' + str(error))
            plan.advance(self.state(), Transport())
            for sequence in ('missing', None, 9, 11, True):
                state = self.state()
                state['revision'] = 4
                state['phase'] = 'starting'
                state['root'].update(presentedSequence=10, storagePrompt=prompt)
                if sequence != 'missing':
                    state['root']['storagePromptPresentedSequence'] = sequence
                plan.advance(state, Transport())
                self.assertFalse(plan.complete, 'must reject missing, stale, future, or noninteger evidence')
            state['root']['storagePromptPresentedSequence'] = 10
            plan.advance(state, Transport())
            self.assertTrue(plan.complete)

    def test_input_prompt_presentation_gate_requires_prompt_and_post_dispatch_frame(self):
        class Transport:
            def execute(self, command, args): return {}
        prompt = {'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True}
        spec = {'schemaVersion': 1, 'requireApps': [1], 'steps': [{
            'name': 'wait-presented', 'events': [{'kind': 'key', 'qcode': 'a', 'down': False}],
            'expect': {'storagePromptPresented': True},
        }]}
        with tempfile.TemporaryDirectory() as work:
            try:
                plan = InputPlan(spec, Path(work) / 'inputs.jsonl', 10)
            except ValueError as error:
                self.fail('explicit storagePromptPresented expectation must be supported: ' + str(error))
            before = self.state()
            before['root'].update(storagePrompt=prompt, storagePromptPresentedSequence=9)
            plan.advance(before, Transport())
            same_frame = self.state()
            same_frame['revision'] = 4
            same_frame['root'].update(storagePrompt=prompt, storagePromptPresentedSequence=9)
            plan.advance(same_frame, Transport())
            self.assertFalse(plan.complete)
            no_prompt = self.state()
            no_prompt['revision'] = 4
            no_prompt['root'].update(presentedSequence=10, storagePromptPresentedSequence=10)
            plan.advance(no_prompt, Transport())
            self.assertFalse(plan.complete)
            no_prompt['root']['storagePrompt'] = prompt
            plan.advance(no_prompt, Transport())
            self.assertTrue(plan.complete)

    def test_input_prompt_presentation_expectation_requires_boolean(self):
        with tempfile.TemporaryDirectory() as work:
            for value in (None, 1, 'true', {}, []):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    InputPlan({'schemaVersion': 1, 'requireApps': [1], 'steps': [{
                        'name': 'invalid', 'events': [{'kind': 'key', 'qcode': 'a', 'down': False}],
                        'expect': {'storagePromptPresented': value},
                    }]}, Path(work) / 'inputs.jsonl', 10)

    def test_install_pointer_receipt_does_not_replace_prompt_presentation(self):
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args=None):
                self.calls.append((command, args))
                return [] if command == 'query-mice' else {}
            def batches(self):
                return [args['events'] for command, args in self.calls if command == 'input-send-event']
        prompt = {'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True}
        spec = {'schemaVersion': 1, 'requireApps': [1], 'steps': [
            {'name': 'native-install', 'events': [
                {'kind': 'pointer', 'x': 1140, 'y': 12},
                {'kind': 'button', 'button': 'left', 'down': True},
                {'kind': 'button', 'button': 'left', 'down': False},
            ], 'expect': {'storagePrompt': prompt, 'storagePromptPresented': True}},
            {'name': 'allow-install', 'events': [
                {'kind': 'pointer', 'x': 880, 'y': 478},
                {'kind': 'button', 'button': 'left', 'down': True},
                {'kind': 'button', 'button': 'left', 'down': False},
            ], 'expect': {'storagePrompt': None}},
        ]}
        with tempfile.TemporaryDirectory() as work:
            plan = InputPlan(spec, Path(work) / 'inputs.jsonl', 10,
                             split_transactions=True, pointer_handshake=True)
            qmp = Transport()
            before = self.state()
            context = {'desktopPid': 66, 'logOffset': 100}
            plan.advance(before, qmp, context)
            self.assertEqual([len(batch) for batch in qmp.batches()], [2])
            plan.advance(before, qmp, context)
            self.assertEqual(len(qmp.batches()), 1, 'buttons require a client receipt after ABS')
            receipt = {'bootId': 'boot-1', 'sessionId': 'session-1', 'desktopPid': 66,
                       'entered': True, 'x': 1140.1, 'y': 12.1, 'pointer': 36,
                       'surface': 15, 'sequence': 1, 'logOffset': 101}
            context['receipt'] = receipt
            plan.advance(before, qmp, context)
            self.assertEqual(qmp.batches()[1:], [
                [{'type': 'btn', 'data': {'down': True, 'button': 'left'}}],
                [{'type': 'btn', 'data': {'down': False, 'button': 'left'}}],
            ])
            pending = copy.deepcopy(before)
            pending['revision'] += 1
            pending['phase'] = 'starting'
            pending['root'].update(presentedSequence=10, storagePrompt=prompt,
                                   storagePromptPresentedSequence=None)
            plan.advance(pending, qmp, context)
            plan.advance(pending, qmp, context)
            self.assertEqual(plan.index, 0, 'root advance and client input receipt cannot prove prompt presentation')
            self.assertEqual(len(qmp.batches()), 3, 'ALLOW must not start while the prompt is unpresented')
            presented = copy.deepcopy(pending)
            presented['revision'] += 1
            presented['root']['storagePromptPresentedSequence'] = 10
            plan.advance(presented, qmp, context)
            self.assertEqual(plan.index, 1)
            plan.advance(presented, qmp, context)
            self.assertEqual([len(batch) for batch in qmp.batches()], [2, 1, 1, 2])

    def test_prompt_invalidated_during_pointer_handshake_defers_buttons(self):
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args=None):
                self.calls.append((command, args))
                return [] if command == 'query-mice' else {}
            def batches(self):
                return [args['events'] for command, args in self.calls if command == 'input-send-event']
        spec = {'schemaVersion': 1, 'requireApps': [1], 'steps': [{
            'name': 'allow-install', 'events': [
                {'kind': 'pointer', 'x': 880, 'y': 478},
                {'kind': 'button', 'button': 'left', 'down': True},
                {'kind': 'button', 'button': 'left', 'down': False},
            ], 'expect': {'storagePrompt': None},
        }]}
        with tempfile.TemporaryDirectory() as work:
            plan = InputPlan(spec, Path(work) / 'inputs.jsonl', 10,
                             split_transactions=True, pointer_handshake=True)
            qmp = Transport()
            presented = self.state()
            presented['phase'] = 'starting'
            presented['root'].update(storagePrompt={
                'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True,
            }, storagePromptPresentedSequence=9)
            context = {'desktopPid': 66, 'logOffset': 100}
            plan.advance(presented, qmp, context)
            self.assertEqual([len(batch) for batch in qmp.batches()], [2])
            invalidated = copy.deepcopy(presented)
            invalidated['revision'] += 1
            invalidated['root']['storagePromptPresentedSequence'] = None
            context['receipt'] = {'bootId': 'boot-1', 'sessionId': 'session-1', 'desktopPid': 66,
                                  'entered': True, 'x': 880.1, 'y': 478.1, 'pointer': 36,
                                  'surface': 15, 'sequence': 1, 'logOffset': 101}
            plan.advance(invalidated, qmp, context)
            self.assertEqual(len(qmp.batches()), 1, 'a matching pointer receipt cannot bypass current prompt readiness')
            refreshed = copy.deepcopy(invalidated)
            refreshed['revision'] += 1
            refreshed['root'].update(presentedSequence=10, storagePromptPresentedSequence=10)
            plan.advance(refreshed, qmp, context)
            self.assertEqual([len(batch) for batch in qmp.batches()], [2, 1, 1])
            self.assertFalse(plan.complete, 'dispatch alone cannot prove installation completed')
            dismissed = copy.deepcopy(refreshed)
            dismissed['revision'] += 1
            dismissed['phase'] = 'ready'
            dismissed['root'].update(storagePrompt=None, storagePromptPresentedSequence=None)
            plan.advance(dismissed, qmp, context)
            self.assertTrue(plan.complete)

    def test_input_is_vm_protocol_only_and_checks_geometry(self):
        s = self.state()
        events = input_events([{"kind": "pointer", "x": 1279, "y": 799},
                               {"kind": "key", "qcode": "esc", "down": True}], s)
        self.assertEqual(events[0], {"type": "abs", "data": {"axis": "x", "value": 32767}})
        self.assertEqual(events[1]["data"]["value"], 32767)
        self.assertEqual(events[2]["data"]["key"]["data"], "esc")
        for event in ({"kind": "pointer", "x": 1280, "y": 0},
                      {"kind": "shell", "command": "anything"},
                      {"kind": "key", "qcode": "esc", "down": 1}):
            with self.subTest(event=event), self.assertRaises(ValueError):
                input_events([event], s)

    def test_timeout_and_abnormal_exit_never_classify_normal(self):
        good = [{"type": "guest-result", "status": "normal", "desktopExitCode": 0,
                 "dataSynced": True, "poweroffRequested": True}]
        self.assertEqual(classify_guest(good, False), "normal-desktop-poweroff")
        self.assertEqual(classify_guest(good, True), "timeout-unaccepted")
        bad = [dict(good[0], desktopExitCode=1)]
        self.assertEqual(classify_guest(bad, False), "guest-failed")
        self.assertEqual(classify_guest([], False), "guest-exited-unverified")
        self.assertEqual(classify_guest([{"type":"guest-error"}]+good, False), "guest-failed")

    def test_input_plan_waits_for_input_ready_and_presentation_ack(self):
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            qmp = Transport()
            spec = {"schemaVersion":1,"requireApps":[1],"steps":[{"name":"tap", "events":[{"kind":"key","qcode":"a","down":True},{"kind":"key","qcode":"a","down":False}],"expect":{"rootAdvance":True}}]}
            plan = InputPlan(spec, Path(work)/"inputs.jsonl", 10)
            state = self.state();state["apps"][0]["inputReady"] = False
            plan.advance(state,qmp);self.assertEqual(qmp.calls,[])
            state = self.state();plan.advance(state,qmp);self.assertEqual(len(qmp.calls),1)
            self.assertFalse(plan.complete)
            next_state = self.state();next_state["revision"] += 1
            plan.advance(next_state,qmp);self.assertFalse(plan.complete)
            next_state["root"]["presentedSequence"] += 1
            plan.advance(next_state,qmp);self.assertTrue(plan.complete)
            self.assertEqual(len(qmp.calls),1)
            self.assertEqual(len((Path(work)/"inputs.jsonl").read_text().splitlines()),2)

    def test_confirmed_input_scanout_precedes_next_dispatch(self):
        import json
        from vm import GuestStream
        class Transport:
            def __init__(self): self.calls=[]
            def execute(self, command, args): self.calls.append((command,args)); return {}
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'guest.jsonl'; qmp=Transport()
            steps=[{'name':name,'events':[{'kind':'key','qcode':'a','down':False}], 'expect':{'rootAdvance':True}} for name in ('type','power')]
            plan=InputPlan({'schemaVersion':1,'requireApps':[1],'steps':steps},Path(work)/'inputs.jsonl',10)
            stream=GuestStream(path,plan)
            first=self.state();second=self.state();second['revision']+=1;second['root']['presentedSequence']+=1
            third=dict(second,revision=second['revision']+1)
            records=[{'type':'guest-start','bootId':'boot-1'}]+[{'type':'desktop-state','state':state} for state in (first,second,third)]
            path.write_text(''.join(json.dumps(record)+'\n' for record in records))
            stream.poll(qmp)
            self.assertEqual([command for command,_ in qmp.calls],['input-send-event','screendump','input-send-event'])

    def test_diagnostic_split_preserves_events_with_separate_button_transactions(self):
        class Transport:
            def __init__(self):self.calls=[]
            def execute(self,command,args=None):self.calls.append((command,args));return [{'name':'virtual','current':True}] if command=='query-mice' else {}
        with tempfile.TemporaryDirectory() as work:
            events=[{'kind':'pointer','x':120,'y':200},{'kind':'button','button':'left','down':True},{'kind':'button','button':'left','down':False}]
            spec={'schemaVersion':1,'requireApps':[1],'steps':[{'name':'same-click','events':events,'expect':{'sourceAdvance':[1]}}]}
            try:plan=InputPlan(spec,Path(work)/'inputs.jsonl',10,split_transactions=True)
            except TypeError as error:self.fail('diagnostic split missing: '+str(error))
            qmp=Transport();plan.advance(self.state(),qmp)
            self.assertEqual(qmp.calls[0][0],'query-mice')
            batches=[args['events'] for command,args in qmp.calls if command=='input-send-event']
            self.assertEqual([len(batch) for batch in batches],[2,1,1])
            self.assertEqual([event for batch in batches for event in batch],input_events(events,self.state()))
            self.assertFalse(plan.complete)

    def test_pointer_steps_split_transactions_without_diagnostic_mode(self):
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args=None): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            events = [{"kind": "pointer", "x": 120, "y": 200},
                      {"kind": "button", "button": "left", "down": True},
                      {"kind": "button", "button": "left", "down": False}]
            spec = {"schemaVersion": 1, "requireApps": [1], "steps": [{"name": "same-click", "events": events}]}
            plan = InputPlan(spec, Path(work) / "inputs.jsonl", 10)
            qmp = Transport(); plan.advance(self.state(), qmp)
            batches = [args["events"] for command, args in qmp.calls if command == "input-send-event"]
            self.assertEqual([len(batch) for batch in batches], [2, 1, 1])

    def test_button_steps_split_down_and_up_transactions(self):
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args=None): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            events = [{"kind": "button", "button": "left", "down": True},
                      {"kind": "button", "button": "left", "down": False}]
            spec = {"schemaVersion": 1, "requireApps": [1], "steps": [{"name": "button-click", "events": events}]}
            plan = InputPlan(spec, Path(work) / "inputs.jsonl", 10)
            qmp = Transport(); plan.advance(self.state(), qmp)
            batches = [args["events"] for command, args in qmp.calls if command == "input-send-event"]
            self.assertEqual([len(batch) for batch in batches], [1, 1])

    def test_virtual_evdev_decoder_has_x86_64_record_boundary(self):
        import importlib.util,struct
        path=Path(__file__).with_name('evdev_observer.py')
        self.assertTrue(path.exists(),'guest virtual evdev observer missing')
        spec=importlib.util.spec_from_file_location('observer_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.EVENT.size,24)
        sample=struct.pack('<qqHHi',3,42,1,272,1)
        self.assertEqual(module.decode(sample)[0],{'seconds':3,'microseconds':42,'type':1,'code':272,'value':1})
        with self.assertRaises(ValueError):module.decode(sample[:-1])

    def test_virtual_evdev_identity_accepts_current_data_disk_generation(self):
        import importlib.util
        path=Path(__file__).with_name('evdev_observer.py')
        spec=importlib.util.spec_from_file_location('observer_identity_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertTrue(module.task_owned_cmdline('console=ttyS0 rdinit=/init mingos.data_uuid=b4ee0135-c979-4bb9-aace-2b7aae417a5f mingos.data_serial=MINGDATAb4ee0135c979'))
        self.assertTrue(module.task_owned_cmdline('rdinit=/init mingos.data_uuid=6472be15-4656-4898-afac-95c1531a8a72 mingos.data_serial=MINGDATA6472be154656'))
        self.assertFalse(module.task_owned_cmdline('console=ttyS0 rdinit=/init'))

    def test_virtual_evdev_bound_stops_across_multiple_ready_fds(self):
        import importlib.util,time
        from unittest import mock
        path=Path(__file__).with_name('evdev_observer.py')
        spec=importlib.util.spec_from_file_location('observer_bound_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        event=module.EVENT.pack(1,2,1,272,1)
        records=[];descriptors={10:{'device':'ten'},11:{'device':'eleven'}}
        with mock.patch.object(module.select,'select',return_value=([10,11],[],[])),mock.patch.object(module.os,'read',side_effect=lambda fd,size:event*(128 if fd==10 else 1)):
            count=module.capture(descriptors,records.append,time.monotonic()+5)
        self.assertEqual(count,4096)
        self.assertEqual(len(records),4096)
        self.assertEqual(records[-1]['device'],'ten')

    def test_pointer_barrier_waits_for_matching_client_receipt_without_replaying_abs(self):
        class Transport:
            def __init__(self):self.calls=[]
            def execute(self,command,args=None):self.calls.append((command,args));return [] if command=='query-mice' else {}
        with tempfile.TemporaryDirectory() as work:
            events=[{'kind':'pointer','x':120,'y':200},{'kind':'button','button':'left','down':True},{'kind':'button','button':'left','down':False}]
            spec={'schemaVersion':1,'requireApps':[1],'steps':[{'name':'same-click','events':events,'expect':{'sourceAdvance':[1]}}]}
            try:plan=InputPlan(spec,Path(work)/'inputs.jsonl',10,split_transactions=True,pointer_handshake=True)
            except TypeError as error:self.fail('pointer receipt barrier missing: '+str(error))
            qmp=Transport();state=self.state();context={'desktopPid':66,'logOffset':100,'receipt':None};plan.advance(state,qmp,context)
            self.assertEqual(len([x for x in qmp.calls if x[0]=='input-send-event']),1)
            for receipt in (None,{'entered':False,'x':120,'y':200},{'entered':True,'x':0,'y':0}):plan.advance(state,qmp,dict(context,receipt=receipt))
            self.assertEqual(len([x for x in qmp.calls if x[0]=='input-send-event']),1)
            receipt={'bootId':'boot-1','sessionId':'session-1','desktopPid':66,'entered':True,'x':120.078,'y':200.242,'pointer':36,'surface':15,'sequence':1,'logOffset':101}
            for bad in (dict(receipt,logOffset=100),dict(receipt,desktopPid=67),dict(receipt,sessionId='old'),dict(receipt,bootId='old')):plan.advance(state,qmp,dict(context,receipt=bad))
            self.assertEqual(len([x for x in qmp.calls if x[0]=='input-send-event']),1)
            plan.advance(state,qmp,dict(context,receipt=receipt))
            sends=[args['events'] for command,args in qmp.calls if command=='input-send-event']
            self.assertEqual([len(batch) for batch in sends],[2,1,1]);self.assertFalse(plan.complete)
            self.assertEqual([e for batch in sends for e in batch],input_events(events,state))

    def test_guest_stream_binds_current_desktop_receipt_and_completes_click(self):
        import json
        from vm import GuestStream
        class Transport:
            def __init__(self):self.calls=[]
            def execute(self,command,args=None):self.calls.append((command,args));return [] if command=='query-mice' else {}
        with tempfile.TemporaryDirectory() as work:
            root=Path(work);qmp=Transport();events=[{'kind':'pointer','x':120,'y':200},{'kind':'button','button':'left','down':True},{'kind':'button','button':'left','down':False}]
            plan=InputPlan({'schemaVersion':1,'requireApps':[1],'steps':[{'name':'click','events':events,'expect':{'sourceAdvance':[1]}}]},root/'inputs.jsonl',10,split_transactions=True,pointer_handshake=True)
            stream=GuestStream(root/'guest.jsonl',plan);state=self.state();later=self.state();later['revision']+=1;later['apps'][0]['sourceSequence']+=1;later['apps'][0]['presentedSourceSequence']+=1
            rows=[{'type':'guest-start','bootId':'boot-1'},{'type':'process-start','name':'desktop','pid':50},
                {'type':'desktop-state','state':state,'desktopPid':50,'diagnosticStderrOffset':100},
                {'type':'desktop-pointer-receipt','bootId':'boot-1','sessionId':'session-1','desktopPid':50,'receipt':{'pointer':36,'surface':15,'entered':True,'x':120.1,'y':200.2,'sequence':1,'logOffset':101}},
                {'type':'desktop-state','state':later,'desktopPid':50,'diagnosticStderrOffset':101}]
            stream.path.write_text(''.join(json.dumps(row)+'\n' for row in rows));stream.poll(qmp)
            self.assertTrue(plan.complete)
            self.assertEqual([len(args['events']) for command,args in qmp.calls if command=='input-send-event'],[2,1,1])

    def test_guest_stream_requires_fault_receipt_to_match_bundle_configuration(self):
        from vm import GuestStream
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            config = {"schemaVersion": 1, "profile": "strict-worker-sigkill-v1", "app": 2,
                      "generation": 1, "signal": "SIGKILL", "maxWaitSeconds": 60}
            receipt = dict(config, pid=71, cgroup="/mingos/app-2-g-1", revision=7,
                           status="sent", monotonicNs=123456)
            stream = GuestStream(root / "guest.jsonl", None, fault_config=config)
            stream.path.write_text("\n".join(json.dumps(row) for row in (
                {"type": "guest-start", "bootId": "boot-1"},
                {"type": "fault-injection", "receipt": receipt},
            )) + "\n")
            stream.poll(None)
            self.assertEqual(stream.fault_receipt, receipt)
            bad = GuestStream(root / "bad.jsonl", None, fault_config=config)
            bad.path.write_text(json.dumps({"type": "fault-injection", "receipt": dict(receipt, app=1)}) + "\n")
            with self.assertRaisesRegex(ValueError, "fault receipt"):
                bad.poll(None)

    def test_fault_profile_blocks_input_until_receipt_and_failed_worker_state(self):
        from vm import GuestStream
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args=None): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            config = {"schemaVersion": 1, "profile": "strict-worker-sigkill-v1", "app": 2,
                      "generation": 1, "signal": "SIGKILL", "maxWaitSeconds": 60}
            receipt = dict(config, pid=71, cgroup="/mingos/app-2-g-1", revision=7,
                           status="sent", monotonicNs=123456)
            plan = InputPlan({"schemaVersion": 1, "requireApps": [1], "steps": [{
                "name": "dock", "events": [{"kind": "key", "qcode": "a", "down": False}], "expect": {}
            }]}, root / "inputs.jsonl", 10)
            running = self.state()
            failed = self.state(); failed["revision"] += 1
            failed["apps"].append({"app": 2, "generation": 1, "workerPid": 71, "state": "failed",
                                   "sourceSequence": 3, "presentedSourceSequence": 3, "inputReady": False})
            stream = GuestStream(root / "guest.jsonl", plan, fault_config=config)
            stream.path.write_text("\n".join(json.dumps(row) for row in (
                {"type": "guest-start", "bootId": "boot-1"},
                {"type": "desktop-state", "state": running},
                {"type": "fault-injection", "receipt": receipt},
                {"type": "desktop-state", "state": failed},
            )) + "\n")
            qmp = Transport(); stream.poll(qmp)
            self.assertEqual([call[0] for call in qmp.calls], ["input-send-event"])
            self.assertEqual(stream.fault_failure_state, failed)

    def test_fault_profile_pointer_receipt_cannot_bypass_input_gate(self):
        from vm import GuestStream
        class Transport:
            def __init__(self): self.calls = []
            def execute(self, command, args=None): self.calls.append((command, args)); return {}
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            config = {"schemaVersion": 1, "profile": "strict-worker-sigkill-v1", "app": 2,
                      "generation": 1, "signal": "SIGKILL", "maxWaitSeconds": 60}
            plan = InputPlan({"schemaVersion": 1, "requireApps": [1], "steps": [{
                "name": "dock", "events": [{"kind": "pointer", "x": 120, "y": 200}], "expect": {}
            }]}, root / "inputs.jsonl", 10, split_transactions=True, pointer_handshake=True)
            state = self.state()
            stream = GuestStream(root / "guest.jsonl", plan, fault_config=config)
            stream.path.write_text("\n".join(json.dumps(row) for row in (
                {"type": "guest-start", "bootId": "boot-1"},
                {"type": "process-start", "name": "desktop", "pid": 50},
                {"type": "desktop-state", "state": state, "desktopPid": 50, "diagnosticStderrOffset": 100},
                {"type": "desktop-pointer-receipt", "bootId": "boot-1", "sessionId": "session-1", "desktopPid": 50,
                 "receipt": {"pointer": 36, "surface": 15, "entered": True, "x": 120.1, "y": 200.2, "sequence": 1, "logOffset": 101}},
            )) + "\n")
            qmp = Transport(); stream.poll(qmp)
            self.assertEqual(qmp.calls, [])

    def test_fault_profile_requires_valid_receipt_before_normal_result(self):
        from vm import fault_result
        config = {"schemaVersion": 1, "profile": "strict-worker-sigkill-v1", "app": 2,
                  "generation": 1, "signal": "SIGKILL", "maxWaitSeconds": 60}
        receipt = dict(config, pid=71, cgroup="/mingos/app-2-g-1", revision=7,
                       status="sent", monotonicNs=123456)
        failure = self.state(); failure["apps"].append({"app": 2, "generation": 1, "workerPid": 71,
                                                         "state": "failed", "sourceSequence": 3,
                                                         "presentedSourceSequence": 3, "inputReady": False})
        self.assertIsNone(fault_result(None, None))
        self.assertEqual(fault_result(config, receipt, failure), receipt)
        with self.assertRaisesRegex(ValueError, "missing fault receipt"):
            fault_result(config, None, failure)
        with self.assertRaisesRegex(ValueError, "missing failed worker state"):
            fault_result(config, receipt, None)

    def test_cold_boot_checks_identity_disk_and_previous_marker(self):
        first = {"status": "normal-desktop-poweroff", "bootId": "one", "bundleIdentity": "same",
                 "diskUuid": "uuid", "marker": {"bootId": "one", "counter": 1}}
        second = {"status": "normal-desktop-poweroff", "bootId": "two", "bundleIdentity": "same",
                  "diskUuid": "uuid", "previousMarker": first["marker"],
                  "marker": {"bootId": "two", "counter": 2}}
        validate_cold_pair(first, second)
        for change in ({"bootId": "one"}, {"diskUuid": "other"}, {"bundleIdentity": "changed"},
                       {"previousMarker": None}, {"status": "timeout-unaccepted"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_cold_pair(first, dict(second, **change))

if __name__ == "__main__":
    unittest.main()
