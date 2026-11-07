import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import linux_host_storage as storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'probe'
        self.root.mkdir(mode=0o700)

    def probe(self):
        return storage.probe(self.root, storage.MINIMUM_FREE_BYTES)

    def test_real_probe_reopen_and_complete_operations(self):
        receipt = self.probe()
        result = storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)
        self.assertEqual(result['root'], receipt['root'])
        self.assertEqual(result['marker'], receipt['marker'])
        self.assertTrue(result['lockCompetitorBlocked'])
        self.assertTrue(result['lockAvailableAfterRelease'])
        self.assertEqual(result['operations'], ['create', 'write', 'file-fsync',
                         'rename', 'directory-fsync', 'delete', 'directory-fsync'])
        self.assertFalse(result['finalSystemAccepted'])

    def test_wrong_uid_rejected(self):
        with self.assertRaises(storage.StorageError):
            storage.probe(Path('/'), storage.MINIMUM_FREE_BYTES)

    def test_wrong_mode_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(storage.StorageError):
            self.probe()

    def test_symlink_root_rejected(self):
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(storage.StorageError):
            storage.probe(alias, storage.MINIMUM_FREE_BYTES)

    def test_symlink_ancestor_rejected(self):
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        nested = self.root / 'nested'
        nested.mkdir(mode=0o700)
        with self.assertRaises(storage.StorageError):
            storage.probe(alias / 'nested', storage.MINIMUM_FREE_BYTES)

    def test_nonempty_probe_rejected_without_deleting(self):
        item = self.root / 'keep'
        item.write_bytes(b'keep')
        with self.assertRaises(storage.StorageError):
            self.probe()
        self.assertEqual(item.read_bytes(), b'keep')

    def test_insufficient_space_rejected_before_writes(self):
        available = os.statvfs(self.root).f_bavail * os.statvfs(self.root).f_frsize
        with self.assertRaises(storage.StorageError):
            storage.probe(self.root, available + 1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_below_reserve_budget_rejected(self):
        with self.assertRaises(storage.StorageError):
            storage.probe(self.root, 1)

    def test_replaced_root_rejected(self):
        receipt = self.probe()
        old = self.root.with_name('old')
        self.root.rename(old)
        self.root.mkdir(mode=0o700)
        with self.assertRaises(storage.StorageError):
            storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)

    def test_directory_replacement_during_lease_rejected(self):
        receipt = self.probe()
        with self.assertRaises(storage.StorageError):
            with storage.storage_session(self.root, receipt, storage.MINIMUM_FREE_BYTES):
                self.root.rename(self.root.with_name('old'))
                self.root.mkdir(mode=0o700)

    def test_hardlink_marker_rejected(self):
        receipt = self.probe()
        os.link(self.root / 'marker', Path(self.temp.name) / 'alias')
        with self.assertRaises(storage.StorageError):
            storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)

    def test_symlink_marker_rejected(self):
        receipt = self.probe()
        (self.root / 'marker').unlink()
        (self.root / 'marker').symlink_to('/etc/passwd')
        with self.assertRaises(storage.StorageError):
            storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)

    def test_modified_marker_rejected(self):
        receipt = self.probe()
        (self.root / 'marker').write_bytes(b'changed')
        with self.assertRaises(storage.StorageError):
            storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)

    def test_bad_lease_permissions_rejected(self):
        receipt = self.probe()
        (self.root / 'lease').chmod(0o666)
        with self.assertRaises(storage.StorageError):
            storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)

    def test_extra_writer_process_rejected(self):
        receipt = self.probe()
        child = subprocess.Popen([sys.executable, '-c',
            'import fcntl,sys; f=open(sys.argv[1],"r+"); '
            'fcntl.flock(f,fcntl.LOCK_EX); print("LOCKED",flush=True); sys.stdin.read()',
            str(self.root / 'lease')], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True)
        try:
            import select
            self.assertTrue(select.select([child.stdout], [], [], 5)[0])
            self.assertEqual(child.stdout.readline().strip(), 'LOCKED')
            with self.assertRaises(storage.StorageError):
                storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)
        finally:
            child.communicate('', timeout=5)
        self.assertEqual(child.returncode, 0)

    def test_extra_entry_rejected(self):
        receipt = self.probe()
        (self.root / 'unknown').write_bytes(b'x')
        with self.assertRaises(storage.StorageError):
            storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)

    def test_receipt_identity_tampering_rejected(self):
        receipt = self.probe()
        receipt['marker']['ino'] += 1
        with self.assertRaises(storage.StorageError):
            storage.reopen(self.root, receipt, storage.MINIMUM_FREE_BYTES)


if __name__ == '__main__':
    unittest.main()
