import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import infra
import bundle


class FaultInjectorTests(unittest.TestCase):
    def config(self):
        return {
            "schemaVersion": 1,
            "profile": "strict-worker-sigkill-v1",
            "app": 2,
            "generation": 1,
            "signal": "SIGKILL",
            "maxWaitSeconds": 60,
        }

    def state(self):
        return {
            "schemaVersion": 1,
            "bootId": "boot",
            "sessionId": "session",
            "revision": 7,
            "phase": "ready",
            "root": {"width": 1280, "height": 800, "presentedSequence": 4},
            "apps": [
                {"app": 1, "generation": 1, "workerPid": 70, "state": "running", "sourceSequence": 3, "presentedSourceSequence": 3, "inputReady": True},
                {"app": 2, "generation": 1, "workerPid": 71, "state": "running", "sourceSequence": 3, "presentedSourceSequence": 3, "inputReady": True},
            ],
            "error": None,
        }

    def test_fault_configuration_is_explicit_and_bounded(self):
        validator = getattr(infra, "validate_fault_injection", None)
        self.assertTrue(callable(validator), "fault-injection config validator missing")
        expected = self.config()
        self.assertEqual(validator(expected), expected)
        for key, value in (
            ("profile", "other"),
            ("app", 1),
            ("generation", 2),
            ("signal", "SIGTERM"),
            ("maxWaitSeconds", 0),
            ("maxWaitSeconds", 61),
        ):
            with self.subTest(key=key, value=value):
                changed = self.config()
                changed[key] = value
                with self.assertRaises(ValueError):
                    validator(changed)

    def test_fault_receipt_binds_target_to_reviewed_configuration(self):
        validator = getattr(infra, "validate_fault_receipt", None)
        self.assertTrue(callable(validator), "fault receipt validator missing")
        receipt = dict(self.config(), pid=71, cgroup="/mingos/app-2-g-1", revision=7,
                       status="sent", monotonicNs=123456)
        self.assertEqual(validator(receipt, self.config()), receipt)
        for changed in (
            dict(receipt, app=1),
            dict(receipt, pid=1),
            dict(receipt, cgroup="/mingos/app-2-g-2"),
            dict(receipt, revision=0),
            dict(receipt, status="failed"),
            dict(receipt, monotonicNs=0),
            dict(receipt, extra=True),
        ):
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    validator(changed, self.config())

    def test_target_requires_matching_worker_command_and_strict_cgroup(self):
        module = importlib.util.find_spec("fault_injector")
        self.assertIsNotNone(module, "fault injector module missing")
        injector = importlib.import_module("fault_injector")
        resolver = getattr(injector, "resolve_target", None)
        self.assertTrue(callable(resolver), "fault injector target resolver missing")
        with tempfile.TemporaryDirectory() as work:
            proc = Path(work) / "proc" / "71"
            proc.mkdir(parents=True)
            (proc / "cgroup").write_text("0::/mingos/app-2-g-1\n")
            (proc / "cmdline").write_bytes(b"/opt/mingos/ming-app-host\0__worker\0")
            target = resolver(self.config(), self.state(), Path(work) / "proc")
            self.assertEqual(target, {"pid": 71, "cgroup": "/mingos/app-2-g-1", "revision": 7})
            for filename, contents in (
                ("cgroup", "0::/mingos/app-2-g-2\n"),
                ("cmdline", b"/opt/mingos/ming-app-host\0desktop\0"),
            ):
                with self.subTest(filename=filename):
                    path = proc / filename
                    if isinstance(contents, bytes):
                        path.write_bytes(contents)
                    else:
                        path.write_text(contents)
                    self.assertIsNone(resolver(self.config(), self.state(), Path(work) / "proc"))
                    (proc / "cgroup").write_text("0::/mingos/app-2-g-1\n")
                    (proc / "cmdline").write_bytes(b"/opt/mingos/ming-app-host\0__worker\0")
            changed = copy.deepcopy(self.state())
            changed["apps"][1]["state"] = "failed"
            self.assertIsNone(resolver(self.config(), changed, Path(work) / "proc"))

    def test_completed_injector_requires_zero_exit_and_valid_receipt(self):
        injector = importlib.import_module("fault_injector")
        checker = getattr(injector, "completed_receipt", None)
        self.assertTrue(callable(checker), "fault injector completion checker missing")
        class Process:
            def __init__(self, code): self.code = code
            def poll(self): return self.code
        receipt = dict(self.config(), pid=71, cgroup="/mingos/app-2-g-1", revision=7,
                       status="sent", monotonicNs=123456)
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "receipt.json"
            self.assertIsNone(checker(Process(None), self.config(), path))
            path.write_text(json.dumps(receipt))
            self.assertEqual(checker(Process(0), self.config(), path), receipt)
            with self.assertRaisesRegex(RuntimeError, "exit"):
                checker(Process(1), self.config(), path)
            path.write_text(json.dumps(dict(receipt, app=1)))
            with self.assertRaises(ValueError):
                checker(Process(0), self.config(), path)

    def test_bundle_only_enables_reviewed_fault_configuration(self):
        selector = getattr(bundle, "fault_config_from_review", None)
        self.assertTrue(callable(selector), "bundle fault configuration gate missing")
        self.assertIsNone(selector({"reviewed": True}))
        self.assertEqual(selector({"reviewed": True, "faultInjection": self.config()}), self.config())
        with self.assertRaises(ValueError):
            selector({"reviewed": True, "faultInjection": dict(self.config(), signal="SIGTERM")})

    def test_bundle_installs_injector_only_for_reviewed_fault_configuration(self):
        installer = getattr(bundle, "install_fault_injection", None)
        self.assertTrue(callable(installer), "fault injector installer missing")
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / "rootfs"
            scripts = Path(bundle.__file__).parent
            self.assertIsNone(installer(root, scripts, None))
            self.assertFalse((root / "etc/mingos/fault-injection.json").exists())
            self.assertFalse((root / "etc/mingos/fault_injector.py").exists())
            self.assertEqual(installer(root, scripts, self.config()), self.config())
            self.assertEqual(json.loads((root / "etc/mingos/fault-injection.json").read_text()), self.config())
            self.assertEqual((root / "etc/mingos/fault_injector.py").read_bytes(), (scripts / "fault_injector.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
