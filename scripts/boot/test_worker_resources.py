"""Resource receipts use bounded kernel reads and keep process identity explicit."""
import tempfile
import unittest
from pathlib import Path

import worker_resources
from worker_resources import process_table, sample_worker


class WorkerResourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.proc = self.root / "proc" / "213"
        self.proc.mkdir(parents=True)
        self.cgroups = self.root / "cgroup" / "mingos"
        self.group = self.cgroups / "app-1-g-2"
        self.group.mkdir(parents=True)
        self.write_stat()
        (self.proc / "cgroup").write_text("0::/mingos/app-1-g-2\n")
        (self.group / "cpu.stat").write_text(
            "usage_usec 120000\nuser_usec 100000\nsystem_usec 20000\n"
            "nr_periods 20\nnr_throttled 10\nthrottled_usec 900000\n")
        (self.group / "cpu.max").write_text("25000 100000\n")

    def write_stat(self, *, pid=213, started=1234, comm="ming host (worker)"):
        fields = ["S"] + ["0"] * 21
        fields[11], fields[12], fields[19] = "17", "3", str(started)
        (self.proc / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")

    def sample(self):
        return sample_worker(self.proc, self.cgroups, clock_ticks=100)

    def test_cpu_receipt_has_units_identity_and_bounded_sampling_window(self):
        receipt = self.sample()
        self.assertEqual(receipt["status"], "sampled")
        self.assertEqual(receipt["process"], {
            "pid": 213, "startTicks": 1234, "userTicks": 17,
            "systemTicks": 3, "clockTicksPerSecond": 100})
        self.assertEqual(receipt["cgroup"]["path"], "/mingos/app-1-g-2")
        self.assertEqual(receipt["cgroup"]["cpuMax"], {"quotaUsec": 25000, "periodUsec": 100000})
        self.assertEqual(receipt["cgroup"]["cpuStat"]["nr_throttled"], 10)
        self.assertEqual(receipt["cgroup"]["cpuStat"]["throttled_usec"], 900000)
        self.assertLessEqual(receipt["sampleStartMonotonicNs"], receipt["sampleEndMonotonicNs"])

    def test_parentheses_in_comm_do_not_shift_stat_columns(self):
        self.write_stat(comm="worker ) (with spaces)")
        self.assertEqual(self.sample()["process"]["startTicks"], 1234)

    def test_startup_and_unlimited_cpu_quota_are_read_without_changing_them(self):
        for text, quota in (("100000 100000\n", 100000), ("max 100000\n", None)):
            with self.subTest(quota=quota):
                (self.group / "cpu.max").write_text(text)
                self.assertEqual(self.sample()["cgroup"]["cpuMax"]["quotaUsec"], quota)
                self.assertEqual((self.group / "cpu.max").read_text(), text)

    def test_process_outside_owned_cgroup_is_explicit_without_reading_it(self):
        for path in ("/", "/other/app-1-g-2", "/mingos/../secret", "/mingos/app-0-g-2",
                     "/mingos/app-65536-g-2", "/mingos/app-1-g-0", "/mingos/app-1-g-18446744073709551616"):
            with self.subTest(path=path):
                (self.proc / "cgroup").write_text("0::" + path + "\n")
                receipt = self.sample()
                self.assertEqual(receipt["status"], "outside-owned-cgroup")
                self.assertNotIn("cgroup", receipt)
                self.assertEqual(receipt["process"]["pid"], 213)

    def test_exited_process_or_removed_cgroup_never_becomes_zero_cpu(self):
        for target in (self.proc / "stat", self.group / "cpu.stat"):
            with self.subTest(target=target):
                content = target.read_bytes()
                target.unlink()
                receipt = self.sample()
                self.assertEqual(receipt["status"], "unavailable")
                self.assertEqual(receipt["reason"], "FileNotFoundError")
                self.assertNotIn("process", receipt)
                self.assertNotIn("cgroup", receipt)
                target.write_bytes(content)

    def test_oversized_or_invalid_kernel_data_are_explicit(self):
        cases = [(self.proc / "stat", "x" * 4097),
                 (self.proc / "stat", "213 (truncated) S 0"),
                 (self.proc / "cgroup", "0::/mingos/app-1-g-2\n" * 300),
                 (self.proc / "cgroup", "0::/mingos/app-1-g-2\n0::/mingos/app-2-g-1\n"),
                 (self.group / "cpu.stat", "usage_usec -1\n"),
                 (self.group / "cpu.stat", "usage_usec 1\nusage_usec 2\n"),
                 (self.group / "cpu.stat", "nr_throttled 1\n"),
                 (self.group / "cpu.max", "25000 0\n"),
                 (self.group / "cpu.max", "-1 100000\n")]
        for target, text in cases:
            with self.subTest(target=target, text=text[:40]):
                original = target.read_bytes()
                target.write_text(text)
                self.assertEqual(self.sample()["status"], "invalid-data")
                target.write_bytes(original)

    def test_pid_mismatch_is_not_attributed_to_the_expected_worker(self):
        self.write_stat(pid=999)
        self.assertEqual(self.sample()["status"], "identity-changed")

    def test_pid_reuse_during_sampling_is_not_a_valid_receipt(self):
        from unittest.mock import patch
        original = worker_resources._read_text
        reads = 0

        def read(path):
            nonlocal reads
            if path == self.proc / "stat":
                reads += 1
                if reads == 2:
                    self.write_stat(started=5678)
            return original(path)

        # Change the real fixture between reads, emulating a PID reused by the kernel.
        with patch.object(worker_resources, "_read_text", read):
            self.assertEqual(self.sample()["status"], "identity-changed")

    def test_process_table_retains_worker_when_resource_sampling_fails(self):
        (self.proc / "cmdline").write_bytes(b"/opt/mingos/ming-app-host\0__worker\0")
        (self.proc / "status").write_text("Name:\tming-app-host\nUid:\t20001\n")
        (self.group / "cpu.stat").unlink()
        records = process_table(self.proc.parent, self.cgroups, resources=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["pid"], 213)
        self.assertIn("__worker", records[0]["argv"])
        self.assertEqual(records[0]["resources"]["status"], "unavailable")

    def test_process_table_default_keeps_original_fields_and_diagnostic_mode_adds_receipt(self):
        (self.proc / "cmdline").write_bytes(b"/opt/mingos/ming-app-host\0__worker\0")
        (self.proc / "status").write_text("Name:\tming-app-host\nUid:\t20001\n")
        other = self.proc.parent / "214"
        other.mkdir()
        (other / "cmdline").write_bytes(b"unrelated\0")
        expected = [{"pid": 213, "argv": "/opt/mingos/ming-app-host __worker ",
                     "status": "Name:\tming-app-host\nUid:\t20001\n"}]
        self.assertEqual(process_table(self.proc.parent, self.cgroups), expected)
        diagnostic = process_table(self.proc.parent, self.cgroups, resources=True)
        self.assertEqual(len(diagnostic), 1)
        self.assertEqual({key: diagnostic[0][key] for key in expected[0]}, expected[0])
        self.assertEqual(diagnostic[0]["resources"]["status"], "sampled")

    def test_cgroup_move_during_sampling_is_not_a_valid_receipt(self):
        from unittest.mock import patch
        original = worker_resources._read_text
        reads = 0

        def read(path):
            nonlocal reads
            if path == self.proc / "cgroup":
                reads += 1
                if reads == 2:
                    path.write_text("0::/mingos/app-1-g-3\n")
            return original(path)

        with patch.object(worker_resources, "_read_text", read):
            self.assertEqual(self.sample()["status"], "identity-changed")


if __name__ == "__main__":
    unittest.main()
