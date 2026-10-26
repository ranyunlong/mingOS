#!/usr/bin/env python3
"""Task-owned QEMU runner. Kernel boot requires exact review; paused probes execute no CPU."""
import argparse
import contextlib
import json
import base64
import subprocess
import time
from pathlib import Path
from bootlib import Qmp, qemu_command, sha256, validate_bundle, validate_reviewed_input
from infra import InputPlan, STATE_PATH, classify_guest, validate_state

class GuestStream:
    def __init__(self, path, plan):
        self.path, self.plan = path, plan
        self.offset, self.buffer = 0, b""
        self.records = []
        self.boot_id = None
        self.state = None
        self.input_captures = []
        self.desktop_pid = None
        self.desktop_log_offset = self.desktop_capture_offset = 0
        self.pointer_receipt = None

    def pointer_context(self):
        return {"desktopPid": self.desktop_pid, "logOffset": max(self.desktop_log_offset, self.desktop_capture_offset), "receipt": self.pointer_receipt}

    def poll(self, qmp):
        if not self.path.exists(): return
        with self.path.open("rb") as source:
            source.seek(self.offset); block = source.read(1024 * 1024); self.offset += len(block)
        self.buffer += block
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            event = json.loads(line)
            kind = event.get("type")
            if kind == "guest-start":
                if self.boot_id is not None and self.boot_id != event["bootId"]: raise ValueError("guest boot identity changed")
                self.boot_id = event["bootId"]
            elif kind == "process-start" and event.get("name") == "desktop":
                if self.desktop_pid is not None and self.desktop_pid != event["pid"]: raise ValueError("desktop PID changed")
                self.desktop_pid = event["pid"]
            elif kind == "log" and event.get("name") == "desktop-wayland.stderr.log":
                if event["encoding"] != "base64" or event["offset"] != self.desktop_log_offset: raise ValueError("desktop diagnostic log offset mismatch")
                self.desktop_log_offset += len(base64.b64decode(event["data"], validate=True))
            elif kind == "desktop-pointer-receipt":
                if not self.state or event.get("bootId") != self.boot_id or event.get("sessionId") != self.state["sessionId"] or event.get("desktopPid") != self.desktop_pid:
                    raise ValueError("pointer receipt identity mismatch")
                self.pointer_receipt = dict(event["receipt"], bootId=event["bootId"], sessionId=event["sessionId"], desktopPid=event["desktopPid"])
                if self.plan: self.plan.advance(self.state, qmp, self.pointer_context())
            elif kind == "desktop-state":
                if self.boot_id is None: raise ValueError("state before guest boot identity")
                value = event["state"]
                validate_state(value, self.boot_id, self.state["sessionId"] if self.state else None,
                               self.state["revision"] if self.state else None)
                if self.state and value["revision"] == self.state["revision"] and value != self.state:
                    raise ValueError("same revision changed state")
                if event.get("desktopPid") is not None and event["desktopPid"] != self.desktop_pid: raise ValueError("state desktop PID mismatch")
                captured = event.get("diagnosticStderrOffset")
                if captured is not None:
                    if type(captured) is not int or captured < self.desktop_capture_offset: raise ValueError("state diagnostic log offset regressed")
                    self.desktop_capture_offset = captured
                self.state = value
                if self.plan:
                    before = self.plan.index
                    self.plan.advance(value, qmp, self.pointer_context())
                    if self.plan.index > before and value["phase"] == "ready":
                        name = "input-confirmed-%02d.ppm" % self.plan.index
                        qmp.execute("screendump", {"filename": str(self.path.parent / name)})
                        self.input_captures.append(name)
            if kind not in ("log", "desktop-state", "process-table"):
                self.records.append(event)
        if len(self.buffer) > 1024 * 1024: raise ValueError("guest evidence line exceeds bound")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-paused", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--data-manifest", help="relative manifest inside owned work-root")
    parser.add_argument("--input-script", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--diagnostic-split-input", action="store_true")
    parser.add_argument("--diagnostic-pointer-handshake", action="store_true")
    args = parser.parse_args()
    if not 10 <= args.timeout <= 1800: parser.error("timeout must be 10..1800 seconds")
    if bool(args.work_root) != bool(args.data_manifest): parser.error("work-root/data-manifest must be used together")
    manifest = None
    if not args.probe_paused:
        if not args.bundle or not args.review: parser.error("kernel boot requires bundle and main-thread review")
        manifest = validate_bundle(args.bundle, sha256(args.review))
        review = json.loads(args.review.read_text())
        if review.get("reviewed") is not True or not review.get("reviewer") or review != manifest["sessionReview"]:
            raise ValueError("main-thread review absent/changed")
        validate_reviewed_input(review, args.input_script)
        if args.data_manifest or args.input_script:
            if manifest.get("infraApiVersion") != 1: raise ValueError("data/input requires infrastructure API v1 bundle")
        if args.input_script:
            extra = review["extraArgs"]
            if "--state-file" not in extra or extra[extra.index("--state-file") + 1] != STATE_PATH:
                raise ValueError("input requires explicitly reviewed --state-file " + STATE_PATH)
    elif args.input_script:
        parser.error("paused probes cannot run ready-driven input scripts")
    if args.diagnostic_split_input and (not manifest or not manifest.get("inputDiagnostics") or not args.input_script):
        parser.error("split input requires an input-diagnostics bundle and reviewed input script")
    if args.diagnostic_pointer_handshake and (not args.diagnostic_split_input or not manifest or manifest.get("diagnosticProfile") != "input-pointer-ready-v1"):
        parser.error("pointer handshake requires explicit instrumented pointer-ready bundle and split input")
    if manifest and manifest.get("diagnosticProfile") == "input-pointer-ready-v1" and not args.diagnostic_pointer_handshake:
        parser.error("pointer-ready bundle cannot fall back to unacknowledged input")
    disk = None
    if args.data_manifest:
        from data_disk import load_disk, lease
        disk = load_disk(args.work_root, args.data_manifest)
        lease_context = lease(args.work_root, disk)
    else:
        lease_context = contextlib.nullcontext()
    args.output.mkdir(parents=True, exist_ok=False)
    plan = InputPlan(json.loads(args.input_script.read_text()), args.output / "inputs.jsonl", args.timeout, split_transactions=args.diagnostic_split_input, pointer_handshake=args.diagnostic_pointer_handshake) if args.input_script else None
    with lease_context:
        if disk:
            disk = load_disk(args.work_root, args.data_manifest)
        command = qemu_command(args.bundle, args.output, paused=args.probe_paused, data_disk=disk, input_diagnostics=bool(manifest and manifest.get("inputDiagnostics")))
        report = {"argv": command, "bootAttempted": False, "status": "planned", "finalSystemAccepted": False,
                  "physicalInput": False, "physicalGpu": False, "probePaused": args.probe_paused,
                  "diagnosticOnly": bool(manifest and manifest.get("diagnosticOnly", manifest.get("inputDiagnostics"))), "diagnosticSplitInput": args.diagnostic_split_input, "pointerHandshake": args.diagnostic_pointer_handshake,
                  "bundleIdentity": sha256(args.bundle / "bundle.json") if manifest else None,
                  "diskUuid": disk["uuid"] if disk else None}
        def save(): (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        save()
        if args.dry_run: print(json.dumps(report, indent=2)); return 0
        qmp = proc = None
        stream = GuestStream(args.output / "guest.jsonl", plan)
        timed_out = False
        try:
            with (args.output / "qemu.stdout.log").open("wb") as out, (args.output / "qemu.stderr.log").open("wb") as err:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            report["bootAttempted"] = not args.probe_paused
            qmp = Qmp(args.output / "qmp.sock", transcript_path=args.output / "qmp.jsonl")
            report["qemuVersion"] = qmp.execute("query-version")
            report["initialStatus"] = qmp.execute("query-status")
            names = {x["name"] for x in qmp.execute("query-commands")}
            if not {"screendump", "input-send-event", "system_powerdown"} <= names: raise RuntimeError("QMP required commands missing")
            if disk: report["blockDevices"] = qmp.execute("query-block")
            if args.probe_paused:
                if report["initialStatus"]["running"]: raise RuntimeError("paused probe running")
                schema = qmp.execute("query-qmp-schema")
                (args.output / "qmp-schema.json").write_text(json.dumps(schema, indent=2) + "\n")
                try:
                    qmp.execute("input-send-event", {"events": [{"type": "key", "data": {"down": False, "key": {"type": "qcode", "data": "shift"}}}]})
                    report["pausedInputProbe"] = "accepted/queued; no guest CPU or input-consumption proof"
                except RuntimeError as error: report["pausedInputProbe"] = str(error)
                qmp.execute("screendump", {"filename": str(args.output / "paused.ppm")})
                report["status"] = "paused-qmp-probe-passed"
            else:
                deadline = time.monotonic() + args.timeout
                capture_at = time.monotonic() + 5
                captures = []
                while proc.poll() is None and time.monotonic() < deadline:
                    stream.poll(qmp)
                    if plan: plan.check_deadline()
                    if time.monotonic() >= capture_at:
                        name = "scanout-%03d.ppm" % len(captures)
                        qmp.execute("screendump", {"filename": str(args.output / name)})
                        captures.append(name); capture_at += 10
                    time.sleep(0.1)
                report["captures"] = captures
                report["inputCaptures"] = stream.input_captures
                timed_out = proc.poll() is None
                if timed_out:
                    report["status"] = "timeout-unaccepted"
                    qmp.execute("system_powerdown")
                else:
                    stream.poll(qmp)
                    report["guestExitCode"] = proc.returncode
                    report["status"] = classify_guest(stream.records, False)
                    serial = (args.output / "kernel-serial.log").read_text(errors="replace")
                    report["kernelPowerdown"] = "reboot: Power down" in serial
                    if report["status"] == "normal-desktop-poweroff" and (proc.returncode != 0 or not report["kernelPowerdown"]):
                        report["status"] = "guest-failed"
                    if plan and not plan.complete: raise RuntimeError("input script unfinished")
                report["bootId"] = stream.boot_id
                report["lastState"] = stream.state
                report["inputComplete"] = plan.complete if plan else None
                terminals = [x for x in stream.records if x.get("type") == "guest-result"]
                if terminals:
                    report["guestResult"] = terminals[-1]
                    for key in ("previousMarker", "marker"): report[key] = terminals[-1].get(key)
                    if report["status"] == "normal-desktop-poweroff":
                        terminal = terminals[-1]
                        identity_ok = terminal.get("bootId") == stream.boot_id
                        if disk:
                            identity_ok = identity_ok and terminal.get("diskUuid") == disk["uuid"] and bool(terminal.get("marker")) and any(x.get("type") == "data-marker" and x.get("uuid") == disk["uuid"] and x.get("readbackVerified") is True for x in stream.records)
                        if not identity_ok:
                            report["status"] = "guest-failed"; report["error"] = "guest boot/disk/readback identity mismatch"
        except Exception as error:
            if isinstance(error, TimeoutError): timed_out = True
            report["status"] = "timeout-unaccepted" if timed_out else "failed"
            report["error"] = repr(error)
        finally:
            if qmp:
                if plan:
                    try: plan.release(qmp)
                    except Exception as error: report["inputReleaseError"] = repr(error)
                if proc and proc.poll() is None:
                    try: qmp.execute("quit")
                    except (OSError, RuntimeError, ValueError): pass
                elif proc: qmp.drain_closed()
                (args.output / "qmp.json").write_text(json.dumps(qmp.transcript, indent=2) + "\n")
                qmp.close()
            if proc and proc.poll() is None:
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=5)
            save()
        print(json.dumps(report, indent=2))
        return 0 if report["status"] in ("paused-qmp-probe-passed", "normal-desktop-poweroff") else 1

if __name__ == "__main__": raise SystemExit(main())
