"""Fixture tests for Linux acceptance; no Linux, Rust, Node or GPU required."""
import copy
import importlib.util
import hashlib
import json
import io
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

CONTAINER_ID = "a" * 64
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "linux-verify.py"
spec = importlib.util.spec_from_file_location("linux_verify", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def png(width=2, height=2):
    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload)))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff" * width * 4) * height))
            + chunk(b"IEND", b""))


def outcome(app="image-viewer", window="headless", renderer="cpu", *, frames=1, snapshot=True):
    text = b"Hello fixture!\n"
    data = {
        "appId": "dev.ming." + app, "runtime": "rust-wasmtime",
        "windowSystem": window,
        "presentationBackend": ("wgpu-surface" if renderer == "gpu" else "softbuffer")
                               if window == "wayland" else "none",
        "exitCode": 0, "requests": 45,
        "durationMs": 2, "fuelConsumed": 123, "entropyBytes": 0,
        "inputCounts": [1, 0, 0, 0, 0, 0, 0, 0, 0],
        "framesSubmitted": frames, "framesPresented": frames if window == "wayland" else 0, "bulkBytesRead": 32,
        "events": [{"code": 21, "value": 32}, {"code": 40, "value": frames}],
        "frame": {"width": 2, "height": 2, "commands": 15, "sequence": frames,
                  "renderMicros": 10,
                  "backend": "cpu-reference" if renderer == "cpu" else
                             "wgpu-compute-surface" if window == "wayland" else "wgpu-compute-readback"},
    }
    if app == "read-file":
        data.update(framesSubmitted=0, framesPresented=0, bulkBytesRead=0, inputCounts=[0] * 9)
        del data["frame"]
        data["events"] = ([{"code": 1, "value": len(text)}]
                          + [{"code": 2, "value": b} for b in text]
                          + [{"code": 3, "value": -1}, {"code": 4, "value": 0}])
    if app in ("text-demo", "ui-demo", "shadow-demo", "compose-demo", "animated-demo"):
        # Main.kt logs only completion on success, opens no file/image, and
        # reports layout/metrics/submit/close/event-budget errors as codes 50..54.
        data.update(bulkBytesRead=0, events=[{"code": 40, "value": frames}])
        data["frame"].update(width=1100, height=760, commands=41, textPrepareMicros=0)
    if renderer == "gpu":
        submitted = data["framesSubmitted"]
        data["gpuStats"] = {
            "imageUploads": 1 if submitted else 0, "imageCacheCompactions": 0,
            "imageUploadBytes": 16 if submitted else 0,
            "sceneUploadBytes": 160 * submitted,
            "readbackBytes": 16 * (int(snapshot) if window == "wayland" else submitted),
            "renderSubmissions": submitted, "surfacePresentations": data["framesPresented"],
            "surfaceReconfigurations": 1 if window == "wayland" else 0,
            "cachedImages": 0,
        }
        if app in ("text-demo", "ui-demo", "shadow-demo", "compose-demo", "animated-demo"):
            data["gpuStats"].update(
                imageUploads=0, imageUploadBytes=0,
                readbackBytes=1100 * 760 * 4 * (int(snapshot) if window == "wayland" else submitted),
                glyphUploads=100, glyphUploadBytes=6400, glyphCacheEvictions=0,
                glyphCacheCompactions=0, glyphResidentBytes=6400, glyphEntries=100)
        if app in ("shadow-demo", "compose-demo", "animated-demo"):
            data["gpuStats"].update(
                shadowCacheHits=2 * (submitted - 1), shadowCacheMisses=2,
                shadowCacheEvictions=0, shadowGenerationWork=3_069_894,
                shadowParameterUploadBytes=64, shadowResidentBytes=215_560, shadowEntries=2)
        if app in ("compose-demo", "animated-demo"):
            data["gpuStats"].update(shadowCacheMisses=1, shadowEntries=1,
                shadowParameterUploadBytes=32, shadowGenerationWork=2_757_827,
                shadowResidentBytes=148_996)
    if app == "animated-demo":
        data["frameClock"] = {"source": "host-monotonic-opportunity", "reads": 4,
                              "waits": 34, "frames": 30, "timers": 0, "inputs": 0,
                              "closes": 1 if window == "headless" else 0, "cancels": 0, "pending": 0}
    data["presentedFrame"] = (
        {name: data["frame"][name] for name in ("sequence", "width", "height")}
        if window == "wayland" and "frame" in data else None)
    return data


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ming linux fixture ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source with spaces"
        self.root.mkdir()
        for app, sdk in (("read-file", 1), ("image-viewer", 2), ("text-demo", 3), ("ui-demo", 4), ("shadow-demo", 4), ("compose-demo", 4), ("animated-demo", 5)):
            directory = self.root / "artifacts" / app
            directory.mkdir(parents=True)
            (directory / "app.json").write_text(json.dumps({
                "schemaVersion": 1, "id": "dev.ming." + app,
                "version": "0.1.0", "sdkVersion": sdk, "entry": "main.wasm",
            }), encoding="utf-8")
            (directory / "main.wasm").write_bytes(b"\0asm\x01\0\0\0fixture")
        for name, data in {
            "samples/read-file/data/hello.txt": b"Hello fixture!\n",
            "samples/image-viewer/data/coastal.png": png(),
            "assets/fonts/NotoSansSC-Regular.otf": b"OTTO minimal font fixture",
            "assets/fonts/OFL.txt": b"fixture OFL-1.1\n",
            "assets/fonts/NOTICE.txt": b"fixture font notice\n",
            "assets/fonts/font-pack.json": b'{"schema_version":1,"font_pack_revision":1}\n',
            "rust-toolchain.toml": b'[toolchain]\nchannel = "1.99.1"\n',
            "Cargo.toml": b'[workspace]\nmembers = ["crates/ming-app-host"]\n',
            "Cargo.lock": b"version = 4\n",
            "crates/ming-app-host/Cargo.toml": b'[package]\nname = "ming-app-host"\n',
            "crates/ming-app-host/src/main.rs": b"fn main() {}\n",
            "scripts/verify-font-pack.py": b"# fixture font-pack verifier\n",
            "tests/test_font_pack.py": b"# fixture font-pack tests\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.output = Path(self.temp.name) / "evidence with spaces"
        self.env = {"PATH": os.environ.get("PATH", ""), "DISPLAY": ":123",
                    "WAYLAND_SOCKET": "456", "CARGO_BUILD_TARGET": "wasm32-wasip1"}
        self.calls = []

    def fake_runner(self, argv, *, cwd, env, timeout, output, name):
        self.calls.append((name, argv, env.copy(), timeout))
        text = "fixture success\n"
        if name == "compiler":
            compiler = self.root / "toolchain/rustc"
            compiler.parent.mkdir(exist_ok=True)
            compiler.write_bytes(b"fixture compiler")
            text = str(compiler.resolve()) + "\n"
        elif name == "rustc":
            text = "rustc 1.99.1 (fixture)\nhost: x86_64-unknown-linux-gnu\nrelease: 1.99.1\n"
        elif name == "build":
            profile = "release" if "--release" in argv else "debug"
            binary = Path(env["CARGO_TARGET_DIR"]) / "x86_64-unknown-linux-gnu" / profile / "ming-app-host"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"fixture release binary" if profile == "release" else b"fixture native binary")
        elif name.startswith(("headless-", "wayland-")):
            app = {"sdk1": "read-file", "sdk2": "image-viewer", "sdk3": "text-demo",
                   "sdk4": "ui-demo", "shadow": "shadow-demo", "compose": "compose-demo", "animated": "animated-demo"}[name.split("-")[-1]]
            if Path(argv[2]).resolve() != (self.root / "artifacts" / app / "app.json").resolve():
                raise AssertionError("Wrong acceptance manifest")
            if app in ("text-demo", "ui-demo", "shadow-demo", "compose-demo", "animated-demo") and "--grant" in argv:
                raise AssertionError(app + " must not receive a file grant")
            renderer = argv[argv.index("--renderer") + 1]
            window = "wayland" if name.startswith("wayland-") else "headless"
            data = outcome(app, window, renderer, frames=25 if app == "animated-demo" else 1)
            text = json.dumps(data)
            if "--snapshot" in argv:
                Path(argv[argv.index("--snapshot") + 1]).write_bytes(
                    png(data["frame"]["width"], data["frame"]["height"]))
        record = self.command_record(output, name, argv, text)
        record["timeoutSeconds"] = timeout
        return record

    def command_record(self, output, name, argv, text, code=0, timed_out=False):
        output = Path(output)
        stdout = output / (name + ".stdout.log")
        stderr = output / (name + ".stderr.log")
        stdout.write_text(text, encoding="utf-8")
        stderr.write_text("fixture error" if code else "", encoding="utf-8")
        return {"name": name, "argv": [str(a) for a in argv], "cwd": str(self.root),
                "timeoutSeconds": 60, "durationSeconds": 0.01,
                "returnCode": code, "timedOut": timed_out, "error": None,
                "stdout": verify.file_record(stdout, output),
                "stderr": verify.file_record(stderr, output)}

    def run_fixture(self, **kwargs):
        with mock.patch.object(verify.platform, "system", return_value="Linux"):
            return verify.verify(self.root, self.output, environ=self.env,
                                 runner=self.fake_runner, **kwargs)

    def socket_session(self, env):
        return ({k: v for k, v in env.items() if k not in ("DISPLAY", "WAYLAND_SOCKET")},
                {"socket": "/run/user/1000/wayland-0", "socketVerified": True,
                 "connectionVerified": True, "forcedBackend": "wayland"})


class OutcomeTests(unittest.TestCase):
    def check(self, data, app="image-viewer", window="headless", renderer="cpu", **kwargs):
        verify.validate_outcome(data, app=app, renderer=renderer,
                                window_system=window, grant=b"Hello fixture!\n", **kwargs)

    def test_wayland_requires_actual_presentation_of_an_application_frame(self):
        for count in (None, 0, -1, True, "1"):
            with self.subTest(count=count), self.assertRaises(verify.VerificationError):
                data = outcome(window="wayland")
                if count is None:
                    del data["framesPresented"]
                else:
                    data["framesPresented"] = count
                self.check(data, window="wayland")

    def test_headless_requires_zero_presented_frames(self):
        for count in (None, 1, False):
            with self.subTest(count=count), self.assertRaises(verify.VerificationError):
                data = outcome()
                data["framesPresented"] = count
                self.check(data)
    def test_valid_sdk1_and_sdk2(self):
        self.check(outcome())
        self.check(outcome("read-file"), "read-file")
        self.check(outcome(window="wayland"), window="wayland")
        self.check(outcome(renderer="gpu"), renderer="gpu")

    def test_socket_environment_is_not_wayland_evidence(self):
        for actual in (None, "headless", "x11", "win32"):
            with self.subTest(actual=actual), self.assertRaises(verify.VerificationError):
                data = outcome(window=actual)
                self.check(data, window="wayland")

    def test_rejects_failed_guest_runtime_identity_and_frame(self):
        for field, value in (("exitCode", 3), ("exitCode", False), ("runtime", "node"),
                             ("appId", "dev.ming.other"), ("framesSubmitted", 0),
                             ("requests", 0), ("fuelConsumed", -1), ("inputCounts", []),
                             ("bulkBytesRead", 0), ("events", []), ("frame", None)):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                data = outcome()
                data[field] = value
                self.check(data)

    def test_rejects_missing_or_wrong_backend_and_invalid_frame_metrics(self):
        for field, value in (("backend", None), ("backend", "wgpu-compute-readback"),
                             ("width", 0), ("height", 9000), ("sequence", 2),
                             ("commands", 0), ("renderMicros", -1)):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                data = outcome()
                data["frame"][field] = value
                self.check(data)

    def test_gpu_does_not_accept_cpu_fallback(self):
        with self.assertRaises(verify.VerificationError):
            self.check(outcome(), renderer="gpu")

    def test_cpu_presentation_backend_and_optional_gpu_stats(self):
        for window, expected in (("headless", "none"), ("wayland", "softbuffer")):
            data = outcome(window=window)
            self.assertEqual(data["presentationBackend"], expected)
            self.assertNotIn("gpuStats", data)
            self.check(data, window=window)
            for value in (None, "wgpu-surface", "softbuffer" if window == "headless" else "none"):
                with self.subTest(window=window, value=value), self.assertRaises(verify.VerificationError):
                    self.check(dict(data, presentationBackend=value), window=window)
        data = outcome("read-file")
        del data["presentationBackend"]
        with self.assertRaises(verify.VerificationError):
            self.check(data, "read-file")

    def test_gpu_surface_snapshot_is_one_readback_for_multiple_submissions(self):
        data = outcome(window="wayland", renderer="gpu", frames=3)
        self.assertEqual(data["frame"]["backend"], "wgpu-compute-surface")
        self.assertEqual(data["presentationBackend"], "wgpu-surface")
        self.assertEqual(data["gpuStats"]["readbackBytes"], 16)
        self.check(data, window="wayland", renderer="gpu")

    def test_gpu_surface_without_snapshot_requires_zero_readback(self):
        data = outcome(window="wayland", renderer="gpu", frames=3, snapshot=False)
        self.check(data, window="wayland", renderer="gpu", snapshot=False)
        data["gpuStats"]["readbackBytes"] = 16
        with self.assertRaises(verify.VerificationError):
            self.check(data, window="wayland", renderer="gpu", snapshot=False)

    def test_gpu_headless_readback_covers_each_submitted_frame(self):
        data = outcome(renderer="gpu", frames=3)
        self.assertEqual(data["gpuStats"]["readbackBytes"], 48)
        self.check(data, renderer="gpu")
        self.check(data, renderer="gpu", snapshot=False)
        for value in (0, 16, 47, 49, 64):
            with self.subTest(value=value), self.assertRaises(verify.VerificationError):
                changed = copy.deepcopy(data)
                changed["gpuStats"]["readbackBytes"] = value
                self.check(changed, renderer="gpu")

    def test_gpu_rejects_backend_mismatch_and_stale_snapshot_counters(self):
        for window in ("headless", "wayland"):
            data = outcome(window=window, renderer="gpu", frames=3)
            for backend in (None, "cpu-reference", "wgpu-compute-readback" if window == "wayland"
                            else "wgpu-compute-surface"):
                with self.subTest(window=window, backend=backend), self.assertRaises(verify.VerificationError):
                    changed = copy.deepcopy(data)
                    changed["frame"]["backend"] = backend
                    self.check(changed, window=window, renderer="gpu")
            for backend in (None, "softbuffer", "none" if window == "wayland" else "wgpu-surface"):
                with self.subTest(window=window, presentation=backend), self.assertRaises(verify.VerificationError):
                    self.check(dict(data, presentationBackend=backend), window=window, renderer="gpu")
        for value in (0, 15, 17, 48):
            with self.subTest(readback=value), self.assertRaises(verify.VerificationError):
                data = outcome(window="wayland", renderer="gpu", frames=3)
                data["gpuStats"]["readbackBytes"] = value
                self.check(data, window="wayland", renderer="gpu")

    def test_gpu_stats_require_all_nonnegative_integer_fields(self):
        for app in ("read-file", "image-viewer"):
            data = outcome(app, renderer="gpu")
            self.check(data, app, renderer="gpu")
            for value in (None, [], "gpu"):
                with self.subTest(app=app, stats=value), self.assertRaises(verify.VerificationError):
                    self.check(dict(data, gpuStats=value), app, renderer="gpu")
            for field in data["gpuStats"]:
                for value in (None, -1, True, 1.5, "1"):
                    with self.subTest(app=app, field=field, value=value), self.assertRaises(verify.VerificationError):
                        changed = copy.deepcopy(data)
                        if value is None:
                            del changed["gpuStats"][field]
                        else:
                            changed["gpuStats"][field] = value
                        self.check(changed, app, renderer="gpu")

    def test_gpu_counters_match_submissions_and_presentation(self):
        for window in ("headless", "wayland"):
            for field, value in (("renderSubmissions", 2), ("surfacePresentations", 2),
                                 ("surfaceReconfigurations", 0 if window == "wayland" else 1)):
                with self.subTest(window=window, field=field), self.assertRaises(verify.VerificationError):
                    data = outcome(window=window, renderer="gpu")
                    data["gpuStats"][field] = value
                    self.check(data, window=window, renderer="gpu")
        data = outcome("read-file", renderer="gpu")
        data["gpuStats"]["readbackBytes"] = 16
        with self.assertRaises(verify.VerificationError):
            self.check(data, "read-file", renderer="gpu")

    def test_sdk1_requires_exact_bytes_denial_and_close(self):
        for index, value in ((0, 0), (1, 0), (-2, 99), (-1, -2)):
            with self.subTest(index=index), self.assertRaises(verify.VerificationError):
                data = outcome("read-file")
                data["events"][index]["value"] = value
                self.check(data, "read-file")

    def test_sdk2_rejects_logged_application_failure(self):
        for code in (5, 6, 9):
            with self.subTest(code=code), self.assertRaises(verify.VerificationError):
                data = outcome()
                data["events"].append({"code": code, "value": -2})
                self.check(data)


class InputTests(Fixture):
    def test_test_fixture_payloads_and_provenance_are_fingerprinted(self):
        relative = "crates/ming-app-host/tests/fixtures/shadow-mask"
        directory = self.root / relative
        directory.mkdir(parents=True)
        for name, data in (("mask.a8", b"\x00\x7f\xff"),
                           ("provenance.json", b'{"oracle":"full-convolution"}'),
                           ("oracle.py", b"# independent fixture producer\n")):
            (directory / name).write_bytes(data)
        before = verify.collect_inputs(self.root)
        for name in ("mask.a8", "provenance.json", "oracle.py"):
            key = relative + "/" + name
            self.assertIn(key, before)
            original = (directory / name).read_bytes()
            (directory / name).write_bytes(original + b"changed")
            self.assertNotEqual(before[key], verify.collect_inputs(self.root)[key])
            (directory / name).write_bytes(original)
        (directory / "mask.a8").unlink()
        self.assertNotEqual(before, verify.collect_inputs(self.root))


    def test_hashes_existing_artifacts_manifests_grants_and_build_inputs(self):
        inputs = verify.collect_inputs(self.root)
        for label in ("read-file.wasm", "image-viewer.wasm", "read-file.manifest",
                      "image-viewer.manifest", "read-file.grant", "image-viewer.grant",
                      "Cargo.lock", "rust-toolchain.toml"):
            self.assertEqual(len(inputs[label]["sha256"]), 64)
            self.assertFalse(Path(inputs[label]["path"]).is_absolute())
        (self.root / "artifacts/read-file/main.wasm").write_bytes(b"\0asm\x01\0\0\0changed")
        self.assertNotEqual(inputs["read-file.wasm"], verify.collect_inputs(self.root)["read-file.wasm"])

    def test_missing_or_non_wasm_artifact_fails(self):
        path = self.root / "artifacts/read-file/main.wasm"
        path.unlink()
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)
        path.write_bytes(b"not wasm")
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)

    def test_manifest_identity_sdk_and_traversal_are_checked(self):
        path = self.root / "artifacts/read-file/app.json"
        original = json.loads(path.read_text(encoding="utf-8"))
        for field, value in (("sdkVersion", 2), ("id", "wrong"), ("entry", "../../Cargo.lock")):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                data = dict(original, **{field: value})
                path.write_text(json.dumps(data), encoding="utf-8")
                verify.collect_inputs(self.root)

    def test_optional_packaged_build_hash_is_checked(self):
        path = self.root / "artifacts/image-viewer/build-info.json"
        path.write_text(json.dumps({"packagedSha256": "0" * 64}), encoding="utf-8")
        with self.assertRaisesRegex(verify.VerificationError, "hash|SHA|sha"):
            verify.collect_inputs(self.root)
        digest = verify.file_record(path.with_name("main.wasm"), self.root)["sha256"]
        path.write_text(json.dumps({"packagedSha256": digest}), encoding="utf-8")
        self.assertIn("image-viewer.build-info", verify.collect_inputs(self.root))


class WorkflowTests(Fixture):
    def test_native_build_test_and_all_headless_apps_no_node(self):
        report = self.run_fixture()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["paths"]["headless-sdk1"]["status"], "passed")
        self.assertEqual(report["paths"]["headless-sdk2"]["status"], "passed")
        self.assertEqual(report["paths"]["wayland-sdk2"]["status"], "pending")
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])
        calls = {name: (args, env, timeout) for name, args, env, timeout in self.calls}
        for name in ("build", "test"):
            args, env, timeout = calls[name]
            self.assertIn("--locked", args)
            self.assertEqual(args[0:2], ["cargo", "+1.99.1"])
            self.assertEqual(args[args.index("--target") + 1], "x86_64-unknown-linux-gnu")
            self.assertEqual(env["RUSTUP_AUTO_INSTALL"], "0")
            self.assertNotIn("CARGO_BUILD_TARGET", env)
            self.assertGreater(timeout, 0)
        for name in ("headless-sdk1", "headless-sdk2"):
            args, env, timeout = calls[name]
            self.assertEqual(Path(args[0]).name, "ming-app-host")
            self.assertEqual(args[1], "run")
            self.assertEqual(args[args.index("--renderer") + 1], "cpu")
            self.assertIn("--timeout-ms", args)
        self.assertTrue(all(args[0] not in ("node", "npm", "npx") for _, args, _, _ in self.calls))

    def test_gpu_feature_and_selected_backend(self):
        report = self.run_fixture(renderer="gpu")
        self.assertEqual(report["status"], "passed")
        for name, args, _, _ in self.calls:
            if name in ("build", "test"):
                self.assertEqual(args[args.index("--features") + 1], "gpu")
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_gpu_wayland_workflow_uses_surface_and_explicit_snapshot(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", renderer="gpu")
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])
        paths = report["paths"]
        self.assertEqual(paths["headless-sdk1"]["outcome"]["gpuStats"]["readbackBytes"], 0)
        self.assertEqual(paths["headless-sdk2"]["outcome"]["frame"]["backend"], "wgpu-compute-readback")
        path = paths["wayland-sdk2"]
        self.assertEqual(path["outcome"]["frame"]["backend"], "wgpu-compute-surface")
        self.assertEqual(path["outcome"]["presentationBackend"], "wgpu-surface")
        self.assertEqual(path["outcome"]["gpuStats"]["readbackBytes"], 16)
        self.assertIn("--snapshot", path["command"]["argv"])
        # Keep raw stdout and fingerprints consistent so policy itself must reject stale metadata.
        changed = copy.deepcopy(report)
        path = changed["paths"]["wayland-sdk2"]
        path["outcome"]["gpuStats"]["readbackBytes"] = 0
        stdout = self.output / path["command"]["stdout"]["path"]
        stdout.write_text(json.dumps(path["outcome"]), encoding="utf-8")
        path["command"]["stdout"] = verify.file_record(stdout, self.output)
        self.assertTrue(verify.validate_report(changed, self.root, self.output))

    def test_failed_build_or_tests_stop_execution_and_leave_paths_pending(self):
        original = self.fake_runner
        for failed in ("build", "test"):
            with self.subTest(failed=failed):
                self.output = Path(self.temp.name) / failed
                self.calls = []
                def runner(argv, **kwargs):
                    record = original(argv, **kwargs)
                    if kwargs["name"] == failed:
                        record["returnCode"] = 7
                    return record
                with mock.patch.object(verify.platform, "system", return_value="Linux"):
                    report = verify.verify(self.root, self.output, environ=self.env, runner=runner)
                self.assertEqual(report["status"], "failed")
                self.assertFalse(any(name.startswith("headless-") for name, *_ in self.calls))
                self.assertTrue(all(p["status"] == "pending" for p in report["paths"].values()))
                self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_failed_process_and_invalid_json_cannot_pass(self):
        for code, text, timeout in ((1, json.dumps(outcome("read-file")), False),
                                    (0, "cargo chatter\n{}", False), (0, "{}", True)):
            with self.subTest(code=code, text=text):
                self.output = Path(self.temp.name) / ("run" + str(len(self.calls)))
                original = self.fake_runner
                def runner(argv, **kwargs):
                    if kwargs["name"] == "headless-sdk1":
                        return self.command_record(kwargs["output"], kwargs["name"], argv,
                                                   text, code, timeout)
                    return original(argv, **kwargs)
                with mock.patch.object(verify.platform, "system", return_value="Linux"):
                    report = verify.verify(self.root, self.output, environ=self.env, runner=runner)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["paths"]["headless-sdk1"]["status"], "failed")
                self.assertEqual(report["paths"]["headless-sdk2"]["status"], "passed")

    def test_non_linux_is_pending_without_invoking_tools(self):
        with mock.patch.object(verify.platform, "system", return_value="Windows"):
            report = verify.verify(self.root, self.output, runner=self.fake_runner)
        self.assertEqual(report["status"], "pending")
        self.assertEqual(self.calls, [])
        self.assertTrue(all(p["status"] == "pending" for p in report["paths"].values()))
        self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_wayland_checks_socket_clears_inherited_fd_and_records_actual_backend(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session) as probe:
            report = self.run_fixture(mode="all")
        self.assertEqual(probe.call_count, 6)
        self.assertEqual(report["status"], "passed")
        name, args, env, timeout = next(c for c in self.calls if c[0] == "wayland-sdk2")
        self.assertEqual(args[1], "view")
        self.assertNotIn("DISPLAY", env)
        self.assertNotIn("WAYLAND_SOCKET", env)
        self.assertIn("--auto-close-ms", args)
        self.assertIn("--snapshot", args)
        self.assertEqual(report["paths"][name]["outcome"]["windowSystem"], "wayland")
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_no_wayland_socket_fails_requested_path_without_masking_headless(self):
        report = self.run_fixture(mode="all")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["paths"]["wayland-sdk2"]["status"], "failed")
        self.assertEqual(report["paths"]["headless-sdk2"]["status"], "passed")

    def test_revalidation_catches_hash_log_snapshot_and_claim_tampering(self):
        report = self.run_fixture()
        for mutate in (lambda r: r["inputs"]["image-viewer.wasm"].update(sha256="0" * 64),
                       lambda r: r["paths"]["headless-sdk1"]["command"].update(returnCode=1),
                       lambda r: r["paths"]["headless-sdk2"]["outcome"].update(windowSystem="wayland"),
                       lambda r: r["paths"]["headless-sdk2"]["snapshot"].update(sha256="0" * 64),
                       lambda r: r["checks"].pop(),
                       lambda r: r.update(status="pending")):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(report)
                mutate(changed)
                self.assertTrue(verify.validate_report(changed, self.root, self.output))
        wasm = self.root / "artifacts/read-file/main.wasm"
        wasm.write_bytes(b"\0asm\x01\0\0\0new bytes")
        self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_existing_output_is_not_reused(self):
        self.output.mkdir()
        (self.output / "old.json").write_text("old evidence", encoding="utf-8")
        with self.assertRaises(verify.VerificationError):
            self.run_fixture()
        self.assertEqual(self.calls, [])

    def test_rejects_non_native_rustc_host_and_wrong_toolchain(self):
        original = self.fake_runner
        for text in ("release: 1.98.0\nhost: x86_64-unknown-linux-gnu\n",
                     "release: 1.99.1\nhost: x86_64-pc-windows-msvc\n"):
            self.output = Path(self.temp.name) / ("toolchain" + str(len(self.calls)))
            def runner(argv, **kwargs):
                record = original(argv, **kwargs)
                if kwargs["name"] == "rustc":
                    record = self.command_record(kwargs["output"], "rustc", argv, text)
                return record
            with mock.patch.object(verify.platform, "system", return_value="Linux"):
                report = verify.verify(self.root, self.output, runner=runner)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(any(n == "build" for n, *_ in self.calls))

    def test_input_changes_during_execution_fail_provenance(self):
        original = self.fake_runner
        def runner(argv, **kwargs):
            record = original(argv, **kwargs)
            if kwargs["name"] == "headless-sdk2":
                (self.root / "artifacts/read-file/main.wasm").write_bytes(b"\0asm\x01\0\0\0drift")
            return record
        with mock.patch.object(verify.platform, "system", return_value="Linux"):
            report = verify.verify(self.root, self.output, runner=runner)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(report["errors"])


class SDK3OutcomeTests(unittest.TestCase):
    app = "text-demo"

    def check(self, data, window="headless", renderer="cpu", **kwargs):
        verify.validate_outcome(data, app=self.app, renderer=renderer,
                                window_system=window, grant=None, **kwargs)

    def test_real_sample_success_events_and_zero_resolution_timing_are_valid(self):
        for window in ("headless", "wayland"):
            for renderer in ("cpu", "gpu"):
                for frames in (1, 3):
                    with self.subTest(window=window, renderer=renderer, frames=frames):
                        self.check(outcome(self.app, window, renderer, frames=frames), window, renderer)

    def test_text_prepare_micros_is_required_nonnegative_integer(self):
        for renderer in ("cpu", "gpu"):
            for value in (None, -1, True, 1.5, "1"):
                with self.subTest(renderer=renderer, value=value), self.assertRaises(verify.VerificationError):
                    data = outcome(self.app, renderer=renderer)
                    if value is None:
                        del data["frame"]["textPrepareMicros"]
                    else:
                        data["frame"]["textPrepareMicros"] = value
                    self.check(data, renderer=renderer)

    def test_text_prepare_time_cannot_exceed_total_render_time(self):
        for renderer in ("cpu", "gpu"):
            data = outcome(self.app, renderer=renderer)
            data["frame"]["textPrepareMicros"] = 11
            with self.subTest(renderer=renderer), self.assertRaises(verify.VerificationError):
                self.check(data, renderer=renderer)
            data["frame"]["textPrepareMicros"] = 10
            self.check(data, renderer=renderer)

    def test_completion_must_be_exact_and_sample_errors_are_rejected(self):
        cases = ([], [{"code": 40, "value": 0}], [{"code": 40, "value": 2}],
                 [{"code": 40, "value": 1}] * 2,
                 [{"code": 21, "value": 32}, {"code": 40, "value": 1}])
        cases += tuple([{"code": code, "value": -2}, {"code": 40, "value": 1}]
                       for code in range(50, 55))
        for events in cases:
            with self.subTest(events=events), self.assertRaises(verify.VerificationError):
                data = outcome(self.app)
                data["events"] = events
                self.check(data)

    def test_requires_frame_close_event_and_no_bulk_file_reads(self):
        for field, value in (("framesSubmitted", 0), ("frame", None), ("inputCounts", [0] * 9),
                             ("bulkBytesRead", 1)):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                data = outcome(self.app)
                data[field] = value
                self.check(data)

    def test_checks_shared_frame_identity_backend_and_sequence(self):
        for field, value in (("width", 0), ("height", 4097), ("commands", 0),
                             ("renderMicros", -1), ("sequence", 2), ("backend", "other")):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                data = outcome(self.app)
                data["frame"][field] = value
                self.check(data)
        for field, value in (("appId", "dev.ming.image-viewer"), ("exitCode", 1),
                             ("runtime", "node"), ("framesPresented", 1)):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                data = outcome(self.app)
                data[field] = value
                self.check(data)

    def test_gpu_glyph_counters_are_required_nonnegative_integers(self):
        for field in ("glyphUploads", "glyphUploadBytes", "glyphCacheEvictions",
                      "glyphCacheCompactions", "glyphResidentBytes", "glyphEntries"):
            for value in (None, -1, True, 1.5, "1"):
                with self.subTest(field=field, value=value), self.assertRaises(verify.VerificationError):
                    data = outcome(self.app, renderer="gpu")
                    if value is None:
                        del data["gpuStats"][field]
                    else:
                        data["gpuStats"][field] = value
                    self.check(data, renderer="gpu")

    def test_gpu_requires_glyph_work_and_zero_image_work(self):
        for field, value in (("glyphUploads", 0), ("glyphUploadBytes", 0),
                             ("glyphResidentBytes", 0), ("glyphEntries", 0),
                             ("imageUploads", 1), ("imageUploadBytes", 4),
                             ("imageCacheCompactions", 1), ("cachedImages", 1),
                             ("sceneUploadBytes", 0)):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                data = outcome(self.app, renderer="gpu")
                data["gpuStats"][field] = value
                self.check(data, renderer="gpu")

    def test_gpu_glyph_bounds_alignment_and_cumulative_residency_are_consistent(self):
        for field, value in (("glyphEntries", 4097), ("glyphEntries", 101),
                             ("glyphResidentBytes", 4 * 1024 * 1024 + 4),
                             ("glyphResidentBytes", 6401), ("glyphResidentBytes", 6404),
                             ("glyphResidentBytes", 396), ("glyphUploadBytes", 6401),
                             ("glyphUploadBytes", 1024 * 1024 + 4),
                             ("glyphCacheCompactions", 2), ("glyphCacheEvictions", 101)):
            with self.subTest(field=field, value=value), self.assertRaises(verify.VerificationError):
                data = outcome(self.app, renderer="gpu")
                data["gpuStats"][field] = value
                self.check(data, renderer="gpu")

    def test_gpu_reuse_and_nonzero_evictions_compactions_are_allowed(self):
        data = outcome(self.app, renderer="gpu", frames=3)
        data["gpuStats"].update(glyphUploads=120, glyphUploadBytes=7680,
                                glyphCacheEvictions=10, glyphCacheCompactions=1)
        self.check(data, renderer="gpu")

    def test_gpu_readbacks_follow_each_headless_frame_or_final_surface_snapshot(self):
        frame_bytes = 1100 * 760 * 4
        for window in ("headless", "wayland"):
            for snapshot in (False, True):
                data = outcome(self.app, window, "gpu", frames=3, snapshot=snapshot)
                self.check(data, window, "gpu", snapshot=snapshot)
                expected = frame_bytes * (int(snapshot) if window == "wayland" else 3)
                for value in (expected + 4, expected - 4, frame_bytes if expected != frame_bytes else 0):
                    with self.subTest(window=window, snapshot=snapshot, value=value), \
                         self.assertRaises(verify.VerificationError):
                        changed = copy.deepcopy(data)
                        changed["gpuStats"]["readbackBytes"] = value
                        self.check(changed, window, "gpu", snapshot=snapshot)


class SDK3InputTests(Fixture):
    def test_sdk3_package_and_all_four_font_files_are_fingerprinted_without_grant(self):
        inputs = verify.collect_inputs(self.root)
        self.assertIn("text-demo", verify.APPS)
        self.assertEqual(verify.APPS["text-demo"], (3, None))
        for label in ("text-demo.manifest", "text-demo.wasm"):
            self.assertIn(label, inputs)
        self.assertNotIn("text-demo.grant", inputs)
        for name in ("NotoSansSC-Regular.otf", "OFL.txt", "NOTICE.txt", "font-pack.json"):
            relative = "assets/fonts/" + name
            content = (self.root / relative).read_bytes()
            self.assertEqual(inputs[relative], {"path": relative, "bytes": len(content),
                                              "sha256": hashlib.sha256(content).hexdigest()})

    def test_font_pack_verifier_and_tests_are_required_fingerprinted_inputs(self):
        inputs = verify.collect_inputs(self.root)
        for relative in ("scripts/verify-font-pack.py", "tests/test_font_pack.py"):
            with self.subTest(relative=relative):
                self.assertIn(relative, inputs)
                path = self.root / relative
                original = path.read_bytes()
                self.assertEqual(inputs[relative]["sha256"], hashlib.sha256(original).hexdigest())
                path.unlink()
                try:
                    with self.assertRaises(verify.VerificationError):
                        verify.collect_inputs(self.root)
                finally:
                    path.write_bytes(original)

    def test_each_font_file_is_required(self):
        for name in ("NotoSansSC-Regular.otf", "OFL.txt", "NOTICE.txt", "font-pack.json"):
            with self.subTest(name=name):
                path = self.root / "assets/fonts" / name
                content = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises(verify.VerificationError):
                        verify.collect_inputs(self.root)
                finally:
                    path.write_bytes(content)

    def test_font_larger_than_json_limit_is_streamed_and_hashed(self):
        path = self.root / "assets/fonts/NotoSansSC-Regular.otf"
        content = b"OTTO" + b"f" * (8_331_336 - 4)
        path.write_bytes(content)
        inputs = verify.collect_inputs(self.root)
        self.assertIn("assets/fonts/NotoSansSC-Regular.otf", inputs)
        record = inputs["assets/fonts/NotoSansSC-Regular.otf"]
        self.assertEqual(record["bytes"], 8_331_336)
        self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())

    def test_sdk3_sdk_identity_wasm_and_optional_packaged_hash_are_checked(self):
        directory = self.root / "artifacts/text-demo"
        manifest = directory / "app.json"
        original = manifest.read_text(encoding="utf-8")
        for field, value in (("sdkVersion", 2), ("id", "dev.ming.image-viewer"), ("entry", "../main.wasm")):
            with self.subTest(field=field):
                data = json.loads(original)
                data[field] = value
                manifest.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(verify.VerificationError):
                    verify.collect_inputs(self.root)
        manifest.write_text(original, encoding="utf-8")
        wasm = directory / "main.wasm"
        content = wasm.read_bytes()
        for invalid in (None, b"not wasm"):
            with self.subTest(invalid=invalid):
                wasm.unlink()
                if invalid is not None:
                    wasm.write_bytes(invalid)
                try:
                    with self.assertRaises(verify.VerificationError):
                        verify.collect_inputs(self.root)
                finally:
                    wasm.write_bytes(content)
        info = directory / "build-info.json"
        info.write_text('{"packagedSha256":"wrong"}', encoding="utf-8")
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)
        info.write_text(json.dumps({"packagedSha256": hashlib.sha256(content).hexdigest()}), encoding="utf-8")
        self.assertIn("text-demo.build-info", verify.collect_inputs(self.root))


class SDK3WorkflowTests(Fixture):
    def test_headless_runs_sdk3_with_snapshot_no_grant_and_wayland_stays_pending(self):
        report = self.run_fixture()
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(set(report["paths"]), {"headless-sdk1", "headless-sdk2", "headless-sdk3", "headless-sdk4",
                                              "wayland-sdk2", "wayland-sdk3", "wayland-sdk4",
                                              "headless-shadow", "wayland-shadow", "headless-compose", "wayland-compose", "headless-animated", "wayland-animated"})
        path = report["paths"]["headless-sdk3"]
        self.assertEqual(path["status"], "passed")
        self.assertEqual(report["paths"]["wayland-sdk3"]["status"], "pending")
        self.assertEqual(path["outcome"]["appId"], "dev.ming.text-demo")
        self.assertNotIn("--grant", path["command"]["argv"])
        self.assertIn("--snapshot", path["command"]["argv"])
        self.assertIn("snapshot", path)
        _, args, env, _ = next(c for c in self.calls if c[0] == "headless-sdk3")
        self.assertEqual(Path(args[0]).resolve(), (self.output / "ming-app-host").resolve())
        self.assertEqual(args[1], "run")
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
            self.assertNotIn(key, env)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_wayland_mode_executes_only_sdk2_through_sdk4_and_checks_actual_presentation(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="wayland")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in ("headless-sdk1", "headless-sdk2", "headless-sdk3", "headless-sdk4"):
            self.assertEqual(report["paths"][name]["status"], "pending")
        path = report["paths"]["wayland-sdk3"]
        self.assertEqual(path["status"], "passed")
        self.assertEqual(path["outcome"]["windowSystem"], "wayland")
        self.assertGreater(path["outcome"]["framesPresented"], 0)
        self.assertEqual(path["command"]["argv"][1], "view")
        self.assertNotIn("--grant", path["command"]["argv"])
        self.assertIn("--auto-close-ms", path["command"]["argv"])
        self.assertIn("snapshot", path)
        self.assertTrue(path["session"]["connectionVerified"])
        self.assertFalse(any(name.startswith("headless-") for name, *_ in self.calls))
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_gpu_all_executes_and_revalidates_both_sdk3_paths(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", renderer="gpu")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in ("headless-sdk3", "wayland-sdk3"):
            self.assertEqual(report["paths"][name]["status"], "passed")
            self.assertEqual(report["paths"][name]["outcome"]["gpuStats"]["imageUploads"], 0)
            self.assertIn("snapshot", report["paths"][name])
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_sdk3_process_failure_timeout_or_invalid_outcome_cannot_pass(self):
        original = self.fake_runner
        for name in ("headless-sdk3", "wayland-sdk3"):
            for code, timed_out, text in ((7, False, json.dumps(outcome("text-demo"))),
                                           (0, True, "{}"), (0, False, "not json"),
                                           (0, False, json.dumps(outcome()))):
                with self.subTest(name=name, code=code, timed_out=timed_out, text=text):
                    self.output = Path(self.temp.name) / (name + "-" + str(len(self.calls)))
                    def runner(argv, **kwargs):
                        if kwargs["name"] == name:
                            self.calls.append((name, argv, kwargs["env"].copy(), kwargs["timeout"]))
                            return self.command_record(kwargs["output"], name, argv, text, code, timed_out)
                        return original(argv, **kwargs)
                    with mock.patch.object(verify.platform, "system", return_value="Linux"), \
                         mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                        report = verify.verify(self.root, self.output, mode="all", environ=self.env, runner=runner)
                    self.assertEqual(report["status"], "failed")
                    self.assertEqual(report["paths"][name]["status"], "failed")
                    self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_missing_sdk3_snapshot_fails_even_with_valid_outcome(self):
        original = self.fake_runner
        def runner(argv, **kwargs):
            record = original(argv, **kwargs)
            if kwargs["name"] == "headless-sdk3":
                (kwargs["output"] / "headless-sdk3.png").unlink()
            return record
        with mock.patch.object(verify.platform, "system", return_value="Linux"):
            report = verify.verify(self.root, self.output, environ=self.env, runner=runner)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["paths"]["headless-sdk3"]["status"], "failed")

    def test_revalidation_rejects_missing_pending_or_forged_sdk3_evidence(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", renderer="gpu")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in ("headless-sdk3", "wayland-sdk3"):
            for change in (lambda p: p.update(status="pending"), lambda p: p.pop("command"),
                           lambda p: p["command"].update(returnCode=1),
                           lambda p: p["snapshot"].update(sha256="0" * 64),
                           lambda p: p["outcome"].update(framesSubmitted=0),
                           lambda p: p.pop("snapshot")):
                with self.subTest(name=name, change=change):
                    changed = copy.deepcopy(report)
                    change(changed["paths"][name])
                    self.assertTrue(verify.validate_report(changed, self.root, self.output))
            changed = copy.deepcopy(report)
            del changed["paths"][name]
            self.assertTrue(verify.validate_report(changed, self.root, self.output))
        changed = copy.deepcopy(report)
        changed["paths"]["wayland-sdk3"]["session"]["connectionVerified"] = False
        self.assertTrue(verify.validate_report(changed, self.root, self.output))

    def test_revalidation_checks_sdk3_policy_when_raw_stdout_hashes_are_updated(self):
        report = self.run_fixture(renderer="gpu")
        self.assertEqual(report["status"], "passed", report["errors"])
        original = report["paths"]["headless-sdk3"]
        stdout = self.output / original["command"]["stdout"]["path"]
        for mutate in (lambda d: d["frame"].pop("textPrepareMicros"),
                       lambda d: d["gpuStats"].update(imageUploads=1),
                       lambda d: d["gpuStats"].update(readbackBytes=0),
                       lambda d: d.update(events=[])):
            with self.subTest(mutate=mutate):
                changed = copy.deepcopy(report)
                path = changed["paths"]["headless-sdk3"]
                mutate(path["outcome"])
                stdout.write_text(json.dumps(path["outcome"]), encoding="utf-8")
                path["command"]["stdout"] = verify.file_record(stdout, self.output)
                self.assertTrue(verify.validate_report(changed, self.root, self.output))

    def test_each_font_and_sdk3_package_change_invalidates_saved_evidence(self):
        report = self.run_fixture()
        self.assertEqual(report["status"], "passed", report["errors"])
        for relative in ("assets/fonts/NotoSansSC-Regular.otf", "assets/fonts/OFL.txt",
                         "assets/fonts/NOTICE.txt", "assets/fonts/font-pack.json",
                         "artifacts/text-demo/main.wasm", "artifacts/text-demo/app.json",
                         "scripts/verify-font-pack.py", "tests/test_font_pack.py"):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                self.assertTrue(verify.validate_report(report, self.root, self.output))
                path.write_bytes(original)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_font_change_during_execution_fails_final_provenance_check(self):
        original = self.fake_runner
        def runner(argv, **kwargs):
            record = original(argv, **kwargs)
            if kwargs["name"] == "headless-sdk3":
                (self.root / "assets/fonts/OFL.txt").write_bytes(b"changed license")
            return record
        with mock.patch.object(verify.platform, "system", return_value="Linux"):
            report = verify.verify(self.root, self.output, environ=self.env, runner=runner)
        self.assertEqual(report["status"], "failed")
        self.assertIn("provenance", " ".join(report["errors"]))


class SDK4OutcomeTests(SDK3OutcomeTests):
    app = "ui-demo"

    def test_sdk4_identity_cannot_be_substituted_with_sdk3(self):
        data = outcome("ui-demo")
        data["appId"] = "dev.ming.text-demo"
        with self.assertRaises(verify.VerificationError):
            self.check(data)

    def test_default_frame_and_resized_wayland_keep_existing_readback_policy(self):
        for window in ("headless", "wayland"):
            for renderer in ("cpu", "gpu"):
                for snapshot in (False, True):
                    with self.subTest(window=window, renderer=renderer, snapshot=snapshot):
                        data = outcome("ui-demo", window, renderer, frames=3, snapshot=snapshot)
                        self.assertEqual((data["frame"]["width"], data["frame"]["height"]), (1100, 760))
                        if window == "wayland":
                            data["frame"].update(width=960, height=640)
                            data["presentedFrame"].update(width=960, height=640)
                            if renderer == "gpu":
                                data["gpuStats"]["readbackBytes"] = 960 * 640 * 4 * int(snapshot)
                        self.check(data, window, renderer, snapshot=snapshot)
                        if renderer == "gpu":
                            data["gpuStats"]["readbackBytes"] += 4
                            with self.assertRaises(verify.VerificationError):
                                self.check(data, window, renderer, snapshot=snapshot)


class SDK4InputTests(Fixture):
    def test_sdk4_package_hashes_and_shared_sources_are_recorded_without_grant(self):
        inputs = verify.collect_inputs(self.root)
        self.assertEqual(verify.APPS.get("ui-demo"), (4, None))
        for label, relative in (("ui-demo.manifest", "artifacts/ui-demo/app.json"),
                                ("ui-demo.wasm", "artifacts/ui-demo/main.wasm"),
                                ("crates/ming-app-host/src/main.rs", "crates/ming-app-host/src/main.rs")):
            with self.subTest(label=label):
                content = (self.root / relative).read_bytes()
                self.assertEqual(inputs[label], {"path": relative, "bytes": len(content),
                                                 "sha256": hashlib.sha256(content).hexdigest()})
        self.assertNotIn("ui-demo.grant", inputs)
        for relative in ("assets/fonts/NotoSansSC-Regular.otf", "assets/fonts/OFL.txt",
                         "assets/fonts/NOTICE.txt", "assets/fonts/font-pack.json",
                         "scripts/verify-font-pack.py", "tests/test_font_pack.py"):
            self.assertIn(relative, inputs)

    def test_sdk4_manifest_is_required_and_identity_sdk_entry_are_strict(self):
        manifest = self.root / "artifacts/ui-demo/app.json"
        original = manifest.read_bytes()
        manifest.unlink()
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)
        for field, value in (("schemaVersion", 2), ("sdkVersion", 3),
                             ("id", "dev.ming.text-demo"), ("entry", "../main.wasm")):
            with self.subTest(field=field):
                data = json.loads(original)
                data[field] = value
                manifest.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(verify.VerificationError):
                    verify.collect_inputs(self.root)
        manifest.write_bytes(original)
        self.assertIn("ui-demo.manifest", verify.collect_inputs(self.root))

    def test_sdk4_wasm_header_and_optional_build_hash_are_checked(self):
        directory = self.root / "artifacts/ui-demo"
        wasm = directory / "main.wasm"
        original = wasm.read_bytes()
        for content in (None, b"not wasm", b"\0asm\x02\0\0\0"):
            with self.subTest(content=content):
                wasm.unlink()
                if content is not None:
                    wasm.write_bytes(content)
                try:
                    with self.assertRaises(verify.VerificationError):
                        verify.collect_inputs(self.root)
                finally:
                    wasm.write_bytes(original)
        info = directory / "build-info.json"
        info.write_text('{"packagedSha256":"wrong"}', encoding="utf-8")
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)
        info.write_text(json.dumps({"packagedSha256": hashlib.sha256(original).hexdigest()}), encoding="utf-8")
        inputs = verify.collect_inputs(self.root)
        self.assertEqual(inputs["ui-demo.build-info"], verify.file_record(info, self.root))


class SDK4WorkflowTests(Fixture):
    def test_each_mode_executes_exactly_its_requested_paths(self):
        headless = {"headless-sdk1", "headless-sdk2", "headless-sdk3", "headless-sdk4", "headless-shadow", "headless-compose", "headless-animated"}
        wayland = {"wayland-sdk2", "wayland-sdk3", "wayland-sdk4", "wayland-shadow", "wayland-compose", "wayland-animated"}
        for mode, expected in (("headless", headless), ("wayland", wayland), ("all", headless | wayland)):
            with self.subTest(mode=mode):
                self.calls.clear()
                self.output = Path(self.temp.name) / mode
                with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                    report = self.run_fixture(mode=mode)
                self.assertEqual(report["status"], "passed", report["errors"])
                self.assertEqual(set(report["paths"]), headless | wayland)
                executed = [name for name, *_ in self.calls if name.startswith(("headless-", "wayland-"))]
                self.assertCountEqual(executed, expected)
                for name, evidence in report["paths"].items():
                    self.assertEqual(evidence["status"], "passed" if name in expected else "pending")
                self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_sdk4_cpu_gpu_commands_snapshots_and_session_evidence(self):
        for renderer in ("cpu", "gpu"):
            with self.subTest(renderer=renderer):
                self.output = Path(self.temp.name) / renderer
                with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                    report = self.run_fixture(mode="all", renderer=renderer)
                self.assertEqual(report["status"], "passed", report["errors"])
                for window in ("headless", "wayland"):
                    name = window + "-sdk4"
                    evidence = report["paths"][name]
                    argv = evidence["command"]["argv"]
                    self.assertEqual(Path(argv[0]).resolve(), (self.output / "ming-app-host").resolve())
                    self.assertEqual(argv[1], "view" if window == "wayland" else "run")
                    self.assertEqual(Path(argv[2]).resolve(),
                                     (self.root / "artifacts/ui-demo/app.json").resolve())
                    self.assertNotIn("--grant", argv)
                    self.assertEqual(argv[argv.index("--renderer") + 1], renderer)
                    self.assertEqual(Path(argv[argv.index("--snapshot") + 1]).resolve(),
                                     (self.output / (name + ".png")).resolve())
                    self.assertEqual(evidence["outcome"]["appId"], "dev.ming.ui-demo")
                    self.assertEqual(evidence["outcome"]["bulkBytesRead"], 0)
                    self.assertEqual(evidence["outcome"]["events"], [{"code": 40, "value": 1}])
                    self.assertEqual(verify.snapshot_size(self.output / evidence["snapshot"]["path"]), (1100, 760))
                    if window == "wayland":
                        self.assertIn("--auto-close-ms", argv)
                        self.assertTrue(evidence["session"]["connectionVerified"])
                    else:
                        _, _, env, _ = next(c for c in self.calls if c[0] == name)
                        for key in ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
                            self.assertNotIn(key, env)
                self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_sdk4_process_failure_timeout_or_invalid_outcome_cannot_pass(self):
        original = self.fake_runner
        for name in ("headless-sdk4", "wayland-sdk4"):
            for code, timed_out, text in ((7, False, "{}"), (0, True, "{}"),
                                          (0, False, "not json"),
                                          (0, False, json.dumps(outcome("text-demo")))):
                with self.subTest(name=name, code=code, timed_out=timed_out, text=text):
                    self.output = Path(self.temp.name) / (name + "-" + str(len(self.calls)))
                    def runner(argv, **kwargs):
                        if kwargs["name"] == name:
                            self.calls.append((name, argv, kwargs["env"].copy(), kwargs["timeout"]))
                            return self.command_record(kwargs["output"], name, argv, text, code, timed_out)
                        return original(argv, **kwargs)
                    with mock.patch.object(verify.platform, "system", return_value="Linux"), \
                         mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                        report = verify.verify(self.root, self.output, mode="all", environ=self.env, runner=runner)
                    self.assertEqual(report["status"], "failed")
                    self.assertEqual(report["paths"][name]["status"], "failed")

    def test_sdk4_missing_or_wrong_size_snapshot_fails_execution(self):
        original = self.fake_runner
        for name in ("headless-sdk4", "wayland-sdk4"):
            for invalid in (None, png()):
                with self.subTest(name=name, missing=invalid is None):
                    self.output = Path(self.temp.name) / (name + "-snapshot-" + str(len(self.calls)))
                    def runner(argv, **kwargs):
                        record = original(argv, **kwargs)
                        if kwargs["name"] == name:
                            snapshot = kwargs["output"] / (name + ".png")
                            if invalid is None:
                                snapshot.unlink()
                            else:
                                snapshot.write_bytes(invalid)
                        return record
                    with mock.patch.object(verify.platform, "system", return_value="Linux"), \
                         mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                        report = verify.verify(self.root, self.output, mode="all", environ=self.env, runner=runner)
                    self.assertEqual(report["status"], "failed")
                    self.assertEqual(report["paths"][name]["status"], "failed")

    def test_revalidation_rejects_missing_pending_or_tampered_sdk4_evidence(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", renderer="gpu")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in ("headless-sdk4", "wayland-sdk4"):
            for change in (lambda p: p.update(status="pending"), lambda p: p.pop("command"),
                           lambda p: p["command"].update(returnCode=1),
                           lambda p: p["command"]["stdout"].update(sha256="0" * 64),
                           lambda p: p["snapshot"].update(sha256="0" * 64),
                           lambda p: p["outcome"].update(framesSubmitted=0),
                           lambda p: p.pop("snapshot")):
                with self.subTest(name=name, change=change):
                    changed = copy.deepcopy(report)
                    change(changed["paths"][name])
                    self.assertTrue(verify.validate_report(changed, self.root, self.output))
            changed = copy.deepcopy(report)
            del changed["paths"][name]
            self.assertTrue(verify.validate_report(changed, self.root, self.output))
        for field in ("socketVerified", "connectionVerified"):
            changed = copy.deepcopy(report)
            changed["paths"]["wayland-sdk4"]["session"][field] = False
            self.assertTrue(verify.validate_report(changed, self.root, self.output))

    def test_revalidation_rechecks_sdk4_policy_with_updated_raw_stdout_hashes(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", renderer="gpu")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in ("headless-sdk4", "wayland-sdk4"):
            stdout = self.output / report["paths"][name]["command"]["stdout"]["path"]
            original = stdout.read_bytes()
            for mutate in (lambda d: d["frame"].pop("textPrepareMicros"),
                           lambda d: d["gpuStats"].update(imageUploads=1),
                           lambda d: d["gpuStats"].update(glyphEntries=4097),
                           lambda d: d["gpuStats"].update(readbackBytes=0),
                           lambda d: d.update(bulkBytesRead=1),
                           lambda d: d.update(events=[{"code": 50, "value": -2}])):
                with self.subTest(name=name, mutate=mutate):
                    changed = copy.deepcopy(report)
                    evidence = changed["paths"][name]
                    mutate(evidence["outcome"])
                    stdout.write_text(json.dumps(evidence["outcome"]), encoding="utf-8")
                    evidence["command"]["stdout"] = verify.file_record(stdout, self.output)
                    self.assertTrue(verify.validate_report(changed, self.root, self.output))
                    stdout.write_bytes(original)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_revalidation_rejects_sdk4_snapshot_dimensions_even_with_updated_hash(self):
        report = self.run_fixture()
        self.assertEqual(report["status"], "passed", report["errors"])
        evidence = report["paths"]["headless-sdk4"]
        snapshot = self.output / evidence["snapshot"]["path"]
        snapshot.write_bytes(png())
        evidence["snapshot"] = verify.file_record(snapshot, self.output)
        self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_sdk4_package_font_and_source_drift_invalidates_saved_evidence(self):
        info = self.root / "artifacts/ui-demo/build-info.json"
        wasm = self.root / "artifacts/ui-demo/main.wasm"
        info.write_text(json.dumps({"packagedSha256": hashlib.sha256(wasm.read_bytes()).hexdigest()}), encoding="utf-8")
        report = self.run_fixture()
        self.assertEqual(report["status"], "passed", report["errors"])
        for relative in ("artifacts/ui-demo/app.json", "artifacts/ui-demo/main.wasm",
                         "artifacts/ui-demo/build-info.json", "crates/ming-app-host/src/main.rs",
                         "assets/fonts/NotoSansSC-Regular.otf", "assets/fonts/OFL.txt",
                         "assets/fonts/NOTICE.txt", "assets/fonts/font-pack.json",
                         "scripts/verify-font-pack.py", "tests/test_font_pack.py"):
            with self.subTest(relative=relative):
                target = self.root / relative
                original = target.read_bytes()
                target.write_bytes(original + b" ")
                self.assertTrue(verify.validate_report(report, self.root, self.output))
                target.write_bytes(original)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_sdk4_wasm_drift_during_execution_fails_final_provenance(self):
        original = self.fake_runner
        def runner(argv, **kwargs):
            record = original(argv, **kwargs)
            if kwargs["name"] == "headless-sdk4":
                wasm = self.root / "artifacts/ui-demo/main.wasm"
                wasm.write_bytes(wasm.read_bytes() + b"drift")
            return record
        with mock.patch.object(verify.platform, "system", return_value="Linux"):
            report = verify.verify(self.root, self.output, environ=self.env, runner=runner)
        self.assertEqual(report["status"], "failed")
        self.assertIn("provenance", " ".join(report["errors"]))


class SocketTests(unittest.TestCase):
    def test_missing_relative_or_regular_file_socket_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            regular = Path(directory) / "wayland-0"
            regular.write_text("not socket", encoding="utf-8")
            for env in ({}, {"XDG_RUNTIME_DIR": ".", "WAYLAND_DISPLAY": "wayland-0"},
                        {"XDG_RUNTIME_DIR": directory, "WAYLAND_DISPLAY": "wayland-0"}):
                with self.subTest(env=env), self.assertRaises(verify.VerificationError):
                    verify.wayland_environment(env)

    @unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix domain sockets unavailable")
    def test_real_listening_socket_is_probed_and_inherited_variables_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            address = Path(directory) / "wl"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(address))
                server.listen(2)
                env = {"XDG_RUNTIME_DIR": directory, "WAYLAND_DISPLAY": "wl",
                       "DISPLAY": ":1", "WAYLAND_SOCKET": "10"}
                child, evidence = verify.wayland_environment(env)
                self.assertTrue(evidence["connectionVerified"])
                self.assertEqual(child["WINIT_UNIX_BACKEND"], "wayland")
                self.assertNotIn("DISPLAY", child)
                self.assertNotIn("WAYLAND_SOCKET", child)
                self.assertIn("WAYLAND_SOCKET", env)
            with self.assertRaises(verify.VerificationError):
                verify.wayland_environment(env)


class RunnerTests(unittest.TestCase):
    def test_stdout_stderr_checked_exit_and_timeout_are_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            record = verify.run_command(
                [sys.executable, "-c", "import sys; print('out'); print('err',file=sys.stderr); sys.exit(7)"],
                cwd=output, env=dict(os.environ), timeout=5, output=output, name="failure")
            self.assertEqual(record["returnCode"], 7)
            self.assertFalse(record["timedOut"])
            self.assertEqual((output / record["stdout"]["path"]).read_text().strip(), "out")
            timed = verify.run_command([sys.executable, "-c", "import time; time.sleep(30)"],
                                       cwd=output, env=dict(os.environ), timeout=0.1,
                                       output=output, name="timeout")
            self.assertTrue(timed["timedOut"])
            self.assertNotEqual(timed["returnCode"], 0)
            self.assertLess(timed["durationSeconds"], 5)

    def test_missing_executable_is_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            record = verify.run_command([str(Path(directory) / "nonexistent")],
                                        cwd=Path(directory), env=dict(os.environ), timeout=1,
                                        output=Path(directory), name="missing")
            self.assertIsNotNone(record["error"])
            self.assertIsNone(record["returnCode"])

    def test_cli_help_and_invalid_options(self):
        for args, expected in ((["--help"], 0), (["--renderer", "automatic"], 2),
                               (["--run-timeout", "0"], 2), (["--command-timeout", "nan"], 2),
                               (["--auto-close-ms", "60001"], 2)):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(SCRIPT), *args],
                                        capture_output=True, timeout=10)
                self.assertEqual(result.returncode, expected, result.stderr.decode())


class ContainerTests(Fixture):
    def setUp(self):
        super().setUp()
        container_spec = importlib.util.spec_from_file_location(
            "linux_container", SCRIPT.with_name("linux-container.py"))
        self.container = importlib.util.module_from_spec(container_spec)
        container_spec.loader.exec_module(self.container)

    def test_container_has_only_project_evidence_and_fresh_linux_cache_mounts(self):
        output = self.root / "artifacts/linux-fixture"
        plan = self.container.docker_commands(self.root, output, "abc123")
        command = plan["create"]
        mounts = [command[i + 1] for i, value in enumerate(command) if value == "--mount"]
        self.assertEqual(len(mounts), 3)
        self.assertIn("type=bind,source=" + str(self.root.resolve()) + ",target=/workspace,readonly", mounts)
        self.assertIn("type=bind,source=" + str(output.resolve()) + ",target=/evidence", mounts)
        self.assertIn("type=volume,source=mingos-linux-task3-abc123-cache,target=/ming-cache", mounts)
        self.assertIn("CARGO_TARGET_DIR=/ming-cache/target", command)
        self.assertIn("CARGO_HOME=/ming-cache/cargo", command)
        self.assertIn("RUSTUP_HOME=/usr/local/rustup", command)
        for forbidden in ("--privileged", "--device", "--gpus", "--network=host", "/var/run/docker.sock"):
            self.assertNotIn(forbidden, " ".join(command))
        self.assertEqual(plan["build"][-1], str((self.root / "scripts/linux").resolve()))

    def test_container_rejects_external_evidence_and_unsafe_names(self):
        for output, identity in ((Path(self.temp.name) / "elsewhere", "abc"),
                                 (self.root / "artifacts/not-linux", "abc"),
                                 (self.root / "artifacts/linux-test", "other/container")):
            with self.subTest(output=output), self.assertRaises(self.container.acceptance.VerificationError):
                self.container.docker_commands(self.root, output, identity)


class AdditionalEvidenceTests(Fixture):
    def test_actual_x11_or_missing_window_system_cannot_pass_forced_wayland(self):
        original = self.fake_runner
        def runner(argv, **kwargs):
            record = original(argv, **kwargs)
            if kwargs["name"] == "wayland-sdk2":
                record = self.command_record(kwargs["output"], "wayland-sdk2", argv,
                                             json.dumps(outcome(window="x11")))
            return record
        with mock.patch.object(verify.platform, "system", return_value="Linux"), \
             mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = verify.verify(self.root, self.output, mode="all", runner=runner, environ=self.env)
        self.assertEqual(report["paths"]["wayland-sdk2"]["status"], "failed")
        self.assertEqual(report["status"], "failed")

    def test_truncated_snapshot_and_modified_logs_fail_revalidation(self):
        report = self.run_fixture()
        (self.output / "headless-sdk2.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        self.assertTrue(verify.validate_report(report, self.root, self.output))
        (self.output / "headless-sdk2.png").write_bytes(png())
        stream = self.output / report["checks"][0]["stdout"]["path"]
        stream.write_text("forged log", encoding="utf-8")
        self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_report_file_revalidates_after_evidence_is_moved(self):
        report = self.run_fixture()
        saved = verify.read_json(self.output / "evidence.json")
        self.assertEqual(saved, report)
        moved = self.output.with_name("moved evidence")
        self.output.rename(moved)
        self.assertEqual(verify.validate_report(saved, self.root, moved), [])

    def test_duplicate_json_and_nonfinite_numbers_are_rejected(self):
        path = Path(self.temp.name) / "invalid.json"
        for text in ('{"exitCode": 1, "exitCode": 0}', '{"exitCode": NaN}'):
            with self.subTest(text=text), self.assertRaises(verify.VerificationError):
                path.write_text(text, encoding="utf-8")
                verify.read_json(path)

class CargoControlTests(Fixture):
    def test_inherited_runner_cannot_turn_cargo_test_into_a_successful_noop(self):
        self.env["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER"] = "/usr/bin/true"
        report = self.run_fixture()
        self.assertEqual(report["status"], "failed")
        self.assertFalse(any(n == "test" for n, *_ in self.calls))
        self.assertIn("RUNNER", " ".join(report["errors"]))
        self.assertEqual(report["inheritedBuildSettings"]["CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUNNER"], "/usr/bin/true")

    def test_project_target_runner_is_rejected(self):
        config = self.root / ".cargo/config.toml"
        config.parent.mkdir()
        config.write_text('[target.x86_64-unknown-linux-gnu]\nrunner = "/usr/bin/true"\n', encoding="utf-8")
        report = self.run_fixture()
        self.assertEqual(report["status"], "failed")
        self.assertFalse(any(n == "test" for n, *_ in self.calls))

    def test_project_cargo_config_is_fingerprinted_and_never_implicitly_loaded(self):
        config = self.root / ".cargo/config.toml"
        config.parent.mkdir()
        config.write_text('[net]\noffline = true\n', encoding="utf-8")
        original = self.fake_runner
        cargo_cwds = []
        def runner(argv, **kwargs):
            if kwargs["name"] in ("cargo", "build", "test"):
                cargo_cwds.append(Path(kwargs["cwd"]).resolve())
            return original(argv, **kwargs)
        with mock.patch.object(verify.platform, "system", return_value="Linux"):
            report = verify.verify(self.root, self.output, runner=runner, environ=self.env)
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertIn(".cargo/config.toml", report["inputs"])
        self.assertTrue(cargo_cwds)
        self.assertTrue(all(not p.is_relative_to(self.root.resolve()) for p in cargo_cwds))
        for name, args, env, timeout in self.calls:
            if name in ("build", "test"):
                self.assertEqual(env["RUSTC"], str((self.root / "toolchain/rustc").resolve()))
                self.assertEqual(env["CARGO_BUILD_RUSTC"], env["RUSTC"])
        config.write_text('[net]\noffline = false\n', encoding="utf-8")
        self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_inherited_compilers_wrappers_rustflags_and_linkers_fail_explicitly(self):
        for key in ("RUSTC", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER", "CARGO_BUILD_RUSTC",
                    "CARGO_ENCODED_RUSTFLAGS", "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_RUSTFLAGS",
                    "CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER", "CC", "LD_PRELOAD"):
            with self.subTest(key=key):
                self.env = {"PATH": os.environ.get("PATH", ""), key: "/unexpected/override"}
                self.output = Path(self.temp.name) / key
                report = self.run_fixture()
                self.assertEqual(report["status"], "failed")
                self.assertIn(key, " ".join(report["errors"]))

    def test_global_cargo_home_configuration_is_rejected(self):
        home = Path(self.temp.name) / "global cargo home"
        home.mkdir()
        (home / "config.toml").write_text('[build]\nrustc = "/bin/false"\n', encoding="utf-8")
        self.env["CARGO_HOME"] = str(home)
        report = self.run_fixture()
        self.assertEqual(report["status"], "failed")
        self.assertFalse(any(n == "test" for n, *_ in self.calls))


class ContainerCleanupTests(Fixture):
    def setUp(self):
        super().setUp()
        container_spec = importlib.util.spec_from_file_location("linux_container", SCRIPT.with_name("linux-container.py"))
        self.container = importlib.util.module_from_spec(container_spec)
        container_spec.loader.exec_module(self.container)

    def cleanup_runner(self, states, stop_timeout=False):
        self.cleanup_calls = []
        def runner(argv, **kwargs):
            self.cleanup_calls.append((argv, kwargs["timeout"]))
            if argv[1] == "inspect":
                running = states.pop(0)
                data = [{"Id": CONTAINER_ID, "Name": "/mingos-linux-task3-fixture", "Config": {
                    "Labels": {"mingos.acceptance": "linux-task3"}},
                    "State": {"Running": running, "ExitCode": 137 if not running else 0}}]
                return self.command_record(kwargs["output"], kwargs["name"], argv, json.dumps(data))
            return self.command_record(kwargs["output"], kwargs["name"], argv, "",
                                       code=1 if stop_timeout else 0,
                                       timed_out=stop_timeout and argv[1] == "stop")
        return runner

    def test_daemon_container_is_stopped_after_cli_timeout(self):
        self.output.mkdir()
        result = self.container.ensure_stopped("mingos-linux-task3-fixture", cwd=self.root,
                    env=self.env, output=self.output, runner=self.cleanup_runner([True, False]))
        self.assertTrue(result["stopped"])
        self.assertIn("stop", [args[1] for args, timeout in self.cleanup_calls])
        self.assertNotIn("kill", [args[1] for args, timeout in self.cleanup_calls])
        self.assertTrue(all(timeout <= 20 for _, timeout in self.cleanup_calls))
        self.assertFalse(result["state"]["Running"])

    def test_stop_timeout_escalates_to_kill_only_for_exact_owned_container(self):
        self.output.mkdir()
        result = self.container.ensure_stopped("mingos-linux-task3-fixture", cwd=self.root,
                    env=self.env, output=self.output,
                    runner=self.cleanup_runner([True, True, False], stop_timeout=True))
        self.assertTrue(result["stopped"])
        self.assertIn("kill", [args[1] for args, timeout in self.cleanup_calls])
        self.assertEqual(self.cleanup_calls[0][0][-1], "mingos-linux-task3-fixture")
        self.assertTrue(all(args[-1] == CONTAINER_ID for args, _ in self.cleanup_calls[1:]))

    def test_exited_container_and_unowned_name_never_receive_stop_or_kill(self):
        self.output.mkdir()
        result = self.container.ensure_stopped("mingos-linux-task3-fixture", cwd=self.root,
                    env=self.env, output=self.output, runner=self.cleanup_runner([False]))
        self.assertTrue(result["stopped"])
        self.assertEqual([args[1] for args, timeout in self.cleanup_calls], ["inspect"])
        with self.assertRaises(self.container.acceptance.VerificationError):
            self.container.ensure_stopped("somebody-else", cwd=self.root, env=self.env,
                        output=self.output, runner=self.cleanup_runner([True]))
        self.assertEqual(self.cleanup_calls, [])

    def test_outer_attach_timeout_always_reaches_owned_container_cleanup(self):
        output = self.root / "artifacts/linux-timeout"
        calls = []
        running = [True, False]
        def runner(argv, **kwargs):
            calls.append(argv)
            data = "fixture"
            if argv[1] == "inspect":
                data = json.dumps([{"Id": CONTAINER_ID, "Name": "/" + argv[-1], "Config": {
                    "Labels": {"mingos.acceptance": "linux-task3"}},
                    "State": {"Running": running.pop(0), "ExitCode": 137}}])
            return self.command_record(kwargs["output"], kwargs["name"], argv, data,
                                       code=137 if argv[1] == "start" else 0,
                                       timed_out=argv[1] == "start")
        helper = self.container.ensure_stopped
        with mock.patch.object(self.container, "__file__", str(self.root / "scripts/linux-container.py")), \
             mock.patch.object(self.container.acceptance, "run_command", side_effect=runner), \
             mock.patch.object(self.container, "ensure_stopped", side_effect=lambda name, **kwargs:
                               helper(name, **kwargs, runner=runner)), mock.patch("sys.stdout", new=io.StringIO()):
            code = self.container.main(["--output", str(output)])
        self.assertEqual(code, 1)
        saved = json.loads((output / "docker-evidence.json").read_text(encoding="utf-8"))
        self.assertTrue(saved["cleanup"]["stopped"])
        self.assertFalse(saved["cleanup"]["state"]["Running"])
        created = next(a[a.index("--name") + 1] for a in calls if a[1] == "create")
        stopped = next(a[-1] for a in calls if a[1] == "stop")
        self.assertEqual(saved["cleanup"]["container"], created)
        self.assertEqual(stopped, CONTAINER_ID)

    def test_container_ownership_mismatch_never_issues_stop(self):
        self.output.mkdir()
        original = self.cleanup_runner([True])
        def runner(argv, **kwargs):
            record = original(argv, **kwargs)
            if argv[1] == "inspect":
                data = [{"Id": CONTAINER_ID, "Name": "/mingos-linux-task3-fixture", "Config": {"Labels": {}},
                         "State": {"Running": True}}]
                return self.command_record(kwargs["output"], kwargs["name"], argv, json.dumps(data))
            return record
        result = self.container.ensure_stopped("mingos-linux-task3-fixture", cwd=self.root,
                    env=self.env, output=self.output, runner=runner)
        self.assertFalse(result["stopped"])
        self.assertEqual([args[1] for args, _ in self.cleanup_calls], ["inspect"])

    def test_all_feature_check_uses_same_pinned_compiler_without_config_discovery(self):
        report = self.run_fixture()
        checks = []
        def runner(argv, **kwargs):
            checks.append((argv, kwargs))
            return self.fake_runner(argv, **kwargs)
        result = self.container.check_all_features(self.root, self.output, self.env, report,
                                                   timeout=600, runner=runner)
        argv, options = checks[0]
        self.assertIn("--locked", argv)
        self.assertIn("--all-features", argv)
        self.assertIn("--all-targets", argv)
        self.assertEqual(options["env"]["RUSTC"], report["compiler"]["path"])
        self.assertFalse(Path(options["cwd"]).is_relative_to(self.root.resolve()))
        self.assertEqual(result["command"]["returnCode"], 0)

    def test_failed_post_stop_inspection_kills_verified_immutable_id(self):
        for final_fails in (False, True):
            with self.subTest(final_fails=final_fails):
                output = Path(self.temp.name) / ("post-stop-" + str(final_fails))
                output.mkdir()
                calls = []
                def runner(argv, **kwargs):
                    label = kwargs["name"]
                    calls.append((label, argv, kwargs["timeout"]))
                    bad = label in ("cleanup-stop", "cleanup-inspect-after-stop") or (
                        final_fails and label == "cleanup-inspect-final")
                    text = json.dumps([{"Id": CONTAINER_ID, "Name": "/mingos-linux-task3-fixture",
                        "Config": {"Labels": {"mingos.acceptance": "linux-task3"}},
                        "State": {"Running": label == "cleanup-inspect-before", "ExitCode": 137}}])
                    return self.command_record(kwargs["output"], label, argv, text,
                                               code=1 if bad else 0, timed_out=bad)
                result = self.container.ensure_stopped("mingos-linux-task3-fixture", cwd=self.root,
                            env=self.env, output=output, runner=runner)
                kill = next(argv for label, argv, timeout in calls if label == "cleanup-kill")
                self.assertEqual(kill[-1], CONTAINER_ID)
                self.assertTrue(all(argv[-1] == CONTAINER_ID for label, argv, _ in calls
                                    if label != "cleanup-inspect-before"))
                self.assertEqual(result["stopped"], not final_fails)
                self.assertEqual(result["stateVerified"], not final_fails)
                if final_fails:
                    self.assertIn("error", result)
                self.assertTrue(all(timeout <= 20 for _, _, timeout in calls))

    def test_initial_inspection_failure_never_attempts_stop_or_kill(self):
        self.output.mkdir()
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return self.command_record(kwargs["output"], kwargs["name"], argv, "unavailable", code=1)
        result = self.container.ensure_stopped("mingos-linux-task3-fixture", cwd=self.root,
                    env=self.env, output=self.output, runner=runner)
        self.assertFalse(result["stopped"])
        self.assertEqual([argv[1] for argv in calls], ["inspect"])


class CargoConfigDriftTests(Fixture):
    def test_config_created_after_build_or_test_is_rejected(self):
        for stage in ("build", "test", "headless-sdk2"):
            with self.subTest(stage=stage):
                self.output = Path(self.temp.name) / stage
                self.calls = []
                original = self.fake_runner
                def runner(argv, **kwargs):
                    record = original(argv, **kwargs)
                    if kwargs["name"] == stage:
                        (Path(kwargs["env"]["CARGO_HOME"]) / "config.toml").write_text(
                            '[target.x86_64-unknown-linux-gnu]\nrunner = "/usr/bin/true"\n', encoding="utf-8")
                    return record
                with mock.patch.object(verify.platform, "system", return_value="Linux"):
                    report = verify.verify(self.root, self.output, runner=runner, environ=self.env)
                self.assertEqual(report["status"], "failed")
                if stage == "build":
                    self.assertFalse(any(name == "test" for name, *_ in self.calls))

    def test_revalidation_rejects_new_config_in_evidence_relative_cargo_home(self):
        report = self.run_fixture()
        home_refs = [ref for ref in report["cargoPolicy"]["configDiscovery"] if ref["role"] == "cargo-home"]
        self.assertEqual(len(home_refs), 2)
        self.assertTrue(all(ref["scope"] == "evidence" and not Path(ref["path"]).is_absolute() for ref in home_refs))
        (self.output / "cargo-home/config.toml").write_text('[build]\nrustc="/bin/false"\n', encoding="utf-8")
        self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_revalidation_checks_accessible_external_cargo_home(self):
        home = Path(self.temp.name) / "external-cargo-cache"
        self.env["CARGO_HOME"] = str(home)
        report = self.run_fixture()
        (home / "config").write_text('[build]\nrustc="/bin/false"\n', encoding="utf-8")
        self.assertTrue(verify.validate_report(report, self.root, self.output))

    def test_foreign_external_paths_use_recorded_final_presence_portably(self):
        report = self.run_fixture()
        policy = report["cargoPolicy"]
        ref = next(ref for ref in policy["configDiscovery"] if ref["scope"] == "external")
        old = dict(ref)
        foreign = dict(ref, style="posix" if os.name == "nt" else "windows",
                       path="/ming-unavailable/.cargo/config" if os.name == "nt" else "Z:/ming-unavailable/.cargo/config")
        ref.update(foreign)
        for check in policy["configChecks"]:
            for observation in check["observations"]:
                if observation["path"] == old:
                    observation["path"] = dict(foreign)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])
        last = policy["configChecks"][-1]
        self.assertEqual(last["stage"], "final")
        next(o for o in last["observations"] if o["path"] == foreign)["present"] = True
        self.assertTrue(verify.validate_report(report, self.root, self.output))



class ShadowOutcomeTests(unittest.TestCase):
    def check(self, data, window="headless", renderer="cpu", *, snapshot=True):
        verify.validate_outcome(data, app="shadow-demo", renderer=renderer,
                                window_system=window, grant=None, snapshot=snapshot)

    def test_shadow_cpu_gpu_headless_wayland_and_snapshot_matrix(self):
        for window in ("headless", "wayland"):
            for renderer in ("cpu", "gpu"):
                for frames in (1, 3):
                    for snapshot in (False, True):
                        with self.subTest(window=window, renderer=renderer, frames=frames, snapshot=snapshot):
                            data = outcome("shadow-demo", window, renderer, frames=frames, snapshot=snapshot)
                            if renderer == "cpu":
                                self.assertNotIn("gpuStats", data)
                            self.check(data, window, renderer, snapshot=snapshot)

    def test_shadow_requires_only_exact_code40_completion_and_rejects_all_errors(self):
        for events in ([], [{"code": 40, "value": 0}], [{"code": 40, "value": 2}],
                       [{"code": 40, "value": True}],
                       [{"code": 40, "value": 1}, {"code": 40, "value": 1}],
                       *([[{"code": 40, "value": 1}, {"code": code, "value": -2}]
                          for code in (50, 51, 52, 53, 54, 55, 99)])):
            with self.subTest(events=events), self.assertRaises(verify.VerificationError):
                self.check(dict(outcome("shadow-demo"), events=events))

    def test_shadow_requires_zero_file_io_and_correct_identity(self):
        for field, value in (("bulkBytesRead", 1), ("appId", "dev.ming.ui-demo"),
                             ("framesSubmitted", 0), ("exitCode", 1)):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                self.check(dict(outcome("shadow-demo"), **{field: value}))

    def test_shadow_requires_text_preparation_metadata(self):
        for value in (None, True, -1, 11, "0"):
            with self.subTest(value=value), self.assertRaises(verify.VerificationError):
                data = outcome("shadow-demo")
                if value is None:
                    del data["frame"]["textPrepareMicros"]
                else:
                    data["frame"]["textPrepareMicros"] = value
                self.check(data)

    def test_shadow_gpu_all_counter_fields_are_required_nonnegative_integers(self):
        names = ("shadowCacheHits", "shadowCacheMisses", "shadowCacheEvictions",
                 "shadowGenerationWork", "shadowParameterUploadBytes",
                 "shadowResidentBytes", "shadowEntries")
        for name in names:
            for value in (None, -1, True, 1.5, "1"):
                with self.subTest(name=name, value=value), self.assertRaises(verify.VerificationError):
                    data = outcome("shadow-demo", renderer="gpu")
                    if value is None:
                        del data["gpuStats"][name]
                    else:
                        data["gpuStats"][name] = value
                    self.check(data, renderer="gpu")

    def test_shadow_gpu_rejects_inconsistent_misses_entries_params_residency_and_work(self):
        mutations = (("shadowCacheMisses", 0), ("shadowCacheMisses", 9),
                     ("shadowEntries", 0), ("shadowEntries", 9), ("shadowEntries", 3),
                     ("shadowCacheEvictions", 3), ("shadowCacheHits", 9),
                     ("shadowParameterUploadBytes", 63), ("shadowParameterUploadBytes", 96),
                     ("shadowResidentBytes", 0), ("shadowResidentBytes", 3),
                     ("shadowResidentBytes", 2 * 1024 * 1024 + 4),
                     ("shadowGenerationWork", 0), ("shadowGenerationWork", 5),
                     ("shadowGenerationWork", 32_000_001))
        for field, value in mutations:
            with self.subTest(field=field, value=value), self.assertRaises(verify.VerificationError):
                data = outcome("shadow-demo", renderer="gpu")
                data["gpuStats"][field] = value
                self.check(data, renderer="gpu")

    def test_shadow_gpu_allows_warm_reuse_eviction_and_exact_resident_budget(self):
        data = outcome("shadow-demo", renderer="gpu", frames=3)
        data["gpuStats"].update(shadowCacheMisses=10, shadowCacheHits=14,
            shadowCacheEvictions=2, shadowEntries=8, shadowParameterUploadBytes=320,
            shadowResidentBytes=2 * 1024 * 1024)
        self.check(data, renderer="gpu")

    def test_shadow_gpu_must_not_upload_or_cache_images(self):
        for field in ("imageUploads", "imageUploadBytes", "imageCacheCompactions", "cachedImages"):
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                data = outcome("shadow-demo", renderer="gpu")
                data["gpuStats"][field] = 1
                self.check(data, renderer="gpu")

    def test_shadow_gpu_preserves_backend_close_sequence_and_readback_checks(self):
        for window in ("headless", "wayland"):
            for change in ("backend", "sequence", "close", "readback"):
                with self.subTest(window=window, change=change), self.assertRaises(verify.VerificationError):
                    data = outcome("shadow-demo", window, "gpu", frames=3)
                    if change == "backend":
                        data["frame"]["backend"] = "cpu-reference"
                    elif change == "sequence":
                        data["frame"]["sequence"] = 2
                    elif change == "close":
                        data["inputCounts"][0] = 0
                    else:
                        data["gpuStats"]["readbackBytes"] += 4
                    self.check(data, window, "gpu")


class ShadowInputTests(Fixture):
    def test_shadow_artifacts_are_fingerprinted_without_replacing_ui_demo(self):
        inputs = verify.collect_inputs(self.root)
        self.assertEqual(verify.APPS.get("shadow-demo"), (4, None))
        self.assertEqual(verify.APPS.get("ui-demo"), (4, None))
        for app in ("ui-demo", "shadow-demo"):
            for label, filename in (("manifest", "app.json"), ("wasm", "main.wasm")):
                self.assertEqual(inputs[app + "." + label], verify.file_record(
                    self.root / "artifacts" / app / filename, self.root))
            self.assertNotIn(app + ".grant", inputs)
        directory = self.root / "artifacts/shadow-demo"
        info = directory / "build-info.json"
        info.write_text(json.dumps({"packagedSha256": inputs["shadow-demo.wasm"]["sha256"]}), encoding="utf-8")
        self.assertEqual(verify.collect_inputs(self.root)["shadow-demo.build-info"],
                         verify.file_record(info, self.root))

    def test_shadow_manifest_is_required_and_strict(self):
        path = self.root / "artifacts/shadow-demo/app.json"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)
        for field, value in (("schemaVersion", 2), ("sdkVersion", 3),
                             ("id", "dev.ming.ui-demo"), ("entry", "../main.wasm")):
            with self.subTest(field=field):
                data = json.loads(original)
                data[field] = value
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(verify.VerificationError):
                    verify.collect_inputs(self.root)
        path.write_bytes(original)

    def test_shadow_wasm_and_optional_build_hash_are_strict(self):
        path = self.root / "artifacts/shadow-demo/main.wasm"
        original = path.read_bytes()
        for payload in (None, b"not wasm", b"\0asm\x02\0\0\0"):
            with self.subTest(payload=payload):
                path.unlink()
                if payload is not None:
                    path.write_bytes(payload)
                with self.assertRaises(verify.VerificationError):
                    verify.collect_inputs(self.root)
                path.write_bytes(original)
        path.with_name("build-info.json").write_text('{"packagedSha256":"wrong"}', encoding="utf-8")
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)


class ShadowWorkflowTests(Fixture):
    legacy_paths = ("headless-sdk1", "headless-sdk2", "headless-sdk3", "headless-sdk4",
                    "wayland-sdk2", "wayland-sdk3", "wayland-sdk4")
    shadow_paths = ("headless-shadow", "wayland-shadow")

    def test_explicit_path_mapping_keeps_both_sdk4_apps_and_old_seven_paths(self):
        self.assertEqual(verify.PATHS, self.legacy_paths + self.shadow_paths + ("headless-compose", "wayland-compose", "headless-animated", "wayland-animated"))
        self.assertEqual(verify.path_app("headless-sdk4"), "ui-demo")
        self.assertEqual(verify.path_app("wayland-sdk4"), "ui-demo")
        for name in self.shadow_paths:
            self.assertEqual(verify.path_app(name), "shadow-demo")
        with self.assertRaises(verify.VerificationError):
            verify.path_app("headless-unknown")
        self.assertEqual(set(verify.requested_paths("headless")),
                         set(self.legacy_paths[:4]) | {"headless-shadow", "headless-compose", "headless-animated"})
        self.assertEqual(set(verify.requested_paths("wayland")),
                         set(self.legacy_paths[4:]) | {"wayland-shadow", "wayland-compose", "wayland-animated"})

    def test_all_nine_paths_execute_on_cpu_and_gpu_and_revalidate(self):
        for renderer in ("cpu", "gpu"):
            with self.subTest(renderer=renderer):
                self.output = Path(self.temp.name) / renderer
                self.calls.clear()
                with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                    report = self.run_fixture(mode="all", renderer=renderer)
                self.assertEqual(report["status"], "passed", report["errors"])
                self.assertEqual(set(report["paths"]), set(self.legacy_paths + self.shadow_paths + ("headless-compose", "wayland-compose", "headless-animated", "wayland-animated")))
                for name in self.shadow_paths:
                    evidence = report["paths"][name]
                    self.assertEqual(evidence["status"], "passed")
                    self.assertEqual(evidence["outcome"]["appId"], "dev.ming.shadow-demo")
                    args = evidence["command"]["argv"]
                    self.assertEqual(Path(args[2]).resolve(),
                                     (self.root / "artifacts/shadow-demo/app.json").resolve())
                    self.assertNotIn("--grant", args)
                    self.assertIn("--snapshot", args)
                    self.assertEqual(args[1], "view" if name.startswith("wayland-") else "run")
                for name in ("headless-sdk4", "wayland-sdk4"):
                    self.assertEqual(report["paths"][name]["outcome"]["appId"], "dev.ming.ui-demo")
                self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_each_mode_includes_only_its_shadow_path(self):
        for mode in ("headless", "wayland"):
            self.output = Path(self.temp.name) / mode
            self.calls.clear()
            with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                report = self.run_fixture(mode=mode)
            self.assertEqual(report["status"], "passed", report["errors"])
            for name in self.shadow_paths:
                expected = "passed" if name.startswith(mode + "-") else "pending"
                self.assertEqual(report["paths"][name]["status"], expected)

    def test_shadow_process_timeout_json_identity_and_app_error_fail_never_skip(self):
        original = self.fake_runner
        for name in self.shadow_paths:
            for kind in ("exit", "timeout", "json", "identity", "app-error", "gpu-stats"):
                with self.subTest(name=name, kind=kind):
                    self.output = Path(self.temp.name) / (name + "-" + kind)
                    def runner(argv, **kwargs):
                        record = original(argv, **kwargs)
                        if kwargs["name"] != name:
                            return record
                        data = outcome("shadow-demo", "wayland" if name.startswith("wayland-") else "headless", "gpu")
                        if kind == "identity":
                            data["appId"] = "dev.ming.ui-demo"
                        elif kind == "app-error":
                            data["events"].append({"code": 50, "value": -2})
                        elif kind == "gpu-stats":
                            del data["gpuStats"]["shadowCacheMisses"]
                        return self.command_record(kwargs["output"], name, argv,
                            "not json" if kind == "json" else json.dumps(data),
                            7 if kind == "exit" else 0, kind == "timeout")
                    with mock.patch.object(verify.platform, "system", return_value="Linux"), \
                         mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                        report = verify.verify(self.root, self.output, mode="all", renderer="gpu",
                                               environ=self.env, runner=runner)
                    self.assertEqual(report["status"], "failed")
                    self.assertEqual(report["paths"][name]["status"], "failed")
                    self.assertTrue(all(report["paths"][old]["status"] == "passed" for old in self.legacy_paths))

    def test_shadow_missing_or_wrong_snapshot_fails_execution(self):
        original = self.fake_runner
        for name in self.shadow_paths:
            for missing in (False, True):
                self.output = Path(self.temp.name) / (name + str(missing))
                def runner(argv, **kwargs):
                    record = original(argv, **kwargs)
                    if kwargs["name"] == name:
                        snapshot = kwargs["output"] / (name + ".png")
                        if missing:
                            snapshot.unlink()
                        else:
                            snapshot.write_bytes(png())
                    return record
                with mock.patch.object(verify.platform, "system", return_value="Linux"), \
                     mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                    report = verify.verify(self.root, self.output, mode="all", environ=self.env, runner=runner)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["paths"][name]["status"], "failed")

    def test_shadow_revalidation_rechecks_gpu_policy_even_after_hashes_are_updated(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", renderer="gpu")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in self.shadow_paths:
            stdout = self.output / report["paths"][name]["command"]["stdout"]["path"]
            original = stdout.read_bytes()
            for field, value in (("shadowParameterUploadBytes", 0), ("shadowEntries", 0),
                                 ("shadowResidentBytes", 2 * 1024 * 1024 + 4),
                                 ("shadowGenerationWork", 0)):
                changed = copy.deepcopy(report)
                data = changed["paths"][name]["outcome"]
                data["gpuStats"][field] = value
                stdout.write_text(json.dumps(data), encoding="utf-8")
                changed["paths"][name]["command"]["stdout"] = verify.file_record(stdout, self.output)
                self.assertTrue(verify.validate_report(changed, self.root, self.output))
                stdout.write_bytes(original)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_shadow_missing_skipped_or_unverified_wayland_evidence_fails_revalidation(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in self.shadow_paths:
            for status in (None, "pending", "skipped"):
                changed = copy.deepcopy(report)
                if status is None:
                    del changed["paths"][name]
                else:
                    changed["paths"][name]["status"] = status
                self.assertTrue(verify.validate_report(changed, self.root, self.output))
        changed = copy.deepcopy(report)
        changed["paths"]["wayland-shadow"]["session"]["connectionVerified"] = False
        self.assertTrue(verify.validate_report(changed, self.root, self.output))

    def test_shadow_artifact_and_build_provenance_drift_invalidates_evidence(self):
        directory = self.root / "artifacts/shadow-demo"
        info = directory / "build-info.json"
        info.write_text(json.dumps({"packagedSha256": verify.file_record(directory / "main.wasm", self.root)["sha256"]}), encoding="utf-8")
        report = self.run_fixture()
        self.assertEqual(report["status"], "passed", report["errors"])
        for filename in ("app.json", "main.wasm", "build-info.json"):
            target = directory / filename
            original = target.read_bytes()
            target.write_bytes(original + b"drift")
            self.assertTrue(verify.validate_report(report, self.root, self.output))
            target.write_bytes(original)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])




class ComposeOutcomeTests(unittest.TestCase):
    def check(self, data, window="headless", renderer="cpu", snapshot=True):
        verify.validate_outcome(data, app="compose-demo", renderer=renderer,
                                window_system=window, grant=None, snapshot=snapshot)

    def test_compose_native_wasm_cpu_gpu_and_readback_matrix(self):
        for window in ("headless", "wayland"):
            for renderer in ("cpu", "gpu"):
                for snapshot in (False, True):
                    data = outcome("compose-demo", window, renderer, frames=3, snapshot=snapshot)
                    self.assertEqual(data["runtime"], "rust-wasmtime")
                    self.assertEqual((data["frame"]["width"], data["frame"]["height"]), (1100, 760))
                    self.check(data, window, renderer, snapshot)
                    if renderer == "gpu":
                        data["gpuStats"]["readbackBytes"] += 4
                        with self.assertRaises(verify.VerificationError):
                            self.check(data, window, renderer, snapshot)

    def test_compose_requires_completion_text_no_file_or_image_work(self):
        changes = (("events", []), ("events", [{"code": 40, "value": 2}]),
                   ("events", [{"code": 40, "value": 1}, {"code": 50, "value": -2}]),
                   ("bulkBytesRead", 1), ("appId", "dev.ming.shadow-demo"))
        for field, value in changes:
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                self.check(dict(outcome("compose-demo"), **{field: value}))
        data = outcome("compose-demo")
        del data["frame"]["textPrepareMicros"]
        with self.assertRaises(verify.VerificationError):
            self.check(data)
        for field in ("imageUploads", "imageUploadBytes", "imageCacheCompactions", "cachedImages"):
            data = outcome("compose-demo", renderer="gpu")
            data["gpuStats"][field] = 1
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                self.check(data, renderer="gpu")

    def test_compose_gpu_requires_glyph_and_shadow_evidence_and_shared_style_reuse(self):
        data = outcome("compose-demo", renderer="gpu", frames=3)
        self.assertEqual(data["gpuStats"]["shadowCacheMisses"], 1)
        self.assertEqual(data["gpuStats"]["shadowEntries"], 1)
        self.check(data, renderer="gpu")
        fields = ("glyphUploads", "glyphUploadBytes", "glyphResidentBytes", "glyphEntries",
                  "shadowCacheHits", "shadowCacheMisses", "shadowCacheEvictions",
                  "shadowGenerationWork", "shadowParameterUploadBytes", "shadowResidentBytes", "shadowEntries")
        for field in fields:
            for value in (None, True, -1, "1"):
                changed = copy.deepcopy(data)
                if value is None:
                    del changed["gpuStats"][field]
                else:
                    changed["gpuStats"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(verify.VerificationError):
                    self.check(changed, renderer="gpu")
        for field, value in (("shadowCacheMisses", 0), ("shadowEntries", 0),
                             ("shadowParameterUploadBytes", 64), ("shadowGenerationWork", 0),
                             ("shadowResidentBytes", 2 * 1024 * 1024 + 4), ("glyphUploads", 0)):
            changed = copy.deepcopy(data)
            changed["gpuStats"][field] = value
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                self.check(changed, renderer="gpu")

    def test_compose_wayland_resize_and_cpu_without_gpu_statistics(self):
        for renderer in ("cpu", "gpu"):
            data = outcome("compose-demo", "wayland", renderer, frames=3)
            data["frame"].update(width=960, height=640)
            data["presentedFrame"].update(width=960, height=640)
            if renderer == "gpu":
                data["gpuStats"]["readbackBytes"] = 960 * 640 * 4
            else:
                self.assertNotIn("gpuStats", data)
            self.check(data, "wayland", renderer)


class ComposeInputTests(Fixture):
    def test_compose_package_is_sdk4_fingerprinted_and_does_not_replace_other_apps(self):
        inputs = verify.collect_inputs(self.root)
        for app in ("ui-demo", "shadow-demo", "compose-demo"):
            self.assertEqual(verify.APPS[app], (4, None))
            for label, filename in (("manifest", "app.json"), ("wasm", "main.wasm")):
                self.assertEqual(inputs[app + "." + label], verify.file_record(
                    self.root / "artifacts" / app / filename, self.root))
            self.assertNotIn(app + ".grant", inputs)
        info = self.root / "artifacts/compose-demo/build-info.json"
        info.write_text(json.dumps({"packagedSha256": inputs["compose-demo.wasm"]["sha256"]}), encoding="utf-8")
        self.assertIn("compose-demo.build-info", verify.collect_inputs(self.root))

    def test_compose_missing_invalid_artifacts_or_provenance_fail_not_skip(self):
        manifest = self.root / "artifacts/compose-demo/app.json"
        original = manifest.read_bytes()
        manifest.unlink()
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)
        for field, value in (("sdkVersion", 3), ("id", "dev.ming.ui-demo"), ("entry", "../main.wasm")):
            data = json.loads(original)
            data[field] = value
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(verify.VerificationError):
                verify.collect_inputs(self.root)
        manifest.write_bytes(original)
        info = manifest.with_name("build-info.json")
        info.write_text('{"packagedSha256":"wrong"}', encoding="utf-8")
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)


class ComposeWorkflowTests(Fixture):
    def test_compose_paths_preserve_all_nine_and_map_to_the_right_sdk4_apps(self):
        old = ("headless-sdk1", "headless-sdk2", "headless-sdk3", "headless-sdk4",
               "wayland-sdk2", "wayland-sdk3", "wayland-sdk4", "headless-shadow", "wayland-shadow")
        self.assertEqual(verify.PATHS, old + ("headless-compose", "wayland-compose", "headless-animated", "wayland-animated"))
        for suffix, app in (("sdk4", "ui-demo"), ("shadow", "shadow-demo"), ("compose", "compose-demo")):
            for window in ("headless", "wayland"):
                self.assertEqual(verify.path_app(window + "-" + suffix), app)
        for profile in ("debug", "release"):
            self.output = Path(self.temp.name) / profile
            with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                report = self.run_fixture(mode="all", renderer="gpu", build_profile=profile)
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(len(report["paths"]), 13)
            self.assertTrue(all(p["status"] == "passed" for p in report["paths"].values()))
            self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_compose_explicit_60s_300m_fuel_even_with_shorter_legacy_timeout(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", run_timeout=2, auto_close_ms=1000)
        self.assertEqual(report["status"], "passed", report["errors"])
        for name, args, env, timeout in self.calls:
            if name in ("headless-compose", "wayland-compose", "headless-animated", "wayland-animated"):
                self.assertEqual(args[args.index("--timeout-ms") + 1], "60000")
                self.assertEqual(args[args.index("--fuel") + 1], "300000000")
                self.assertEqual(timeout, 65)
                self.assertNotIn("--grant", args)
            elif name in verify.PATHS:
                self.assertEqual(args[args.index("--timeout-ms") + 1], "2000")
                self.assertNotIn("--fuel", args)

    def test_compose_timeout_fuel_command_and_outcome_tampering_is_rejected(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in ("headless-compose", "wayland-compose"):
            for kind in ("timeout", "fuel", "missing-fuel", "duplicate-fuel", "process-timeout", "wrong-app", "wrong-binary"):
                changed = copy.deepcopy(report)
                command = changed["paths"][name]["command"]
                argv = command["argv"]
                if kind in ("timeout", "fuel"):
                    argv[argv.index("--timeout-ms" if kind == "timeout" else "--fuel") + 1] = "1"
                elif kind == "missing-fuel":
                    index = argv.index("--fuel")
                    del argv[index:index + 2]
                elif kind == "duplicate-fuel":
                    argv.extend(["--fuel", "300000000"])
                elif kind == "process-timeout":
                    command["timeoutSeconds"] = 7
                elif kind == "wrong-app":
                    argv[2] = str(self.root / "artifacts/shadow-demo/app.json")
                else:
                    argv[0] = "/tmp/unverified-host"
                with self.subTest(name=name, kind=kind):
                    self.assertTrue(verify.validate_report(changed, self.root, self.output))

    def test_compose_failed_execution_is_failed_not_skipped(self):
        original = self.fake_runner
        for name in ("headless-compose", "wayland-compose"):
            self.output = Path(self.temp.name) / name
            def runner(argv, **kwargs):
                result = original(argv, **kwargs)
                if kwargs["name"] == name:
                    result["timedOut"] = True
                return result
            with mock.patch.object(verify.platform, "system", return_value="Linux"), \
                 mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                report = verify.verify(self.root, self.output, mode="all", environ=self.env, runner=runner)
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["paths"][name]["status"], "failed")


class BuildProfileTests(Fixture):
    def test_default_debug_keeps_existing_commands_and_binary(self):
        report = self.run_fixture()
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(report["buildProfile"], "debug")
        self.assertEqual(report["binary"]["path"], "ming-app-host")
        self.assertTrue(report["binaryBuild"]["sourcePath"].replace("\\", "/").endswith(
            "/x86_64-unknown-linux-gnu/debug/ming-app-host"))
        for c in report["checks"][-2:]:
            self.assertNotIn("--release", c["argv"])
        self.assertEqual((self.output / report["binary"]["path"]).read_bytes(), b"fixture native binary")

    def test_release_build_and_test_are_explicit_and_copy_the_release_binary(self):
        for renderer in ("cpu", "gpu"):
            self.output = Path(self.temp.name) / renderer
            report = self.run_fixture(renderer=renderer, build_profile="release")
            self.assertEqual(report["status"], "passed", report["errors"])
            self.assertEqual(report["buildProfile"], "release")
            self.assertEqual(report["binary"]["path"], "ming-app-host-release")
            self.assertTrue(report["binaryBuild"]["sourcePath"].replace("\\", "/").endswith(
                "/x86_64-unknown-linux-gnu/release/ming-app-host"))
            for c in report["checks"][-2:]:
                self.assertEqual(c["argv"].count("--release"), 1)
                self.assertEqual(c["argv"][c["argv"].index("--target") + 1], "x86_64-unknown-linux-gnu")
            self.assertEqual((self.output / report["binary"]["path"]).read_bytes(), b"fixture release binary")
            self.assertEqual(report["binaryBuild"]["sha256"], report["binary"]["sha256"])
            self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_release_never_falls_back_to_an_existing_debug_binary(self):
        original = self.fake_runner
        def runner(argv, **kwargs):
            result = original(argv, **kwargs)
            if kwargs["name"] == "build":
                target = Path(kwargs["env"]["CARGO_TARGET_DIR"]) / "x86_64-unknown-linux-gnu"
                (target / "release/ming-app-host").unlink()
                debug = target / "debug/ming-app-host"
                debug.parent.mkdir(parents=True, exist_ok=True)
                debug.write_bytes(b"unrelated debug binary")
            return result
        with mock.patch.object(verify.platform, "system", return_value="Linux"):
            report = verify.verify(self.root, self.output, build_profile="release", environ=self.env, runner=runner)
        self.assertEqual(report["status"], "failed")
        self.assertTrue(all(p["status"] == "pending" for p in report["paths"].values()))

    def test_profile_commands_source_copy_and_target_are_bound_during_revalidation(self):
        report = self.run_fixture(build_profile="release")
        self.assertEqual(report["status"], "passed", report["errors"])
        for kind in ("missing", "debug", "invalid", "build-flag", "test-flag", "duplicate-flag",
                     "custom-profile", "source-debug", "source-hash", "copy-path", "target", "copied-record"):
            changed = copy.deepcopy(report)
            if kind == "missing":
                del changed["buildProfile"]
            elif kind in ("debug", "invalid"):
                changed["buildProfile"] = kind
            elif kind in ("build-flag", "test-flag"):
                changed["checks"][-2 if kind == "build-flag" else -1]["argv"].remove("--release")
            elif kind == "duplicate-flag":
                changed["checks"][-1]["argv"].append("--release")
            elif kind == "custom-profile":
                changed["checks"][-1]["argv"].extend(["--profile", "dev"])
            elif kind == "source-debug":
                changed["binaryBuild"]["sourcePath"] = changed["binaryBuild"]["sourcePath"].replace("release", "debug")
            elif kind == "source-hash":
                changed["binaryBuild"]["sha256"] = "0" * 64
            elif kind == "copy-path":
                changed["binaryBuild"]["copiedPath"] = "/tmp/ming-app-host"
            elif kind == "target":
                changed["nativeTarget"] = "wasm32-wasip1"
            else:
                debug = self.output / "ming-app-host"
                debug.write_bytes(b"fixture native binary")
                changed["binary"] = verify.file_record(debug, self.output)
            with self.subTest(kind=kind):
                self.assertTrue(verify.validate_report(changed, self.root, self.output))

    def test_release_saved_evidence_remains_recheckable_after_move(self):
        report = self.run_fixture(build_profile="release")
        self.assertEqual(report["status"], "passed", report["errors"])
        moved = self.output.with_name("moved release evidence")
        self.output.rename(moved)
        self.assertEqual(verify.validate_report(report, self.root, moved), [])

    def test_unknown_profile_is_rejected_before_any_commands(self):
        with self.assertRaises(verify.VerificationError):
            self.run_fixture(build_profile="dev")
        self.assertEqual(self.calls, [])

    def test_verifier_cli_default_release_and_invalid_choice(self):
        for profile in ("debug", "release"):
            args = ["--output", str(self.output)] + (["--build-profile", profile] if profile == "release" else [])
            with mock.patch.object(verify, "verify", return_value={"status": "passed"}) as run, \
                 mock.patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(verify.main(args), 0)
            self.assertEqual(run.call_args.kwargs["build_profile"], profile)
        with mock.patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit) as result:
            verify.main(["--build-profile", "dev"])
        self.assertEqual(result.exception.code, 2)


class ComposeContainerTests(Fixture):
    def setUp(self):
        super().setUp()
        spec = importlib.util.spec_from_file_location("compose_linux_container", SCRIPT.with_name("linux-container.py"))
        self.container = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.container)

    def test_docker_create_profile_and_owned_cache_mounts(self):
        for profile in ("debug", "release"):
            plan = self.container.docker_commands(self.root, self.root / "artifacts/linux-profile", "fixture", build_profile=profile)
            argv = plan["create"]
            self.assertEqual(argv.count("--build-profile"), 1)
            self.assertEqual(argv[argv.index("--build-profile") + 1], profile)
            self.assertIn("type=volume,source=" + plan["volume"] + ",target=/ming-cache", argv)
            self.assertTrue(any("target=/workspace,readonly" in a for a in argv))

    def test_all_features_check_uses_the_same_profile_compiler_and_isolation(self):
        for profile in ("debug", "release"):
            self.output = Path(self.temp.name) / profile
            report = self.run_fixture(build_profile=profile)
            calls = []
            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                return self.fake_runner(argv, **kwargs)
            result = self.container.check_all_features(self.root, self.output, self.env, report,
                timeout=600, build_profile=profile, runner=runner)
            argv, kwargs = calls[0]
            self.assertEqual("--release" in argv, profile == "release")
            self.assertEqual(result["buildProfile"], profile)
            self.assertIn("--all-features", argv)
            self.assertIn("--all-targets", argv)
            self.assertEqual(kwargs["env"]["RUSTC"], report["compiler"]["path"])
            self.assertFalse(Path(kwargs["cwd"]).is_relative_to(self.root.resolve()))
        with self.assertRaises(self.container.acceptance.VerificationError):
            self.container.check_all_features(self.root, self.output, self.env, report,
                timeout=600, build_profile="debug", runner=runner)

    def test_container_inside_cli_forwards_build_profile(self):
        for profile in ("debug", "release"):
            args = ["--inside", "--output", str(self.output)] + (["--build-profile", profile] if profile == "release" else [])
            with mock.patch.object(self.container, "inside", return_value=0) as run:
                self.assertEqual(self.container.main(args), 0)
            self.assertEqual(run.call_args.kwargs["build_profile"], profile)

    def test_inside_passes_release_to_verify_and_all_feature_check(self):
        output = Path(self.temp.name) / "inside"
        output.mkdir()
        record = self.command_record(output, "fake", ["fixture"], "unit fixture")
        report = {"status": "passed", "errors": [], "paths": {}, "buildProfile": "release"}
        with mock.patch.object(self.container.acceptance, "run_command", return_value=record) as command, \
             mock.patch.object(self.container.acceptance, "verify", return_value=report) as run, \
             mock.patch.object(self.container, "check_all_features", return_value={"command": record}) as check, \
             mock.patch.object(self.container.acceptance, "validate_report", return_value=[]), \
             mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(self.container.inside(output, "headless", "gpu", 900, build_profile="release"), 0)
        self.assertEqual(run.call_args.kwargs["build_profile"], "release")
        self.assertEqual(check.call_args.kwargs["build_profile"], "release")
        fixture_call = next(c for c in command.call_args_list if c.kwargs["name"] == "fixture-tests")
        self.assertEqual(fixture_call.kwargs["timeout"], 900)

    def test_outer_cli_forwards_release_and_preserves_cleanup_on_timeout(self):
        output = self.root / "artifacts/linux-profile-timeout"
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return self.command_record(kwargs["output"], kwargs["name"], argv, "fixture",
                code=137 if argv[1] == "start" else 0, timed_out=argv[1] == "start")
        with mock.patch.object(self.container, "__file__", str(self.root / "scripts/linux-container.py")), \
             mock.patch.object(self.container.acceptance, "run_command", side_effect=runner), \
             mock.patch.object(self.container, "ensure_stopped", return_value={"stopped": True}) as cleanup, \
             mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(self.container.main(["--output", str(output), "--build-profile", "release"]), 1)
        argv = next(a for a in calls if a[1] == "create")
        self.assertEqual(argv[argv.index("--build-profile") + 1], "release")
        cleanup.assert_called_once()

    def test_outer_cli_rejects_report_profile_mismatch(self):
        output = self.root / "artifacts/linux-profile-mismatch"
        def runner(argv, **kwargs):
            return self.command_record(kwargs["output"], kwargs["name"], argv, "fixture")
        with mock.patch.object(self.container, "__file__", str(self.root / "scripts/linux-container.py")), \
             mock.patch.object(self.container.acceptance, "run_command", side_effect=runner), \
             mock.patch.object(self.container.acceptance, "read_json", return_value={"buildProfile": "debug"}), \
             mock.patch.object(self.container.acceptance, "validate_report", return_value=[]), \
             mock.patch.object(self.container, "ensure_stopped", return_value={"stopped": True}), \
             mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(self.container.main(["--output", str(output), "--build-profile", "release"]), 1)




class PresentationIdentityTests(unittest.TestCase):
    graphics_apps = ("image-viewer", "text-demo", "ui-demo", "shadow-demo", "compose-demo", "animated-demo")

    def check(self, data, app, renderer="cpu", window="headless"):
        verify.validate_outcome(data, app=app, renderer=renderer, window_system=window,
                                grant=b"Hello fixture!\n" if app == "read-file" else None)

    def test_headless_presented_frame_must_be_explicit_null_for_all_apps(self):
        for app in ("read-file", *self.graphics_apps):
            for renderer in ("cpu", "gpu"):
                with self.subTest(app=app, renderer=renderer):
                    data = outcome(app, renderer=renderer, frames=2)
                    self.assertIn("presentedFrame", data)
                    self.assertIsNone(data["presentedFrame"])
                    self.check(data, app, renderer)
                    del data["presentedFrame"]
                    with self.assertRaises(verify.VerificationError):
                        self.check(data, app, renderer)

    def test_headless_rejects_forged_identity_even_when_presented_count_is_zero(self):
        for app in ("read-file", "ui-demo"):
            for value in ({"sequence": 2, "width": 1100, "height": 760}, {}, [], False, 0, "null"):
                data = outcome(app)
                data["presentedFrame"] = value
                with self.subTest(app=app, value=value), self.assertRaises(verify.VerificationError):
                    self.check(data, app)

    def test_wayland_identity_is_required_and_must_be_an_object(self):
        for renderer in ("cpu", "gpu"):
            for value in (None, [], "frame", 1, False):
                data = outcome("ui-demo", "wayland", renderer, frames=2)
                data["presentedFrame"] = value
                with self.subTest(renderer=renderer, value=value), self.assertRaises(verify.VerificationError):
                    self.check(data, "ui-demo", renderer, "wayland")
            del data["presentedFrame"]
            with self.assertRaises(verify.VerificationError):
                self.check(data, "ui-demo", renderer, "wayland")

    def test_wayland_identity_fields_are_exact_positive_integers(self):
        for renderer in ("cpu", "gpu"):
            for field in ("sequence", "width", "height"):
                for value in (None, True, False, 0, -1, 2.0, 1100.0, 760.0, "2"):
                    data = outcome("ui-demo", "wayland", renderer, frames=2)
                    if value is None:
                        del data["presentedFrame"][field]
                    else:
                        data["presentedFrame"][field] = value
                    with self.subTest(renderer=renderer, field=field, value=value), self.assertRaises(verify.VerificationError):
                        self.check(data, "ui-demo", renderer, "wayland")

    def test_wayland_rejects_stale_future_or_wrong_logical_dimensions(self):
        for app in self.graphics_apps:
            for renderer in ("cpu", "gpu"):
                for field, value in (("sequence", 1), ("sequence", 3), ("width", 960), ("height", 640)):
                    data = outcome(app, "wayland", renderer, frames=2)
                    data["presentedFrame"][field] = value
                    with self.subTest(app=app, renderer=renderer, field=field, value=value), self.assertRaises(verify.VerificationError):
                        self.check(data, app, renderer, "wayland")

    def test_coalesced_two_submissions_one_present_accepts_latest_identity(self):
        for app in self.graphics_apps:
            for renderer in ("cpu", "gpu"):
                data = outcome(app, "wayland", renderer, frames=2)
                data["framesPresented"] = 1
                data["inputCounts"][1] = 1
                if renderer == "gpu":
                    data["gpuStats"]["surfacePresentations"] = 1
                self.assertEqual(data["presentedFrame"]["sequence"], 2)
                self.check(data, app, renderer, "wayland")

    def test_zero_present_count_cannot_be_replaced_by_latest_frame_identity(self):
        for renderer in ("cpu", "gpu"):
            data = outcome("compose-demo", "wayland", renderer, frames=2)
            data["framesPresented"] = 0
            if renderer == "gpu":
                data["gpuStats"]["surfacePresentations"] = 0
            with self.assertRaises(verify.VerificationError):
                self.check(data, "compose-demo", renderer, "wayland")


class PresentationIdentityWorkflowTests(Fixture):
    def test_saved_evidence_accepts_coalescing_and_rechecks_identity_after_updated_hashes(self):
        original = self.fake_runner
        def runner(argv, **kwargs):
            record = original(argv, **kwargs)
            if kwargs["name"] in ("wayland-sdk2", "wayland-sdk3"):
                app = "image-viewer" if kwargs["name"] == "wayland-sdk2" else "text-demo"
                data = outcome(app, "wayland", "gpu", frames=2)
                data["framesPresented"] = 1
                data["inputCounts"][1] = 1
                data["gpuStats"]["surfacePresentations"] = 1
                stdout = kwargs["output"] / record["stdout"]["path"]
                stdout.write_text(json.dumps(data), encoding="utf-8")
                record["stdout"] = verify.file_record(stdout, kwargs["output"])
            return record
        with mock.patch.object(verify.platform, "system", return_value="Linux"), \
             mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = verify.verify(self.root, self.output, mode="all", renderer="gpu", environ=self.env, runner=runner)
        self.assertEqual(report["status"], "passed", report["errors"])
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])
        for name in ("wayland-sdk2", "wayland-sdk3", "headless-compose"):
            evidence = report["paths"][name]
            if name.startswith("wayland-"):
                self.assertEqual((evidence["outcome"]["framesSubmitted"], evidence["outcome"]["framesPresented"]), (2, 1))
            stdout = self.output / evidence["command"]["stdout"]["path"]
            original_bytes = stdout.read_bytes()
            for kind in ("missing", "null", "stale", "width", "height", "forged-headless"):
                if name.startswith("headless-") and kind != "forged-headless":
                    continue
                if name.startswith("wayland-") and kind == "forged-headless":
                    continue
                changed = copy.deepcopy(report)
                data = changed["paths"][name]["outcome"]
                if kind == "missing":
                    del data["presentedFrame"]
                elif kind == "null":
                    data["presentedFrame"] = None
                elif kind == "stale":
                    data["presentedFrame"]["sequence"] = 1
                elif kind in ("width", "height"):
                    data["presentedFrame"][kind] += 1
                else:
                    data["presentedFrame"] = {field: data["frame"][field] for field in ("sequence", "width", "height")}
                stdout.write_text(json.dumps(data), encoding="utf-8")
                changed["paths"][name]["command"]["stdout"] = verify.file_record(stdout, self.output)
                with self.subTest(name=name, kind=kind):
                    self.assertTrue(verify.validate_report(changed, self.root, self.output))
                stdout.write_bytes(original_bytes)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])




class AnimatedJSONOutcomeTests(unittest.TestCase):
    clock_counters = ("reads", "waits", "frames", "timers", "inputs", "closes", "cancels", "pending")

    def data(self, window="headless", renderer="cpu", frames=25, snapshot=True):
        # JSON round-trip fixtures preserve malicious scalar types, not Python coercions.
        return json.loads(json.dumps(outcome("animated-demo", window, renderer,
                                            frames=frames, snapshot=snapshot)))

    def check(self, data, window="headless", renderer="cpu", snapshot=True):
        verify.validate_outcome(data, app="animated-demo", renderer=renderer,
                                window_system=window, grant=None, snapshot=snapshot)

    def test_animated_cpu_gpu_headless_wayland_25_frame_json_fixtures(self):
        for window in ("headless", "wayland"):
            for renderer in ("cpu", "gpu"):
                for snapshot in (False, True):
                    data = self.data(window, renderer, snapshot=snapshot)
                    if window == "wayland":
                        data["framesPresented"] = 1
                        if renderer == "gpu":
                            data["gpuStats"]["surfacePresentations"] = 1
                        self.assertEqual(data["presentedFrame"]["sequence"], 25)
                    else:
                        self.assertIsNone(data["presentedFrame"])
                    self.check(data, window, renderer, snapshot)

    def test_animated_clock_object_and_exact_source_are_mandatory(self):
        for value in (None, [], False, 0, "clock"):
            data = self.data()
            data["frameClock"] = value
            with self.subTest(value=value), self.assertRaises(verify.VerificationError):
                self.check(data)
        data = self.data()
        del data["frameClock"]
        with self.assertRaises(verify.VerificationError):
            self.check(data)
        for source in (None, True, "vsync", "physical-scanout", "javascript", "host-monotonic"):
            data = self.data()
            if source is None:
                del data["frameClock"]["source"]
            else:
                data["frameClock"]["source"] = source
            with self.subTest(source=source), self.assertRaises(verify.VerificationError):
                self.check(data)

    def test_all_animated_clock_counters_are_required_exact_unsigned_u64(self):
        for name in self.clock_counters:
            for value in (None, -1, True, False, 1.0, "1", 2**64):
                data = self.data()
                if value is None:
                    del data["frameClock"][name]
                else:
                    data["frameClock"][name] = value
                # Feed exactly the shape delivered by JSON host stdout.
                data = json.loads(json.dumps(data))
                with self.subTest(name=name, value=value), self.assertRaises(verify.VerificationError):
                    self.check(data)

    def test_clock_requires_reads_waits_and_two_opportunities_but_timer_can_be_zero(self):
        for name, value in (("reads", 0), ("waits", 0), ("frames", 0), ("frames", 1)):
            data = self.data()
            data["frameClock"][name] = value
            with self.subTest(name=name, value=value), self.assertRaises(verify.VerificationError):
                self.check(data)
        data = self.data()
        self.assertEqual(data["frameClock"]["timers"], 0)
        self.check(data)

    def test_clock_opportunities_are_independent_of_submissions_and_presentations(self):
        for submitted, opportunities in ((25, 2), (2, 40)):
            data = self.data("wayland", "gpu", frames=submitted)
            data["frameClock"].update(reads=1, waits=50, frames=opportunities, timers=0)
            data["framesPresented"] = 1
            data["gpuStats"]["surfacePresentations"] = 1
            self.assertNotEqual(opportunities, submitted)
            self.check(data, "wayland", "gpu")

    def test_animated_submission_completion_identity_and_no_file_io_remain_strict(self):
        for frames in (0, 1):
            with self.subTest(frames=frames), self.assertRaises(verify.VerificationError):
                self.check(self.data(frames=frames))
        for field, value in (("events", []), ("events", [{"code": 40, "value": 24}]),
                             ("events", [{"code": 40, "value": 25}, {"code": 50, "value": -2}]),
                             ("bulkBytesRead", 1), ("appId", "dev.ming.compose-demo")):
            data = self.data()
            data[field] = value
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                self.check(data)

    def test_animated_final_frame_and_presented_frame_sequence_anchors_are_not_clock_counts(self):
        for target, field, value in (("frame", "sequence", 24), ("frame", "sequence", 30),
                                     ("presentedFrame", "sequence", 24), ("presentedFrame", "sequence", 30),
                                     ("presentedFrame", "width", 960), ("presentedFrame", "height", 640),
                                     ("presentedFrame", "sequence", 25.0)):
            data = self.data("wayland", "gpu")
            data[target][field] = value
            with self.subTest(target=target, field=field, value=value), self.assertRaises(verify.VerificationError):
                self.check(data, "wayland", "gpu")
        data = self.data()
        data["presentedFrame"] = {field: data["frame"][field] for field in ("sequence", "width", "height")}
        with self.assertRaises(verify.VerificationError):
            self.check(data)

    def test_animated_gpu_glyph_shadow_image_and_readback_policies_are_preserved(self):
        data = self.data(renderer="gpu")
        self.assertEqual(data["gpuStats"]["shadowCacheMisses"], 1)
        self.assertEqual(data["gpuStats"]["shadowEntries"], 1)
        self.check(data, renderer="gpu")
        for field, value in (("glyphUploads", 0), ("glyphEntries", 0),
                             ("shadowCacheMisses", 0), ("shadowEntries", 0),
                             ("shadowParameterUploadBytes", 64), ("shadowGenerationWork", 0),
                             ("shadowResidentBytes", 2 * 1024 * 1024 + 4),
                             ("imageUploads", 1), ("imageUploadBytes", 1), ("cachedImages", 1),
                             ("readbackBytes", 0)):
            changed = copy.deepcopy(data)
            changed["gpuStats"][field] = value
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                self.check(changed, renderer="gpu")
        for field in ("textPrepareMicros",):
            changed = self.data()
            del changed["frame"][field]
            with self.assertRaises(verify.VerificationError):
                self.check(changed)


class AnimatedInputTests(Fixture):
    def test_sdk5_package_mapping_and_build_info_do_not_replace_sdk4(self):
        inputs = verify.collect_inputs(self.root)
        self.assertEqual(verify.APPS["animated-demo"], (5, None))
        for app in ("ui-demo", "shadow-demo", "compose-demo"):
            self.assertEqual(verify.APPS[app], (4, None))
        for label, filename in (("manifest", "app.json"), ("wasm", "main.wasm")):
            self.assertEqual(inputs["animated-demo." + label], verify.file_record(
                self.root / "artifacts/animated-demo" / filename, self.root))
        self.assertNotIn("animated-demo.grant", inputs)
        info = self.root / "artifacts/animated-demo/build-info.json"
        info.write_text(json.dumps({"packagedSha256": inputs["animated-demo.wasm"]["sha256"]}), encoding="utf-8")
        self.assertIn("animated-demo.build-info", verify.collect_inputs(self.root))

    def test_sdk5_missing_wrong_manifest_wasm_and_build_hash_fail_not_skip(self):
        path = self.root / "artifacts/animated-demo/app.json"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)
        for field, value in (("sdkVersion", 4), ("id", "dev.ming.compose-demo"), ("entry", "../main.wasm")):
            data = json.loads(original)
            data[field] = value
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.subTest(field=field), self.assertRaises(verify.VerificationError):
                verify.collect_inputs(self.root)
        path.write_bytes(original)
        wasm = path.with_name("main.wasm")
        payload = wasm.read_bytes()
        wasm.write_bytes(b"not wasm")
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)
        wasm.write_bytes(payload)
        path.with_name("build-info.json").write_text('{"packagedSha256":"wrong"}', encoding="utf-8")
        with self.assertRaises(verify.VerificationError):
            verify.collect_inputs(self.root)


class AnimatedWorkflowTests(Fixture):
    def test_thirteen_paths_keep_the_old_eleven_and_profiles_on_cpu_gpu(self):
        old = ("headless-sdk1", "headless-sdk2", "headless-sdk3", "headless-sdk4",
               "wayland-sdk2", "wayland-sdk3", "wayland-sdk4", "headless-shadow", "wayland-shadow",
               "headless-compose", "wayland-compose")
        self.assertEqual(verify.PATHS, old + ("headless-animated", "wayland-animated"))
        for renderer in ("cpu", "gpu"):
            for profile in ("debug", "release"):
                self.output = Path(self.temp.name) / (renderer + profile)
                with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                    report = self.run_fixture(mode="all", renderer=renderer, build_profile=profile)
                self.assertEqual(report["status"], "passed", report["errors"])
                self.assertEqual(len(report["paths"]), 13)
                for name in old:
                    self.assertEqual(report["paths"][name]["status"], "passed")
                for name in ("headless-animated", "wayland-animated"):
                    self.assertEqual(report["paths"][name]["outcome"]["appId"], "dev.ming.animated-demo")
                    self.assertEqual(report["paths"][name]["outcome"]["framesSubmitted"], 25)
                self.assertEqual(verify.path_app("headless-sdk4"), "ui-demo")
                self.assertEqual(verify.path_app("wayland-shadow"), "shadow-demo")
                self.assertEqual(verify.path_app("wayland-compose"), "compose-demo")
                self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_animated_and_compose_have_explicit_budgets_no_event_script_and_native_lifetime(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", build_profile="release")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name, args, _, timeout in self.calls:
            if name in ("headless-animated", "wayland-animated", "headless-compose", "wayland-compose"):
                self.assertEqual(args[args.index("--timeout-ms") + 1], "60000")
                self.assertEqual(args[args.index("--fuel") + 1], "300000000")
                self.assertEqual(timeout, 65)
                self.assertNotIn("--events", args)
                self.assertNotIn("--grant", args)
                if name.startswith("wayland-"):
                    self.assertEqual(args[args.index("--auto-close-ms") + 1], "15000")
                else:
                    self.assertNotIn("--auto-close-ms", args)

    def test_animated_budget_arguments_are_bound_and_forgery_fails(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in ("headless-animated", "wayland-animated"):
            for kind in ("fuel", "timeout", "missing-fuel", "duplicate-fuel", "process-timeout"):
                changed = copy.deepcopy(report)
                command = changed["paths"][name]["command"]
                argv = command["argv"]
                if kind in ("fuel", "timeout"):
                    argv[argv.index("--fuel" if kind == "fuel" else "--timeout-ms") + 1] = "1"
                elif kind == "missing-fuel":
                    i = argv.index("--fuel")
                    del argv[i:i+2]
                elif kind == "duplicate-fuel":
                    argv.extend(["--fuel", "300000000"])
                else:
                    command["timeoutSeconds"] = 7
                with self.subTest(name=name, kind=kind):
                    self.assertTrue(verify.validate_report(changed, self.root, self.output))

    def test_animated_saved_json_rechecks_clock_and_strong_final_frame_anchors(self):
        with mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
            report = self.run_fixture(mode="all", renderer="gpu", build_profile="release")
        self.assertEqual(report["status"], "passed", report["errors"])
        for name in ("headless-animated", "wayland-animated"):
            evidence = report["paths"][name]
            stdout = self.output / evidence["command"]["stdout"]["path"]
            original = stdout.read_bytes()
            for kind in ("missing-clock", "source", "reads", "waits", "frames", "timer-type", "sequence", "presentation"):
                changed = copy.deepcopy(report)
                data = changed["paths"][name]["outcome"]
                if kind == "missing-clock":
                    del data["frameClock"]
                elif kind in ("source", "reads", "waits", "frames"):
                    data["frameClock"][kind] = "vsync" if kind == "source" else 0
                elif kind == "timer-type":
                    data["frameClock"]["timers"] = True
                elif kind == "sequence":
                    data["frame"]["sequence"] = 30  # clock opportunities, not final submitted sequence 25
                elif name.startswith("wayland-"):
                    data["presentedFrame"]["sequence"] = 24
                else:
                    data["presentedFrame"] = {f: data["frame"][f] for f in ("sequence", "width", "height")}
                stdout.write_text(json.dumps(data), encoding="utf-8")
                changed["paths"][name]["command"]["stdout"] = verify.file_record(stdout, self.output)
                with self.subTest(name=name, kind=kind):
                    self.assertTrue(verify.validate_report(changed, self.root, self.output))
                stdout.write_bytes(original)
        self.assertEqual(verify.validate_report(report, self.root, self.output), [])

    def test_animated_process_timeout_or_missing_snapshot_fail_without_skip(self):
        original = self.fake_runner
        for name in ("headless-animated", "wayland-animated"):
            for kind in ("exit", "timeout", "missing-snapshot"):
                self.output = Path(self.temp.name) / (name + kind)
                def runner(argv, **kwargs):
                    result = original(argv, **kwargs)
                    if kwargs["name"] == name:
                        if kind == "exit":
                            result["returnCode"] = 7
                        elif kind == "timeout":
                            result["timedOut"] = True
                        else:
                            (kwargs["output"] / (name + ".png")).unlink()
                    return result
                with mock.patch.object(verify.platform, "system", return_value="Linux"), \
                     mock.patch.object(verify, "wayland_environment", side_effect=self.socket_session):
                    report = verify.verify(self.root, self.output, mode="all", environ=self.env, runner=runner)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["paths"][name]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
