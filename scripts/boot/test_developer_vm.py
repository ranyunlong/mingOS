"""Real temporary-file lifecycle controls; no Docker, mkfs, QEMU or guest runs.

The host policy, complete-pack verifier and disk formatter are explicit boundary
doubles on Windows. Locks are exercised with a specific ownership double here;
the supported Linux fcntl path requires the separate Linux controls.
"""
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

HERE = Path(__file__).resolve().parent
BOOT = HERE
sys.path.insert(0, str(BOOT))
sys.path.insert(0, str(HERE))
import developer_vm as d
from developer_profile import ARGS
from isolation_profiles import load_profile

PIN = 'a' * 64
FLAGS = dict(diagnosticOnly=True, instrumentedQualification=False,
             formalQualification=False, finalSystemAccepted=False)

def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def put(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value) if isinstance(value, dict) else value)
    return path

def review():
    return dict(reviewed=True, reviewer='independent synthetic reviewer',
        profile='developer-vm-v1', sessionMode='bounded', extraArgs=list(ARGS),
        hostSha256='b'*64, catalogSha256='c'*64, payloadFiles={},
        packageEvidenceSha256={'1':'d'*64, '2':'e'*64},
        hostProvenance={key:'f'*64 for key in ('freezeSha256','resultSha256','artifactsSha256')},
        strictProbeProfile='source-v2', strictProbeSha256=load_profile('source-v2')['artifactSha256'],
        probeSourceFreezeSha256='1'*64, probeBuildReceiptSha256='2'*64, **FLAGS)

def make_pack(root):
    r = review()
    put(root/'bundle/review.json', r)
    put(root/'bundle/vmlinuz', b'synthetic kernel, never executed')
    put(root/'bundle/initramfs.cpio.gz', b'synthetic initramfs, never executed')
    put(root/'bundle/bundle.json', dict(schemaVersion=1, infraApiVersion=1,
        payloadProfile='developer-vm-v1', developerProfile='developer-vm-v1',
        sessionReview=r, reviewSha256=sha(root/'bundle/review.json'),
        sha256={leaf:sha(root/'bundle'/leaf) for leaf in ('vmlinuz','initramfs.cpio.gz')},
        autostart=[1,2], isolationProfile='source-v2', strictProbeSha256=r['strictProbeSha256'],
        inputDiagnostics=False, diagnosticProfile=None, faultInjection=None,
        probeBuildReceiptSha256=r['probeBuildReceiptSha256'],
        probeSourceFreezeSha256=r['probeSourceFreezeSha256'], hostProvenance=r['hostProvenance'], **FLAGS))

def fake_disk(root, name, size):
    if name != 'developer-128' or size != 128:
        raise AssertionError('disk formatter boundary received nonfixed disk request')
    identifier = '12345678-1234-4234-8234-123456789abc'
    image = root/'disks/developer-128.raw'
    image.parent.mkdir(exist_ok=True)
    with image.open('xb') as f:
        f.truncate(128*1024**2)
        superblock = bytearray(1024)
        struct.pack_into('<I', superblock, 4, 131072)
        struct.pack_into('<I', superblock, 24, 0)
        struct.pack_into('<H', superblock, 56, 0xef53)
        struct.pack_into('<I', superblock, 96, 0x40)
        superblock[104:120] = uuid.UUID(identifier).bytes
        superblock[120:136] = b'MINGOS-DATA'.ljust(16,b'\0')
        f.seek(1024); f.write(superblock)
    manifest = dict(schemaVersion=1, task='boot-infra-20261025', format='raw', filesystem='ext4',
        image='disks/developer-128.raw', sizeMiB=128, uuid=identifier,
        serial='MINGDATA123456781234', creationSha256=sha(image))
    put(root/'disks/developer-128.json', manifest)
    return dict(manifest, imagePath=str(image), manifestPath=str(root/'disks/developer-128.json'))

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='dev-vm-control-')
        self.addCleanup(self.scratch.cleanup)
        self.base=Path(self.scratch.name); self.pack=self.base/'pack'; make_pack(self.pack)
        self.root=self.base/'work/mingos-boot-infra-20261025'
        self.root.parent.mkdir()
        self.results=self.base/'results'; self.results.mkdir()
        self.runtime=self.base/'runtime'; self.runtime.mkdir()
        self.locked=set()
        # Windows stat(path).ctime is creation time while fstat(fd).ctime is
        # metadata change time in this runtime. Linux production stamps retain
        # ctime; this explicit host boundary double removes it only here.
        if sys.platform=='win32':
            original_stamp=d.stamp
            p=patch.object(d,'stamp',lambda info: original_stamp(info)[:-1]);p.start();self.addCleanup(p.stop)
        for name,value in (
            ('_platform',lambda:None), ('_private',lambda p, directory=False: Path(p).stat()),
            ('_read_only_pack',lambda p:None), ('_workspace',self.workspace),
            ('_runtime',lambda p: Path(p) if Path(p)==self.runtime else self.fail('runtime path')),
            ('_output_root',lambda: self.results), ('_runtime_root',lambda:self.runtime),
            ('_lock',self.lock), ('_verify_pack',self.verify_pack), ('_create_disk',fake_disk)):
            p=patch.object(d,name,value,create=True); p.start(); self.addCleanup(p.stop)

    def workspace(self, path, create=False):
        path=Path(path)
        if path != self.root: raise ValueError('workspace')
        if create:
            path.mkdir(); put(path/'.mingos-boot-workspace.json',dict(schemaVersion=1,task='boot-infra-20261025',uid=65534))
        elif not path.is_dir(): raise ValueError('missing workspace')
        return path

    def verify_pack(self, pack, pin):
        if Path(pack)!=self.pack or pin!=PIN: raise ValueError('external pack pin')
        return dict(schemaVersion=1,status='developer-pack-bytes-verified',profile='developer-vm-v1',manifestSha256=PIN,
                    fileCount=4,directoryCount=1,formalQualification=False,finalSystemAccepted=False)

    @contextlib.contextmanager
    def lock(self, path, *, readonly=False):
        path=Path(path)
        if not path.is_file(): raise ValueError('missing lock')
        if path in self.locked: raise ValueError('busy lock')
        self.locked.add(path)
        try: yield
        finally: self.locked.remove(path)

    def initialized(self):
        self.workspace(self.root,True); disk=fake_disk(self.root,'developer-128',128)
        identity=dict(schemaVersion=1,profile='developer-vm-v1',generation='34567890-1234-4234-8234-123456789abc',
            nonce='3'*32,packManifestSha256=PIN,bundleSha256=sha(self.pack/'bundle/bundle.json'),
            diskManifest='disks/developer-128.json',diskUuid=disk['uuid'],diskSerial=disk['serial'],**FLAGS)
        put(self.root/'developer-instance.json',identity)
        put(self.root/'developer-instance.sha256',sha(self.root/'developer-instance.json').encode()+b'\n')
        put(self.root/'developer-instance.lock',b''); (self.root/'attempts').mkdir()
        put(self.disk_lock(),b'')
        return self.root/'developer-instance.json'

    def disk_lock(self):
        key=hashlib.sha256(os.fsencode(self.root/'disks/developer-128.raw')).hexdigest()
        return self.runtime/('disk-'+key+'.lock')

    def report(self,index=1,previous=None):
        boot='boot-'+str(index); marker=dict(bootId=boot,counter=index)
        terminal=dict(type='guest-result',status='normal',desktopExitCode=0,dataSynced=True,poweroffRequested=True,
            bootId=boot,diskUuid='12345678-1234-4234-8234-123456789abc',marker=marker,previousMarker=previous,error=None)
        return dict(status='normal-desktop-poweroff',bootAttempted=True,guestExitCode=0,kernelPowerdown=True,
            bundleIdentity=sha(self.pack/'bundle/bundle.json'),diskUuid=terminal['diskUuid'],bootId=boot,
            lastState=dict(sessionId='session-'+str(index)),marker=marker,previousMarker=previous,
            guestResult=terminal,bootNumber=None,inputScriptSha256=None,diagnosticOnly=True,finalSystemAccepted=False)

    def runner(self,report=None,error=None,execution=None):
        def run(command,attempt):
            out=Path(command[command.index('--output')+1]); out.mkdir()
            put(out/'preserved.log',b'evidence\n')
            if error: raise error
            put(out/'result.json', report or self.report())
            return execution or dict(exitCode=0,timedOut=False,processGroupEmpty=True)
        return patch.object(d,'_execute_vm',run,create=True)

    def start(self,instance,index=1,pin=PIN):
        return d.start(self.pack,pin,instance,self.results/('boot-%04d'%index),self.runtime)

    def test_verify_never_writes_or_executes(self):
        before=sorted(str(p) for p in self.base.rglob('*'))
        with patch('subprocess.Popen',side_effect=AssertionError('process')):
            self.assertEqual(d.verify(self.pack,PIN)['status'],'developer-pack-bytes-verified')
        self.assertEqual(before,sorted(str(p) for p in self.base.rglob('*')))

    def test_wrong_pin_init_leaves_workspace_absent(self):
        with self.assertRaises(ValueError): d.init(self.pack,'0'*64,self.root)
        self.assertFalse(self.root.exists())

    def test_complete_verifier_refusal_precedes_metadata_consumption(self):
        with patch.object(d,'_verify_pack',side_effect=ValueError('complete tree mismatch')), \
             patch.object(d,'_bytes',side_effect=AssertionError('read before admission')):
            with self.assertRaisesRegex(ValueError,'complete tree mismatch'):d.init(self.pack,PIN,self.root)
        self.assertFalse(self.root.exists())

    def test_writable_pack_refuses_before_workspace(self):
        with patch.object(d,'_read_only_pack',side_effect=ValueError('pack must be RO'),create=True):
            with self.assertRaises(ValueError): d.init(self.pack,PIN,self.root)
        self.assertFalse(self.root.exists())

    def test_init_creates_one_new_disk_closed_identity(self):
        result=d.init(self.pack,PIN,self.root)
        self.assertEqual(result['status'],'developer-instance-initialized')
        identity=json.loads((self.root/'developer-instance.json').read_bytes())
        self.assertEqual(identity['diskManifest'],'disks/developer-128.json')
        self.assertEqual(identity['diskUuid'],'12345678-1234-4234-8234-123456789abc')
        self.assertEqual(len(list((self.root/'disks').glob('*.raw'))),1)
        self.assertEqual(sha(self.root/'developer-instance.json'),(self.root/'developer-instance.sha256').read_text().strip())
        self.assertTrue((self.root/'developer-instance.lock').is_file())
        self.assertFalse(identity['formalQualification']); self.assertFalse(identity['finalSystemAccepted'])

    def test_existing_workspace_never_reformatted(self):
        self.initialized(); before=sha(self.root/'disks/developer-128.raw')
        with self.assertRaises((ValueError,FileExistsError)): d.init(self.pack,PIN,self.root)
        self.assertEqual(sha(self.root/'disks/developer-128.raw'),before)

    def test_unreviewed_and_qualification_bundles_refused(self):
        for key,value in [('reviewed',False),('profile','persistent-install'),('extraArgs',['--duration-ms','1000'])]:
            with self.subTest(key=key):
                make_pack(self.pack); r=review(); r[key]=value; put(self.pack/'bundle/review.json',r)
                b=json.loads((self.pack/'bundle/bundle.json').read_bytes()); b['sessionReview']=r;b['reviewSha256']=sha(self.pack/'bundle/review.json');put(self.pack/'bundle/bundle.json',b)
                with self.assertRaises(ValueError): d.init(self.pack,PIN,self.root)
                self.assertFalse(self.root.exists())

    def test_inspect_readonly_and_disk_identity(self):
        instance=self.initialized(); before={str(p):sha(p) for p in self.root.rglob('*') if p.is_file()}
        with patch('subprocess.Popen',side_effect=AssertionError('process')),patch('subprocess.run',side_effect=AssertionError('process')):
            result=d.inspect(instance)
        self.assertEqual(result['status'],'developer-instance-inspected');self.assertEqual(result['state'],'ready')
        self.assertFalse(result['guestBooted']);self.assertEqual(result['normalBoots'],0)
        self.assertEqual(before,{str(p):sha(p) for p in self.root.rglob('*') if p.is_file()})

    def test_inspect_missing_or_busy_lock_never_repairs(self):
        instance=self.initialized(); lock=self.root/'developer-instance.lock'; lock.unlink()
        with self.assertRaises(ValueError): d.inspect(instance)
        self.assertFalse(lock.exists());put(lock,b'');self.locked.add(lock)
        with self.assertRaises(ValueError): d.inspect(instance)

    def test_inspect_checks_existing_runtime_disk_lock(self):
        instance=self.initialized(); disklock=self.disk_lock(); self.locked.add(disklock)
        with self.assertRaises(ValueError): d.inspect(instance)
        self.locked.remove(disklock);disklock.unlink()
        with self.assertRaises(ValueError): d.inspect(instance)
        self.assertFalse(disklock.exists())

    def test_identity_seal_extra_fields_uuid_serial_and_fixed_paths(self):
        instance=self.initialized(); original=json.loads(instance.read_bytes())
        for update in [dict(extra='x'),dict(profile='persistent-install'),dict(diskManifest='../qualified.json'),
                       dict(diskUuid=str(uuid.uuid4())),dict(diskSerial='other'),dict(nonce=True),dict(formalQualification=True)]:
            with self.subTest(update=update):
                put(instance,dict(original,**update));put(self.root/'developer-instance.sha256',sha(instance).encode()+b'\n')
                with self.assertRaises(ValueError): d.inspect(instance)
        put(instance,original);put(self.root/'developer-instance.sha256',b'0'*64+b'\n')
        with self.assertRaises(ValueError):d.inspect(instance)

    def test_superblock_uuid_label_extents_and_size_refused(self):
        instance=self.initialized();image=self.root/'disks/developer-128.raw'
        for offset,change in [(1080,b'\0\0'),(1120,b'\0'*4),(1128,b'\0'*16),(1144,b'WRONG'.ljust(16,b'\0'))]:
            with self.subTest(offset=offset):
                with image.open('r+b') as f:f.seek(offset);old=f.read(len(change));f.seek(offset);f.write(change)
                with self.assertRaises(ValueError):d.inspect(instance)
                with image.open('r+b') as f:f.seek(offset);f.write(old)
        with image.open('r+b') as f:f.truncate(127*1024**2)
        with self.assertRaises(ValueError):d.inspect(instance)

    def test_two_normal_boots_reopen_same_disk_new_identity_chain(self):
        instance=self.initialized(); before=instance.read_bytes();disk=sha(self.root/'disks/developer-128.raw')
        with self.runner(): first=self.start(instance)
        self.assertEqual(first['status'],'developer-session-normal');self.assertEqual(first['normalBoots'],1)
        with self.runner(self.report(2,self.report()['marker'])): second=self.start(instance,2)
        self.assertEqual(second['normalBoots'],2);self.assertTrue(second['developerNormalDiskReopenVerified'])
        self.assertFalse(second['formalQualification']);self.assertFalse(second['finalSystemAccepted'])
        self.assertEqual(before,instance.read_bytes());self.assertEqual(disk,sha(self.root/'disks/developer-128.raw'))
        self.assertEqual(d.inspect(instance)['state'],'ready')

    def test_failure_latches_generation_preserves_output_and_disk(self):
        instance=self.initialized();before=sha(self.root/'disks/developer-128.raw')
        with self.runner(error=RuntimeError('synthetic interrupted runtime')):
            with self.assertRaises(ValueError):self.start(instance)
        self.assertTrue((self.root/'failure-latch.json').is_file());self.assertTrue((self.results/'boot-0001/preserved.log').is_file())
        self.assertEqual(d.inspect(instance).get('state'),'locked')
        with self.runner():
            with self.assertRaises(ValueError):self.start(instance,2)
        self.assertFalse((self.results/'boot-0002').exists());self.assertEqual(before,sha(self.root/'disks/developer-128.raw'))

    def test_incomplete_attempt_locks_without_writing_inspect(self):
        instance=self.initialized();(self.root/'attempts/0001').mkdir()
        before=sorted(str(p) for p in self.root.rglob('*'))
        self.assertEqual(d.inspect(instance).get('state'),'locked')
        self.assertEqual(before,sorted(str(p) for p in self.root.rglob('*')))
        with self.runner():
            with self.assertRaises(ValueError):self.start(instance)

    def test_new_output_required_and_fixed_command_no_qualification(self):
        instance=self.initialized(); out=self.results/'boot-0001';out.mkdir();put(out/'sentinel',b'keep')
        with self.runner():
            with self.assertRaises(ValueError):self.start(instance)
        self.assertEqual((out/'sentinel').read_bytes(),b'keep')
        self.assertFalse((self.root/'attempts/0001').exists())
        out.rename(self.results/'preserved-existing')
        with self.runner():self.start(instance)
        attempt=json.loads((self.root/'attempts/0001/attempt.json').read_bytes())
        command=attempt['command'];self.assertEqual(command[command.index('--timeout')+1],'900')
        self.assertEqual(attempt['budgets'],dict(desktopMs=15000,bootSeconds=900,supervisionSeconds=945,outerSeconds=1950))
        for forbidden in ('--input-script','--boot-number','--dry-run','--probe-paused','--diagnostic-split-input'):
            self.assertNotIn(forbidden,command)

    def test_invalid_normal_or_terminal_or_runner_cleanup_latches(self):
        mutations=[{'status':'timeout-unaccepted'},{'guestExitCode':1},{'kernelPowerdown':False},
                   {'bootAttempted':False},{'diskUuid':str(uuid.uuid4())},{'bundleIdentity':'0'*64},
                   {'guestResult':None},{'previousMarker':{'bootId':'old','counter':8}},
                   {'marker':{'bootId':'boot-1','counter':True}},{'lastState':{}},
                   {'bootNumber':1},{'finalSystemAccepted':True}]
        # Every case gets a separate fresh generation; no reset/delete operation exists.
        for n,mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                self.root=self.base/('work-'+str(n))/'mingos-boot-infra-20261025';self.root.parent.mkdir()
                instance=self.initialized()
                self.results=self.base/('results-'+str(n));self.results.mkdir()
                with self.runner(dict(self.report(),**mutation)):
                    with self.assertRaises(ValueError):self.start(instance)
                self.assertEqual(d.inspect(instance)['state'],'locked')

    def test_second_boot_reused_boot_or_session_or_wrong_chain_latches(self):
        for n,mutation in enumerate([dict(bootId='boot-1'),dict(lastState={'sessionId':'session-1'}),
                                    dict(previousMarker=None),dict(marker={'bootId':'boot-2','counter':3})]):
            with self.subTest(mutation=mutation):
                self.root=self.base/('work-b-'+str(n))/'mingos-boot-infra-20261025';self.root.parent.mkdir()
                self.results=self.base/('results-b-'+str(n));self.results.mkdir();instance=self.initialized()
                with self.runner():self.start(instance)
                with self.runner(dict(self.report(2,self.report()['marker']),**mutation)):
                    with self.assertRaises(ValueError):self.start(instance,2)
                self.assertEqual(d.inspect(instance)['state'],'locked')

    def test_changed_retained_result_refuses_reopen(self):
        instance=self.initialized()
        with self.runner():self.start(instance)
        put(self.results/'boot-0001/result.json',dict(self.report(),bootId='tampered'))
        self.assertEqual(d.inspect(instance).get('state'),'locked')
        with self.runner():
            with self.assertRaises(ValueError):self.start(instance,2)

    def test_changed_admitted_pack_latches_existing_generation(self):
        instance=self.initialized();new_pin='9'*64
        def different(pack,pin):
            result=self.verify_pack(pack,PIN);result['manifestSha256']=new_pin;return result
        with patch.object(d,'_verify_pack',different),self.runner():
            with self.assertRaises(ValueError):self.start(instance,pin=new_pin)
        self.assertTrue((self.root/'failure-latch.json').is_file())
        self.assertFalse((self.results/'boot-0001').exists())

    @unittest.skipIf(sys.platform=='win32','requires actual no-follow Linux symbolic links')
    def test_broken_failure_latch_is_locked_not_repaired(self):
        instance=self.initialized();latch=self.root/'failure-latch.json'
        latch.symlink_to(self.root/'absent-target')
        self.assertEqual(d.inspect(instance)['state'],'locked')
        with self.runner():
            with self.assertRaises(ValueError):self.start(instance)
        self.assertTrue(latch.is_symlink());self.assertFalse((self.results/'boot-0001').exists())

    def test_third_boot_cannot_reuse_first_session_identity(self):
        instance=self.initialized()
        with self.runner():self.start(instance)
        second=self.report(2,self.report()['marker'])
        with self.runner(second):self.start(instance,2)
        third=self.report(3,second['marker']);third['lastState']={'sessionId':'session-1'}
        with self.runner(third):
            with self.assertRaises(ValueError):self.start(instance,3)
        self.assertEqual(d.inspect(instance)['state'],'locked')

    def test_terminal_boolean_exit_code_is_not_normal_integer_zero(self):
        instance=self.initialized();report=self.report();report['guestResult']['desktopExitCode']=False
        with self.runner(report):
            with self.assertRaises(ValueError):self.start(instance)
        self.assertEqual(d.inspect(instance)['state'],'locked')

    def test_timeout_nonzero_or_remaining_owned_processes_not_accepted(self):
        for n,execution in enumerate([dict(exitCode=0,timedOut=True,processGroupEmpty=True),
            dict(exitCode=1,timedOut=False,processGroupEmpty=True),dict(exitCode=0,timedOut=False,processGroupEmpty=False)]):
            with self.subTest(execution=execution):
                self.root=self.base/('work-c-'+str(n))/'mingos-boot-infra-20261025';self.root.parent.mkdir()
                self.results=self.base/('results-c-'+str(n));self.results.mkdir();instance=self.initialized()
                with self.runner(execution=execution):
                    with self.assertRaises(ValueError):self.start(instance)
                self.assertEqual(d.inspect(instance)['state'],'locked')

    def test_adapter_wait_interruptions_latch_and_reject_reopen(self):
        import signal
        for n,error_type in enumerate((KeyboardInterrupt,RuntimeError)):
            with self.subTest(error_type=error_type.__name__):
                self.root=self.base/('work-interrupt-'+str(n))/'mingos-boot-infra-20261025';self.root.parent.mkdir()
                self.results=self.base/('results-interrupt-'+str(n));self.results.mkdir();instance=self.initialized()
                calls=[]
                class Child:
                    pid=43210
                    def wait(self,timeout):
                        calls.append(('wait',timeout))
                        if timeout==945:raise error_type('synthetic wait interruption')
                        return -9
                def kill(pid,sig):
                    calls.append(('killpg',pid,sig))
                    if sig==0:raise ProcessLookupError
                with patch('subprocess.Popen',return_value=Child()), \
                     patch('os.killpg',kill,create=True),patch.object(signal,'SIGKILL',9,create=True):
                    with self.assertRaises(ValueError):self.start(instance)
                self.assertEqual(d.inspect(instance)['state'],'locked')
                self.assertTrue((self.root/'failure-latch.json').is_file())
                self.assertTrue((self.root/'attempts/0001/result.json').is_file())
                self.assertIn(('killpg',43210,9),calls)
                self.assertIn(('wait',5),calls)
                with patch('subprocess.Popen',side_effect=AssertionError('failed generation restarted')):
                    with self.assertRaises(ValueError):self.start(instance,2)

class AdapterTests(unittest.TestCase):
    def setUp(self):
        import signal
        p=patch.object(signal,'SIGKILL',9,create=True);p.start();self.addCleanup(p.stop)

    def test_fixed_adapter_waits_945_and_receipts_normal_exit(self):
        self.assertTrue(callable(getattr(d,'_execute_vm',None)),'missing supervised runtime adapter')
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            class Child:
                pid=43210
                def wait(self,timeout):calls.append(('wait',timeout));return 0
            def spawn(command,**options):
                self.assertEqual(command,['trusted-vm','fixed-args'])
                self.assertTrue(options['start_new_session']);self.assertEqual(options['stdin'],-3)
                self.assertEqual(options.get('umask'),0o077,'child result.json must be private')
                self.assertNotIn('shell',options);calls.append(('spawn',));return Child()
            with patch('subprocess.Popen',spawn),patch('os.killpg',side_effect=ProcessLookupError,create=True):
                result=d._execute_vm(['trusted-vm','fixed-args'],Path(temp))
            self.assertEqual(result,dict(exitCode=0,timedOut=False,processGroupEmpty=True))
            self.assertEqual(calls,[('spawn',),('wait',945)])
            self.assertTrue((Path(temp)/'supervisor.stdout.log').is_file())

    def test_timeout_kills_only_owned_group_preserves_failure(self):
        self.assertTrue(callable(getattr(d,'_execute_vm',None)),'missing supervised runtime adapter')
        import signal
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            class Child:
                pid=43210
                def wait(self,timeout):
                    calls.append(('wait',timeout))
                    if timeout==945:raise subprocess.TimeoutExpired('trusted-vm',945)
                    return -9
            with patch('subprocess.Popen',return_value=Child()),patch('os.killpg',side_effect=lambda pid,sig:calls.append(('killpg',pid,sig)),create=True):
                result=d._execute_vm(['trusted-vm'],Path(temp))
            self.assertTrue(result['timedOut']);self.assertNotEqual(result['exitCode'],0)
            self.assertIn(('killpg',43210,signal.SIGKILL),calls)
            self.assertTrue(all(c[1]==43210 for c in calls if c[0]=='killpg'))

    def test_remaining_group_after_vm_exit_is_failure(self):
        self.assertTrue(callable(getattr(d,'_execute_vm',None)),'missing supervised runtime adapter')
        import signal
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            class Child:
                pid=43210
                def wait(self,timeout):return 0
            with patch('subprocess.Popen',return_value=Child()),patch('os.killpg',side_effect=lambda pid,sig:calls.append((pid,sig)),create=True):
                result=d._execute_vm(['trusted-vm'],Path(temp))
            self.assertFalse(result['processGroupEmpty'])
            self.assertIn((43210,signal.SIGKILL),calls)

    def test_wait_interruptions_kill_owned_group_reap_and_preserve_original(self):
        for error_type in (KeyboardInterrupt,RuntimeError):
            with self.subTest(error_type=error_type.__name__),tempfile.TemporaryDirectory() as temp:
                calls=[];original=error_type('synthetic wait interruption')
                class Child:
                    pid=43210
                    def wait(self,timeout):
                        calls.append(('wait',timeout))
                        if timeout==945:raise original
                        return -9
                def kill(pid,sig):
                    calls.append(('killpg',pid,sig))
                    if sig==0:raise ProcessLookupError
                with patch('subprocess.Popen',return_value=Child()),patch('os.killpg',kill,create=True):
                    with self.assertRaises(error_type) as caught:d._execute_vm(['trusted-vm'],Path(temp))
                self.assertIs(caught.exception,original)
                self.assertIn(('killpg',43210,9),calls);self.assertIn(('wait',5),calls)
                self.assertTrue(all(x[1]==43210 for x in calls if x[0]=='killpg'))
                receipt=json.loads((Path(temp)/'supervisor-interruption.json').read_bytes())
                self.assertEqual(receipt['originalError'],repr(original));self.assertEqual(receipt['cleanupErrors'],[])
                self.assertIs(receipt['processGroupEmpty'],True)

    def test_wait_exception_retains_cleanup_failure_without_empty_group_claim(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            original=RuntimeError('synthetic original error')
            class Child:
                pid=43210
                def wait(self,timeout):
                    if timeout==945:raise original
                    raise subprocess.TimeoutExpired('cleanup wait',timeout)
            def kill(pid,sig):
                self.assertEqual(pid,43210)
                if sig==0:raise ProcessLookupError
            with patch('subprocess.Popen',return_value=Child()),patch('os.killpg',kill,create=True):
                with self.assertRaises(RuntimeError) as caught:d._execute_vm(['trusted-vm'],Path(temp))
            self.assertIs(caught.exception,original)
            self.assertTrue((Path(temp)/'supervisor-interruption.json').is_file(),'cleanup evidence missing')
            receipt=json.loads((Path(temp)/'supervisor-interruption.json').read_bytes())
            self.assertFalse(receipt['processGroupEmpty'])
            self.assertIn('TimeoutExpired',receipt['cleanupErrors'][0])

    def test_actual_isolated_vm_help_from_unrelated_cwd_ignores_pythonpath(self):
        """Only argparse --help executes; no guest/disk/pack is consumed."""
        self.assertTrue(callable(getattr(d,'_command',None)),'missing fixed command builder')
        import subprocess
        with tempfile.TemporaryDirectory(prefix='vm-help-control-') as temp:
            directory=Path(temp);marker=directory/'untrusted-imported'
            put(directory/'bootlib.py',('from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\nraise AssertionError("untrusted PYTHONPATH")\n').encode())
            command=d._command(Path('/pack'),Path('/work/mingos-boot-infra-20261025'),Path('/results/boot-0001'),Path('/run/mingos-runtime'))
            env=dict(os.environ,PYTHONPATH=str(directory),PYTHONHOME=str(directory/'nonexistent'))
            result=subprocess.run(command+['--help'],cwd=directory,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('--data-manifest',result.stdout)
            self.assertFalse(marker.exists())

    @unittest.skipUnless(sys.platform=='linux','requires actual POSIX subprocess umask')
    def test_actual_child_outputs_private_under_parent_umask_022(self):
        import stat
        with tempfile.TemporaryDirectory(prefix='vm-umask-control-') as temp:
            directory=Path(temp);attempt=directory/'attempt';attempt.mkdir(mode=0o700)
            result_dir=directory/'child-output'
            program='import pathlib,sys;p=pathlib.Path(sys.argv[1]);p.mkdir();(p/"result.json").write_text("synthetic child only\\n")'
            before=os.umask(0o022)
            try:
                receipt=d._execute_vm([sys.executable,'-I','-B','-c',program,str(result_dir)],attempt)
            finally:os.umask(before)
            self.assertEqual(receipt,dict(exitCode=0,timedOut=False,processGroupEmpty=True))
            self.assertEqual(stat.S_IMODE((result_dir/'result.json').stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE(result_dir.stat().st_mode),0o700)
            self.assertEqual(stat.S_IMODE((attempt/'supervisor.stdout.log').stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE((attempt/'supervisor.stderr.log').stat().st_mode),0o600)

class PublicArgumentsTests(unittest.TestCase):
    def test_cli_refuses_disk_input_timeout_and_arbitrary_runtime_options(self):
        self.assertTrue(callable(getattr(d,'main',None)),'missing closed CLI')
        for option in ['--existing-disk','--timeout','--boot-number','--qemu-args','--input-script','--reset']:
            with self.subTest(option=option),patch('sys.stderr'):
                with self.assertRaises(SystemExit) as caught:
                    d.main(['start','--pack','/pack','--manifest-sha256',PIN,
                        '--instance','/work/mingos-boot-infra-20261025/developer-instance.json',
                        '--output','/results/boot-0001','--runtime-root','/run/mingos-runtime',option,'value'])
                self.assertEqual(caught.exception.code,2)

if __name__=='__main__':unittest.main()
