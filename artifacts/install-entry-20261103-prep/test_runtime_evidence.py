"""Synthetic controls are verifier tests, never real runtime acceptance evidence."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("install_runtime_audit", HERE / "verify-runtime-evidence.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def write_lines(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class RawEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.boot = Path(self.temp.name)
        self.spec = json.loads((HERE / "input.json").read_text())
        self.guest, self.progress, self.qmp = [], [], []
        self.clock, self.request_id = 1.0, 0
        self.stderr = bytearray(b"client get_pointer(new id wl_pointer@36)\n")
        self.outcome = {"activeWorkersAfterShutdown": 0, "droppedTrace": 0,
                        "trace": [{"kind": "installed", "detail": "dev.ming.install-demo"}],
                        "instances": [{"app": app, "generation": 1, "window": app,
                                       "packageId": package, "workerPid": 50 + app,
                                       "firstReadyMs": 1, "presentedSourceSequence": 1,
                                       "completed": True, "error": None}
                                      for app, package in [(1, "dev.ming.text-entry-demo"),
                                                           (2, "dev.ming.install-demo")]]}
        self.emit("guest-start", bootId="boot-1")
        marker = {"bootId": "boot-1", "counter": 1}
        self.emit("data-marker", uuid="disk-1", marker=marker, previousMarker=None,
                  readbackVerified=True)
        self.emit("process-start", name="desktop", pid=44, argv=["/opt/mingos/ming-app-host"])
        self.qmp.append({"monotonic": self.tick(), "direction": "receive", "message": {"QMP": {}}})
        self.command("qmp_capabilities", result={})
        self.command("query-mice", result=[{"name": "tablet", "absolute": True, "current": True}])
        state = self.state(1, 1, apps=[1])
        self.snapshot(state)
        self.record("input-routing", mice=[{"name": "tablet", "absolute": True, "current": True}], state=state)
        for index, planned in enumerate(self.spec["steps"]):
            events = self.events(planned, state)
            transactions = [events[:2]] + [[event] for event in events[2:]]
            self.command("input-send-event", {"events": transactions[0]})
            after_offset = len(self.stderr)
            point = planned["events"][0]
            self.record("input-pointer-dispatched", step=planned["name"], point=point,
                        desktopPid=44, afterLogOffset=after_offset, waitSeconds=5, state=state)
            if index == 0:
                line = "client wl_pointer@36.enter(1, wl_surface@15, %s, %s)\n" % (point["x"], point["y"])
            else:
                line = "client wl_pointer@36.motion(1, %s, %s)\n" % (point["x"], point["y"])
            self.stderr.extend((line + "client wl_pointer@36.frame()\n").encode())
            receipt = {"pointer": 36, "surface": 15, "entered": True, "x": float(point["x"]),
                       "y": float(point["y"]), "sequence": index + 1, "logOffset": len(self.stderr)}
            self.emit("desktop-pointer-receipt", bootId="boot-1", sessionId="session-1",
                      desktopPid=44, receipt=receipt)
            bound = dict(receipt, bootId="boot-1", sessionId="session-1", desktopPid=44)
            self.record("input-pointer-ready", step=planned["name"], receipt=bound,
                        afterLogOffset=after_offset, state=state)
            for transaction in transactions[1:]:
                self.command("input-send-event", {"events": transaction})
            self.record("input-dispatched", step=planned["name"], events=events,
                        transactions=transactions, diagnosticSplit=True, pointerHandshake=True, state=state)
            if index == 0:
                state = self.state(2, 2, apps=[1], prompt=planned["expect"]["storagePrompt"])
            elif index == 1:
                state = self.state(3, 3, apps=[1])
            elif index == 2:
                state = self.state(4, 4, apps=[1, 2])
            else:
                state = self.state(5, 4, apps=[1, 2], phase="stopped")
            self.snapshot(state)
            self.record("input-expectation", step=planned["name"], state=state)
        self.emit("desktop-exit", code=0)
        self.emit("desktop-workers-stopped", active=[])
        self.emit("data-synced", uuid="disk-1", marker=marker, unmounted=True)
        self.emit("desktop-diagnostic-stderr", bytes=len(self.stderr),
                  sha256=hashlib.sha256(self.stderr).hexdigest(), complete=True, limitBytes=1024 * 1024)
        self.log("desktop-wayland.stderr.log", bytes(self.stderr))
        self.log("desktop.log", json.dumps(self.outcome).encode() + b"\n")
        self.emit("guest-result", status="normal", desktopExitCode=0, error=None,
                  dataSynced=True, poweroffRequested=True, bootId="boot-1", diskUuid="disk-1",
                  previousMarker=None, marker=marker)
        self.emit("guest-stop")
        self.report = {"status": "normal-desktop-poweroff", "bootId": "boot-1", "diskUuid": "disk-1",
                       "previousMarker": None, "marker": marker, "lastState": state,
                       "inputComplete": True, "kernelPowerdown": True, "guestExitCode": 0}
        self.flush()

    def tick(self):
        self.clock += 1
        return self.clock

    def emit(self, kind, **fields):
        self.guest.append(dict(type=kind, uptime=float(len(self.guest)), **copy.deepcopy(fields)))

    def record(self, kind, **fields):
        self.progress.append(dict(type=kind, monotonic=self.tick(), **copy.deepcopy(fields)))

    def command(self, name, arguments=None, result=None):
        self.request_id += 1
        request = {"execute": name, "id": self.request_id}
        if arguments is not None:
            request["arguments"] = arguments
        self.qmp.append({"monotonic": self.tick(), "direction": "send", "message": request})
        self.qmp.append({"monotonic": self.tick(), "direction": "receive",
                         "message": {"id": self.request_id, "return": {} if result is None else result}})

    def snapshot(self, state):
        self.emit("desktop-state", state=state, desktopPid=44, diagnosticStderrOffset=len(self.stderr))

    def state(self, revision, sequence, apps, prompt=None, phase="ready"):
        return {"schemaVersion": 1, "bootId": "boot-1", "sessionId": "session-1", "revision": revision,
                "phase": phase, "error": None,
                "root": {"width": 1280, "height": 800, "presentedSequence": sequence,
                         "storagePrompt": prompt, "storagePromptPresentedSequence": sequence if prompt else None},
                "apps": [{"app": app, "generation": 1, "state": "closed" if phase == "stopped" else "running",
                          "inputReady": phase != "stopped", "minimized": False, "workerPid": 50 + app,
                          "sourceSequence": 1, "presentedSourceSequence": 1} for app in apps]}

    def events(self, planned, state):
        result = []
        for event in planned["events"]:
            if event["kind"] == "pointer":
                result += [{"type": "abs", "data": {"axis": axis,
                            "value": round(event[axis] * 32767 / (state["root"][extent] - 1))}}
                           for axis, extent in [("x", "width"), ("y", "height")]]
            else:
                result.append({"type": "btn", "data": {"button": event["button"], "down": event["down"]}})
        return result

    def log(self, name, data):
        self.emit("log", name=name, offset=0, encoding="base64", data=base64.b64encode(data).decode())

    def flush(self):
        write_lines(self.boot / "guest.jsonl", self.guest)
        write_lines(self.boot / "inputs.jsonl", self.progress)
        write_lines(self.boot / "qmp.jsonl", self.qmp)
        (self.boot / "qmp.json").write_text(json.dumps([row["message"] for row in self.qmp]))
        (self.boot / "kernel-serial.log").write_text("reboot: Power down\n")

    def check(self):
        self.flush()
        self.assertTrue(callable(getattr(audit, "audit_raw_boot", None)), "raw audit entry point is required")
        return audit.audit_raw_boot(self.boot, self.spec, self.report)

    def test_complete_synthetic_control_is_accepted_without_claiming_real_execution(self):
        self.assertEqual(self.check()["pointerHandshakes"], 4)

    def test_missing_reordered_or_duplicated_handshake_is_rejected(self):
        baseline = copy.deepcopy(self.progress)
        for mutation in ["missing", "reordered", "duplicate"]:
            with self.subTest(mutation=mutation):
                self.progress = copy.deepcopy(baseline)
                if mutation == "missing": self.progress.pop(2)
                elif mutation == "reordered": self.progress[1], self.progress[2] = self.progress[2], self.progress[1]
                else: self.progress.insert(3, copy.deepcopy(self.progress[2]))
                with self.assertRaises(ValueError): self.check()

    def test_receipt_identity_coordinates_offset_surface_and_raw_binding_are_required(self):
        baseline = copy.deepcopy(self.progress)
        for field, value in [("bootId", "other"), ("sessionId", "other"), ("desktopPid", 99),
                             ("surface", 16), ("logOffset", 0), ("x", float("nan")), ("x", 0)]:
            with self.subTest(field=field, value=value):
                self.progress = copy.deepcopy(baseline)
                self.progress[2]["receipt"][field] = value
                with self.assertRaises(ValueError): self.check()

    def test_qmp_buttons_before_ready_and_duplicate_reply_are_rejected(self):
        original = copy.deepcopy(self.qmp)
        first_button = next(row for row in self.qmp if row["message"].get("arguments", {}).get("events", [{}])[0].get("type") == "btn")
        first_button["monotonic"] = self.progress[2]["monotonic"] - 0.1
        with self.assertRaises(ValueError): self.check()
        self.qmp = original
        self.qmp.insert(3, copy.deepcopy(self.qmp[2]))
        with self.assertRaises(ValueError): self.check()

    def test_qmp_array_cannot_replace_or_disagree_with_raw_transcript(self):
        self.flush()
        (self.boot / "qmp.json").write_text("[]")
        with self.assertRaises(ValueError): audit.audit_raw_boot(self.boot, self.spec, self.report)

    def test_qmp_must_not_add_unreviewed_mutating_commands(self):
        self.command("send-key", {"keys": [{"type": "qcode", "data": "ret"}]})
        with self.assertRaisesRegex(ValueError, "unreviewed mutating/unknown QMP"):
            self.check()

    def test_slow_abs_ack_cannot_restart_pointer_deadline(self):
        first_abs = next(row for row in self.qmp if row["direction"] == "send"
                         and row["message"].get("execute") == "input-send-event")
        acknowledgement = next(row for row in self.qmp if row["direction"] == "receive"
                               and row["message"].get("id") == first_abs["message"]["id"])
        boundary = acknowledgement["monotonic"]
        for row in self.qmp:
            if row["monotonic"] >= boundary: row["monotonic"] += 4
        for row in self.progress:
            if row["monotonic"] >= boundary: row["monotonic"] += 4
        self.assertLessEqual(self.progress[2]["monotonic"] - self.progress[1]["monotonic"], 5)
        self.assertGreater(self.progress[2]["monotonic"] - first_abs["monotonic"], 5)
        with self.assertRaisesRegex(ValueError, "before pointer-ready"):
            self.check()

    def test_raw_log_offsets_digest_and_complete_receipt_are_enforced(self):
        original = copy.deepcopy(self.guest)
        for field, value in [("offset", 1), ("complete", False), ("sha256", "0" * 64), ("bytes", 1)]:
            with self.subTest(field=field):
                self.guest = copy.deepcopy(original)
                kind = "log" if field == "offset" else "desktop-diagnostic-stderr"
                next(row for row in self.guest if row["type"] == kind)[field] = value
                with self.assertRaises(ValueError): self.check()

    def test_outcome_must_be_one_complete_stdout_object(self):
        row = next(row for row in self.guest if row.get("name") == "desktop.log")
        row["data"] = base64.b64encode(b"prefix " + json.dumps(self.outcome).encode()).decode()
        with self.assertRaises(ValueError): self.check()

    def test_worker_outcome_must_bind_raw_generation_pid_and_presented_state(self):
        original = copy.deepcopy(self.outcome)
        for field, value in [("workerPid", 999), ("generation", 2), ("presentedSourceSequence", 0)]:
            with self.subTest(field=field):
                self.outcome = copy.deepcopy(original)
                self.outcome["instances"][1][field] = value
                row = next(row for row in self.guest if row.get("name") == "desktop.log")
                row["data"] = base64.b64encode(json.dumps(self.outcome).encode()).decode()
                with self.assertRaises(ValueError): self.check()

    def test_abs_baseline_and_ready_deadline_are_required(self):
        original = copy.deepcopy(self.progress)
        for field, value in [("afterLogOffset", 0), ("waitSeconds", 6), ("desktopPid", 999)]:
            with self.subTest(field=field):
                self.progress = copy.deepcopy(original)
                self.progress[1][field] = value
                with self.assertRaises(ValueError): self.check()
        self.progress = copy.deepcopy(original)
        for row in self.progress[2:]: row["monotonic"] += 6
        for row in self.qmp:
            if row["monotonic"] >= original[2]["monotonic"]: row["monotonic"] += 6
        with self.assertRaisesRegex(ValueError, "before pointer-ready"):
            self.check()

    def test_raw_pointer_requires_prior_state_and_normal_cleanup_order(self):
        original = copy.deepcopy(self.guest)
        receipt = next(row for row in self.guest if row["type"] == "desktop-pointer-receipt")
        self.guest.remove(receipt)
        self.guest.insert(3, receipt)
        for index, row in enumerate(self.guest): row["uptime"] = index
        with self.assertRaises(ValueError): self.check()
        self.guest = original
        exited = next(index for index, row in enumerate(self.guest) if row["type"] == "desktop-exit")
        self.guest[exited], self.guest[exited + 1] = self.guest[exited + 1], self.guest[exited]
        for index, row in enumerate(self.guest): row["uptime"] = index
        with self.assertRaises(ValueError): self.check()

    def test_pair_entry_point_requires_raw_audit_and_pointer_profile(self):
        import bootlib
        root = self.boot / "integration"
        root.mkdir()
        inputs, bundle, pair = root / "inputs", root / "bundle", root / "pair"
        (inputs / "payload").mkdir(parents=True)
        bundle.mkdir()
        (pair / "boot-1").mkdir(parents=True)
        helper = inputs / "payload/prepare-install-fixture"
        helper.write_bytes(b"synthetic helper")
        (inputs / "input.json").write_text(json.dumps(self.spec))
        (bundle / "bundle.json").write_text("{}")
        (root / "review.json").write_text("{}")
        (root / "fixture.json").write_text(json.dumps({
            "status": "actual-fixture-signatures-and-fingerprints-verified", "helperSha256": audit.sha(helper)}))
        report = dict(self.report, bundleIdentity=audit.sha(bundle / "bundle.json"))
        (pair / "boot-1/result.json").write_text(json.dumps(report))
        args = SimpleNamespace(repo=HERE.parents[1], inputs=inputs, bundle=bundle, pair=pair,
                               review=root / "review.json", fixture_verification=root / "fixture.json")
        manifest = {"sessionReview": {}, "payloadSha256": {}, "instrumentedQualification": True,
                    "inputDiagnostics": True, "diagnosticOnly": False, "diagnosticProfile": "input-pointer-ready-v1"}
        with patch.object(bootlib, "checked_session"), patch.object(bootlib, "validate_reviewed_input"), \
                patch.object(bootlib, "validate_bundle", return_value=manifest), \
                patch.object(audit, "audit_raw_boot", side_effect=ValueError("raw gate exercised")) as raw:
            with self.assertRaisesRegex(ValueError, "raw gate exercised"): audit.pair(args)
            raw.assert_called_once()
            manifest["diagnosticProfile"] = "input-wayland-client-v1"
            with self.assertRaisesRegex(ValueError, "pointer-ready instrumented"): audit.pair(args)

    def test_progress_snapshot_must_appear_in_raw_guest_stream(self):
        self.progress[2]["state"]["revision"] = 999
        with self.assertRaises(ValueError): self.check()

    def test_success_summary_cannot_override_raw_failure_or_wrong_boot_identity(self):
        next(row for row in self.guest if row["type"] == "guest-result")["status"] = "failed"
        with self.assertRaises(ValueError): self.check()
        next(row for row in self.guest if row["type"] == "guest-result")["status"] = "normal"
        self.report["bootId"] = "forged-summary"
        with self.assertRaises(ValueError): self.check()

    def test_truncated_jsonl_and_duplicate_keys_are_rejected(self):
        self.flush()
        source = self.boot / "inputs.jsonl"
        source.write_bytes(source.read_bytes().rstrip(b"\n"))
        with self.assertRaises(ValueError): audit.audit_raw_boot(self.boot, self.spec, self.report)
        self.flush()
        source.write_bytes(source.read_bytes().replace(b'"pointerHandshake": true', b'"pointerHandshake": false, "pointerHandshake": true', 1))
        with self.assertRaises(ValueError): audit.audit_raw_boot(self.boot, self.spec, self.report)

    def test_historical_failed_first_boot_cannot_pass_even_with_forged_summary(self):
        historical = HERE.parent / "install-entry-actual-20261103-171939/cold-pair-stage/cold-pair/boot-1"
        if not historical.is_dir(): self.skipTest("historical failed artifact unavailable")
        self.assertTrue(callable(getattr(audit, "audit_raw_boot", None)), "raw audit entry point is required")
        report = json.loads((historical / "result.json").read_text())
        report.update(status="normal-desktop-poweroff", inputComplete=True, kernelPowerdown=True, guestExitCode=0)
        with self.assertRaises(ValueError): audit.audit_raw_boot(historical, self.spec, report)


if __name__ == "__main__":
    unittest.main()
