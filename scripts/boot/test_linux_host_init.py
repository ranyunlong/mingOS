"""Safe Linux PID1 script checks: never execute the real bootstrap."""
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
INIT = ROOT / "scripts/boot/init"


@unittest.skipUnless(sys.platform == "linux", "requires Linux kernel shebang execution")
class InitTests(unittest.TestCase):
    def test_init_has_lf_shebang_and_lines(self):
        data = INIT.read_bytes()
        self.assertEqual(data.splitlines(keepends=True)[0], b"#!/bin/sh\n")
        self.assertNotIn(b"\r", data)

    def test_actual_shebang_executes_harmless_script(self):
        # Copy only the interpreter line, never mount or power off on the host.
        prefix = INIT.read_bytes().splitlines(keepends=True)[0]
        with tempfile.TemporaryDirectory() as directory:
            script = pathlib.Path(directory) / "harmless-init"
            script.write_bytes(prefix + b"printf 'INIT_SHEBANG_OK\\n'\n")
            script.chmod(0o700)
            try:
                result = subprocess.run([str(script)], capture_output=True, timeout=5)
            except FileNotFoundError as error:
                self.fail(f"Linux rejected the actual init interpreter line: {error}")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"INIT_SHEBANG_OK\n")

    def test_crlf_negative_control(self):
        with tempfile.TemporaryDirectory() as directory:
            script = pathlib.Path(directory) / "crlf-control"
            script.write_bytes(b"#!/bin/sh\r\nprintf 'UNREACHABLE\\n'\n")
            script.chmod(0o700)
            with self.assertRaises(FileNotFoundError):
                subprocess.run([str(script)], capture_output=True, timeout=5)

    def test_shell_parse_only(self):
        result = subprocess.run(["/bin/sh", "-n", str(INIT)], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_extensionless_init_is_pinned_to_lf(self):
        self.assertIn(b"scripts/boot/init text eol=lf", (ROOT / ".gitattributes").read_bytes().splitlines())


if __name__ == "__main__":
    unittest.main()
