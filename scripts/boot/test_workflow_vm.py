"""Linux real filesystem/flock controls; compiler, disk formatter and guest are doubles."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
import workflow_vm as d
import workflow_pack as wp
import developer_pack as dp
import vm
import data_disk
import test_workflow_pack as pack_controls

ROOT=Path('/work/mingos-boot-infra-20261025'); RUNTIME=Path('/run/mingos-runtime')
PIN='a'*64; BUNDLE='b'*64; CID1='1'*64; CID2='2'*64

def put(path,value):
    path=Path(path)
    raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode() if isinstance(value,dict) else value
    path.write_bytes(raw);path.chmod(0o600)

def publish(path,value):
    d._publish(path,value)

def fake_disk(root,name,size):
    assert name=='workflow-128' and size==128
    identifier='12345678-1234-4234-8234-123456789abc'
    (root/'disks').mkdir(mode=0o700)
    image=root/d.DISK_IMAGE
    with image.open('xb') as f:
        f.truncate(128*1024**2);sb=bytearray(1024)
        struct.pack_into('<I',sb,4,131072);struct.pack_into('<H',sb,56,0xef53)
        struct.pack_into('<I',sb,96,0x40);sb[104:120]=uuid.UUID(identifier).bytes
        sb[120:136]=b'MINGOS-DATA'.ljust(16,b'\0');f.seek(1024);f.write(sb)
    image.chmod(0o600)
    manifest=dict(schemaVersion=1,task='boot-infra-20261025',format='raw',filesystem='ext4',
        image=d.DISK_IMAGE,sizeMiB=128,uuid=identifier,serial='MINGDATA123456781234',creationSha256=d._file_hash(image))
    put(root/d.DISK_MANIFEST,manifest);return manifest

def report(index,previous=None):
    marker=dict(bootId='boot-'+str(index),counter=index)
    terminal=dict(type='guest-result',status='normal',desktopExitCode=0,dataSynced=True,poweroffRequested=True,
        bootId=marker['bootId'],diskUuid='12345678-1234-4234-8234-123456789abc',marker=marker,previousMarker=previous,error=None)
    return dict(status='normal-desktop-poweroff',bootAttempted=True,guestExitCode=0,kernelPowerdown=True,
        bundleIdentity=BUNDLE,diskUuid=terminal['diskUuid'],bootId=marker['bootId'],
        lastState=dict(sessionId='session-'+str(index)),marker=marker,previousMarker=previous,
        guestResult=terminal,bootNumber=None,inputScriptSha256=None,diagnosticOnly=True,finalSystemAccepted=False)

class Lifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if (sys.platform!='linux' or not hasattr(os,'geteuid') or os.geteuid()!=65534
                or os.getegid()!=65534 or os.environ.get('MINGOS_WORKFLOW_TEST_TMPFS')!='1'):
            raise unittest.SkipTest('destructive fixtures require explicit Linux uid65534 isolated tmpfs opt-in')
        mounts={}
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            fields=line.split();separator=fields.index('-')
            mounts[fields[4]]=fields[separator+1]
        required=('/work','/backup','/results','/run/mingos-runtime')
        if any(mounts.get(path)!='tmpfs' for path in required):
            raise unittest.SkipTest('every fixed destructive fixture root must be its own tmpfs mount')

    def setUp(self):
        prior_mask=os.umask(0o077);self.addCleanup(os.umask,prior_mask)
        for directory in (ROOT,Path('/backup/snapshot')):
            if directory.exists():shutil.rmtree(directory)
        for base in (RUNTIME,Path('/results'),Path('/backup')):
            for child in base.iterdir():
                if child.is_dir():shutil.rmtree(child)
                else:child.unlink()
        preserved=Path('/work/preserved-original')
        if preserved.exists():shutil.rmtree(preserved)
        publish(RUNTIME/'current-container-id',(CID1+'\n').encode())
        self.patches=[]
        for name,value in (('_admit',lambda pack,pin:dict(pack=Path('/pack'),pin=pin,bundleSha256=BUNDLE)),
                           ('_create_disk',fake_disk)):
            p=patch.object(d,name,value);p.start();self.addCleanup(p.stop)
        self.instance=ROOT/d.INSTANCE

    def init(self):return d.init(Path('/pack'),PIN,ROOT,RUNTIME)

    @contextlib.contextmanager
    def runner(self,index,error=None):
        def run(command,attempt):
            # This actual second process-independent descriptor conflicts with the
            # coordinator's lock, demonstrating that guest execution is covered.
            with self.assertRaises(ValueError):
                with d._lock(ROOT/'workflow-instance.lock'):pass
            put(attempt/'supervisor.stdout.log',b'synthetic guest boundary\n')
            put(attempt/'supervisor.stderr.log',b'')
            if error:raise error
            out=Path(command[command.index('--output')+1]);out.mkdir(mode=0o700)
            prior=dict(bootId='boot-'+str(index-1),counter=index-1) if index>1 else None
            put(out/'result.json',report(index,prior))
            return dict(exitCode=0,timedOut=False,processGroupEmpty=True)
        with patch.object(d,'_execute_vm',run):yield

    def boot1(self):
        self.init()
        with self.runner(1):return d.start(Path('/pack'),PIN,self.instance,Path('/results/boot-0001'),RUNTIME)

    def fresh(self,cid=CID2):
        for child in RUNTIME.iterdir():
            if child.is_dir():shutil.rmtree(child)
            else:child.unlink()
        publish(RUNTIME/'current-container-id',(cid+'\n').encode())

    @contextlib.contextmanager
    def gate(self,change=None):
        errors=[]
        def outer():
            try:
                deadline=time.monotonic()+5
                while not d._ready_path(RUNTIME/'transition-request.json').exists():
                    if time.monotonic()>deadline:raise AssertionError('no transition request')
                    time.sleep(.01)
                raw=d._published(RUNTIME/'transition-request.json');request=json.loads(raw)
                with self.assertRaises(ValueError):
                    with d._lock(ROOT/'workflow-instance.lock'):pass
                receipt=dict(schemaVersion=1,profile=d.PROFILE,requestSha256=hashlib.sha256(raw).hexdigest(),
                    priorContainerId=request['priorContainerId'],currentContainerId=request['currentContainerId'],
                    priorState='exited',activeWritableContainerIds=[request['currentContainerId']],
                    priorInspectSha256='3'*64,writerScanSha256='4'*64)
                if change:receipt.update(change)
                publish(RUNTIME/'transition-receipt.json',receipt)
            except BaseException as error:errors.append(error)
        thread=threading.Thread(target=outer);thread.start()
        try:yield
        finally:
            thread.join(timeout=6)
            if errors:raise errors[0]

    def test_first_and_explicit_fresh_runtime_reopen(self):
        self.boot1();self.fresh()
        with self.gate(),self.runner(2):
            result=d.start(Path('/pack'),PIN,self.instance,Path('/results/boot-0002'),RUNTIME,reopen=True)
        self.assertEqual(result['normalBoots'],2)
        inspected=d.inspect(self.instance)
        self.assertEqual(inspected['state'],'ready');self.assertEqual(inspected['normalBoots'],2)

    def test_completed_start_refuses_and_latches(self):
        self.boot1();self.fresh()
        with self.assertRaisesRegex(ValueError,'explicit reopen'):
            d.start(Path('/pack'),PIN,self.instance,Path('/results/boot-0002'),RUNTIME)
        self.assertEqual(d.inspect(self.instance)['state'],'locked')

    def test_wrong_outer_gate_refuses_before_guest(self):
        self.boot1();self.fresh()
        with self.gate({'priorState':'running'}),patch.object(d,'_execute_vm',side_effect=AssertionError('guest')):
            with self.assertRaisesRegex(ValueError,'receipt differs'):
                d.start(Path('/pack'),PIN,self.instance,Path('/results/boot-0002'),RUNTIME,reopen=True)
        self.assertEqual(d.inspect(self.instance)['state'],'locked')

    def test_guest_failure_retained_and_no_continuation(self):
        self.init()
        with self.runner(1,error=RuntimeError('owned driver failed')):
            with self.assertRaisesRegex(ValueError,'retained and locked'):
                d.start(Path('/pack'),PIN,self.instance,Path('/results/boot-0001'),RUNTIME)
        self.assertTrue((ROOT/'attempts/0001/result.json').is_file())
        self.assertEqual(d.inspect(self.instance)['state'],'locked')

    def test_disk_tamper_refuses_readonly(self):
        self.boot1()
        with (ROOT/d.DISK_IMAGE).open('r+b') as f:f.seek(8192);f.write(b'changed')
        before=(ROOT/d.INSTANCE).read_bytes()
        self.assertEqual(d.inspect(self.instance)['state'],'locked')
        self.assertEqual(before,(ROOT/d.INSTANCE).read_bytes())

    def test_stopped_backup_has_full_self_contained_inventory(self):
        self.boot1();self.fresh()
        with self.gate():result=d.backup(self.instance,Path('/backup'),RUNTIME)
        value=d._verify_backup(Path('/backup'),result['backupSha256'])
        self.assertEqual(value['normalBoots'],1)
        self.assertIn('attempts/0001/report.json',value['files'])
        self.assertIn(d.DISK_IMAGE,value['files'])
        self.assertFalse((ROOT/'failure-latch.json').exists())

    def test_incomplete_or_changed_history_refuses_backup(self):
        self.boot1();self.fresh()
        put(ROOT/'attempts/0001/report.json',b'{}\n')
        with self.assertRaisesRegex(ValueError,'report changed'):d.backup(self.instance,Path('/backup'),RUNTIME)
        self.assertEqual(list(Path('/backup').iterdir()),[])

    def test_busy_persistent_lock_refuses(self):
        self.init()
        with d._lock(ROOT/'workflow-instance.lock'):
            with self.assertRaisesRegex(ValueError,'busy owned lock'):d.inspect(self.instance)

    def test_symlink_copy_member_rejected(self):
        self.boot1();(ROOT/'escape').symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):d._inventory(ROOT)

    def test_source_helpers_do_not_mutate_preview_profile(self):
        self.assertEqual(wp.PROFILE,'developer-workflow-v1')
        self.assertEqual(dp.PROFILE,'developer-vm-v1')
        # Identical generic schema still admits only its own exact purpose/profile.
        for verifier,profile,purpose in ((wp,'developer-vm-v1','developer-preview-input-only'),
                                       (dp,'developer-workflow-v1','developer-workflow-input-only')):
            with self.assertRaises(ValueError):verifier.validate_schema(dict(schemaVersion=1,
                profile=profile,purpose=purpose,algorithm='sha256',files={},directories={},origins={},trust={},
                formalQualification=False,finalSystemAccepted=False))

    def test_restore_preserves_original_and_branches_history(self):
        self.boot1();self.fresh()
        with self.gate():saved=d.backup(self.instance,Path('/backup'),RUNTIME)
        original=d._inventory(ROOT)
        os.rename(ROOT,Path('/work/preserved-original'))
        self.fresh('5'*64)
        actual_statvfs=os.statvfs
        class Readonly:
            f_flag=os.ST_RDONLY
        # Mount read-only is an explicit boundary double in this control only.
        with patch.object(d.os,'statvfs',lambda p:Readonly() if Path(p)==Path('/backup') else actual_statvfs(p)):
            restored=d.restore(Path('/pack'),PIN,Path('/backup'),saved['backupSha256'],ROOT,RUNTIME)
        self.assertNotEqual(restored['generation'],saved['generation'])
        self.assertEqual(restored['normalBoots'],1)
        self.assertEqual(d._inventory(Path('/work/preserved-original')),original)
        self.assertEqual(d.inspect(self.instance)['state'],'ready')
        self.fresh('6'*64)
        with self.gate(),self.runner(2):
            result=d.start(Path('/pack'),PIN,self.instance,Path('/results/boot-0002'),RUNTIME,reopen=True)
        self.assertEqual(result['normalBoots'],2)
        self.assertEqual(d.inspect(self.instance)['state'],'ready')
        self.assertEqual(d._inventory(Path('/work/preserved-original')),original)

    def test_retained_transition_tamper_locks_history(self):
        self.boot1();self.fresh()
        with self.gate(),self.runner(2):
            d.start(Path('/pack'),PIN,self.instance,Path('/results/boot-0002'),RUNTIME,reopen=True)
        path=ROOT/'transitions/0002.json';value=json.loads(path.read_bytes())
        value['receipt']['priorState']='running';put(path,value)
        self.assertEqual(d.inspect(self.instance)['state'],'locked')

    def test_real_vm_main_parser_reaches_runtime_for_generated_interactive_command(self):
        command=d._command(Path('/pack'),ROOT,Path('/results/boot-0001'),RUNTIME)
        # Reaching this boundary proves real parser/admission flags passed. No
        # QEMU, bundle artifacts or guest are executed by this control.
        review=dict(profile=d.PROFILE,reviewed=True,reviewer='synthetic admission control',
            inputScriptSha256=None,extraArgs=[])
        manifest=dict(sessionReview=review,infraApiVersion=1,inputDiagnostics=True,
            diagnosticProfile='input-pointer-ready-v1',developerProfile=d.PROFILE,
            workflowBudgets=d.BUDGETS,formalQualification=False,finalSystemAccepted=False,
            instrumentedQualification=False)
        boundary=RuntimeError('reached actual runtime admission')
        with patch.object(sys,'argv',['vm.py']+command[5:]), \
             patch.object(vm,'validate_bundle',return_value=manifest), \
             patch.object(vm,'sha256',return_value=PIN), \
             patch.object(vm.Path,'read_text',return_value=json.dumps(review)), \
             patch.object(vm,'validate_reviewed_input',return_value=(None,None)), \
             patch.object(vm,'reviewed_input_spec',return_value=None), \
             patch('workflow_profile.session_contract',return_value=review), \
             patch.object(data_disk,'runtime_directory',side_effect=boundary):
            with self.assertRaisesRegex(RuntimeError,'actual runtime admission'):vm.main()

    def test_current_waits_for_ready_despite_early_empty_data_file(self):
        path=RUNTIME/'current-container-id';path.unlink();d._ready_path(path).unlink()
        put(path,b'')
        def producer():
            time.sleep(.15)
            with path.open('wb') as output:
                output.write((CID2+'\n').encode());output.flush();os.fsync(output.fileno())
            d._write(d._ready_path(path),b'')
        thread=threading.Thread(target=producer);thread.start()
        try:self.assertEqual(d._current(RUNTIME),CID2)
        finally:thread.join(timeout=3)

    def test_publish_data_precedes_ready_and_fails_without_repair(self):
        path=RUNTIME/'protocol-probe.json';observed=[];original=d._write
        def monitor(target,value):
            observed.append(Path(target).name)
            if Path(target)==d._ready_path(path):
                self.assertEqual(json.loads(path.read_bytes()),{'complete':True})
            return original(target,value)
        with patch.object(d,'_write',side_effect=monitor):d._publish(path,{'complete':True})
        self.assertEqual(observed,['protocol-probe.json','protocol-probe.json.ready'])
        self.assertEqual(d._published(path),path.read_bytes())
        with self.assertRaises(FileExistsError):d._publish(path,{'changed':True})
        self.assertEqual(json.loads(d._published(path)),{'complete':True})

    def test_invalid_ready_marker_refuses_before_data_read(self):
        path=RUNTIME/'protocol-probe.json';put(path,b'{}\n');put(d._ready_path(path),b'bad')
        with self.assertRaises(ValueError):d._published(path)

    def test_gate_waits_for_receipt_ready_despite_early_empty_file(self):
        self.boot1();self.fresh()
        original=d._publish
        def delayed(path,value):
            if Path(path).name=='transition-receipt.json':
                put(path,b'');time.sleep(.15)
                raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
                with path.open('wb') as output:
                    output.write(raw);output.flush();os.fsync(output.fileno())
                d._write(d._ready_path(path),b'')
            else:original(path,value)
        with patch.object(d,'_publish',side_effect=delayed),self.gate(),self.runner(2):
            result=d.start(Path('/pack'),PIN,self.instance,Path('/results/boot-0002'),RUNTIME,reopen=True)
        self.assertEqual(result['normalBoots'],2)

class PackInteroperability(unittest.TestCase):
    def test_complete_valid_workflow_fixture_and_cross_profile_refusal(self):
        fixture=pack_controls.PackTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        for directory in (fixture.pack/'bin',fixture.pack/'meta'):directory.chmod(0o755)
        self.assertEqual(wp.verify_pack(fixture.pack,fixture.pin)['profile'],wp.PROFILE)
        with self.assertRaisesRegex(ValueError,'profile'):dp.validate_schema(fixture.value)
        old=dict(fixture.value,profile=dp.PROFILE,purpose='developer-preview-input-only')
        dp.validate_schema(old)
        with self.assertRaisesRegex(ValueError,'profile'):wp.validate_schema(old)
        wrongpin=fixture.seal(old)
        with self.assertRaisesRegex(ValueError,'profile'):wp.verify_pack(fixture.pack,wrongpin)

if __name__=='__main__':unittest.main(verbosity=2)
