import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
try:
    from wsl_environment_guest import receive_file
except ImportError:
    receive_file = None

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
