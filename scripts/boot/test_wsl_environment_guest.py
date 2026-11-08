import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wsl_environment_guest as guest

class LifecycleTests(unittest.TestCase):
    def test_config_is_generation_local(self):
        value=guest.daemon_config('p01-test')
        self.assertEqual(value['hosts'],['unix:///run/p01-test/docker.sock'])
        self.assertEqual(value['data-root'],'/var/lib/p01-test/docker')
        self.assertEqual(value['cgroup-parent'],'/workloads')
        self.assertEqual(value['containerd-namespace'],'p01-test')
        self.assertNotIn('containerd',value)
        for name in ('iptables','ip6tables','ip-forward','ip-masq','userland-proxy'):
            self.assertIs(value[name],False)
    def test_clean_environment_ignores_inherited_values(self):
        with patch.dict(os.environ,{'XDG_RUNTIME_DIR':'/mnt/wslg/runtime-dir','DOCKER_HOST':'bad','HTTPS_PROXY':'bad'}):
            self.assertEqual(set(guest.daemon_environment('p01-test')),{'PATH','HOME','LANG','TMPDIR'})
            self.assertEqual(guest.daemon_environment('p01-test')['TMPDIR'],'/run/p01-test/tmp')
    def test_launcher_moves_before_unshare_and_cleans_environment(self):
        script=guest.boundary_script('p01-test')
        self.assertIn('env -i',script)
        self.assertIn('mode=0700',script)
        self.assertIn('TMPDIR=/run/p01-test/tmp',script)
        self.assertNotIn('/sys/fs/cgroup/../',script)
    def test_handle_rejects_all_identity_changes(self):
        handle={'pid':8,'starttime':'3','exeSha256':'a','namespaces':{'mnt':'m'},'socket':{'inode':4,'uid':0,'gid':0},'daemonId':'d','containerdNamespaces':{'Containers':'p01-test','Plugins':'p01-test-plugins'}}
        guest.validate_handle(handle,dict(handle))
        for key,value in [('pid',9),('starttime','4'),('exeSha256','b'),('namespaces',{'mnt':'n'}),('socket',{'inode':5,'uid':0,'gid':0}),('daemonId','e')]:
            observed=dict(handle); observed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): guest.validate_handle(handle,observed)
    def test_info_contract_fails_nonempty_and_wrong_driver(self):
        info=namespace_info()
        guest.validate_info(info,'p01-test')
        for key,value in [('Images',1),('Containers',1),('Driver','overlay2'),('ServerVersion','29.0'),('CgroupVersion','1')]:
            changed=dict(info);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): guest.validate_info(changed,'p01-test')
try:
    from wsl_environment_guest import receive_file
except ImportError:
    receive_file = None

@unittest.skipUnless(os.name=='posix','Linux openat/O_NOFOLLOW receiver contract')
class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(receive_file, 'receive_file API missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
    def receive(self, data=b'abc', digest=None, size=3, name='target'):
        return receive_file(str(self.root / name), digest or hashlib.sha256(b'abc').hexdigest(), size, 0o444, io.BytesIO(data))
    def test_success_mode_and_content(self):
        self.receive()
        self.assertEqual((self.root/'target').read_bytes(), b'abc')
        self.assertEqual((self.root/'target').stat().st_mode & 0o777, 0o444)
    def test_failed_transfer_is_not_reused(self):
        for name, data, digest in [('short',b'ab',None),('long',b'abcd',None),('hash',b'abc','0'*64)]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError): self.receive(data,digest,name=name)
                self.assertTrue((self.root/name).exists())
                with self.assertRaises(FileExistsError): self.receive(name=name)
    def test_symlink_parent_and_target_rejected(self):
        (self.root/'real').mkdir()
        (self.root/'link').symlink_to(self.root/'real', target_is_directory=True)
        with self.assertRaises(OSError): self.receive(name='link/out')
        self.assertFalse((self.root/'real/out').exists())
        (self.root/'target').symlink_to(self.root/'absent')
        with self.assertRaises(FileExistsError): self.receive()
    def test_partial_os_write(self):
        from unittest.mock import patch
        original=os.write
        with patch('wsl_environment_guest.os.write', side_effect=lambda fd,data: original(fd,data[:1])):
            self.receive()
        self.assertEqual((self.root/'target').read_bytes(),b'abc')


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.handle={'generation':'p01-test','pid':8,'starttime':'3','exeSha256':'a','namespaces':{'mnt':'m'},'socket':{'inode':4,'uid':0,'gid':0},'daemonId':'d','containerdNamespaces':{'Containers':'p01-test','Plugins':'p01-test-plugins'}}
    def test_stale_handle_never_enters_or_restarts(self):
        for key,value in [('pid',9),('starttime','4'),('daemonId','other'),('socket',{'inode':5,'uid':0,'gid':0})]:
            changed=dict(self.handle);changed[key]=value
            with self.subTest(key=key),patch.object(guest,'observe_handle',return_value=changed),patch.object(guest,'_entered') as entered,patch.object(guest.subprocess,'Popen') as popen:
                with self.assertRaises(ValueError):guest.admit_environment(self.handle,['/bin/true'])
                entered.assert_not_called();popen.assert_not_called()
    def test_admission_moves_management_and_rechecks_before_enter(self):
        from unittest.mock import MagicMock
        path=MagicMock()
        with patch.object(guest,'observe_handle',return_value=dict(self.handle)) as observed,patch.object(guest,'Path',return_value=path),patch.object(guest,'_entered',return_value='result') as entered:
            self.assertEqual(guest.admit_environment(self.handle,['/bin/true']),'result')
            self.assertEqual(observed.call_count,2)
            path.write_text.assert_called_once_with(str(os.getpid())+'\n')
            entered.assert_called_once_with('p01-test',8,['/bin/true'],timeout=30)
    def test_pid_changes_during_admission_prevent_exec(self):
        from unittest.mock import MagicMock
        changed=dict(self.handle);changed['starttime']='4'
        with patch.object(guest,'observe_handle',side_effect=[self.handle,changed]),patch.object(guest,'Path',return_value=MagicMock()),patch.object(guest,'_entered') as entered:
            with self.assertRaises(ValueError):guest.admit_environment(self.handle,['/bin/true'])
            entered.assert_not_called()

class LauncherFailureTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import MagicMock
        self.path=MagicMock();self.process=MagicMock();self.process.pid=42;self.process.poll.return_value=None
        self.info=namespace_info()
    def launch(self,*,clock=None,info=None,listing=b'',boundary=0,controls=None):
        import json,subprocess
        from contextlib import ExitStack
        from unittest.mock import mock_open
        response=subprocess.CompletedProcess([],0,json.dumps(info or self.info).encode(),b'')
        empty=subprocess.CompletedProcess([],0,listing,b'')
        with ExitStack() as stack:
            stack.enter_context(patch.object(guest,'Path',return_value=self.path))
            stack.enter_context(patch.object(guest.os,'readlink',return_value='shared-ns'))
            stack.enter_context(patch.object(guest.os,'fsync'))
            stack.enter_context(patch.object(guest,'durable_json'))
            stack.enter_context(patch('builtins.open',mock_open()))
            popen=stack.enter_context(patch.object(guest.subprocess,'Popen',return_value=self.process))
            stack.enter_context(patch.object(guest,'shared_controls',side_effect=controls or [{},{}]))
            self.docker=stack.enter_context(patch.object(guest,'_docker',side_effect=[response,empty,empty,empty]))
            stack.enter_context(patch.object(guest,'_entered',return_value=subprocess.CompletedProcess([],boundary,b'',b'')))
            if clock is not None:stack.enter_context(patch.object(guest.time,'monotonic',side_effect=clock))
            with self.assertRaises((ValueError,TimeoutError)):
                guest.launch_environment('p01-test')
            popen.assert_called_once()
    def test_daemon_exit_blocks_without_restart(self):
        self.process.poll.return_value=1;self.launch()
    def test_timeout_blocks_without_restart(self):
        self.launch(clock=[0,31])
    def test_readiness_caps_nested_command_and_rechecks_deadline(self):
        self.launch(clock=[0,1,2,31])
        self.assertEqual(self.docker.call_args.kwargs['timeout'],28)
    def test_nonempty_info_blocks_without_restart(self):
        self.launch(info={**self.info,'Images':1})
    def test_nonempty_listing_blocks_without_restart(self):
        self.launch(listing=b'old-image\n')
    def test_default_socket_or_private_boundary_failure_blocks(self):
        self.launch(boundary=1)
    def test_shared_controls_change_blocks(self):
        self.launch(controls=[{'cgroup.subtree_control':'cpu'},{'cgroup.subtree_control':'cpu memory'}])


class GuestCommandEvidenceTests(unittest.TestCase):
    def test_timeout_retains_partial_logs_and_result(self):
        import json,subprocess
        with tempfile.TemporaryDirectory() as tmp:
            def run(argv,**kwargs):
                kwargs['stdout'].write(b'partial stdout');kwargs['stderr'].write(b'partial stderr')
                raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
            with patch.object(guest,'paths',return_value={'base':tmp,'run':'/run/p01-test'}),patch.object(guest.subprocess,'run',side_effect=run):
                with self.assertRaises(subprocess.TimeoutExpired):guest._entered('p01-test',8,['/bin/true'],timeout=1)
            root=Path(tmp)
            self.assertEqual(next(root.glob('*.stdout.log')).read_bytes(),b'partial stdout')
            self.assertEqual(next(root.glob('*.stderr.log')).read_bytes(),b'partial stderr')
            result=json.loads(next(root.glob('*.result.json')).read_text())
            self.assertTrue(result['processMayRemain']);self.assertIsNone(result['exitCode'])

class FullStageTests(unittest.TestCase):
    def test_unverified_archives_never_load(self):
        self.assertTrue(callable(getattr(guest,'load_archives',None)), 'load_archives API missing')
        import development_image_migration as migration
        with tempfile.TemporaryDirectory() as tmp, patch.object(migration,'_prepare_all',side_effect=ValueError('third archive invalid')), patch.object(guest.subprocess,'run') as run:
            with self.assertRaises(Exception): guest.load_archives('p01-test',tmp,Path(tmp)/'load.json')
            run.assert_not_called()
            self.assertEqual(__import__('json').loads((Path(tmp)/'load.json').read_text())['completedLoads'],[])
    def test_partial_load_receipt_retained_without_smoke(self):
        self.assertTrue(callable(getattr(guest,'load_archives',None)), 'load_archives API missing')
        import development_image_migration as migration
        import json, subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'a').write_bytes(b'archive')
            image={'role':'rust','imageId':'sha256:r','diffIds':[],'configObject':{'config':{}}}
            loaded={'Id':'sha256:r','Descriptor':{'digest':'sha256:r','mediaType':migration.INDEX},'Architecture':'amd64','Os':'linux','RootFS':{'Type':'layers','Layers':[]},'Config':{}}
            with (root/'a').open('rb') as source, patch.object(migration,'_prepare_all',return_value=[(source,image),(source,{**image,'role':'tools'})]),patch.object(migration,'_check_sealed_source'),patch.object(guest.subprocess,'run',side_effect=[subprocess.CompletedProcess([],0,b'',b''),subprocess.CompletedProcess([],0,json.dumps([loaded]).encode(),b''),subprocess.CompletedProcess([],1,b'',b'failed')]):
                with self.assertRaises(Exception):guest.load_archives('p01-test',root,root/'load.json')
            report=json.loads((root/'load.json').read_text());self.assertEqual(report['completedLoads'],['rust']);self.assertEqual(report['status'],'failed')
            commands=[v['command'] for v in report['operations']]
            self.assertTrue(all(c[1:3]==['--host','unix:///run/p01-test/docker.sock'] for c in commands))
    def test_actual_effective_limits_are_independently_rejected(self):
        self.assertTrue(callable(getattr(guest,'validate_effective',None)), 'validate_effective API missing')
        good={'uid':[65534]*4,'gid':[65534]*4,'capabilities':{'CapInh':'0000000000000000','CapPrm':'0000000000000000','CapEff':'0000000000000000','CapBnd':'0000000000000000','CapAmb':'0000000000000000'},'noNewPrivs':1,'rootReadOnly':True,'cpuMax':'200000 100000','memoryMax':'4294967296','pidsMax':'256','interfaces':['lo'],'routes':[],'desktopPathsVisible':False}
        guest.validate_effective(good)
        for key,value in [('cpuMax','max 100000'),('memoryMax','max'),('pidsMax','max'),('uid',[0]*4),('rootReadOnly',False),('interfaces',['lo','eth0']),('routes',['default']),('desktopPathsVisible',True)]:
            with self.subTest(key=key),self.assertRaises(ValueError):guest.validate_effective({**good,key:value})
    def test_start_exit_zero_does_not_override_oom_or_container_exit(self):
        self.assertTrue(callable(getattr(guest,'validate_smoke_state',None)), 'validate_smoke_state API missing')
        good={'Running':False,'ExitCode':0,'OOMKilled':False,'Status':'exited'}
        guest.validate_smoke_state(good)
        for changed in ({**good,'ExitCode':42},{**good,'OOMKilled':True},{**good,'Running':True}):
            with self.assertRaises(ValueError): guest.validate_smoke_state(changed)



class SmokeBoundaryTests(unittest.TestCase):
    def setUp(self):
        import json,development_image_migration as migration
        self.cid='a'*64;self.image=migration.FIXED['tools']['imageId'];self.calls=[]
        self.actual={'uid':[65534]*4,'gid':[65534]*4,'capabilities':{k:'0000000000000000' for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')},'noNewPrivs':1,'rootReadOnly':True,'cpuMax':'200000 100000','memoryMax':'4294967296','pidsMax':'256','interfaces':['lo'],'routes':[],'desktopPathsVisible':False}
        self.inspection={'Id':self.cid,'Image':self.image,'Mounts':[],
            'Config':{'Image':self.image,'User':'65534:65534','OpenStdin':True,'Entrypoint':['/usr/bin/python3'],'Cmd':['-']},
            'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Init':True,'NanoCpus':2000000000,'Memory':4294967296,'MemorySwap':8589934592,'PidsLimit':256,'CapDrop':['ALL'],'AutoRemove':False,'RestartPolicy':{'Name':'no','MaximumRetryCount':0},'CgroupnsMode':'private','Privileged':False,'SecurityOpt':['no-new-privileges']},
            'State':{'Running':False,'ExitCode':0,'OOMKilled':False,'Status':'exited'}}
        self.stdin=None;self.state=None
    def boundary(self,handle,argv,**kwargs):
        import json,subprocess,copy
        self.calls.append(argv)
        self.assertGreater(kwargs['timeout'],0);self.assertLessEqual(kwargs['timeout'],60)
        self.assertEqual(argv[:5],['/usr/local/bin/docker','--host','unix:///run/p01-test/docker.sock','--config',self.client])
        if 'create' in argv:
            self.assertNotIn('-c',argv);self.assertNotIn('--volume',argv);self.assertNotIn('--mount',argv)
            return subprocess.CompletedProcess(argv,0,self.cid.encode(),b'')
        if 'start' in argv:
            stream=kwargs['stdin'];self.assertGreaterEqual(stream.fileno(),0);self.stdin=stream.read()
            self.assertEqual(stream.mode,'rb')
            return subprocess.CompletedProcess(argv,0,json.dumps(self.actual).encode(),b'')
        value=copy.deepcopy(self.inspection)
        if len(self.calls)>3 and self.state is not None:value['State']=self.state
        return subprocess.CompletedProcess(argv,0,json.dumps([value]).encode(),b'')
    def run_smoke(self,root):
        self.client=str(root)+'/client'
        with patch.object(guest,'paths',return_value={'base':str(root),'run':'/run/p01-test'}),patch.object(guest,'admit_environment',side_effect=self.boundary):
            return guest.run_smoke({'generation':'p01-test'})
    def test_smoke_fd_and_mismatched_actual_limits(self):
        for key,value in [('cpuMax','max 100000'),('memoryMax','max'),('pidsMax','max')]:
            with tempfile.TemporaryDirectory() as tmp:
                self.calls=[];old=self.actual[key];self.actual[key]=value
                with self.assertRaises(ValueError):self.run_smoke(Path(tmp))
                self.assertEqual(self.stdin,guest.SMOKE_SCRIPT)
                self.assertTrue((Path(tmp)/'smoke-inspect-after.json').is_file())
                self.assertTrue((Path(tmp)/'smoke-actual-effective.json').is_file())
                self.actual[key]=old
    def test_attach_zero_still_rejects_exit_and_oom(self):
        for state in ({'Running':False,'ExitCode':17,'OOMKilled':False,'Status':'exited'},{'Running':False,'ExitCode':0,'OOMKilled':True,'Status':'exited'}):
            with tempfile.TemporaryDirectory() as tmp:
                self.calls=[];self.state=state
                with self.assertRaises(ValueError):self.run_smoke(Path(tmp))
                self.assertTrue((Path(tmp)/'smoke-inspect-after.json').is_file())
    def test_success_records_fd_script_pin_and_both_inspections(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=self.run_smoke(Path(tmp))
            self.assertEqual(result['script'],{'sha256':hashlib.sha256(self.stdin).hexdigest(),'bytes':len(self.stdin)})
            self.assertEqual(result['actualEffective'],self.actual)
    def test_every_mount_or_privilege_override_rejected(self):
        import copy
        guest.validate_smoke_inspect(self.inspection,self.cid)
        for key,value in [('Privileged',True),('Binds',['/host:/guest']),('Memory',1),('NanoCpus',1),('PidsLimit',1),('SecurityOpt',[])]:
            changed=copy.deepcopy(self.inspection);changed['HostConfig'][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):guest.validate_smoke_inspect(changed,self.cid)
    def test_start_nonzero_retains_result_and_attempts_final_inspect_once(self):
        import json,subprocess
        original=self.boundary
        for inspect_failure in (False,True):
            with self.subTest(inspect_failure=inspect_failure),tempfile.TemporaryDirectory() as tmp:
                self.calls=[]
                def boundary(handle,argv,**kwargs):
                    result=original(handle,argv,**kwargs)
                    if 'start' in argv:return subprocess.CompletedProcess(argv,17,b'partial stdout',b'container failed')
                    if len(self.calls)==4 and inspect_failure:raise OSError('final inspection unavailable')
                    return result
                with patch.object(self,'boundary',side_effect=boundary),self.assertRaises(ValueError) as caught:
                    self.run_smoke(Path(tmp))
                self.assertEqual([argv[5] for argv in self.calls],['create','container','start','container'])
                self.assertRegex(str(caught.exception),'attach.*17')
                result=json.loads((Path(tmp)/'smoke-start-result.json').read_text())
                self.assertEqual(result['exitCode'],17);self.assertEqual(result['stdout'],'partial stdout');self.assertEqual(result['stderr'],'container failed')
                if inspect_failure:
                    error=json.loads((Path(tmp)/'smoke-inspect-after-error.json').read_text())
                    self.assertIn('final inspection unavailable',error['error'])
                else:self.assertTrue((Path(tmp)/'smoke-inspect-after.json').is_file())
    def test_start_timeout_preserves_original_exception_and_attempts_final_inspect_once(self):
        import json,subprocess
        original=self.boundary
        for inspect_failure in (False,True):
            with self.subTest(inspect_failure=inspect_failure),tempfile.TemporaryDirectory() as tmp:
                self.calls=[];attach_error=subprocess.TimeoutExpired(['start'],60,output=b'partial stdout',stderr=b'partial stderr')
                def boundary(handle,argv,**kwargs):
                    result=original(handle,argv,**kwargs)
                    if 'start' in argv:raise attach_error
                    if len(self.calls)==4 and inspect_failure:raise OSError('final inspection unavailable')
                    return result
                with patch.object(self,'boundary',side_effect=boundary),self.assertRaises(subprocess.TimeoutExpired) as caught:
                    self.run_smoke(Path(tmp))
                self.assertIs(caught.exception,attach_error)
                self.assertEqual([argv[5] for argv in self.calls],['create','container','start','container'])
                result=json.loads((Path(tmp)/'smoke-start-result.json').read_text())
                self.assertIsNone(result['exitCode']);self.assertTrue(result['processMayRemain']);self.assertIn('TimeoutExpired',result['error'])
                self.assertEqual(result['stdout'],'partial stdout');self.assertEqual(result['stderr'],'partial stderr')
                if inspect_failure:
                    error=json.loads((Path(tmp)/'smoke-inspect-after-error.json').read_text())
                    self.assertIn('final inspection unavailable',error['error'])
                else:self.assertTrue((Path(tmp)/'smoke-inspect-after.json').is_file())

class TrustedDispatchTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','Linux openat closure verification')
    def test_tampered_helper_never_imported_and_success_does_not_write_pycache(self):
        import subprocess,json,sys
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);file=root/'wsl_environment_guest.py'
            data=b'def dispatch(request):\n print("executed")\n';file.write_bytes(data)
            pins={'wsl_environment_guest.py':{'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}}
            argv=[sys.executable,'-I','-c',guest.TRUSTED_DISPATCH,str(root),json.dumps(pins),'{}']
            result=subprocess.run(argv,capture_output=True);self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout,b'executed\n')
            self.assertEqual({p.name for p in root.iterdir()},{'wsl_environment_guest.py'})
            file.write_bytes(data.replace(b'executed',b'tampered'))
            result=subprocess.run(argv,capture_output=True);self.assertNotEqual(result.returncode,0);self.assertEqual(result.stdout,b'')


class LoadEvidenceTests(unittest.TestCase):
    def test_partial_load_operations_include_timeout_and_fd_pin(self):
        import development_image_migration as migration,json,subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);file=root/'archive';file.write_bytes(b'archive')
            image={'role':'rust','imageId':'sha256:r','diffIds':[],'configObject':{'config':{}}}
            with file.open('rb') as source,patch.object(migration,'_prepare_all',return_value=[(source,image)]),patch.object(migration,'_check_sealed_source'),patch.object(guest.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'partial',b'failed')):
                with self.assertRaises(Exception):guest.load_archives('p01-test',root,root/'load.json')
            operation=json.loads((root/'load.json').read_text())['operations'][0]
            self.assertEqual(operation.get('timeout'),1800)
            self.assertEqual(operation.get('stdin'),{'bytes':7,'sha256':hashlib.sha256(b'archive').hexdigest()})

class LiveObservationTests(unittest.TestCase):
    def test_live_store_and_daemon_configuration_changes_rejected(self):
        self.assertTrue(callable(getattr(guest,'validate_live_info',None)),'validate_live_info API missing')
        good=namespace_info(images=3,containers=1)
        guest.validate_live_info(good,'p01-test')
        for key,value in [('DockerRootDir','/var/lib/docker'),('Images',0),('Containers',0),('ServerVersion','wrong'),('CgroupDriver','systemd')]:
            with self.subTest(key=key),self.assertRaises(ValueError):guest.validate_live_info({**good,key:value},'p01-test')

def namespace_info(*,images=0,containers=0):
    return {'ID':'inner','ServerVersion':'29.8.1','OSType':'linux','Architecture':'x86_64',
            'CgroupVersion':'2','CgroupDriver':'cgroupfs','Driver':'overlayfs',
            'DriverStatus':[['driver-type','io.containerd.snapshotter.v1']],
            'DockerRootDir':'/var/lib/p01-test/docker','Images':images,'Containers':containers,
            'Containerd':{'Namespaces':{'Containers':'p01-test','Plugins':'p01-test-plugins'}}}


class NamespaceContractTests(unittest.TestCase):
    def test_config_uses_official_singular_plugin_namespace_key(self):
        config=guest.daemon_config('p01-test')
        self.assertEqual(config.get('containerd-plugin-namespace'),'p01-test-plugins')
        self.assertNotIn('containerd-plugins-namespace',config)
    def test_live_info_rejects_missing_default_or_foreign_namespace_pair(self):
        good=namespace_info(images=3,containers=1);guest.validate_live_info(good,'p01-test')
        for value in (None,{}, {'Namespaces':None},{'Namespaces':{}},
                      {'Namespaces':{'Containers':'p01-test','Plugins':'plugins.moby'}},
                      {'Namespaces':{'Containers':'moby','Plugins':'p01-test-plugins'}},
                      {'Namespaces':{'Containers':'other','Plugins':'other-plugins'}},
                      {'Namespaces':{'Containers':'p01-test','Plugins':None}}):
            with self.subTest(value=value),self.assertRaises(ValueError):
                guest.validate_live_info({**good,'Containerd':value},'p01-test')
    def test_launcher_rejects_actual_namespace_before_empty_lists(self):
        for key,value in (('Plugins','plugins.moby'),('Containers','moby')):
            fixture=LauncherFailureTests('test_nonempty_info_blocks_without_restart');fixture.setUp()
            info=namespace_info();info['Containerd']['Namespaces'][key]=value
            fixture.launch(info=info)
            self.assertEqual(fixture.docker.call_count,1)
    def test_admission_checks_actual_pair_before_and_after_management_move(self):
        import json,subprocess,stat
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        identity={'pid':8,'starttime':'3','exeSha256':'hash','namespaces':{'mnt':'m','net':'n','cgroup':'c'}}
        handle={**identity,'generation':'p01-test','daemonId':'inner',
                'containerdNamespaces':{'Containers':'p01-test','Plugins':'p01-test-plugins'},
                'socket':{'device':1,'inode':2,'uid':0,'gid':0}}
        socket=SimpleNamespace(st_mode=stat.S_IFSOCK,st_uid=0,st_gid=0,st_dev=1,st_ino=2)
        good=namespace_info();bad=namespace_info();bad['Containerd']['Namespaces']['Plugins']='plugins.moby'
        for stage,responses in ((1,[bad,bad]),(2,[good,bad])):
            with self.subTest(check=stage),patch.object(guest,'process_identity',return_value=identity),patch.object(guest.os,'stat',return_value=socket),patch.object(guest,'_docker',side_effect=[subprocess.CompletedProcess([],0,json.dumps(info).encode(),b'') for info in responses]),patch.object(guest,'Path',return_value=MagicMock()),patch.object(guest,'_entered') as entered:
                with self.assertRaises(ValueError):guest.admit_environment(handle,['/bin/true'])
                entered.assert_not_called()
    def test_observe_rejects_actual_live_pair_before_replaying_proofs(self):
        import json,subprocess
        info=namespace_info(images=3,containers=1);info['Containerd']['Namespaces']['Plugins']='plugins.moby'
        handle={'generation':'p01-test','pid':8}
        with patch.object(guest,'validate_handle'),patch.object(guest,'observe_handle',return_value={}),patch.object(guest,'_entered',return_value=subprocess.CompletedProcess([],0,json.dumps(info).encode(),b'')),patch.object(guest,'Path',side_effect=AssertionError('live namespace not rejected')):
            with self.assertRaises(ValueError):guest.observe_guest(handle,{}, {})
    def test_observe_replays_first_empty_namespace_pair(self):
        import json,subprocess
        from unittest.mock import MagicMock
        first=namespace_info();first['Containerd']['Namespaces']['Plugins']='plugins.moby'
        path=MagicMock();path.read_text.return_value=json.dumps({'info':first,'lists':{k:{'exitCode':0,'stdout':''} for k in ('images','containers','volumes')}})
        with patch.object(guest,'validate_handle'),patch.object(guest,'observe_handle',return_value={}),patch.object(guest,'_entered',return_value=subprocess.CompletedProcess([],0,json.dumps(namespace_info(images=3,containers=1)).encode(),b'')),patch.object(guest,'Path',return_value=path),patch.object(guest,'shared_controls',side_effect=AssertionError('first-empty namespace not rejected')):
            with self.assertRaises(ValueError):guest.observe_guest({'generation':'p01-test','pid':8}, {}, {})


if __name__ == '__main__': unittest.main()
