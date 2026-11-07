"""Public runtime initializer controls; no Docker, builds, or guests."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

SCRIPT = Path(__file__).with_name('snapshot_linux_host_runtime.py')


class RuntimeInitializerControls(unittest.TestCase):
    def load(self):
        self.assertTrue(SCRIPT.is_file(), 'public runtime snapshot helper required')
        spec = importlib.util.spec_from_file_location('runtime_snapshot_control', SCRIPT)
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value

    def test_import_and_help_do_not_start_processes_or_initialize_volumes(self):
        with mock.patch('subprocess.Popen', side_effect=AssertionError('unexpected child')):
            self.load()
        result = subprocess.run([sys.executable, '-B', str(SCRIPT), '--help'], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_explicit_review_must_match_every_pending_field_without_autoapproval(self):
        module = self.load()
        base = {'reviewed': False, 'reviewer': None, 'hostSha256': 'a' * 64, 'frozenSource': 'b' * 64}
        approved = dict(base, reviewed=True, reviewer='independent root reviewer')
        self.assertEqual(module.validate_explicit_review(base, approved), approved)
        self.assertEqual(base['reviewed'], False)
        for value in (base, dict(approved, reviewer=''), dict(approved, hostSha256='c' * 64),
                      dict(approved, extra=True)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                module.validate_explicit_review(base, value)

    def test_output_initializer_checks_empty_before_preopened_ownership_handoff(self):
        module = self.load()
        events = []
        with mock.patch.object(module, 'require_root'), \
             mock.patch.object(module.q, 'checked_directory', side_effect=lambda path: path), \
             mock.patch.object(module, 'require_empty_volumes', side_effect=lambda paths: events.append(('empty', paths))), \
             mock.patch.object(module, 'initialize_volume', side_effect=lambda path: events.append(('handoff', path))):
            module.initialize_output(Path('/out'))
        self.assertEqual(events, [('empty', (Path('/out'),)), ('handoff', Path('/out'))])


if __name__ == '__main__':
    unittest.main()
