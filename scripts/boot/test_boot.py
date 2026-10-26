"""Boundary tests run without Docker, kernel, or a running guest."""
import base64
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from bootlib import checked_path, checked_session, qemu_command, sha256, validate_bundle, validate_kernel_config

class BootBoundaryTests(unittest.TestCase):
    def test_pending_review_does_not_create_rootfs(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            payload = base / "payload"; payload.mkdir()
            review = base / "review.json"; review.write_text('{"reviewed":false}')
            output = base / "bundle"
            proc = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("bundle.py")),
                                   "--payload", str(payload), "--review", str(review), "--output", str(output)],
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("review is pending", proc.stderr)
            self.assertFalse(output.exists())

    def test_guest_log_decoder_preserves_bytes_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            original = b"guest-log\xff\x00\n"
            entry = {"type": "log", "name": "desktop.log", "offset": 0, "encoding": "base64",
                     "data": base64.b64encode(original).decode()}
            source = base / "guest.jsonl"
            source.write_bytes((json.dumps(entry) + "\n").encode() + b'{"type":')
            proc = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("extract-evidence.py")),
                                   "--input", str(source), "--output", str(base / "decoded")],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((base / "decoded/desktop.log").read_bytes(), original)
            self.assertEqual(json.loads(proc.stdout)["incompleteTrailingLines"], 1)

    def test_guest_log_decoder_rejects_missing_chunk(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            source = base / "guest.jsonl"
            source.write_text(json.dumps({"type": "log", "name": "desktop.log", "offset": 8,
                                          "encoding": "base64", "data": "YQ=="}) + "\n")
            proc = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("extract-evidence.py")),
                                   "--input", str(source), "--output", str(base / "decoded")],
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("out of sequence", proc.stderr)

    def test_payload_path_cannot_escape_package(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            for value in ("../secret", "/etc/passwd", "C:/secret", "x\\secret"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    checked_path(base, value)

    def test_pending_review_does_not_authorize_bundle_or_boot(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(ValueError, "review"):
                checked_session({"reviewed": False}, Path(work))

    def test_review_is_bound_to_exact_host_and_catalog(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            (base / "ming-app-host").write_bytes(b"linux-release-host")
            (base / "catalog.json").write_text("{}")
            review = {"reviewed": True, "reviewer": "main-thread", "sessionMode": "bounded",
                      "hostSha256": sha256(base / "ming-app-host"),
                      "catalogSha256": sha256(base / "catalog.json"),
                      "extraArgs": ["--duration-ms", "60000"]}
            self.assertEqual(checked_session(review, base), review)
            (base / "ming-app-host").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "host"):
                checked_session(review, base)

    def test_bounded_session_keeps_current_limit_and_native_renderer(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            (base / "ming-app-host").write_bytes(b"host")
            (base / "catalog.json").write_text("{}")
            review = {"reviewed": True, "reviewer": "main-thread", "sessionMode": "bounded",
                      "hostSha256": sha256(base / "ming-app-host"),
                      "catalogSha256": sha256(base / "catalog.json")}
            for extra in (["--duration-ms", "60001"], ["--backend", "offscreen"], [],
                          ["--duration-ms", "60000", "--duration-ms", "60001"]):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    checked_session(dict(review, extraArgs=extra), base)

    def test_qemu_only_uses_task_files_no_host_devices_or_network(self):
        command = qemu_command(Path("/bundle"), Path("/out"), paused=True)
        self.assertIn("-S", command)
        self.assertIn("tcg,thread=multi", command)
        self.assertEqual(command[command.index("-nic") + 1], "none")
        self.assertIn("virtio-vga,xres=1280,yres=800", command)
        self.assertIn("rng-random,id=mingrng,filename=/dev/urandom", command)
        self.assertIn("virtio-rng-pci,rng=mingrng", command)
        self.assertNotIn("-kernel", command)
        self.assertNotIn("-initrd", command)
        self.assertNotIn("-drive", command)

    def test_builtin_virtio_console_is_valid_and_missing_gpu_is_rejected(self):
        settings = "\n".join(("CONFIG_DEVTMPFS=y", "CONFIG_RD_GZIP=y",
                              "CONFIG_SERIAL_8250_CONSOLE=y", "CONFIG_DRM_VIRTIO_GPU=m",
                              "CONFIG_VIRTIO_CONSOLE=y", "CONFIG_VIRTIO_INPUT=m", "CONFIG_VIRTIO_PCI=y",
                              "CONFIG_HW_RANDOM_VIRTIO=m", "CONFIG_INPUT_EVDEV=m", "CONFIG_EXT4_FS=m", "CONFIG_VIRTIO_BLK=m",
                              "CONFIG_CGROUPS=y", "CONFIG_MEMCG=y", "CONFIG_CGROUP_SCHED=y", "CONFIG_CFS_BANDWIDTH=y", "CONFIG_CGROUP_PIDS=y", "CONFIG_SECCOMP_FILTER=y", "CONFIG_NAMESPACES=y"))
        validate_kernel_config(settings)
        with self.assertRaisesRegex(ValueError, "DRM_VIRTIO_GPU"):
            validate_kernel_config(settings.replace("CONFIG_DRM_VIRTIO_GPU=m", "# disabled"))
        with self.assertRaisesRegex(ValueError, "HW_RANDOM_VIRTIO"):
            validate_kernel_config(settings.replace("CONFIG_HW_RANDOM_VIRTIO=m", "# disabled"))
        with self.assertRaisesRegex(ValueError, "INPUT_EVDEV"):
            validate_kernel_config(settings.replace("CONFIG_INPUT_EVDEV=m", "# disabled"))

    def test_changed_bundle_is_rejected_before_qemu(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            for name in ("vmlinuz", "initramfs.cpio.gz"):
                (base / name).write_bytes(name.encode())
            manifest = {"sha256": {name: sha256(base / name) for name in
                                    ("vmlinuz", "initramfs.cpio.gz")}, "reviewSha256": "a" * 64}
            (base / "bundle.json").write_text(json.dumps(manifest))
            validate_bundle(base, "a" * 64)
            (base / "vmlinuz").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "vmlinuz"):
                validate_bundle(base, "a" * 64)

if __name__ == "__main__":
    unittest.main()
