"""Bounded startup failure evidence controls; never launches a guest or service."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import guest


NAMES = ('weston.log', 'weston-stderr.log', 'seatd.log')


class StartupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        patcher = mock.patch.multiple(guest, LOG=self.root, channel=None, log_offsets={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def diagnostics(self):
        self.assertTrue(callable(getattr(guest, 'startup_diagnostics', None)),
                        'startup diagnostics collector must exist')
        return guest.startup_diagnostics()

    def test_fixed_three_names_and_missing_files_are_bounded_metadata(self):
        (self.root / 'unrelated.log').write_bytes(b'must not be read')
        value = self.diagnostics()
        self.assertEqual(set(value['files']), set(NAMES))
        self.assertEqual(value['maxBytesPerFile'], 4096)
        self.assertEqual(value['maxTotalBytes'], 12288)
        for row in value['files'].values():
            self.assertEqual(row['status'], 'unavailable')
            self.assertNotIn('data', row)

    def test_each_tail_is_at_most_4096_exact_bytes_with_original_offset(self):
        raw = b'older log\n' * 3000 + b'Weston fatal: missing shared object\n'
        for name in NAMES: (self.root / name).write_bytes(raw)
        value = self.diagnostics()
        self.assertEqual(sum(row['bytes'] for row in value['files'].values()), 12288)
        for row in value['files'].values():
            self.assertEqual(row['status'], 'captured')
            self.assertEqual(row['offset'], len(raw) - 4096)
            self.assertEqual(row['observedBytes'], len(raw))
            self.assertEqual(base64.b64decode(row['data']), raw[-4096:])
            self.assertEqual(row['encoding'], 'base64')
            self.assertTrue(row['truncated'])
            self.assertTrue(row['complete'])

    def test_one_pread_per_fixed_file_and_no_following_appends(self):
        for name in NAMES: (self.root / name).write_bytes(b'short original\n')
        actual = os.pread
        def appending_read(fd, count, offset):
            for name in NAMES:
                with (self.root / name).open('ab') as stream: stream.write(b'later append\n')
            return actual(fd, count, offset)
        self.assertTrue(callable(getattr(guest, 'startup_diagnostics', None)))
        with mock.patch.object(guest.os, 'pread', side_effect=appending_read) as read:
            value = guest.startup_diagnostics()
        self.assertEqual(read.call_count, 3)
        for call in read.call_args_list: self.assertLessEqual(call.args[1], 4096)
        first = value['files'][NAMES[0]]
        self.assertEqual(base64.b64decode(first['data']), b'short original\n')

    def test_fifo_symlink_and_hardlink_rejected_without_blocking(self):
        self.assertTrue(callable(getattr(guest, 'startup_diagnostics', None)))
        target = self.root / 'private-target'; target.write_bytes(b'must not escape')
        (self.root / NAMES[0]).symlink_to(target)
        os.mkfifo(self.root / NAMES[1])
        os.link(target, self.root / NAMES[2])
        code = ('import sys,json; from pathlib import Path; '
                'sys.path.insert(0,sys.argv[1]); import guest; guest.LOG=Path(sys.argv[2]); '
                'print(json.dumps(guest.startup_diagnostics()))')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code,
            str(Path(guest.__file__).parent), str(self.root)], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        for row in value['files'].values():
            self.assertEqual(row['status'], 'unavailable')
            self.assertNotIn('data', row)

    def test_symlinked_log_directory_is_not_followed(self):
        (self.root / NAMES[0]).write_bytes(b'private content')
        alias = self.root / 'alias'; alias.symlink_to(self.root, target_is_directory=True)
        with mock.patch.object(guest, 'LOG', alias): value = self.diagnostics()
        self.assertEqual(value['files'], {})
        self.assertIn('directoryError', value)

    def test_guest_error_contains_logs_in_the_same_only_fatal_event(self):
        (self.root / NAMES[0]).write_bytes(b'Weston: dlopen missing library\n')
        self.assertTrue(callable(getattr(guest, 'emit_guest_error', None)))
        with mock.patch.object(guest, 'emit') as emitted:
            guest.emit_guest_error('original error', 'original traceback')
        emitted.assert_called_once()
        self.assertEqual(emitted.call_args.args, ('guest-error',))
        fields = emitted.call_args.kwargs
        self.assertEqual(fields['error'], 'original error')
        self.assertEqual(fields['traceback'], 'original traceback')
        self.assertEqual(base64.b64decode(fields['startupDiagnostics']['files'][NAMES[0]]['data']),
                         b'Weston: dlopen missing library\n')

    def test_diagnostic_collection_exception_cannot_replace_original_failure(self):
        self.assertTrue(callable(getattr(guest, 'emit_guest_error', None)))
        with mock.patch.object(guest, 'startup_diagnostics', side_effect=OSError(5, 'unbounded secret ' * 1000)), \
             mock.patch.object(guest, 'emit') as emitted:
            guest.emit_guest_error('original error', 'original traceback')
        fields = emitted.call_args.kwargs
        self.assertEqual(fields['error'], 'original error')
        self.assertEqual(fields['traceback'], 'original traceback')
        self.assertLess(len(json.dumps(fields['startupDiagnostics'])), 256)
        self.assertNotIn('secret', json.dumps(fields))

    def test_main_exception_path_attaches_diagnostics_before_final_log_flush(self):
        (self.root / NAMES[0]).write_bytes(b'Weston original startup error\n')
        events = []
        def record(kind, **fields): events.append(dict(type=kind, **fields))
        # The first host-service call fails before launching anything. The only
        # subsequent subprocess is a mocked final poweroff; no service executes.
        with mock.patch.object(guest.subprocess, 'run', side_effect=[RuntimeError('startup failed'), SimpleNamespace(returncode=0)]) as run, \
             mock.patch.object(guest, 'emit', side_effect=record):
            guest.main()
        self.assertEqual(run.call_count, 2)
        self.assertEqual(events[0]['type'], 'guest-error')
        self.assertEqual(base64.b64decode(events[0]['startupDiagnostics']['files'][NAMES[0]]['data']),
                         b'Weston original startup error\n')
        self.assertIn("RuntimeError('startup failed')", events[0]['error'])
        self.assertIn('RuntimeError: startup failed', events[0]['traceback'])


if __name__ == '__main__':
    unittest.main()
