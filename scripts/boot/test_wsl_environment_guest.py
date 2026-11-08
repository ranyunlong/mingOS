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
        handle={'pid':8,'starttime':'3','exeSha256':'a','namespaces':{'mnt':'m'},'socket':{'inode':4,'uid':0,'gid':0},'daemonId':'d'}
        guest.validate_handle(handle,dict(handle))
        for key,value in [('pid',9),('starttime','4'),('exeSha256','b'),('namespaces',{'mnt':'n'}),('socket',{'inode':5,'uid':0,'gid':0}),('daemonId','e')]:
            observed=dict(handle); observed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): guest.validate_handle(handle,observed)
    def test_info_contract_fails_nonempty_and_wrong_driver(self):
        info={'ID':'id','ServerVersion':'29.8.1','OSType':'linux','Architecture':'x86_64','CgroupVersion':'2','CgroupDriver':'cgroupfs','Driver':'overlayfs','DriverStatus':[['driver-type','io.containerd.snapshotter.v1']],'Images':0,'Containers':0}
        guest.validate_info(info)
        for key,value in [('Images',1),('Containers',1),('Driver','overlay2'),('ServerVersion','29.0'),('CgroupVersion','1')]:
            changed=dict(info);changed[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError): guest.validate_info(changed)
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

if __name__ == '__main__': unittest.main()

class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.handle={'generation':'p01-test','pid':8,'starttime':'3','exeSha256':'a','namespaces':{'mnt':'m'},'socket':{'inode':4,'uid':0,'gid':0},'daemonId':'d'}
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
        self.info={'ID':'id','ServerVersion':'29.8.1','OSType':'linux','Architecture':'x86_64','CgroupVersion':'2','CgroupDriver':'cgroupfs','Driver':'overlayfs','DriverStatus':[['driver-type','io.containerd.snapshotter.v1']],'Images':0,'Containers':0}
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
