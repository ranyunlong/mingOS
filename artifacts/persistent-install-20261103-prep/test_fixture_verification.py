"""Synthetic signed-package controls; never actual helper execution evidence."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from cryptography.exceptions import InvalidSignature

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('fixture_audit',HERE/'verify-persistent-install-evidence.py')
audit=importlib.util.module_from_spec(s);s.loader.exec_module(audit)

class FixtureVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=HERE);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.inputs=self.root/'inputs';self.fixture=self.root/'fixture'
        payload=self.inputs/'payload';install=self.fixture/'install-root';payload.mkdir(parents=True);install.mkdir(parents=True)
        (payload/'prepare-persistent-install-fixture').write_bytes(b'synthetic helper marker')
        wasm=b'\0asm\x01\0\0\0';self.key=Ed25519PrivateKey.from_private_bytes(bytes([7])*32)
        public=self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        (install/'trusted-publishers-v1').write_text('ming-trusted-publishers-v1\nmingos-dev-2026\tactive\t'+base64.b64encode(public).decode()+'\n')
        def package(id,version,base):
            base.mkdir(parents=True);(base/'main.wasm').write_bytes(wasm)
            msg=(b'ming-package-signature-v1\0'+audit.base.text('mingos-dev-2026')+struct.pack('>I',1)+audit.base.text(id)+audit.base.text(version)
                +struct.pack('>I',6)+audit.base.text('main.wasm')+b'\0\0'+struct.pack('>Q',4)+b'null'+struct.pack('>Q',len(wasm))+wasm)
            manifest=dict(schemaVersion=1,id=id,version=version,sdkVersion=6,entry='main.wasm',privateStorage=False,publicStorage=False,dataMigration=None,
                publisherKeyId='mingos-dev-2026',publisherSignature=base64.b64encode(self.key.sign(msg)).decode())
            (base/'app.json').write_text(json.dumps(manifest))
            fp=hashlib.sha256(b'ming-private-storage-package-v1\0'+struct.pack('>I',1)+audit.base.text(id)+audit.base.text(version)+audit.base.text('main.wasm')
                +struct.pack('>I',6)+struct.pack('>Q',len(wasm))+wasm+b'\0publisher-key-id-v1'+audit.base.text('mingos-dev-2026')).hexdigest()
            return fp
        initial=payload/'packages/initial';fp=package('dev.ming.text-entry-demo','0.1.0',initial)
        source=payload/'packages/new';package('dev.ming.install-demo','1.0.0',source)
        destination=install/f'packages/{"dev.ming.text-entry-demo".encode().hex()}/0.1.0-{fp}';destination.mkdir(parents=True)
        for name in ['app.json','main.wasm']:(destination/name).write_bytes((initial/name).read_bytes())
        (self.fixture/'install-source').mkdir()
        for name in ['app.json','main.wasm']:(self.fixture/'install-source'/name).write_bytes((source/name).read_bytes())
        catalog=dict(schemaVersion=3,apps=[dict(id=1,title='Text Lab',packageId='dev.ming.text-entry-demo')],autostart=[1])
        (payload/'catalog.json').write_text(json.dumps(catalog));(install/'catalog.json').write_bytes((payload/'catalog.json').read_bytes())
        (install/'installed-packages-v1').write_text('ming-installed-packages-v1\ndev.ming.text-entry-demo\tactive\t0.1.0\t'+fp+'\n')
        (install/'desktop-install-state-v1.json').write_text('{}')
        files=[dict(path=str(path.relative_to(install)),sha256=audit.base.sha(path)) for path in sorted(install.rglob('*')) if path.is_file()]
        self.content=dict(**{field:audit.base.sha(install/name) for field,name in audit.ROOT_FILES.items()},apps=[dict(app=1,packageId='dev.ming.text-entry-demo',version='0.1.0',fingerprint=fp,
            manifestPath=str((destination/'app.json').relative_to(install)),manifestSha256=audit.base.sha(destination/'app.json'),wasmPath=str((destination/'main.wasm').relative_to(install)),wasmSha256=audit.base.sha(destination/'main.wasm'))],files=files)
        self.receipt=dict(schemaVersion=1,status='initialized-first',actions={k:1 for k in audit.ACTION_KEYS},content=self.content)
        (self.fixture/'fixture-receipt.json').write_text(json.dumps(self.receipt));(self.fixture/'stdout.log').write_text(json.dumps(self.receipt));(self.fixture/'stderr.log').write_text('')
        self.review=dict(catalogSha256=audit.base.sha(payload/'catalog.json'),persistentInstallFixtureSha256=dict(helper=audit.base.sha(payload/'prepare-persistent-install-fixture'),initialWasm=audit.base.sha(initial/'main.wasm'),sourceWasm=audit.base.sha(source/'main.wasm')))
        argv=[str(payload/'prepare-persistent-install-fixture'),'initialize-first',str(install),str(initial/'main.wasm'),str(source/'main.wasm'),str(self.fixture/'install-source/app.json'),str(payload/'catalog.json'),self.review['catalogSha256'],str(self.fixture/'fixture-receipt.json')]
        (self.fixture/'command.json').write_text(json.dumps(dict(stage='fixture',argv=argv,exitCode=0,executableSha256=self.review['persistentInstallFixtureSha256']['helper'])))
    def check(self):return audit.verify_fixture_directory(self.fixture,self.inputs,self.review)
    def test_recomputes_signatures_fingerprints_bytes_and_template(self):
        result=self.check();self.assertTrue(result['source']['signatureVerified']);self.assertEqual(result['initialContent'],self.content)
    def test_forged_success_summaries_signature_tamper_paths_and_content_rejected(self):
        for mutation in ['command-failed','wrong-helper','wrong-template','source-signature','source-wasm','content-drift','extra-file','stdout-drift','symlink','keyring','bool-schema','unknown-manifest']:
            with self.subTest(mutation=mutation):
                self.setUp()
                if mutation in ['command-failed','wrong-helper']:
                    p=self.fixture/'command.json';v=json.loads(p.read_text());v['exitCode' if mutation=='command-failed' else 'executableSha256']=1;p.write_text(json.dumps(v))
                elif mutation=='wrong-template':self.review['catalogSha256']='f'*64
                elif mutation in ['source-signature','bool-schema','unknown-manifest']:
                    p=self.fixture/'install-source/app.json';v=json.loads(p.read_text())
                    if mutation=='source-signature':v['publisherSignature']=base64.b64encode(bytes(64)).decode()
                    elif mutation=='bool-schema':v['schemaVersion']=True
                    else:v['extra']=True
                    p.write_text(json.dumps(v))
                elif mutation=='source-wasm':(self.fixture/'install-source/main.wasm').write_bytes(b'\0asm\x01\0\0\0tamper')
                elif mutation=='content-drift':(self.fixture/'install-root/installed-packages-v1').write_text('forged')
                elif mutation=='extra-file':(self.fixture/'install-root/extra').write_text('forged')
                elif mutation=='stdout-drift':(self.fixture/'stdout.log').write_text('{}')
                elif mutation=='symlink':(self.fixture/'install-root/link').symlink_to(self.inputs/'payload/catalog.json')
                else:(self.fixture/'install-root/trusted-publishers-v1').write_text('forged')
                with self.assertRaises((ValueError,InvalidSignature)):self.check()
