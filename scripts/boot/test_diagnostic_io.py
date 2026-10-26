"""Diagnostic stream caps are explicit; they never silently imply complete logs."""
import importlib.util
import io
import unittest
from pathlib import Path

class DiagnosticIoTests(unittest.TestCase):
    def module(self):
        path=Path(__file__).with_name('bounded_log.py')
        self.assertTrue(path.exists(),'bounded diagnostic stderr capture missing')
        spec=importlib.util.spec_from_file_location('bounded_log_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    def test_stderr_below_limit_is_preserved_byte_for_byte(self):
        module=self.module();output=io.BytesIO();log=module.BoundedLog(output,5)
        log.append(b'ab');log.append(b'c\xffd')
        self.assertEqual(output.getvalue(),b'abc\xffd');self.assertEqual(log.captured,5)
    def test_overflow_stops_at_explicit_limit_and_reports_missing_bytes(self):
        module=self.module();output=io.BytesIO();log=module.BoundedLog(output,5)
        log.append(b'ab')
        with self.assertRaises(module.LogLimitExceeded) as error:log.append(b'cdef')
        self.assertEqual(output.getvalue(),b'abcde')
        self.assertEqual((error.exception.limit,error.exception.captured,error.exception.observed),(5,5,6))
if __name__=='__main__':unittest.main()
