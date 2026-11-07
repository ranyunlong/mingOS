"""Linux uid/gid65534 filesystem controls; no mkfs, Docker or guest.

Only the fixed workspace/runtime locations are relocated into a fresh private
temporary directory. Real O_NOFOLLOW, owner/mode checks, ext4 byte identity,
readonly opens, flock conflicts and missing-lock refusals remain enabled.
"""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_developer_vm import d, fake_disk, put, sha, FLAGS, PIN

@unittest.skipUnless(sys.platform=='linux' and os.geteuid()==65534 and os.getegid()==65534,
                     'requires supported Linux uid/gid65534')
class LinuxFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.scratch=tempfile.TemporaryDirectory(prefix='developer-vm-linux-')
        self.addCleanup(self.scratch.cleanup)
        self.base=Path(self.scratch.name);self.root=self.base/'mingos-boot-infra-20261025';self.root.mkdir(mode=0o700)
        self.runtime=self.base/'runtime';self.runtime.mkdir(mode=0o700)
        disk=fake_disk(self.root,'developer-128',128)
        self.instance=self.root/'developer-instance.json'
        put(self.instance,dict(schemaVersion=1,profile='developer-vm-v1',generation='34567890-1234-4234-8234-123456789abc',
            nonce='3'*32,packManifestSha256=PIN,bundleSha256='b'*64,diskManifest='disks/developer-128.json',
            diskUuid=disk['uuid'],diskSerial=disk['serial'],**FLAGS))
        put(self.root/'developer-instance.sha256',(sha(self.instance)+'\n').encode())
        put(self.root/'developer-instance.lock',b'');(self.root/'attempts').mkdir(mode=0o700)
        key=hashlib.sha256(os.fsencode(self.root/'disks/developer-128.raw')).hexdigest()
        self.disklock=self.runtime/('disk-'+key+'.lock');put(self.disklock,b'')
        for path in self.base.rglob('*'):path.chmod(0o700 if path.is_dir() else 0o600)
        def workspace(path,create=False):
            if Path(path)!=self.root or create:raise ValueError('fixture workspace policy')
            d._private(path,directory=True);return Path(path)
        for name,value in [('_workspace',workspace),('_runtime_root',lambda:self.runtime)]:
            p=patch.object(d,name,value);p.start();self.addCleanup(p.stop)

    def test_inspect_real_readonly_opens_no_processes_no_byte_changes(self):
        before={str(p):sha(p) for p in self.base.rglob('*') if p.is_file()}
        original_open=os.open
        def readonly_open(path,flags,*args,**kwargs):
            self.assertEqual(flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND),0)
            return original_open(path,flags,*args,**kwargs)
        with patch.object(os,'open',readonly_open),patch('subprocess.Popen',side_effect=AssertionError('process')), \
             patch('subprocess.run',side_effect=AssertionError('process')):
            result=d.inspect(self.instance)
        self.assertEqual(result['state'],'ready')
        self.assertEqual(before,{str(p):sha(p) for p in self.base.rglob('*') if p.is_file()})

    def test_busy_same_image_runtime_lease_refuses_inspect(self):
        import fcntl
        fd=os.open(self.disklock,os.O_RDWR|os.O_NOFOLLOW)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError,'busy'):d.inspect(self.instance)
        finally:os.close(fd)

    def test_missing_lock_is_never_created(self):
        self.disklock.unlink()
        with self.assertRaises((ValueError,FileNotFoundError)):d.inspect(self.instance)
        self.assertFalse(self.disklock.exists())

    def test_linked_instance_lock_refused(self):
        lock=self.root/'developer-instance.lock';lock.unlink()
        target=put(self.base/'target',b'preserve');target.chmod(0o600)
        lock.symlink_to(target)
        with self.assertRaises(ValueError):d.inspect(self.instance)
        self.assertEqual(target.read_bytes(),b'preserve');lock.unlink()
        os.link(target,lock)
        with self.assertRaises(ValueError):d.inspect(self.instance)
        self.assertEqual(target.read_bytes(),b'preserve')

    def test_nonprivate_image_refused(self):
        (self.root/'disks/developer-128.raw').chmod(0o644)
        with self.assertRaisesRegex(ValueError,'private'):d.inspect(self.instance)

    def test_writable_pack_mount_refused(self):
        with self.assertRaisesRegex(ValueError,'read-only'):d._read_only_pack(self.base)

    def test_broken_failure_latch_is_readonly_locked(self):
        latch=self.root/'failure-latch.json';latch.symlink_to(self.base/'absent-target')
        result=d.inspect(self.instance)
        self.assertEqual(result['state'],'locked');self.assertTrue(latch.is_symlink())

if __name__=='__main__':unittest.main()
