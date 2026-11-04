"""Closed data-text package/review boundary tests; synthetic Wasm is not build evidence."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import bundle
from bootlib import INSTALL_LIFECYCLE_EVENTS, INSTALL_LIFECYCLE_INSTALL_ROOT, INSTALL_LIFECYCLE_SOURCE, PERSISTENT_UPDATE_INSTALL_ROOT, checked_session, sha256
from bundle import verify_payload
from wayland_receipt import POINTER_SYNC_POLICY

class ProfileTests(unittest.TestCase):
    def persistent_fixture(self, root):
        (root / 'ming-app-host').write_bytes(b'\x7fELFsynthetic-host')
        helper = root / 'prepare-persistent-install-fixture'
        helper.write_bytes(b'\x7fELFsynthetic-helper')
        for name in ('initial', 'new'):
            package = root / 'packages' / name
            package.mkdir(parents=True)
            (package / 'main.wasm').write_bytes(b'\0asm\x01\0\0\0' + name.encode())
        catalog = {'schemaVersion': 3, 'apps': [
            {'id': 1, 'title': 'Text Lab', 'packageId': 'dev.ming.text-entry-demo'}], 'autostart': [1]}
        self.put(root / 'catalog.json', catalog)
        review = {'reviewed': True, 'reviewer': 'synthetic test only', 'profile': 'persistent-install',
                  'sessionMode': 'long-lived', 'budgetEvidence': 'no VM or production review',
                  'extraArgs': ['--duration-ms', '0', '--isolation', 'strict', '--data-root', '/data/mingos',
                                '--state-file', '/run/evidence/desktop-state.json',
                                '--qualification-receipts', 'persistent-install-pointer-ready-v1'],
                  'persistentInstallState': '/data/mingos/install-root',
                  'catalogPath': '/data/mingos/install-root/catalog.json',
                  'installSource': '/opt/mingos/install-source/app.json',
                  'inputScripts': {'boot1Sha256': '1' * 64, 'boot2Sha256': '2' * 64},
                  'instrumentedQualification': True,
                  'instrumentedProfile': 'persistent-install-pointer-ready-v1',
                  'inputSynchronization': POINTER_SYNC_POLICY,
                  'persistentInstallFixture': {
                      'helper': '/opt/mingos/prepare-persistent-install-fixture',
                      'installRoot': '/data/mingos/install-root',
                      'initialWasm': '/opt/mingos/packages/initial/main.wasm',
                      'sourceWasm': '/opt/mingos/packages/new/main.wasm',
                      'sourceManifest': '/opt/mingos/install-source/app.json',
                      'receipt': '/run/evidence/persistent-install-fixture.json'},
                  'persistentInstallFixtureSha256': {
                      'helper': sha256(helper), 'initialWasm': sha256(root / 'packages/initial/main.wasm'),
                      'sourceWasm': sha256(root / 'packages/new/main.wasm')},
                  'hostSha256': sha256(root / 'ming-app-host'), 'catalogSha256': sha256(root / 'catalog.json')}
        return catalog, review

    def update_fixture(self, root):
        from bootlib import PERSISTENT_UPDATE_ARGS, PERSISTENT_UPDATE_FIXTURE, PERSISTENT_UPDATE_PROFILE
        catalog, review = self.persistent_fixture(root)
        (root / 'prepare-persistent-install-fixture').rename(root / 'prepare-persistent-update-fixture')
        (root / 'packages/initial').rename(root / 'packages/bootstrap')
        (root / 'packages/old').mkdir()
        (root / 'packages/old/main.wasm').write_bytes(b'\0asm\x01\0\0\0old')
        review.pop('persistentInstallFixture'); review.pop('persistentInstallFixtureSha256')
        review.update(profile='persistent-update', extraArgs=PERSISTENT_UPDATE_ARGS.copy(),
                      instrumentedProfile=PERSISTENT_UPDATE_PROFILE,
                      persistentUpdateFixture=PERSISTENT_UPDATE_FIXTURE.copy(),
                      persistentUpdateFixtureSha256={key: sha256(root / PERSISTENT_UPDATE_FIXTURE[key].removeprefix('/opt/mingos/'))
                          for key in ('helper', 'bootstrapWasm', 'oldWasm', 'newWasm')})
        return catalog, review

    def test_persistent_update_freezes_three_modules_helper_and_own_qualification(self):
        from bootlib import qemu_command
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.update_fixture(root)
            self.assertEqual(verify_payload(root, review), catalog)
            command = qemu_command(root, root, data_disk={'imagePath':'disk', 'serial':'disk', 'uuid':'uuid'},
                                   boot_number=2, persistent_profile='persistent-update')
            self.assertIn('mingos.persistent_update_boot=2 mingos.persistent_update_role=open-existing', command[-1])
            self.assertNotIn('mingos.persistent_install_boot', command[-1])
            for key in ('helper', 'bootstrapWasm', 'oldWasm', 'newWasm'):
                file = root / review['persistentUpdateFixture'][key].removeprefix('/opt/mingos/')
                raw = file.read_bytes(); file.write_bytes(raw + b'drift')
                with self.subTest(key=key), self.assertRaises(ValueError): verify_payload(root, review)
                file.write_bytes(raw)
            for change in ({'persistentInstallFixture': {}}, {'persistentUninstallFixture': {}},
                           {'updateFixture': {}}, {'inputScriptSha256':'1'*64},
                           {'instrumentedProfile':'persistent-install-pointer-ready-v1'}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    verify_payload(root, dict(review, **change))

    def uninstall_fixture(self, root):
        catalog, review = self.persistent_fixture(root)
        (root / 'prepare-persistent-install-fixture').rename(root / 'prepare-persistent-uninstall-fixture')
        review['profile'] = 'persistent-uninstall'
        review['extraArgs'][-1] = 'persistent-uninstall-pointer-ready-v1'
        review['instrumentedProfile'] = 'persistent-uninstall-pointer-ready-v1'
        fixture = review.pop('persistentInstallFixture')
        fixture.update(helper='/opt/mingos/prepare-persistent-uninstall-fixture',
                       receipt='/run/evidence/persistent-uninstall-fixture.json')
        review['persistentUninstallFixture'] = fixture
        review['persistentUninstallFixtureSha256'] = review.pop('persistentInstallFixtureSha256')
        return catalog, review

    def test_persistent_uninstall_accepts_only_its_frozen_helper_and_packaged_wasm(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.uninstall_fixture(root)
            try: actual = verify_payload(root, review)
            except ValueError as error: self.fail('frozen uninstall profile rejected: ' + str(error))
            self.assertEqual(actual, catalog)
            self.assertEqual(checked_session(review, root), review)
            self.assertFalse((root / 'install-source').exists())
            self.assertFalse((root / 'install-root').exists())
            for relative in ('prepare-persistent-uninstall-fixture', 'packages/initial/main.wasm', 'packages/new/main.wasm'):
                path = root / relative; raw = path.read_bytes(); path.write_bytes(raw + b'drift')
                with self.subTest(relative=relative), self.assertRaises(ValueError): verify_payload(root, review)
                path.write_bytes(raw)

    def test_persistent_uninstall_rejects_legacy_unknown_fixture_paths_args_and_hash_fallbacks(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); _, review = self.uninstall_fixture(root)
            changes = [
                {'persistentInstallFixture': {}}, {'persistentInstallFixtureSha256': {}},
                {'installFixture': {}}, {'updateFixture': {}}, {'installRoot': '/data/mingos/install-root'},
                {'persistentInstallState': '/opt/mingos/install-root'}, {'catalogPath': '/opt/mingos/catalog.json'},
                {'installSource': '/opt/mingos/other/app.json'}, {'installEvents': '/opt/mingos/events.json'},
                {'inputScriptSha256': '1' * 64}, {'inputScripts': {'boot1Sha256': '1' * 64}},
                {'inputScripts': {'boot1Sha256': '1' * 64, 'boot2Sha256': '1' * 64}},
                {'inputScripts': dict(review['inputScripts'], unknown='3' * 64)},
                {'extraArgs': review['extraArgs'][:-1] + ['persistent-install-pointer-ready-v1']},
                {'extraArgs': review['extraArgs'] + ['--events', '/opt/mingos/events.json']},
                {'extraArgs': review['extraArgs'] + ['--data-root', '/tmp/data']},
                {'instrumentedProfile': 'persistent-install-pointer-ready-v1'}, {'instrumentedQualification': False},
                {'persistentUninstallFixtureSha256': dict(review['persistentUninstallFixtureSha256'], unknown='3' * 64)},
            ]
            for field, value in [('helper', '/opt/mingos/prepare-persistent-install-fixture'),
                                 ('installRoot', '/tmp/install-root'), ('initialWasm', '/opt/mingos/packages/new/main.wasm'),
                                 ('sourceWasm', '/opt/mingos/packages/initial/main.wasm'),
                                 ('sourceManifest', '/opt/mingos/source.json'), ('receipt', '/run/evidence/desktop-state.json'),
                                 ('privatePath', '/arbitrary/private')]:
                changes.append({'persistentUninstallFixture': dict(review['persistentUninstallFixture'], **{field: value})})
            for change in changes:
                with self.subTest(change=change), self.assertRaises(ValueError): verify_payload(root, dict(review, **change))
            (root / 'install-source').mkdir()
            with self.assertRaises(ValueError): verify_payload(root, review)

    def test_persistent_uninstall_catalog_is_strict_and_never_autostarts_app2(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.uninstall_fixture(root)
            for change in (lambda c: c.update(autostart=[1, 2]), lambda c: c.update(autostart=[True]),
                           lambda c: c['apps'][0].update(id=True), lambda c: c.update(schemaVersion=3.0),
                           lambda c: c.update(unknown=True)):
                changed = copy.deepcopy(catalog); change(changed); self.put(root / 'catalog.json', changed)
                review['catalogSha256'] = sha256(root / 'catalog.json')
                with self.subTest(change=change), self.assertRaises(ValueError): verify_payload(root, review)
            (root / 'catalog.json').write_text(json.dumps(catalog).replace('"schemaVersion": 3', '"schemaVersion": 1, "schemaVersion": 3'))
            review['catalogSha256'] = sha256(root / 'catalog.json')
            with self.assertRaises(ValueError): verify_payload(root, review)

    def test_persistent_install_accepts_frozen_helper_without_ram_registry_or_events(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.persistent_fixture(root)
            self.assertEqual(verify_payload(root, review), catalog)
            self.assertFalse((root / 'install-root').exists())
            self.assertFalse((root / 'install-events.json').exists())

    def test_persistent_install_requires_explicit_exact_qualification_receipts(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.persistent_fixture(root)
            self.assertEqual(verify_payload(root, review), catalog)
            for extra in (review['extraArgs'][:-2], review['extraArgs'][:-1] + ['wrong-profile'],
                          review['extraArgs'] + ['--qualification-receipts', 'persistent-install-pointer-ready-v1'],
                          review['extraArgs'][:-2] + ['--qualification-receipts=persistent-install-pointer-ready-v1']):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    verify_payload(root, dict(review, extraArgs=extra))

    def test_persistent_install_rejects_legacy_roots_inputs_and_mutable_fixture(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); _, review = self.persistent_fixture(root)
            for change in ({'persistentInstallState': '/opt/mingos/install-root'},
                           {'catalogPath': '/opt/mingos/catalog.json'}, {'installRoot': '/data/mingos/install-root'},
                           {'installFixture': {}}, {'updateFixture': {}}, {'installEvents': '/opt/mingos/events.json'},
                           {'inputScriptSha256': '1' * 64}, {'inputScripts': {'boot1Sha256': '1' * 64}},
                           {'inputScripts': {'boot1Sha256': '1' * 64, 'boot2Sha256': '1' * 64}},
                           {'extraArgs': review['extraArgs'] + ['--install-root', '/opt/mingos/install-root']},
                           {'instrumentedQualification': False}, {'instrumentedProfile': 'install-lifecycle-pointer-ready-v1'}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    verify_payload(root, dict(review, **change))
            for relative in ('prepare-persistent-install-fixture', 'packages/initial/main.wasm', 'packages/new/main.wasm'):
                path = root / relative; original = path.read_bytes(); path.write_bytes(original + b'drift')
                with self.subTest(relative=relative), self.assertRaises(ValueError): verify_payload(root, review)
                path.write_bytes(original)
            (root / 'install-root').mkdir()
            with self.assertRaises(ValueError): verify_payload(root, review)

    def test_persistent_install_catalog_rejects_duplicate_keys_boolean_ids_and_autostart_app2(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.persistent_fixture(root)
            for change in (lambda c: c.update(autostart=[1, 2]), lambda c: c.update(autostart=[True]),
                           lambda c: c['apps'][0].update(id=True), lambda c: c.update(schemaVersion=3.0)):
                changed = copy.deepcopy(catalog); change(changed); self.put(root / 'catalog.json', changed)
                review['catalogSha256'] = sha256(root / 'catalog.json')
                with self.subTest(change=change), self.assertRaises(ValueError): verify_payload(root, review)
            raw = json.dumps(catalog).replace('"schemaVersion": 3', '"schemaVersion": 1, "schemaVersion": 3')
            (root / 'catalog.json').write_text(raw); review['catalogSha256'] = sha256(root / 'catalog.json')
            with self.assertRaises(ValueError): verify_payload(root, review)

    def test_persistent_profiles_vm_input_has_no_single_hash_or_wrong_boot_fallback(self):
        from bootlib import validate_reviewed_input
        for profile in ('persistent-install', 'persistent-uninstall'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as work:
                first, second = Path(work) / 'first.json', Path(work) / 'second.json'
                first.write_text('{"boot":1}'); second.write_text('{"boot":2}')
                review = {'profile': profile, 'inputScripts': {
                    'boot1Sha256': sha256(first), 'boot2Sha256': sha256(second)}}
                validate_reviewed_input(review, first, boot_number=1)
                validate_reviewed_input(review, second, boot_number=2)
                for path, number in ((first, 2), (second, 1), (first, None), (first, True), (first, 3), (None, 2)):
                    with self.subTest(number=number, path=path), self.assertRaises(ValueError):
                        validate_reviewed_input(review, path, boot_number=number)
                for changed in ({'inputScriptSha256': sha256(first)},
                                {'inputScripts': {'boot1Sha256': sha256(first)}},
                                {'inputScripts': dict(review['inputScripts'], extra='0' * 64)}):
                    with self.subTest(change=changed), self.assertRaises(ValueError):
                        validate_reviewed_input(dict(review, **changed), first, boot_number=1)
                first.write_text('{"boot":2}')
                with self.assertRaises(ValueError): validate_reviewed_input(review, first, boot_number=1)


    def test_reviewed_input_rejects_oversized_bytes_even_with_matching_hash(self):
        from bootlib import validate_reviewed_input
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / 'input.json'
            source.write_bytes(b'{}' + b' ' * (1024 * 1024 - 1))
            digest = sha256(source)
            for review, number in (({'profile': 'data-text', 'inputScriptSha256': digest}, None),
                                   ({'profile': 'persistent-install', 'inputScripts': {
                                       'boot1Sha256': digest, 'boot2Sha256': '0' * 64}}, 1)):
                with self.subTest(profile=review['profile']), self.assertRaises(ValueError):
                    validate_reviewed_input(review, source, boot_number=number)

    def put(self, path, data): path.write_text(json.dumps(data))
    def fixture(self, root):
        (root / 'ming-app-host').write_bytes(b'\x7fELFsynthetic-test-only')
        apps = []
        for number, name in ((1, 'app-data-desktop'), (2, 'text-entry-demo')):
            package = root / 'packages' / str(number); package.mkdir(parents=True)
            manifest = {'schemaVersion':1, 'id':'dev.ming.'+name, 'sdkVersion':6, 'entry':'main.wasm'}
            self.put(package/'app.json', manifest)
            (package/'main.wasm').write_bytes(b'\0asm\x01\0\0\0packaged')
            (package/'compiler.wasm').write_bytes(b'\0asm\x01\0\0\0raw')
            hashes = {'rawSha256':sha256(package/'compiler.wasm'), 'packagedSha256':sha256(package/'main.wasm')}
            receipt = dict(hashes, schemaVersion=1, status='passed', exitCode=0, manifestSha256=sha256(package/'app.json'))
            if number == 1:
                receipt = {'status':'passed','mode':'appdata','stage':'package','compilerExitCode':0,'package':hashes}
            else:
                self.put(package/'build-info.json', hashes)
            self.put(package/'build-receipt.json', receipt)
            app = {'id':number,'title':name,'manifest':'packages/%d/app.json' % number}
            if number == 1: app.update(dataKey='counter-main', slots=[1])
            apps.append(app)
        catalog = {'schemaVersion':1,'apps':apps,'autostart':[1,2]}
        self.put(root/'catalog.json', catalog)
        self.envelope(root)
        review = {'reviewed':True,'reviewer':'main test fixture','profile':'data-text','sessionMode':'long-lived',
                  'extraArgs':['--duration-ms','0','--isolation','strict','--data-root','/data/mingos','--state-file','/run/evidence/desktop-state.json'],
                  'budgetEvidence':'synthetic unit test, no execution'}
        self.bind(root, review)
        return catalog, review

    def envelope(self, root):
        package = root/'packages/1'
        self.put(package/'boot-receipt.json', {'schemaVersion':1,'kind':'derived-evidence-envelope',
            'provenance':'original-receipt-no-recompile','packageId':'dev.ming.app-data-desktop',
            'manifestSha256':sha256(package/'app.json'), 'originalReceiptSha256':sha256(package/'build-receipt.json'),
            'rawSha256':sha256(package/'compiler.wasm'), 'packagedSha256':sha256(package/'main.wasm')})

    def bind(self, root, review):
        review.update(hostSha256=sha256(root/'ming-app-host'), catalogSha256=sha256(root/'catalog.json'),
                      packageEvidenceSha256={'1':sha256(root/'packages/1/boot-receipt.json'),'2':sha256(root/'packages/2/build-receipt.json')})

    def test_data_text_accepts_original_probe_receipt_without_recompile(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.fixture(root)
            before = (root/'packages/1/build-receipt.json').read_bytes()
            try: actual = verify_payload(root, review)
            except Exception as error: self.fail('approved data-text profile not accepted: '+repr(error))
            self.assertEqual(actual, catalog)
            self.assertEqual((root/'packages/1/build-receipt.json').read_bytes(), before)
            self.assertFalse((root/'packages/1/build-info.json').exists())

    def test_legacy_default_desktop_profile_still_accepts_flat_receipts(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.fixture(root)
            for number, name in ((1, 'compose-demo'), (2, 'animated-demo')):
                package=root/'packages'/str(number)
                manifest=json.loads((package/'app.json').read_text()); manifest['id']='dev.ming.'+name
                self.put(package/'app.json', manifest)
                self.put(package/'build-receipt.json', {'status':'passed','manifestSha256':sha256(package/'app.json'),
                    'packagedSha256':sha256(package/'main.wasm'),'rawSha256':sha256(package/'compiler.wasm')})
            review.pop('profile');review['sessionMode']='bounded';review['extraArgs']=['--duration-ms','15000']
            self.bind(root,review)
            self.assertEqual(verify_payload(root,review),catalog)

    def test_profile_cannot_fall_back_or_admit_unreviewed_evidence(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); _, review = self.fixture(root)
            for changed in (dict(review, profile='other'), dict(review, packageEvidenceSha256={}), dict(review, packageEvidenceSha256={'1':'0'*64,'2':'0'*64})):
                with self.subTest(changed=changed), self.assertRaises(ValueError): verify_payload(root, changed)
            with self.assertRaises(Exception): verify_payload(root, {k:v for k,v in review.items() if k!='profile'})

    def test_data_text_vm_input_is_bound_to_review_hash(self):
        import bootlib
        validator=getattr(bootlib,'validate_reviewed_input',None)
        self.assertTrue(callable(validator),'data-text reviewed input SHA gate missing')
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'inputs.json';path.write_text('{}')
            review={'profile':'data-text','inputScriptSha256':sha256(path)}
            validator(review,path)
            for changed,source in (({},path),(review,None),(dict(review,inputScriptSha256='0'*64),path)):
                with self.subTest(changed=changed),self.assertRaises(ValueError):validator(dict(changed,profile='data-text'),source)
            path.write_text('{"changed":true}')
            with self.assertRaises(ValueError):validator(review,path)
            validator({},None)

    def test_bounded_public_probe_does_not_require_input_script(self):
        from bootlib import validate_reviewed_input
        validate_reviewed_input({'profile':'public-probe'}, None)

    def test_update_profile_requires_install_root_on_persistent_data_mount(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / 'ming-app-host').write_bytes(b'host')
            (root / 'catalog.json').write_text('{}')
            review = {
                'reviewed': True,
                'reviewer': 'main test fixture',
                'profile': 'data-text-update',
                'sessionMode': 'long-lived',
                'extraArgs': ['--duration-ms', '0', '--isolation', 'strict', '--data-root', '/data/mingos',
                              '--state-file', '/run/evidence/desktop-state.json'],
                'budgetEvidence': 'synthetic unit test',
                'installRoot': '/data/mingos-install-root',
                'hostSha256': sha256(root / 'ming-app-host'),
                'catalogSha256': sha256(root / 'catalog.json'),
            }
            with self.assertRaises(ValueError):
                checked_session(review, root)
            review['installRoot'] = PERSISTENT_UPDATE_INSTALL_ROOT
            self.assertIs(checked_session(review, root), review)

    def test_install_lifecycle_profile_requires_reviewed_signed_source_contract(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / 'ming-app-host').write_bytes(b'\x7fELFsynthetic-test-only')
            for name in ('initial', 'new'):
                package = root / 'packages' / name
                package.mkdir(parents=True)
                (package / 'main.wasm').write_bytes(b'\0asm\x01\0\0\0fixture-' + name.encode())
            self.put(root / 'catalog.json', {
                'schemaVersion': 3,
                'apps': [{'id': 1, 'title': 'Text Lab', 'packageId': 'dev.ming.text-entry-demo'}],
                'autostart': [1],
            })
            events = {
                'schemaVersion': 1,
                'requireApps': [1],
                'steps': [
                    {'name': 'native-install', 'afterMs': 1000, 'events': [
                        {'kind': 'pointer', 'x': 1140, 'y': 12},
                        {'kind': 'button', 'button': 'left', 'down': True},
                        {'kind': 'button', 'button': 'left', 'down': False},
                    ], 'expect': {'storagePrompt': {
                        'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True,
                    }}},
                    {'name': 'script-launch', 'afterMs': 8000, 'action': {
                        'op': 'launch', 'app': 2,
                    }, 'events': [], 'expect': {}},
                    {'name': 'wait-install', 'events': [], 'expect': {'storagePrompt': {
                        'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True,
                    }}},
                    {'name': 'allow-install', 'events': [
                        {'kind': 'pointer', 'x': 880, 'y': 478},
                        {'kind': 'button', 'button': 'left', 'down': True},
                        {'kind': 'button', 'button': 'left', 'down': False},
                    ], 'expect': {'storagePrompt': None}},
                    {'name': 'wait-new-app', 'events': [], 'expect': {'apps': [
                        {'app': 2, 'generation': 1, 'state': 'running'},
                    ]}},
                    {'name': 'power-off', 'events': [
                        {'kind': 'pointer', 'x': 1250, 'y': 12},
                        {'kind': 'button', 'button': 'left', 'down': True},
                        {'kind': 'button', 'button': 'left', 'down': False},
                    ], 'expect': {'phase': 'stopped'}},
                ],
            }
            self.put(root / 'install-events.json', events)
            review = {
                'reviewed': True,
                'reviewer': 'main test fixture',
                'profile': 'install-lifecycle',
                'sessionMode': 'long-lived',
                'extraArgs': ['--duration-ms', '0', '--isolation', 'strict', '--data-root', '/data/mingos',
                              '--state-file', '/run/evidence/desktop-state.json'],
                'budgetEvidence': 'synthetic unit test',
                'installRoot': INSTALL_LIFECYCLE_INSTALL_ROOT,
                'installEvents': INSTALL_LIFECYCLE_EVENTS,
                'installEventsSha256': sha256(root / 'install-events.json'),
                'installSource': INSTALL_LIFECYCLE_SOURCE,
                'installFixture': {
                    'helper': '/opt/mingos/prepare-install-fixture',
                    'installRoot': INSTALL_LIFECYCLE_INSTALL_ROOT,
                    'initialWasm': '/opt/mingos/packages/initial/main.wasm',
                    'sourceWasm': '/opt/mingos/packages/new/main.wasm',
                    'sourceManifest': '/opt/mingos/install-source/app.json',
                    'receipt': '/run/evidence/install-fixture.json',
                },
                'installLifecycle': {'initialPackageId': 'dev.ming.text-entry-demo',
                                     'newPackageId': 'dev.ming.install-demo', 'newVersion': '1.0.0'},
            }
            review.update(hostSha256=sha256(root / 'ming-app-host'), catalogSha256=sha256(root / 'catalog.json'))
            self.assertEqual(verify_payload(root, review), json.loads((root / 'catalog.json').read_text()))
            self.assertIs(checked_session(review, root), review)

            # The event file is part of the reviewed input boundary; changing it
            # after review must fail before any bundle is assembled.
            events['steps'][0]['afterMs'] += 1
            self.put(root / 'install-events.json', events)
            with self.assertRaisesRegex(ValueError, '^data-text: reviewed install event file required$'):
                verify_payload(root, review)

    def test_install_lifecycle_review_rejects_path_contract_drift(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            (root / 'ming-app-host').write_bytes(b'host')
            (root / 'catalog.json').write_text('{}')
            review = {
                'reviewed': True,
                'reviewer': 'main test fixture',
                'profile': 'install-lifecycle',
                'sessionMode': 'long-lived',
                'extraArgs': ['--duration-ms', '0', '--isolation', 'strict', '--data-root', '/data/mingos',
                              '--state-file', '/run/evidence/desktop-state.json'],
                'budgetEvidence': 'synthetic unit test',
                'installRoot': INSTALL_LIFECYCLE_INSTALL_ROOT,
                'installEvents': INSTALL_LIFECYCLE_EVENTS,
                'installSource': INSTALL_LIFECYCLE_SOURCE,
                'hostSha256': sha256(root / 'ming-app-host'),
                'catalogSha256': sha256(root / 'catalog.json'),
            }
            for field, value in (
                    ('installRoot', '/opt/mingos/other-install-root'),
                    ('installEvents', '/opt/mingos/other-install-events.json'),
                    ('installSource', '/opt/mingos/other-source/app.json')):
                with self.subTest(field=field):
                    changed = dict(review, **{field: value})
                    with self.assertRaises(ValueError):
                        checked_session(changed, root)

    def test_data_grant_and_identity_are_exact(self):
        changes = [lambda c: c['apps'][0].update(dataKey='other'), lambda c: c['apps'][0].update(slots=[1,2]),
                   lambda c: c['apps'][1].update(dataKey='counter-main',slots=[1]),
                   lambda c: c['apps'][1].update(dataKey=None), lambda c: c['apps'][0].update(id=2),
                   lambda c: c.update(autostart=[1,1,2]), lambda c: c['apps'][0].update(manifest='../outside')]
        for change in changes:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as work:
                root = Path(work); catalog, review = self.fixture(root)
                change(catalog); self.put(root/'catalog.json', catalog); self.bind(root,review)
                with self.assertRaises(ValueError): verify_payload(root,review)

    def test_tampered_manifest_receipt_or_wasm_is_rejected(self):
        for filename in ('app.json','build-receipt.json','compiler.wasm','main.wasm','boot-receipt.json'):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as work:
                root = Path(work); _, review = self.fixture(root)
                path = root/'packages/1'/filename
                path.write_bytes(path.read_bytes()+b' ')
                with self.assertRaises(ValueError): verify_payload(root,review)

    def test_probe_receipt_must_be_successful_and_semantically_bound(self):
        for change in ({'status':'failed'},{'mode':'other'},{'compilerExitCode':1},{'stage':'other'}):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as work:
                root = Path(work); _, review = self.fixture(root)
                path = root/'packages/1/build-receipt.json'; receipt=json.loads(path.read_text())
                receipt.update(change);self.put(path,receipt);self.envelope(root);self.bind(root,review)
                with self.assertRaises(ValueError): verify_payload(root,review)

    def private_fixture(self, root):
        catalog, review = self.fixture(root)
        package = root/'packages/private'; package.mkdir()
        self.put(package/'app.json', {'schemaVersion':1, 'id':'dev.ming.private-kernel-probe',
                                    'version':'0.1.0', 'sdkVersion':6, 'entry':'main.wasm'})
        (package/'main.wasm').write_bytes(b'\0asm\x01\0\0\0private-probe')
        catalog['apps'].append({'id':3, 'title':'Private Probe', 'manifest':'packages/private/app.json'})
        catalog['autostart'].append(3)
        self.put(root/'catalog.json', catalog)
        self.bind(root, review)
        review.update(profile='data-text-private-probe', privateProbeSha256={
            'manifest':sha256(package/'app.json'), 'wasm':sha256(package/'main.wasm')})
        return catalog, review

    def test_private_probe_profile_accepts_only_bound_sdk6_package(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.private_fixture(root)
            self.assertEqual(verify_payload(root, review), catalog)
            (root/'packages/private/main.wasm').write_bytes(b'\0asm\x01\0\0\0changed')
            with self.assertRaises(ValueError): verify_payload(root, review)

    def test_private_probe_profile_rejects_unreviewed_identity_and_grants(self):
        for change in (lambda c: c['apps'][2].update(dataKey='x', slots=[1]),
                       lambda c: c['apps'][2].update(id=2),
                       lambda c: c.update(autostart=[1,2]),
                       lambda c: c['apps'][2].update(manifest='../outside')):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as work:
                root = Path(work); catalog, review = self.private_fixture(root)
                change(catalog); self.put(root/'catalog.json', catalog); self.bind(root, review)
                with self.assertRaises(ValueError): verify_payload(root, review)

    def test_private_probe_profile_requires_reviewed_input(self):
        from bootlib import validate_reviewed_input
        with self.assertRaises(ValueError): validate_reviewed_input({'profile':'data-text-private-probe'}, None)

    def public_fixture(self, root):
        catalog, review = self.fixture(root)
        package = root/'packages/public'; package.mkdir()
        self.put(package/'app.json', {'schemaVersion':1, 'id':'dev.ming.public-kernel-probe',
                                    'version':'0.1.0', 'sdkVersion':6, 'entry':'main.wasm', 'publicStorage':True})
        (package/'main.wasm').write_bytes(b'\0asm\x01\0\0\0public-probe')
        catalog['apps'].append({'id':3, 'title':'Public Probe', 'manifest':'packages/public/app.json'})
        catalog['autostart'].append(3)
        self.put(root/'catalog.json', catalog)
        self.bind(root, review)
        review.update(profile='data-text-public-probe', publicProbeSha256={
            'manifest':sha256(package/'app.json'), 'wasm':sha256(package/'main.wasm')},
            publicPreauthorization={'packageId':'dev.ming.public-kernel-probe', 'policy':'guest-seeded-public-grant-v1'})
        return catalog, review

    def test_public_probe_profile_accepts_only_reviewed_preauthorized_package(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work); catalog, review = self.public_fixture(root)
            self.assertEqual(verify_payload(root, review), catalog)
            review['publicPreauthorization']['packageId'] = 'dev.ming.other'
            with self.assertRaises(ValueError): verify_payload(root, review)

    def test_bounded_public_probe_profile_is_single_app_and_exactly_bound(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            package = root/'packages/public'; package.mkdir(parents=True)
            self.put(package/'app.json', {'schemaVersion':1, 'id':'dev.ming.public-kernel-probe',
                                          'version':'0.1.0', 'sdkVersion':6, 'entry':'main.wasm', 'publicStorage':True})
            (package/'main.wasm').write_bytes(b'\0asm\x01\0\0\0public-probe')
            catalog = {'schemaVersion':1, 'apps':[{'id':1, 'title':'Public Probe', 'manifest':'packages/public/app.json'}], 'autostart':[1]}
            self.put(root/'catalog.json', catalog)
            (root/'ming-app-host').write_bytes(b'\x7fELFsynthetic-test-only')
            review = {'reviewed':True, 'reviewer':'main test fixture', 'profile':'public-probe',
                      'sessionMode':'bounded', 'extraArgs':['--duration-ms','15000','--isolation','strict',
                                                           '--data-root','/data/mingos','--state-file',
                                                           '/run/evidence/desktop-state.json'], 'budgetEvidence':'synthetic',
                      'publicProbeSha256':{'manifest':sha256(package/'app.json'), 'wasm':sha256(package/'main.wasm')},
                      'publicPreauthorization':{'packageId':'dev.ming.public-kernel-probe', 'policy':'guest-seeded-public-grant-v1'}}
            review.update(hostSha256=sha256(root/'ming-app-host'), catalogSha256=sha256(root/'catalog.json'))
            self.assertEqual(verify_payload(root, review), catalog)

class InputQualificationTests(unittest.TestCase):
    class ValidationComplete(Exception):
        pass

    def review(self, profile, instrumented_profile):
        return {
            'profile': profile,
            'instrumentedQualification': True,
            'instrumentedProfile': instrumented_profile,
            'inputSynchronization': copy.deepcopy(POINTER_SYNC_POLICY),
            'instrumentation': {
                'desktopEnv': {'WAYLAND_DEBUG': 'client'},
                'stderrLimitBytes': 8388608,
                'observerSha256': sha256(Path(__file__).with_name('evdev_observer.py')),
                'observerMaxEvents': 4096,
                'observerMaxSeconds': 900,
                'exclusiveGrab': False,
                'readyCaptureSeconds': None,
            },
        }

    def validate(self, root, review, flags=None):
        review_path = root / 'review.json'
        review_path.write_text(json.dumps(review))
        output = root / 'bundle'
        if flags is None:
            flags = ['--input-diagnostics', '--wayland-client-diagnostics', '--pointer-handshake-diagnostics']
        argv = ['bundle.py', '--payload', str(root / 'payload'), '--review', str(review_path),
                '--output', str(output), '--isolation-profile', 'source-v2',
                '--isolation-probes', str(root / 'probes')] + flags
        # Payload validation has separate real fixtures above. Stop at the probe
        # boundary so the actual CLI checks run without assembling a Linux rootfs.
        with mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(bundle, 'verify_payload', return_value={'apps': [], 'autostart': []}), \
                mock.patch.object(bundle, 'verify_review_and_files', side_effect=self.ValidationComplete):
            try:
                bundle.main()
            except self.ValidationComplete:
                return True
            finally:
                self.assertFalse(output.exists(), 'qualification must be checked before rootfs writes')
        self.fail('test must stop at the probe boundary')

    def test_install_qualification_accepts_its_own_tag_and_preserves_data_text_tag(self):
        with tempfile.TemporaryDirectory() as work:
            for profile, tag in (
                    ('install-lifecycle', 'install-lifecycle-pointer-ready-v1'),
                    ('persistent-install', 'persistent-install-pointer-ready-v1'),
                    ('persistent-uninstall', 'persistent-uninstall-pointer-ready-v1'),
                    ('data-text', 'data-text-pointer-ready-v1')):
                with self.subTest(profile=profile):
                    self.assertTrue(self.validate(Path(work), self.review(profile, tag)))

    def test_qualification_rejects_tags_from_another_payload_profile(self):
        with tempfile.TemporaryDirectory() as work:
            for profile, tag in (
                    ('install-lifecycle', 'data-text-pointer-ready-v1'),
                    ('data-text', 'install-lifecycle-pointer-ready-v1'),
                    ('persistent-install', 'persistent-uninstall-pointer-ready-v1'),
                    ('persistent-uninstall', 'persistent-install-pointer-ready-v1'),
                    ('persistent-uninstall-extra', 'persistent-uninstall-pointer-ready-v1'),
                    ('install-lifecycle', None),
                    ('install-lifecycle', 'unknown')):
                with self.subTest(profile=profile, tag=tag), self.assertRaises(ValueError):
                    self.validate(Path(work), self.review(profile, tag))

    def test_install_qualification_still_requires_every_diagnostic_flag(self):
        flags = ['--input-diagnostics', '--wayland-client-diagnostics', '--pointer-handshake-diagnostics']
        with tempfile.TemporaryDirectory() as work:
            for missing in flags:
                with self.subTest(missing=missing), self.assertRaises(ValueError):
                    self.validate(Path(work), self.review('install-lifecycle', 'install-lifecycle-pointer-ready-v1'),
                                  [flag for flag in flags if flag != missing])

    def test_install_qualification_rejects_changed_sync_policy(self):
        with tempfile.TemporaryDirectory() as work:
            review = self.review('install-lifecycle', 'install-lifecycle-pointer-ready-v1')
            for key in review['inputSynchronization']:
                changed = copy.deepcopy(review)
                del changed['inputSynchronization'][key]
                with self.subTest(missing=key), self.assertRaises(ValueError):
                    self.validate(Path(work), changed)
            changed = copy.deepcopy(review)
            changed['inputSynchronization']['pointerWaitMs'] = 5001
            with self.assertRaises(ValueError):
                self.validate(Path(work), changed)

    def test_install_qualification_rejects_changed_instrumentation_bounds(self):
        changes = {
            'desktopEnv': {}, 'stderrLimitBytes': 8388609, 'observerSha256': '0' * 64,
            'observerMaxEvents': 4097, 'observerMaxSeconds': 901,
            'exclusiveGrab': True, 'readyCaptureSeconds': 15, 'extra': True,
        }
        with tempfile.TemporaryDirectory() as work:
            for key, value in changes.items():
                changed = self.review('install-lifecycle', 'install-lifecycle-pointer-ready-v1')
                changed['instrumentation'][key] = value
                with self.subTest(field=key), self.assertRaisesRegex(ValueError, 'instrumentation'):
                    self.validate(Path(work), changed)


if __name__ == '__main__': unittest.main()
