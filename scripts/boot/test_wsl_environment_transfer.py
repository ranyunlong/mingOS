"""INPUT transfer controls. Synthetic proof fixtures never establish actual S3 admission."""
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_source_build import ProofFixture, digest
import source_build

try:
    import wsl_environment_transfer as transfer
except ModuleNotFoundError:
    transfer = None


class TransferAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(transfer, 'admitted INPUT transfer implementation is missing')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.f = ProofFixture(self.root)
        self.enterContext(patch.object(source_build, 'OBSERVER_CODE_SHA256', digest(self.f.observer_bytes)))
        self.enterContext(patch.object(source_build, 'WITNESS_CODE_SHA256', digest(self.f.witness_bytes)))
        self.q = source_build.qualify_source_build(self.f.spec_path, digest(self.f.spec_path.read_bytes()))
        h = self.q['spec']['handoff']
        self.kw = dict(environment_receipt=Path(self.q['environment']['path']), source=self.f.archive,
            destination='/var/lib/'+self.f.gen+'/s3/'+self.q['spec']['generation']+'/inputs/handoff/handoff.tar',
            sha256=h['sha256'], size=h['bytes'], mode=0o444, spec=self.f.spec_path,
            spec_sha256=digest(self.f.spec_path.read_bytes()), output_directory=self.root/'operation', timeout=1)

    def test_invalid_external_authority_never_starts_runtime(self):
        for change in ({'spec_sha256':'f'*64}, {'spec_sha256':'invalid'},
                       {'environment_receipt':self.f.archive}):
            with self.subTest(change=change), patch.object(subprocess, 'Popen', side_effect=AssertionError('WSL forbidden')):
                with self.assertRaises(ValueError): transfer.transfer_environment(**{**self.kw, **change})
                self.assertFalse((self.root/'operation').exists())

    def test_fixed_role_destination_pin_mode_and_timeout_before_runtime(self):
        for change in ({'source':self.f.harness}, {'destination':'/tmp/arbitrary'},
                       {'destination':self.kw['destination']+'/../escape'}, {'mode':0o555},
                       {'size':True}, {'size':0}, {'sha256':'a'*64}, {'timeout':-1},
                       {'timeout':True}, {'timeout':self.q['spec']['limits']['transferTimeoutSeconds']+1}):
            with self.subTest(change=change), patch.object(subprocess, 'Popen', side_effect=AssertionError('WSL forbidden')):
                with self.assertRaises(ValueError): transfer.transfer_environment(**{**self.kw, **change})
                self.assertFalse((self.root/'operation').exists())

    def test_capacity_counts_all_inputs_and_manifest_twice(self):
        context = transfer._local_context(self.kw['environment_receipt'], self.kw['spec'], self.kw['spec_sha256'], 1)
        payload = self.q['spec']['handoff']['bytes'] + len((self.f.harness/'manifest.json').read_bytes()) + len((self.f.harness/'scripts/boot/helper.py').read_bytes())
        self.assertEqual(context['inputBytes'], payload)
        needed = transfer._capacity_required(context, retained=0)
        self.assertGreaterEqual(needed, 2*payload+self.q['spec']['limits']['linuxReserveBytes'])
        self.assertEqual(needed-transfer._capacity_required(context, retained=13), 13)
        with self.assertRaises(ValueError): transfer._check_capacity(context, needed-1, retained=0)
        transfer._check_capacity(context, needed, retained=0)

    def test_frozen_harness_checks_exact_membership_and_original_manifest(self):
        frozen = self.root/'frozen'
        source_build.freeze_harness(self.q['spec'], self.q, output_directory=frozen)
        context = transfer._local_context(self.kw['environment_receipt'], self.kw['spec'], self.kw['spec_sha256'], 1)
        members = transfer._frozen_harness(context, frozen)
        self.assertEqual(set(members), {'scripts/boot/helper.py','manifest.json'})
        (frozen/'harness'/'extra').write_bytes(b'extra')
        with self.assertRaises(ValueError): transfer._frozen_harness(context, frozen)

    def test_source_recheck_detects_mutation_even_with_open_descriptor(self):
        with transfer._pinned_source(self.f.archive, self.kw['sha256'], self.kw['size']) as original:
            self.assertEqual(original.read(), b'fixture handoff bytes')
        with self.assertRaises(ValueError):
            with transfer._pinned_source(self.f.archive, self.kw['sha256'], self.kw['size']):
                self.f.archive.write_bytes(b'x'*self.kw['size'])

    def test_operation_record_rejects_extra_keys_types_caps_and_bad_identity(self):
        record = dict(schemaVersion=1,operationNonce='a'*32,action='receive',status='started',child=None,
            bytes=0,sha256=None,sealSha256=None,error=None,possibleSurvivors=False,observations=[])
        transfer.validate_operation_record(record)
        for change in ({'bytes':True},{'schemaVersion':True},{'arbitrary':{}},{'error':'x'*4097},
                       {'observations':[{}]*33},{'child':{'pid':1,'starttime':'1','pgid':2,'sid':1}},
                       {'status':'succeeded','possibleSurvivors':True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                transfer.validate_operation_record({**record,**change})


@unittest.skipUnless(os.name=='posix' and Path('/proc/self/stat').exists(), 'real Linux pipes, sessions and /proc required')
class LinuxStreamTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(transfer, 'admitted INPUT transfer implementation is missing')
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.n=0

    def operation(self):
        self.n+=1; path=self.root/str(self.n);path.mkdir();return path

    def run_command(self, code, *, input_file=None, timeout=2, policy='RECEIVER_CONTROL'):
        return transfer._supervise([sys.executable,'-I','-B','-c',code], input_file=input_file,
            output_policy=policy,guest_deadline=time.monotonic()+timeout,operation_directory=self.operation())

    def test_binary_regular_descriptor_forwarding_and_independent_channels(self):
        data=bytes(range(256))*4097
        p=self.root/'binary';p.write_bytes(data)
        with p.open('rb') as incoming:
            r=self.run_command('import sys,hashlib,json; d=sys.stdin.buffer.read();sys.stderr.buffer.write(b"diagnostic\\x00"); print(json.dumps({"bytes":len(d),"sha256":hashlib.sha256(d).hexdigest()}))',input_file=incoming)
        self.assertEqual(json.loads(r['stdout']),{'bytes':1048832,'sha256':hashlib.sha256(data).hexdigest()})
        self.assertEqual(r['stderr'],b'diagnostic\x00')
        self.assertEqual(r['exitCode'],0)
        self.assertEqual(r['child']['pgid'],r['child']['pid'])
        self.assertEqual(r['child']['sid'],r['child']['pid'])

    def test_nonseekable_pipe_spools_exclusively_then_rb_and_retains_partial(self):
        data=bytes(range(256))*4097
        p=self.root/'source';p.write_bytes(data)
        producer=subprocess.Popen([sys.executable,'-I','-c','import sys;sys.stdout.buffer.write(open(sys.argv[1],"rb").read())',str(p)],stdout=subprocess.PIPE)
        with producer.stdout:
            spool=transfer.stage_input(producer.stdout,self.root/'input.partial',hashlib.sha256(data).hexdigest(),len(data),guest_deadline=time.monotonic()+5)
        self.assertEqual(producer.wait(2),0)
        with spool.open('rb') as stream:
            self.assertTrue(stream.seekable());self.assertEqual(hashlib.file_digest(stream,'sha256').hexdigest(),hashlib.sha256(data).hexdigest())
        with io.BytesIO(b'partial') as incoming, self.assertRaises(ValueError):
            transfer.stage_input(incoming,self.root/'bad.partial','0'*64,10,guest_deadline=time.monotonic()+2)
        self.assertEqual((self.root/'bad.partial').read_bytes(),b'partial')
        with io.BytesIO(data) as incoming,self.assertRaises(FileExistsError):
            transfer.stage_input(incoming,self.root/'input.partial',hashlib.sha256(data).hexdigest(),len(data),guest_deadline=time.monotonic()+2)

    def test_spool_sentinel_rejects_extra_byte_and_retains_it(self):
        with io.BytesIO(b'ab') as incoming,self.assertRaises(ValueError):
            transfer.stage_input(incoming,self.root/'extra.partial',hashlib.sha256(b'a').hexdigest(),1,guest_deadline=time.monotonic()+2)
        self.assertLessEqual((self.root/'extra.partial').stat().st_size,2)

    def test_stalled_real_pipe_obeys_deadline(self):
        rd,wr=os.pipe()
        started=time.monotonic()
        try:
            with os.fdopen(rd,'rb',buffering=0) as incoming,self.assertRaises(TimeoutError):
                transfer.stage_input(incoming,self.root/'stalled.partial','0'*64,1,guest_deadline=time.monotonic()+0.25)
        finally: os.close(wr)
        self.assertLess(time.monotonic()-started,6)
        self.assertTrue((self.root/'stalled.partial').exists())

    def test_no_output_hang_stderr_flood_and_control_limit_fail_bounded(self):
        for code in ('import time;time.sleep(20)', 'import os;os.write(2,b"x"*1048577)', 'import os;os.write(1,b"x"*65537)'):
            start=time.monotonic()
            with self.subTest(code=code),self.assertRaises((ValueError,TimeoutError)):
                self.run_command(code,timeout=0.4)
            self.assertLess(time.monotonic()-start,6)

    def test_nonzero_preserves_partial_channels_and_failed_record(self):
        with self.assertRaises(ValueError): self.run_command('import os;os.write(1,b"partial");os.write(2,b"reason");raise SystemExit(4)')
        folder=self.root/'1'
        self.assertEqual((folder/'stdout.log').read_bytes(),b'partial')
        self.assertEqual((folder/'stderr.log').read_bytes(),b'reason')
        record=json.loads((folder/'process-result.json').read_text())
        self.assertEqual(record['exitCode'],4)
        self.assertEqual(record['status'],'failed')

    def test_dead_leader_descendant_held_pipe_deadline_never_signals_unowned_group(self):
        code='import os,time; p=os.fork(); time.sleep(1.0) if p==0 else None; os._exit(0)'
        start=time.monotonic()
        with self.assertRaises(TimeoutError):self.run_command(code,timeout=0.2)
        self.assertLess(time.monotonic()-start,6)
        record=json.loads((self.root/'1'/'process-result.json').read_text())
        self.assertTrue(record['possibleSurvivors'])
        self.assertFalse(record['signals'])

    def test_incremental_process_identity_matches_accepted_semantics(self):
        import wsl_environment_guest as accepted
        actual=transfer.bounded_process_identity(os.getpid(),guest_deadline=time.monotonic()+2,operation_directory=self.operation())
        self.assertEqual(actual,accepted.process_identity(os.getpid()))
        bad={**actual,'starttime':'0'}
        with self.assertRaises(ValueError):accepted.validate_handle({**bad,'socket':{},'daemonId':'x','containerdNamespaces':{}},{**actual,'socket':{},'daemonId':'x','containerdNamespaces':{}})

    def test_socket_requires_real_root_socket_full_identity(self):
        with socket.socket(socket.AF_UNIX) as endpoint:
            path=self.root/'daemon.sock';endpoint.bind(str(path))
            if os.getuid()!=0:
                with self.assertRaises(ValueError):transfer._socket_identity(str(path))
            else:
                result=transfer._socket_identity(str(path));self.assertEqual(result['uid'],0);self.assertEqual(result['gid'],0)
                self.assertEqual(result['inode'],path.lstat().st_ino)
        regular=self.root/'regular';regular.write_bytes(b'')
        with self.assertRaises(ValueError):transfer._socket_identity(str(regular))

    def test_real_accepted_entered_hash_rewind_and_pipe_rejection(self):
        import wsl_environment_guest as accepted
        from docker_transport import _stdin
        data=b'\x00\xffbinary';path=self.root/'accepted';path.write_bytes(data)
        # Execute accepted _entered's real hash/rewind/persistence; replace only the namespace entry subprocess.
        real_run=subprocess.run
        def local_run(argv,**kwargs):return real_run(['/usr/bin/cat'],**kwargs)
        with path.open('rb') as source,patch.object(accepted,'paths',return_value={'base':str(self.root)}),patch.object(accepted,'daemon_environment',return_value={}),patch.object(accepted.subprocess,'run',side_effect=local_run):
            _stdin(source);result=accepted._entered('fixture',os.getpid(),['/usr/bin/cat'],stdin=source)
        self.assertEqual(result.stdout,data)
        record=json.loads(next(self.root.glob('operation-*.command.json')).read_text())
        self.assertEqual(record['stdin'],{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)})
        rd,wr=os.pipe();os.close(wr)
        with os.fdopen(rd,'rb') as pipe,patch.object(accepted,'paths',return_value={'base':str(self.root)}),patch.object(accepted,'daemon_environment',return_value={}):
            _stdin(pipe)
            with self.assertRaises(OSError):accepted._entered('fixture',os.getpid(),['/usr/bin/cat'],stdin=pipe)


if __name__=='__main__':unittest.main()

class HostTransportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(transfer)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_real_binary_descriptor_host_pipe_and_channel_caps(self):
        data=bytes(range(256))*4097;path=self.root/'input';path.write_bytes(data)
        with path.open('rb') as incoming:
            result=transfer._pump_host([sys.executable,'-I','-B','-c',
                'import sys,hashlib,json;h=hashlib.sha256();n=0\nwhile b:=sys.stdin.buffer.read(65536):h.update(b);n+=len(b)\nprint(json.dumps({"bytes":n,"sha256":h.hexdigest()}));sys.stderr.buffer.write(b"separate\\x00")'],
                input_file=incoming,deadline=time.monotonic()+3)
        self.assertEqual(json.loads(result['stdout']),{'bytes':1048832,'sha256':hashlib.sha256(data).hexdigest()})
        self.assertEqual(result['stderr'],b'separate\x00')
        with self.assertRaises(ValueError):transfer._pump_host([sys.executable,'-I','-c','import os;os.write(1,b"x"*65537)'],input_file=None,deadline=time.monotonic()+3)

    def test_host_deadline_cancels_no_output_hang(self):
        start=time.monotonic()
        with self.assertRaises(TimeoutError):transfer._pump_host([sys.executable,'-I','-c','import time;time.sleep(30)'],input_file=None,deadline=time.monotonic()+.2)
        self.assertLess(time.monotonic()-start,6)

    def test_fixed_adapter_role_missing_or_substituted_is_rejected_locally(self):
        context={'manifest':{'files':{}},'spec':{'harness':{'path':str(self.root)}}}
        with self.assertRaises(ValueError):transfer._adapter_pin(context)
        context['manifest']['files'][transfer.ADAPTER_ROLE]={'bytes':1,'sha256':'0'*64,'transferMode':0o444}
        with self.assertRaises(ValueError):transfer._adapter_pin(context)

@unittest.skipUnless(os.name=='posix' and Path('/proc/self/stat').exists(), 'real Linux supervision required')
class LinuxHardeningTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_spool_linked_ancestor_never_writes_outside(self):
        outside=self.root/'outside';outside.mkdir();link=self.root/'linked';link.symlink_to(outside,target_is_directory=True)
        with io.BytesIO(b'x') as incoming,self.assertRaises((OSError,ValueError)):
            transfer.stage_input(incoming,link/'input.partial',hashlib.sha256(b'x').hexdigest(),1,guest_deadline=time.monotonic()+2)
        self.assertFalse((outside/'input.partial').exists())

    def test_supervised_disk_job_registers_before_task_and_cancels_stall(self):
        evidence=self.root/'worker.json';marker=self.root/'ran'
        def task():
            identity=json.loads(evidence.read_text())
            self.assertEqual(identity['pid'],os.getpid());marker.write_text('ran');time.sleep(20)
        start=time.monotonic()
        with self.assertRaises(TimeoutError):transfer._fork_job(task,deadline=time.monotonic()+.2,evidence_path=evidence)
        self.assertTrue(marker.exists());self.assertLess(time.monotonic()-start,6)
        record=json.loads(evidence.read_text());self.assertEqual(record['pid'],record['pgid']);self.assertEqual(record['pid'],record['sid'])

    def test_partial_regular_writes_are_completed_without_data_loss(self):
        real_write=os.write
        def partial(fd,data):
            import stat
            return real_write(fd,data[:7] if stat.S_ISREG(os.fstat(fd).st_mode) else data)
        with patch.object(os,'write',side_effect=partial),io.BytesIO(b'raw\x00\xff'*100) as incoming:
            path=transfer.stage_input(incoming,self.root/'partial',hashlib.sha256(b'raw\x00\xff'*100).hexdigest(),500,guest_deadline=time.monotonic()+2)
        self.assertEqual(path.read_bytes(),b'raw\x00\xff'*100)

    def test_failed_management_stops_before_command_and_postcheck(self):
        phases=[]
        def observe(*args):phases.append('observe');return {}
        def management(*args):phases.append('management');raise ValueError('actual membership differs')
        with patch.object(transfer,'_observe',side_effect=observe),patch.object(transfer,'_management',side_effect=management),patch.object(transfer,'_supervise',side_effect=AssertionError('payload forbidden')):
            with self.assertRaises(ValueError):transfer.stream_admitted({},['/usr/bin/true'],input_file=None,output_policy='RECEIVER_CONTROL',guest_deadline=time.monotonic()+2,operation_directory=self.root)
        self.assertEqual(phases,['observe','management'])

@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0, 'root-owned isolated Linux filesystem controls required')
class LinuxClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.top=Path(self.tmp.name);self.root=self.top/'generation'
        self.context={'spec':'a'*64};self.nonce='a'*32
        self.members={'handoff':{'handoff.tar':{'bytes':1,'sha256':hashlib.sha256(b'x').hexdigest(),'mode':0o444}},
            'harness':{'bin/helper':{'bytes':1,'sha256':hashlib.sha256(b'y').hexdigest(),'mode':0o555},
                       'manifest.json':{'bytes':2,'sha256':hashlib.sha256(b'{}').hexdigest(),'mode':0o444}}}
    def command(self,action,role='handoff',**kw):
        return transfer._payload_filesystem(self.root,self.context,self.members,['bin'],action,{'role':role,'nonce':self.nonce,**kw},capacity_root=self.top)
    def record(self,role,name,ordinal):
        pin=self.members[role][name]
        record={'schemaVersion':1,'operationNonce':self.nonce,'action':'receive','status':'succeeded',
            'child':{'pid':51,'pgid':51,'sid':51,'starttime':'1'},'bytes':pin['bytes'],'sha256':pin['sha256'],
            'sealSha256':None,'error':None,'possibleSurvivors':False,'observations':[]}
        self.command('record',role,name=name,ordinal=ordinal,record=record)
    def test_capacity_uses_real_bavail_and_retained_ledger_once(self):
        observed=self.command('capacity');fs=os.statvfs(self.top)
        self.assertLessEqual(observed['available'],fs.f_bavail*fs.f_frsize+1024*1024)
        self.assertEqual(observed['retained'],0)
        self.command('prepare',first=True);self.record('handoff','handoff.tar',0)
        self.assertEqual(self.command('capacity')['retained'],2)
    def test_incomplete_sibling_failed_context_and_role_retries_are_rejected(self):
        self.command('prepare',first=True)
        with self.assertRaises((FileNotFoundError,ValueError)):self.command('prepare','harness',first=True)
        with self.assertRaises(FileExistsError):self.command('prepare',first=True)
        self.nonce='b'*32
        with self.assertRaises(ValueError):self.command('prepare',first=True)
        self.nonce='a'*32;self.context={'spec':'b'*64}
        with self.assertRaises(ValueError):self.command('capacity')
    def test_successful_sibling_allows_new_owned_aggregate_and_requires_all_members(self):
        self.command('prepare',first=True);self.record('handoff','handoff.tar',0);self.command('complete')
        self.nonce='b'*32;self.command('prepare','harness',first=True)
        self.record('harness','bin/helper',0)
        with self.assertRaises(ValueError):self.command('complete','harness')
        self.record('harness','manifest.json',1);self.command('complete','harness')
        self.assertEqual(self.command('capacity','harness')['retained'],8)
    def test_real_closure_finalizer_exact_files_modes_and_extra_member_rejection(self):
        import wsl_environment_guest as accepted
        self.command('prepare','harness',first=True)
        target=self.root/'inputs/harness'
        accepted.receive_file(str(target/'bin/helper'),hashlib.sha256(b'y').hexdigest(),1,0o555,io.BytesIO(b'y'))
        accepted.receive_file(str(target/'manifest.json'),hashlib.sha256(b'{}').hexdigest(),2,0o444,io.BytesIO(b'{}'))
        extra=target/'unexpected';extra.write_bytes(b'bad')
        argv=[sys.executable,'-I','-B','-c',transfer._FINALIZE,str(target),json.dumps(self.members['harness']),json.dumps(['bin'])]
        op=self.top/'failed';op.mkdir()
        with self.assertRaises(ValueError):transfer._supervise(argv,input_file=None,output_policy='COMPLETION_CONTROL',guest_deadline=time.monotonic()+2,operation_directory=op)
        self.assertTrue(extra.exists())
        # A separate independent clean generation proves successful finalization;
        # the failed generation is retained and never retried in place.
        clean=self.top/'clean';(clean/'bin').mkdir(parents=True)
        accepted.receive_file(str(clean/'bin/helper'),hashlib.sha256(b'y').hexdigest(),1,0o555,io.BytesIO(b'y'))
        accepted.receive_file(str(clean/'manifest.json'),hashlib.sha256(b'{}').hexdigest(),2,0o444,io.BytesIO(b'{}'))
        argv[-3]=str(clean);op=self.top/'success';op.mkdir()
        result=transfer._supervise(argv,input_file=None,output_policy='COMPLETION_CONTROL',guest_deadline=time.monotonic()+2,operation_directory=op)
        self.assertEqual(json.loads(result['stdout']),{'status':'input-closure-complete','files':2,'directories':1})
        import stat
        self.assertEqual(stat.S_IMODE(clean.stat().st_mode),0o555);self.assertEqual(stat.S_IMODE((clean/'bin').stat().st_mode),0o555)
        self.assertEqual(stat.S_IMODE((clean/'bin/helper').stat().st_mode),0o555)


class PublicHostFlowTests(unittest.TestCase):
    def setUp(self):
        TransferAuthorityTests.setUp(self)
        adapter=self.f.harness/transfer.ADAPTER_ROLE
        adapter.write_bytes(Path(transfer.__file__).read_bytes())
        import stat
        self.f.harness_manifest['files'][transfer.ADAPTER_ROLE]={**self.f.pin_path(adapter),'hostMode':stat.S_IMODE(adapter.stat().st_mode),'transferMode':0o444}
        self.f.refresh()
        self.kw.update(spec_sha256=digest(self.f.spec_path.read_bytes()),timeout=30)
        self.ready={**self.f.env,'wslExecutable':str(Path(sys.executable).absolute())}
        import wsl_environment
        self.enterContext(patch.object(wsl_environment,'_read_ready',return_value=self.ready))
        self.outcome='success';self.source_fds=[]
    def guest_boundary(self,output,key,argv,incoming,deadline):
        if key=='admission':
            request=json.loads(argv[-1]);return {'status':'admission-succeeded','nonce':request['nonce'],'deadline':1.0}
        request=json.loads(argv[-4]);action=argv[-3];argument=json.loads(argv[-2])
        if action=='receive':
            self.source_fds.append(incoming.fileno());data=incoming.read()
            return dict(schemaVersion=1,operationNonce=request['nonce'],action='receive',status='succeeded',
                child=dict(pid=51,starttime='1',pgid=51,sid=51),bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),
                sealSha256=None,error=None,possibleSurvivors=False,observations=[])
        if action=='finalize':return {'status':'input-closure-complete','files':len(request['members'][argument]),'directories':len(request['directories']) if argument=='harness' else 0}
        if self.outcome=='missing':raise FileNotFoundError('completion missing')
        return {'status':'succeeded','nonce':request['nonce'] if self.outcome=='success' else '0'*32}
    def test_public_handoff_retains_exact_frozen_code_argv_and_binary_descriptor(self):
        # WSL itself is unavailable here. Only that external boundary is replaced;
        # local qualification, actual files, hashes, worker pipes and evidence run.
        with patch.object(transfer,'_host_call',side_effect=self.guest_boundary):result=transfer.transfer_environment(**self.kw)
        self.assertEqual(result['status'],'input-transfer-complete');self.assertEqual(len(self.source_fds),1)
        self.assertEqual((self.root/'operation'/'adapter.py').read_bytes(),Path(transfer.__file__).read_bytes())
        argv=json.loads((self.root/'operation'/'bootstrap.argv.json').read_text())
        self.assertEqual(argv[1:9],['-d',self.f.distro,'--user','root','--exec','/usr/bin/python3','-I','-B'])
    def test_public_missing_changed_completion_retains_failed_evidence(self):
        for outcome in ('missing','changed'):
            self.outcome=outcome;self.kw['output_directory']=self.root/outcome
            with self.subTest(outcome=outcome),patch.object(transfer,'_host_call',side_effect=self.guest_boundary):
                with self.assertRaises((FileNotFoundError,ValueError)):transfer.transfer_environment(**self.kw)
            record=json.loads((self.root/outcome/'result.json').read_text());self.assertEqual(record['status'],'failed')
            self.assertTrue((self.root/outcome/'adapter.py').exists())

@unittest.skipUnless(os.name=='posix' and Path('/proc/self/stat').exists(), 'real inherited Linux binary output pipe required')
class LinuxOutputPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def start(self,payload,timeout):
        op=self.root/'operation';op.mkdir()
        code='import sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import wsl_environment_transfer as t;t._supervise([sys.executable,"-I","-c",sys.argv[3]],input_file=None,output_policy="EXPORT_ARCHIVE",guest_deadline=time.monotonic()+float(sys.argv[4]),operation_directory=Path(sys.argv[2]))'
        return subprocess.Popen([sys.executable,'-I','-B','-c',code,str(Path(transfer.__file__).parent),str(op),payload,str(timeout)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    def test_generic_export_policy_forwards_only_raw_channel_bytes(self):
        p=self.start('import os;os.write(1,b"raw\\x00\\xff");os.write(2,b"diagnostic")',2)
        out,err=p.communicate(timeout=6)
        self.assertEqual(p.returncode,0,err);self.assertEqual(out,b'raw\x00\xff')
        self.assertEqual((self.root/'operation'/'stderr.log').read_bytes(),b'diagnostic')
    def test_blocked_sink_and_host_disconnect_cancel_real_process(self):
        p=self.start('import os;os.write(1,b"x"*4194304)',.2)
        self.assertNotEqual(p.wait(timeout=6),0);p.communicate(timeout=2)
        record=json.loads((self.root/'operation'/'process-result.json').read_text());self.assertEqual(record['status'],'failed')
        # Use a fresh operation; preserve the failed blocked-sink operation.
        self.root=self.root/'disconnect';self.root.mkdir()
        p=self.start('import os,time;os.write(1,b"x"*1048576);time.sleep(20)',2)
        p.stdout.close();self.assertNotEqual(p.wait(timeout=6),0);p.stderr.close()
        record=json.loads((self.root/'operation'/'process-result.json').read_text());self.assertEqual(record['status'],'failed')

@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0, 'real root Unix socket and Linux process identity controls required')
class LinuxIdentityControls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.endpoint=socket.socket(socket.AF_UNIX);self.addCleanup(self.endpoint.close);self.endpoint.bind(str(self.root/'docker.sock'))
        self.n=0;self.argv=[];self.generation='p01-transfer-identity-fixture'
        op=self.operation();identity=transfer.bounded_process_identity(os.getpid(),guest_deadline=time.monotonic()+2,operation_directory=op)
        self.handle={**identity,'generation':self.generation,'socket':transfer._socket_identity(str(self.root/'docker.sock')),
            'daemonId':'fixture-daemon','containerdNamespaces':{'Containers':self.generation,'Plugins':self.generation+'-plugins'}}
        self.info=json.dumps({'ID':'fixture-daemon','Containerd':{'Namespaces':self.handle['containerdNamespaces']}}).encode()
        import wsl_environment_guest as accepted
        original=accepted.paths
        self.enterContext(patch.object(accepted,'paths',side_effect=lambda generation:{**original(generation),'run':str(self.root),'base':str(self.root)}))
        self.real_supervise=transfer._supervise
        self.enterContext(patch.object(transfer,'_supervise',side_effect=self.info_process))
    def operation(self):
        self.n+=1;path=self.root/str(self.n);path.mkdir();return path
    def info_process(self,argv,**kwargs):
        self.argv.append(argv)
        # Only target namespace/Docker entry is replaced by a real fixture process;
        # the observer, socket/proc reads, validators and bounded supervisor run.
        info_path=kwargs['operation_directory']/'fixture-info.bin';info_path.write_bytes(self.info)
        return self.real_supervise([sys.executable,'-I','-B','-c','import os,sys;f=open(sys.argv[1],"rb")\nwhile b:=f.read(65536):\n p=0\n while p<len(b):p+=os.write(1,b[p:])',str(info_path)],**kwargs)
    def observe(self,handle=None):
        return transfer._observe(self.handle if handle is None else handle,time.monotonic()+3,self.operation())
    def test_full_observation_and_exact_namespace_endpoint_clean_env_argv(self):
        self.assertEqual(self.observe(),{k:v for k,v in self.handle.items() if k!='generation'})
        argv=self.argv[0]
        self.assertEqual(argv[:9],['/usr/bin/nsenter','--target',str(os.getpid()),'--mount','--net','--cgroup','/usr/bin/env','-i','PATH=/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'])
        self.assertEqual(argv[-8:],['/usr/local/bin/docker','--host','unix://'+str(self.root/'docker.sock'),'--config',str(self.root)+'/client','info','--format','{{json .}}'])
        self.assertFalse(any(v.startswith(('DOCKER_','CONTAINERD_')) for v in argv))
    def test_stale_full_handle_fields_and_actual_info_pair_are_rejected(self):
        changes=[{'pid':99999999},{'starttime':'0'},{'exeSha256':'0'*64},
            {'namespaces':{**self.handle['namespaces'],'mnt':'mnt:[0]'}},
            {'socket':{**self.handle['socket'],'device':self.handle['socket']['device']+1}},
            {'socket':{**self.handle['socket'],'inode':self.handle['socket']['inode']+1}},
            {'socket':{**self.handle['socket'],'uid':1}},{'socket':{**self.handle['socket'],'gid':1}},
            {'daemonId':'old-daemon'},{'containerdNamespaces':{'Containers':self.generation,'Plugins':'old'}}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises((ValueError,OSError)):self.observe({**self.handle,**change})
        self.info=json.dumps({'ID':'fixture-daemon','Containerd':{'Namespaces':{'Containers':self.generation,'Plugins':'old'}}}).encode()
        with self.assertRaises(ValueError):self.observe()
    def test_incomplete_oversized_duplicate_or_empty_info_is_not_admission(self):
        for raw in (b'{',b'x'*65537,b'{"ID":"a","ID":"b"}',b'{"ID":"","Containerd":{"Namespaces":{}}}'):
            self.info=raw
            with self.subTest(raw=raw[:40]),self.assertRaises(ValueError):self.observe()


@unittest.skipUnless(os.name=='posix' and Path('/proc/self/stat').exists(), 'Linux process memory/pipes required')
class LinuxBufferControls(unittest.TestCase):
    def test_transfer_owned_memory_does_not_grow_with_input_size(self):
        import tracemalloc
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);peaks=[]
            for count in (16,128):
                path=root/('source-'+str(count))
                with path.open('wb') as outgoing:
                    for _ in range(count):outgoing.write(b'x'*65536)
                op=root/('operation-'+str(count));op.mkdir()
                tracemalloc.start()
                with path.open('rb') as incoming:
                    result=transfer._supervise([sys.executable,'-I','-c','import sys,hashlib,json;h=hashlib.sha256();n=0\nwhile b:=sys.stdin.buffer.read(65536):h.update(b);n+=len(b)\nprint(json.dumps({"bytes":n}))'],input_file=incoming,output_policy='RECEIVER_CONTROL',guest_deadline=time.monotonic()+3,operation_directory=op)
                _,peak=tracemalloc.get_traced_memory();tracemalloc.stop();peaks.append(peak)
                self.assertEqual(json.loads(result['stdout'])['bytes'],count*65536)
                self.assertLess(peak,8*1024*1024)
            self.assertLess(peaks[1]-peaks[0],2*1024*1024)
