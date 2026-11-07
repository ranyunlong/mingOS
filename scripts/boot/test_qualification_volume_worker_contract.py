"""Linux contract tests for the descriptor worker.

These are not satisfied by Windows skips. Run with Linux UID/GID65534 after
qualification_volume_worker.py; a missing module then fails.
The independent successful RO verification additionally needs a real RO mount.
"""
import hashlib
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import qualification_volume_worker as worker
except ModuleNotFoundError:
    worker = None

import test_qualification_volume_archive as fixtures

digest = fixtures.digest


LINUX_WORKER = (sys.platform == 'linux' and hasattr(os, 'geteuid')
    and os.geteuid() == 65534 and os.getegid() == 65534)


@unittest.skipIf(sys.platform == 'linux', 'non-Linux admission controls')
class WorkerPlatformBoundary(unittest.TestCase):
    def test_restore_rejects_nonlinux_before_any_open_or_stream_read(self):
        self.assertIsNotNone(worker)
        source = mock.Mock()
        with mock.patch.object(os, 'open', side_effect=AssertionError('no target access before platform admission')):
            with self.assertRaises(worker.WorkerError) as error:
                worker.restore(Path('/restore'), source, 'a' * 64, 'b' * 32)
        source.read.assert_not_called()
        self.assertEqual(error.exception.receipt['phase'], 'platform')

    def test_verify_rejects_nonlinux_before_any_open(self):
        self.assertIsNotNone(worker)
        with mock.patch.object(os, 'open', side_effect=AssertionError('no target access before platform admission')):
            with self.assertRaises(worker.WorkerError) as error:
                worker.verify_readonly(Path('/restore'), {})
        self.assertEqual(error.exception.receipt['phase'], 'platform')


@unittest.skipUnless(LINUX_WORKER, 'requires real Linux UID/GID65534; skips are not worker validation')
class LinuxWorkerContract(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(worker, 'qualification_volume_worker must be implemented before Linux acceptance')
        self.temp = tempfile.TemporaryDirectory(prefix='mingos-restore-worker-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'restore'
        self.root.mkdir(mode=0o700)
        for name in ('payload', 'control'):
            (self.root / name).mkdir(mode=0o700)
        fixture = fixtures.VolumeArchiveTests()
        fixture.setUp()
        self.raw, self.expected, self.nonce = fixture.raw(), fixture.manifest, 'd' * 32

    def restore(self, raw=None, expected_hash=None):
        raw = self.raw if raw is None else raw
        return worker.restore(self.root, io.BytesIO(raw), expected_hash or digest(raw), self.nonce)

    def test_real_restore_has_exact_owner_modes_bytes_nlink_and_privatecopy(self):
        receipt = self.restore()
        self.assertEqual((self.root / 'payload/cache/data').read_bytes(), b'hello')
        self.assertEqual(receipt['archiveSha256'], digest(self.raw))
        self.assertEqual(receipt['nonce'], self.nonce)
        for path, mode, regular in ((self.root, 0o700, False),
                (self.root / 'payload', 0o700, False), (self.root / 'control', 0o700, False),
                (self.root / 'payload/cache', 0o700, False), (self.root / 'payload/cache/data', 0o600, True),
                (self.root / 'control/archive-privatecopy.tar', 0o600, True),
                (self.root / 'control/restore.lease', 0o600, True)):
            observed = path.lstat()
            self.assertEqual((observed.st_uid, observed.st_gid, stat.S_IMODE(observed.st_mode)), (65534, 65534, mode))
            self.assertTrue(stat.S_ISREG(observed.st_mode) if regular else stat.S_ISDIR(observed.st_mode))
            if regular:
                self.assertEqual(observed.st_nlink, 1)
        self.assertEqual((self.root / 'control/archive-privatecopy.tar').read_bytes(), self.raw)
        self.assertFalse(receipt['formalQualification'])
        self.assertFalse(receipt['finalSystemAccepted'])

    def test_bad_last_member_fails_before_payload_creation_and_preserves_copy(self):
        invalid = bytearray(self.raw)
        location = invalid.find(b'hello')
        self.assertGreater(location, 0)
        invalid[location] ^= 1
        with self.assertRaises(ValueError):
            self.restore(bytes(invalid))
        self.assertEqual(list((self.root / 'payload').iterdir()), [])
        self.assertEqual((self.root / 'control/archive-privatecopy.tar').read_bytes(), bytes(invalid))

    def test_wrong_digest_preserves_copy_without_payload_writes(self):
        with self.assertRaises(ValueError):
            self.restore(expected_hash='0' * 64)
        self.assertEqual(list((self.root / 'payload').iterdir()), [])
        self.assertTrue((self.root / 'control/archive-privatecopy.tar').is_file())

    def test_nonempty_payload_is_never_overwritten_or_removed(self):
        sentinel = self.root / 'payload/keep'
        sentinel.write_bytes(b'unchanged')
        sentinel.chmod(0o600)
        with self.assertRaises(ValueError):
            self.restore()
        self.assertEqual(sentinel.read_bytes(), b'unchanged')
        self.assertEqual([item.name for item in (self.root / 'payload').iterdir()], ['keep'])

    def test_failed_privatecopy_is_never_reused_or_overwritten(self):
        retained = self.root / 'control/archive-privatecopy.tar'
        retained.write_bytes(b'keep partial failure')
        retained.chmod(0o600)
        with self.assertRaises((ValueError, FileExistsError)):
            self.restore()
        self.assertEqual(retained.read_bytes(), b'keep partial failure')
        self.assertEqual(list((self.root / 'payload').iterdir()), [])

    def test_unknown_control_entry_fails_closed_and_is_preserved(self):
        retained = self.root / 'control/unexpected'
        retained.write_bytes(b'keep control')
        retained.chmod(0o600)
        with self.assertRaises(ValueError):
            self.restore()
        self.assertEqual(retained.read_bytes(), b'keep control')
        self.assertEqual(list((self.root / 'payload').iterdir()), [])

    def test_symlink_root_rejected_without_target_changes(self):
        alias = self.root.parent / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            worker.restore(alias, io.BytesIO(self.raw), digest(self.raw), self.nonce)
        self.assertEqual(list((self.root / 'payload').iterdir()), [])
        self.assertEqual(list((self.root / 'control').iterdir()), [])

    def test_symlink_ancestor_rejected_without_target_changes(self):
        alias = self.root.parent / 'alias'
        alias.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            worker.restore(alias / 'restore', io.BytesIO(self.raw), digest(self.raw), self.nonce)
        self.assertEqual(list((self.root / 'payload').iterdir()), [])

    def test_payload_and_control_wrong_mode_rejected(self):
        for name in ('payload', 'control'):
            with self.subTest(name=name):
                path = self.root / name
                path.chmod(0o755)
                with self.assertRaises(ValueError):
                    self.restore()
                path.chmod(0o700)
        self.assertEqual(list((self.root / 'payload').iterdir()), [])

    def test_same_volume_cannot_be_restored_again(self):
        self.restore()
        snapshot = {path.relative_to(self.root).as_posix(): path.read_bytes()
                    for path in self.root.rglob('*') if path.is_file()}
        with self.assertRaises((ValueError, FileExistsError)):
            self.restore()
        after = {path.relative_to(self.root).as_posix(): path.read_bytes()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(after, snapshot)

    def test_independent_real_process_can_hold_existing_lease(self):
        self.restore()
        lease = self.root / 'control/restore.lease'
        code = ('import fcntl,os,sys; '
            'fd=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW); '
            'fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); print("LOCKED",flush=True); sys.stdin.read()')
        child = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(lease)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            import select
            self.assertTrue(select.select([child.stdout], [], [], 5)[0], 'lease child must respond within deadline')
            self.assertEqual(child.stdout.readline().strip(), 'LOCKED')
            import fcntl
            fd = os.open(lease, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
        finally:
            child.communicate('', timeout=5)
        self.assertEqual(child.returncode, 0)

    def test_readonly_verifier_rejects_writable_mount_before_success(self):
        receipt = self.restore()
        self.assertFalse(os.statvfs(self.root).f_flag & os.ST_RDONLY)
        before = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.root.rglob('*') if path.is_file()}
        with self.assertRaises(ValueError):
            worker.verify_readonly(self.root, receipt)
        after = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
