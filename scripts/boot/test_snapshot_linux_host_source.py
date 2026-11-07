"""Snapshot controls use temporary directories and never Docker or a guest."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).with_name('snapshot_linux_host_source.py')


class SnapshotControls(unittest.TestCase):
    def load(self):
        self.assertTrue(SCRIPT.is_file(), 'public source snapshot helper required')
        spec = importlib.util.spec_from_file_location('source_snapshot_control', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_import_and_help_have_no_filesystem_or_process_side_effects(self):
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no child on import')):
            self.load()
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--help'], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'source', result.stdout)

    def test_copy_preserves_source_bytes_modes_and_empty_directories_excludes_caches(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / 'checkout', root / 'copy'
            (source / 'src/empty').mkdir(parents=True)
            (source / 'build').mkdir()
            (source / 'build/cache').write_bytes(b'ignored')
            (source / 'src/main.py').write_bytes(b'actual source\n')
            (source / 'src/main.py').chmod(0o755)
            before = {'src/main.py': {'sha256': hashlib.sha256(b'actual source\n').hexdigest(), 'bytes': 14}}
            # Windows has no dirfd/O_NOFOLLOW APIs. Keep the Linux production
            # inventory on Linux; adapt only those APIs for local copy controls.
            def portable(path):
                return {str(p.relative_to(path)).replace('\\', '/'): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
                        for p in sorted(path.rglob('*')) if p.is_file() and 'build' not in p.relative_to(path).parts}
            with mock.patch.object(module.q, 'source_inventory', side_effect=portable) if sys.platform != 'linux' else mock.patch.object(module.q, 'source_inventory', wraps=module.q.source_inventory):
                result = module.copy_source(source, destination)
            self.assertEqual(result, before)
            self.assertEqual((destination / 'src/main.py').read_bytes(), b'actual source\n')
            self.assertTrue((destination / 'src/empty').is_dir())
            self.assertFalse((destination / 'build').exists())
            with self.assertRaises(FileExistsError):
                module.copy_source(source, destination)

    def test_nonempty_generation_volume_rejected_before_copy(self):
        module = self.load()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = [root / name for name in ('source', 'prep', 'target')]
            for path in paths:
                path.mkdir()
            (paths[2] / 'existing').write_bytes(b'preserve')
            with self.assertRaisesRegex(ValueError, 'literal empty'):
                module.require_empty_volumes(paths)
            self.assertEqual((paths[2] / 'existing').read_bytes(), b'preserve')
            self.assertEqual(list(paths[0].iterdir()), [])

    def test_ownership_handoff_fsyncs_preopened_fd_without_reopening_private_directory(self):
        module = self.load()
        self.assertTrue(callable(getattr(module, 'initialize_volume', None)), 'preopened ownership handoff required')
        events = []
        with mock.patch.object(module.q, 'open_directory', side_effect=lambda path: events.append('open') or 123), \
             mock.patch.object(module.os, 'fchmod', side_effect=lambda *args: events.append('chmod'), create=True), \
             mock.patch.object(module.os, 'fchown', side_effect=lambda *args: events.append('chown'), create=True), \
             mock.patch.object(module.os, 'fsync', side_effect=lambda fd: events.append(('fsync', fd))), \
             mock.patch.object(module.os, 'close', side_effect=lambda fd: events.append(('close', fd))), \
             mock.patch.object(module.q, 'sync_directory', side_effect=PermissionError('root has only CHOWN after handoff')):
            module.initialize_volume(Path('/prep'))
        self.assertEqual(events, ['open', 'chmod', 'chown', ('fsync', 123), ('close', 123)])


if __name__ == '__main__':
    unittest.main()
