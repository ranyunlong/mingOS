"""Closed data-text package/review boundary tests; synthetic Wasm is not build evidence."""
import json
import tempfile
import unittest
from pathlib import Path
from bootlib import sha256
from bundle import verify_payload

class ProfileTests(unittest.TestCase):
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

if __name__ == '__main__': unittest.main()
