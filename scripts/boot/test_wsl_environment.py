import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
try:
    import wsl_environment as env
except ImportError:
    env = None

class InputsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(env, 'wsl_environment API missing')
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
    def tar(self,name,members):
        path=self.root/name
        with tarfile.open(path,'w') as archive:
            for name,kind,data in members:
                info=tarfile.TarInfo(name)
                if kind=='file': info.size=len(data); archive.addfile(info,io.BytesIO(data))
                elif kind=='dir': info.type=tarfile.DIRTYPE; archive.addfile(info)
                else: info.type=tarfile.SYMTYPE if kind=='link' else tarfile.LNKTYPE; info.linkname=data; archive.addfile(info)
        return path
    def bundle(self):
        return self.tar('bundle.tar',[('docker','dir','')]+[('docker/'+name,'file',name.encode()) for name in env.BINARY_PINS])
    def prepare(self,members):
        source=self.tar('root.tar',members); bundle=self.bundle()
        with patch.object(env,'BUNDLE_BYTES',bundle.stat().st_size), patch.object(env,'BUNDLE_SHA256',hashlib.sha256(bundle.read_bytes()).hexdigest()), patch.object(env,'BINARY_PINS',{name:hashlib.sha256(name.encode()).hexdigest() for name in env.BINARY_PINS}):
            return env.prepare_rootfs(source,bundle,self.root/'out.tar')
    def test_rootfs_preserves_guest_links_and_injects_files(self):
        self.prepare([('bin','link','usr/bin'),('usr/bin/sh','file',b'shell'),('usr/bin/dash','hard','usr/bin/sh')])
        with tarfile.open(self.root/'out.tar') as archive:
            self.assertTrue(archive.getmember('bin').issym())
            self.assertTrue(archive.getmember('usr/bin/dash').islnk())
            self.assertIn(b'enabled=false',archive.extractfile('etc/wsl.conf').read())
            self.assertEqual(archive.extractfile('usr/local/bin/docker').read(),b'docker')
        with self.assertRaises(FileExistsError): self.prepare([])
    def test_rejects_unsafe_rootfs(self):
        bad=[[('/etc/x','file',b'')],[('../x','file',b'')],[('x','file',b''),('x','file',b'')],[('etc','link','usr/etc')],[('usr','link','/outside')],[('x','link','../../escape')],[('etc/wsl.conf','dir','')]]
        for i,members in enumerate(bad):
            with self.subTest(members=members), tempfile.TemporaryDirectory() as tmp:
                old=self.root; self.root=Path(tmp)
                try:
                    with self.assertRaises(ValueError): self.prepare(members)
                finally: self.root=old
    def test_bundle_pin_failures(self):
        bundle=self.bundle(); source=self.tar('root.tar',[])
        with self.assertRaises(ValueError): env.prepare_rootfs(source,bundle,self.root/'out.tar')
        with patch.object(env,'BUNDLE_BYTES',bundle.stat().st_size):
            with self.assertRaises(ValueError): env.prepare_rootfs(source,bundle,self.root/'out2.tar')
    def test_freeze_is_exclusive_and_source_independent(self):
        source=self.root/'helper.py'; source.write_bytes(b'abc')
        result=env.freeze_inputs([source],self.root/'frozen')
        source.write_bytes(b'changed')
        self.assertEqual((self.root/'frozen/helper.py').read_bytes(),b'abc')
        self.assertEqual(result['files']['helper.py']['sha256'],hashlib.sha256(b'abc').hexdigest())
        env.verify_frozen(self.root/'frozen',result)
        (self.root/'frozen/helper.py').write_bytes(b'abd')
        with self.assertRaises(ValueError): env.verify_frozen(self.root/'frozen',result)
        with self.assertRaises(FileExistsError): env.freeze_inputs([source],self.root/'frozen')
    def test_capture_exclusive_and_errors_retained(self):
        env.capture(self.root,'ok',[sys.executable,'-c','print(123)'],timeout=10)
        self.assertIn('123',(self.root/'ok.stdout.log').read_text())
        with self.assertRaises(FileExistsError): env.capture(self.root,'ok',[sys.executable,'-c','pass'],timeout=10)
        with self.assertRaises(OSError): env.capture(self.root,'missing',[str(self.root/'missing')],timeout=1)
        self.assertIn('error',json.loads((self.root/'missing.result.json').read_text()))
        with self.assertRaises(subprocess.TimeoutExpired): env.capture(self.root,'timeout',[sys.executable,'-c','import time; time.sleep(10)'],timeout=0.02)
        self.assertTrue(json.loads((self.root/'timeout.result.json').read_text())['processMayRemain'])
    def test_parameter_validation(self):
        env.validate_parameters('test-1','MingOS-P01-test')
        for generation,distro in [('UPPER','MingOS-P01-test'),('ok','Ubuntu'),('../x','MingOS-P01-test')]:
            with self.assertRaises(ValueError): env.validate_parameters(generation,distro)

if __name__=='__main__': unittest.main()


class AdditionalInputsTests(unittest.TestCase):
    setUp=InputsTests.setUp
    tar=InputsTests.tar
    bundle=InputsTests.bundle
    prepare=InputsTests.prepare
    def assert_bundle_rejected(self,bundle,reason,output_name):
        source=self.tar(output_name+'-root.tar',[])
        pins={name:hashlib.sha256(name.encode()).hexdigest() for name in env.BINARY_PINS}
        with patch.object(env,'BUNDLE_BYTES',bundle.stat().st_size), patch.object(env,'BUNDLE_SHA256',hashlib.sha256(bundle.read_bytes()).hexdigest()), patch.object(env,'BINARY_PINS',pins):
            with self.assertRaisesRegex(ValueError,'^'+reason+'$'):
                env.prepare_rootfs(source,bundle,self.root/output_name)
    def test_bundle_member_controls(self):
        cases=[([('docker/docker','link','/tmp/x')],'unexpected bundle member'),
               ([('evil','file',b'x')],'unexpected bundle member'),
               ([('docker/'+name,'file',name.encode()) for name in env.BINARY_PINS if name!='docker'],'incomplete bundle')]
        for number,(members,reason) in enumerate(cases):
            with self.subTest(number=number):
                bundle=self.tar('bad'+str(number)+'.tar',members)
                self.assert_bundle_rejected(bundle,reason,'out'+str(number))
    def test_bundle_duplicate_after_valid_binary_rejected(self):
        # First binary matches its synthetic trusted pin, reaching duplicate admission.
        bundle=self.tar('duplicate.tar',[('docker/docker','file',b'docker'),('docker/docker','file',b'duplicate')])
        self.assert_bundle_rejected(bundle,'duplicate bundle member','duplicate-output')
    def test_bundle_oversized_header_rejected_without_payload(self):
        header=tarfile.TarInfo('docker/docker'); header.size=128*1024*1024+1
        bundle=self.root/'oversized.tar'
        bundle.write_bytes(header.tobuf(format=tarfile.USTAR_FORMAT)+bytes(1024))
        self.assertEqual(bundle.stat().st_size,1536)
        # A parser/payload/hash failure must not mask rejection of the advertised size.
        self.assert_bundle_rejected(bundle,'unexpected bundle member','oversized-output')
    @unittest.skipUnless(sys.platform=='win32', 'Windows junction control')
    def test_windows_junction_parent_rejected(self):
        actual=self.root/'actual'; actual.mkdir(); junction=self.root/'junction'
        completed=subprocess.run(['cmd.exe','/c','mklink','/J',str(junction),str(actual)],capture_output=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        source=actual/'helper.py'; source.write_bytes(b'abc')
        with self.assertRaises(ValueError): env.freeze_inputs([junction/'helper.py'],self.root/'frozen')
        with self.assertRaises(ValueError): env.capture(junction,'op',[sys.executable,'-c','pass'],timeout=1)
    def test_partial_log_collision_is_retained(self):
        (self.root/'op.stderr.log').write_bytes(b'old')
        with self.assertRaises(FileExistsError): env.capture(self.root,'op',[sys.executable,'-c','pass'],timeout=1)
        self.assertEqual((self.root/'op.stderr.log').read_bytes(),b'old')
        self.assertTrue((self.root/'op.command.json').exists())
    def test_freeze_duplicate_basename(self):
        (self.root/'a').mkdir(); (self.root/'b').mkdir()
        first=self.root/'a/helper.py'; second=self.root/'b/helper.py'
        first.write_bytes(b'first'); second.write_bytes(b'second')
        with self.assertRaises(ValueError): env.freeze_inputs([first,second],self.root/'frozen')
        self.assertEqual((self.root/'frozen/helper.py').read_bytes(),b'first')

class CaptureStdinTests(unittest.TestCase):
    def test_records_only_transmitted_stdin_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'stdin'; source.write_bytes(b'prefixpayload')
            with source.open('rb') as stream:
                stream.seek(6)
                env.capture(root,'stdin',[sys.executable,'-c','import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())'],timeout=10,stdin=stream)
            self.assertEqual((root/'stdin.stdout.log').read_bytes(),b'payload')
            pin=json.loads((root/'stdin.command.json').read_text())['stdin']
            self.assertEqual(pin,{'bytes':7,'sha256':hashlib.sha256(b'payload').hexdigest()})

class FrozenClosureTests(unittest.TestCase):
    def test_manifest_contract_and_extra_files_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'helper.py'; source.write_bytes(b'abc')
            manifest=env.freeze_inputs([source],root/'frozen')
            self.assertEqual(manifest['root'],str(root/'frozen'))
            self.assertEqual(set(manifest['files']['helper.py']),{'sha256','bytes','mode'})
            (root/'frozen/extra.py').write_bytes(b'extra')
            with self.assertRaises(ValueError): env.verify_frozen(root/'frozen',manifest)

class CreationTests(unittest.TestCase):
    def test_tools_identity_rejects_each_mismatch(self):
        verified={'imageId':'sha256:x','diffIds':['sha256:y'],'configObject':{'config':{'User':'root'}}}
        image={'Id':'sha256:x','Descriptor':{'digest':'sha256:x','mediaType':'application/vnd.oci.image.index.v1+json'},'Architecture':'amd64','Os':'linux','RootFS':{'Type':'layers','Layers':['sha256:y']},'Config':{'User':'root'}}
        env.validate_tools_image(image,verified)
        for key,value in [('Id','wrong'),('Config',{}),('RootFS',{}),('Architecture','arm64'),('Descriptor',{})]:
            changed=dict(image);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): env.validate_tools_image(changed,verified)
    def test_existing_output_fails_before_any_process(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(env.subprocess,'run') as run:
            with self.assertRaises(FileExistsError):
                env.create_empty_environment(generation='p01-test',distro='MingOS-P01-test',output_directory=Path(tmp),wsl_executable=Path(sys.executable).resolve(),outer_docker=None,docker_bundle=Path(sys.executable).resolve(),archive_root=Path(tmp))
            run.assert_not_called()

class LifecycleSequenceTests(unittest.TestCase):
    def setUp(self):
        from docker_transport import DockerTransport
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bundle=self.root/'bundle';self.bundle.write_bytes(b'bundle')
        self.transport=DockerTransport(str(Path(sys.executable).resolve()),'npipe:////./pipe/test',str(self.root/'config'))
        self.calls=[];self.cid='a'*64
        import development_image_migration as migration
        self.tools={'imageId':migration.FIXED['tools']['imageId'],'diffIds':['sha256:y'],'configObject':{'config':{'User':'root'}}}
        self.image={'Id':self.tools['imageId'],'Descriptor':{'digest':self.tools['imageId'],'mediaType':migration.INDEX},'Architecture':'amd64','Os':'linux','RootFS':{'Type':'layers','Layers':['sha256:y']},'Config':{'User':'root'}}
        self.missing=False;self.old=False
        self.old_ids=[]
    def capture(self,directory,key,argv,**kwargs):
        self.calls.append((key,argv,kwargs)); value=b''
        if key=='tools-inspect':
            if self.missing: raise ValueError('Tools missing')
            value=json.dumps([self.image]).encode()
        elif key=='wsl-list': value=(('MingOS-P01-test\n' if self.old else '')).encode('utf-16-le')
        elif key=='outer-info':value=b'{"ID":"outer"}'
        elif key=='outer-context':value=b'desktop-linux\n'
        elif key=='outer-container-ids':value='\n'.join(self.old_ids).encode()
        elif key.startswith('outer-containers'):value=b'[]'
        elif key=='export-create':value=self.cid.encode()
        elif key=='export-inspect':value=json.dumps([{'Id':self.cid,'Image':self.tools['imageId'],'Config':{'Image':self.tools['imageId']},'HostConfig':{'RestartPolicy':{'Name':'no','MaximumRetryCount':0},'AutoRemove':False},'State':{'Running':False},'Mounts':[]}]).encode()
        elif key=='export':(directory/'export.tar').write_bytes(b'export')
        elif key=='wsl-import':
            (directory/'distro').mkdir();(directory/'distro/ext4.vhdx').write_bytes(b'vhd')
        elif key=='guest-launch':value=b'{"generation":"p01-test","pid":42}'
        (directory/(key+'.stdout.log')).write_bytes(value)
        return subprocess.CompletedProcess(argv,0)
    def create(self):
        import development_image_migration as migration
        def prepare(a,b,c):c.write_bytes(b'rootfs');return {'rootfs':{'sha256':'fake'}}
        with patch.object(env,'BUNDLE_BYTES',6),patch.object(env,'BUNDLE_SHA256',hashlib.sha256(b'bundle').hexdigest()),patch.object(env,'capture',side_effect=self.capture),patch.object(env,'prepare_rootfs',side_effect=prepare),patch.object(migration,'verify_all',return_value={'images':[self.tools]}),patch.object(migration,'verify_one',return_value=self.tools):
            return env.create_empty_environment(generation='p01-test',distro='MingOS-P01-test',output_directory=self.root/'output',wsl_executable=Path(sys.executable).resolve(),outer_docker=self.transport,docker_bundle=self.bundle,archive_root=self.root)
    def test_sequence_stopped_inspected_id_only_and_no_destructive_calls(self):
        receipt=self.create();names=[k for k,_,_ in self.calls]
        self.assertEqual(receipt['status'],'creating');self.assertEqual(receipt['phase'],'empty-daemon-observed')
        self.assertLess(names.index('export-inspect'),names.index('export'))
        self.assertLess(names.index('outer-info'),names.index('export-create'))
        export=next(argv for key,argv,_ in self.calls if key=='export');self.assertEqual(export[-1],self.cid)
        for key,argv,options in self.calls:
            self.assertFalse(set(argv)&{'delete','prune','reset','--unregister','--shutdown','rm','rmi','start'})
            if key.startswith(('outer-','tools-','export')): self.assertEqual(argv[1:3],['--host',self.transport.endpoint])
        self.assertEqual(receipt['exportContainerId'],self.cid)
    def test_missing_tools_prevents_mutation(self):
        self.missing=True
        with self.assertRaises(ValueError):self.create()
        self.assertEqual([k for k,_,_ in self.calls],['tools-inspect'])
        self.assertEqual(json.loads((self.root/'output/environment-result.json').read_text())['status'],'failed')
    def test_existing_distro_prevents_mutation(self):
        self.old=True
        with self.assertRaises(ValueError):self.create()
        self.assertFalse(any(k in {'export-create','export','wsl-import'} for k,_,_ in self.calls))
    def test_large_outer_inventory_batches_inspections(self):
        self.old_ids=[format(i,'064x') for i in range(130)]
        self.create()
        commands=[argv for key,argv,_ in self.calls if key.startswith('outer-containers')]
        self.assertEqual(len(commands),3)
        self.assertEqual([len(argv[7:]) for argv in commands],[64,64,2])
    def test_bootstrap_opens_root_parent_without_following_links(self):
        self.create()
        argv=next(argv for key,argv,_ in self.calls if key=='guest-helper-transfer')
        bootstrap=argv[argv.index('-c')+1]
        self.assertIn('O_DIRECTORY',bootstrap)
        self.assertIn('dir_fd=',bootstrap)
