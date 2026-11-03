"""Real Linux tmpfs lease checks; no QEMU, kernel, or guest is started."""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import data_disk
import test_boot
from bootlib import qemu_command


class RuntimeVmBoundaryTests(unittest.TestCase):
    persistent_bundle = test_boot.BootBoundaryTests.persistent_bundle

    def test_explicit_runtime_changes_only_qmp_address_and_preserves_default_command(self):
        import vm
        with tempfile.TemporaryDirectory() as work:
            base = Path(work); review, first, _ = self.persistent_bundle(base, 'persistent-uninstall')
            disk = {'imagePath': '/task/disk.raw', 'uuid': 'disk-uuid', 'serial': 'MINGDATA0123456789ab'}
            output = base / 'out'
            argv = ['vm.py', '--bundle', str(base), '--review', str(review), '--output', str(output),
                    '--input-script', str(first), '--boot-number', '1', '--work-root', '/unused-test-root',
                    '--data-manifest', 'disk.json', '--diagnostic-split-input', '--diagnostic-pointer-handshake',
                    '--runtime-root', '/run/mingos-runtime', '--dry-run']
            expected = qemu_command(base, output, data_disk=disk, input_diagnostics=True,
                                    boot_number=1, persistent_profile='persistent-uninstall')
            with mock.patch.object(sys, 'argv', argv), mock.patch('data_disk.load_disk', return_value=disk), \
                 mock.patch('data_disk.lease', return_value=contextlib.nullcontext()), \
                 mock.patch('data_disk.runtime_directory', return_value=Path('/run/mingos-runtime'), create=True), \
                 mock.patch('subprocess.Popen', side_effect=AssertionError('dry run started a process')):
                try:
                    with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(vm.main(), 0)
                except SystemExit as error:
                    self.fail('explicit runtime root was rejected: ' + str(error))
            result = json.loads((output / 'result.json').read_text())
            index = expected.index('-qmp') + 1
            address = result['argv'][index]
            self.assertTrue(address.startswith('unix:/run/mingos-runtime/qmp-'))
            self.assertTrue(address.endswith('.sock,server=on,wait=off'))
            result['argv'][index] = expected[index]
            self.assertEqual(result['argv'], expected)
            self.assertFalse(result['bootAttempted'])


    def test_explicit_runtime_rejects_changed_qmp_contract_before_process_start(self):
        import vm
        for change in ('duplicate', 'different-address', 'missing', 'missing-address'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as work:
                base = Path(work); review, first, _ = self.persistent_bundle(base, 'persistent-uninstall')
                output = base / 'out'
                disk = {'imagePath': '/task/disk.raw', 'uuid': 'disk-uuid', 'serial': 'MINGDATA0123456789ab'}
                command = qemu_command(base, output, data_disk=disk, input_diagnostics=True,
                                       boot_number=1, persistent_profile='persistent-uninstall')
                if change == 'duplicate': command += ['-qmp', 'unix:/unexpected']
                elif change == 'different-address': command[command.index('-qmp') + 1] = 'unix:/unexpected'
                elif change == 'missing':
                    index = command.index('-qmp'); del command[index:index + 2]
                else: command = command[:command.index('-qmp') + 1]
                argv = ['vm.py', '--bundle', str(base), '--review', str(review), '--output', str(output),
                        '--input-script', str(first), '--boot-number', '1', '--work-root', '/unused-test-root',
                        '--data-manifest', 'disk.json', '--diagnostic-split-input', '--diagnostic-pointer-handshake',
                        '--runtime-root', '/run/mingos-runtime', '--dry-run']
                with mock.patch.object(sys, 'argv', argv), mock.patch('data_disk.load_disk', return_value=disk), \
                     mock.patch('data_disk.lease', return_value=contextlib.nullcontext()), \
                     mock.patch('data_disk.runtime_directory', return_value=Path('/run/mingos-runtime'), create=True), \
                     mock.patch.object(vm, 'qemu_command', return_value=command), \
                     mock.patch('subprocess.Popen', side_effect=AssertionError('invalid command started process')):
                    try:
                        with self.assertRaises(ValueError): vm.main()
                    except SystemExit as error:
                        self.fail('runtime parser rejected before command validation: ' + str(error))


    def test_runtime_rejects_stale_socket_and_long_address_before_output_creation(self):
        import vm
        for invalid in ('file', 'symlink', 'long'):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as work:
                base = Path(work); runtime = base / 'runtime'; runtime.mkdir()
                output = base / 'out'
                argv = ['vm.py', '--probe-paused', '--output', str(output),
                        '--runtime-root', str(runtime), '--dry-run']
                with mock.patch.object(sys, 'argv', argv), \
                     mock.patch('data_disk.runtime_directory', return_value=runtime), \
                     mock.patch('subprocess.Popen', side_effect=AssertionError('dry run started process')):
                    with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(vm.main(), 0)
                command = json.loads((output / 'result.json').read_text())['argv']
                address = command[command.index('-qmp') + 1]
                socket = Path(address.removeprefix('unix:').removesuffix(',server=on,wait=off'))
                shutil.rmtree(output)
                if invalid == 'file': socket.touch()
                elif invalid == 'symlink': socket.symlink_to(base / 'missing')
                else: runtime = base / ('x' * 100)
                with mock.patch.object(sys, 'argv', argv), \
                     mock.patch('data_disk.runtime_directory', return_value=runtime), \
                     mock.patch('subprocess.Popen', side_effect=AssertionError('invalid runtime started process')):
                    with self.assertRaises((FileExistsError, ValueError)): vm.main()
                self.assertFalse(output.exists())



@unittest.skipUnless(sys.platform == 'linux' and os.geteuid() == 65534
                     and Path('/run/mingos-runtime').is_dir() and Path('/work').is_dir(),
                     'requires isolated uid65534 /work and Linux tmpfs /run/mingos-runtime')
class RuntimeStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = data_disk.workspace(Path('/work/mingos-boot-infra-20261025'), create=True)
        (cls.work / 'disks').mkdir(mode=0o700)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir='/run/mingos-runtime')
        self.runtime = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        name = uuid.uuid4().hex
        self.image = self.work / ('disks/' + name + '.raw')
        self.image.write_bytes(b'new isolated test image')
        self.image.chmod(0o600)
        self.manifest = {'image': 'disks/' + name + '.raw', 'uuid': str(uuid.uuid4())}

    def contender(self, manifest=None):
        script = '''import json,sys
from pathlib import Path
from data_disk import lease
try:
    with lease(Path(sys.argv[1]), json.loads(sys.argv[2]), runtime_root=Path(sys.argv[3])): pass
except BlockingIOError: sys.exit(73)
'''
        return subprocess.run([sys.executable, '-B', '-c', script, str(self.work),
                               json.dumps(manifest or self.manifest), str(self.runtime)],
                              capture_output=True, text=True, timeout=10)

    def test_linux_runtime_rejects_other_filesystem(self):
        self.assertEqual(data_disk.runtime_directory(self.runtime), self.runtime)
        with self.assertRaisesRegex(ValueError, 'tmpfs'):
            data_disk.runtime_directory(self.work)

    def test_runtime_rejects_alias_wrong_owner_and_permissions(self):
        alias = self.runtime / 'alias'; alias.symlink_to(self.runtime, target_is_directory=True)
        for path in (alias, self.runtime / '..' / self.runtime.name, Path('relative')):
            with self.subTest(path=str(path)), self.assertRaises(ValueError):
                data_disk.runtime_directory(path)
        self.runtime.chmod(0o750)
        with self.assertRaises(ValueError): data_disk.runtime_directory(self.runtime)
        self.runtime.chmod(0o700)
        with mock.patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)):
            with self.assertRaises(ValueError): data_disk.runtime_directory(self.runtime)
        with mock.patch.object(data_disk.sys, 'platform', 'darwin'):
            with self.assertRaises(ValueError): data_disk.runtime_directory(self.runtime)

    def test_linux_lease_rejects_an_independent_process_and_releases_after_exception(self):
        with self.assertRaisesRegex(RuntimeError, 'test exception'):
            with data_disk.lease(self.work, self.manifest, runtime_root=self.runtime):
                result = self.contender(dict(self.manifest, uuid=str(uuid.uuid4())))
                self.assertEqual(result.returncode, 73, result.stderr)
                self.assertFalse(self.image.with_suffix('.raw.lock').exists())
                raise RuntimeError('test exception')
        result = self.contender()
        self.assertEqual(result.returncode, 0, result.stderr)
        locks = list(self.runtime.glob('*.lock'))
        self.assertEqual(len(locks), 1)
        self.assertEqual(stat.S_IMODE(locks[0].stat().st_mode), 0o600)

    def test_runtime_lease_rejects_image_hardlink(self):
        other = self.image.with_suffix('.alias'); os.link(self.image, other)
        with self.assertRaises(ValueError):
            with data_disk.lease(self.work, self.manifest, runtime_root=self.runtime): pass

    def test_runtime_lease_rejects_replaced_lock(self):
        for invalid in ('symlink', 'hardlink', 'permissions'):
            with self.subTest(invalid=invalid):
                with data_disk.lease(self.work, self.manifest, runtime_root=self.runtime): pass
                lock = next(self.runtime.glob('*.lock'))
                extra = self.runtime / 'extra'
                if invalid == 'symlink':
                    lock.unlink(); lock.symlink_to(self.image)
                elif invalid == 'hardlink': os.link(lock, extra)
                else: lock.chmod(0o644)
                with self.assertRaises((OSError, ValueError)):
                    with data_disk.lease(self.work, self.manifest, runtime_root=self.runtime): pass
                lock.unlink()
                if extra.exists(): extra.unlink()
                self.assertEqual(self.image.read_bytes(), b'new isolated test image')

    def test_default_lease_still_uses_image_sidecar(self):
        with data_disk.lease(self.work, self.manifest):
            self.assertTrue(self.image.with_suffix('.raw.lock').is_file())
            self.assertEqual(list(self.runtime.iterdir()), [])


if __name__ == '__main__': unittest.main()
