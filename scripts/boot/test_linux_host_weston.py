"""Real Weston config parser controls without a VM, DRM devices or data disk."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'scripts/boot/weston.ini'


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('weston'), 'requires installed Linux Weston')
class WestonConfigControls(unittest.TestCase):
    def run_config(self, raw):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            config, log = root / 'weston.ini', root / 'weston.log'
            config.write_bytes(raw)
            # Only a headless userspace compositor: true exits immediately and
            # causes Weston to stop. Never execute init, mount or a guest.
            command = ['weston', '--backend=headless', '--renderer=pixman', '--shell=kiosk-shell.so',
                       '--config=' + str(config), '--socket=weston-config-control',
                       '--log=' + str(log), '--idle-time=0', '--', '/usr/bin/true']
            result = subprocess.run(command, env=dict(os.environ, XDG_RUNTIME_DIR=str(root)),
                                    capture_output=True, text=True, timeout=10)
            return result, log.read_text() if log.exists() else ''

    def test_actual_config_starts_bounded_headless_weston(self):
        result, log = self.run_config(CONFIG.read_bytes())
        self.assertEqual(result.returncode, 0, result.stderr + log)
        self.assertIn('Using Pixman renderer', log)
        self.assertIn('kiosk-shell.so', log)

    def test_crlf_parser_negative_and_lf_positive_controls(self):
        lf = CONFIG.read_bytes().replace(b'\r\n', b'\n')
        result, log = self.run_config(lf.replace(b'\n', b'\r\n'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('malformed section header', result.stderr)
        self.assertIn('error opening or reading config file', log)
        result, log = self.run_config(lf)
        self.assertEqual(result.returncode, 0, result.stderr + log)

    def test_actual_config_bytes_and_checkout_rule_are_lf(self):
        raw = CONFIG.read_bytes()
        self.assertTrue(raw.startswith(b'[core]\n'))
        self.assertNotIn(b'\r', raw)
        self.assertIn(b'scripts/boot/weston.ini text eol=lf', (ROOT / '.gitattributes').read_bytes().splitlines())


if __name__ == '__main__':
    unittest.main()
