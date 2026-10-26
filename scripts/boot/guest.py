#!/usr/bin/python3
"""Trusted guest PID1 supervisor; SDK-only apps run in separate host workers."""
import base64
import json
import hashlib
import os
import subprocess
import time
import traceback
from pathlib import Path
from infra import STATE_PATH, validate_state
from guest_platform import Platform
from bounded_log import BoundedLog, LogLimitExceeded
from wayland_receipt import PointerReceipt

LOG = Path("/run/evidence")
LOG.mkdir(parents=True, exist_ok=True)
channel = None
log_offsets = {}

def emit(kind, **fields):
    record = dict(type=kind, uptime=round(time.monotonic(), 3), **fields)
    line = json.dumps(record, ensure_ascii=True) + "\n"
    print("MINGOS_EVIDENCE " + line, end="", flush=True)
    if channel:
        channel.write(line); channel.flush()

def flush_logs():
    for log in LOG.glob("*.log"):
        offset = log_offsets.get(log.name, 0)
        with log.open("rb") as source:
            source.seek(offset)
            while chunk := source.read(16384):
                emit("log", name=log.name, offset=offset, encoding="base64", data=base64.b64encode(chunk).decode("ascii"))
                offset += len(chunk)
        log_offsets[log.name] = offset

def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    emit("command", argv=argv, code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    if result.returncode: raise RuntimeError("guest command failed: " + repr(argv))

def process_table():
    records = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal(): continue
        try:
            argv = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            if "ming-app-host" not in argv: continue
            records.append(dict(pid=int(proc.name), argv=argv, status=(proc / "status").read_text()))
        except (FileNotFoundError, PermissionError, ProcessLookupError): pass
    return records

def main():
    global channel
    desktop = weston = seat = observer = platform = None
    streams = []
    error = None
    desktop_code = None
    data_synced = False
    state = None
    state_bytes = None
    stderr_source = stderr_capture = None
    stderr_limited = False
    diagnostic = {}
    ready_capture_deadline = None
    pointer_parser = None
    def relay_state():
        nonlocal state, state_bytes
        path = Path(STATE_PATH)
        if not path.exists(): return
        if path.is_symlink() or path.stat().st_size > 262144: raise RuntimeError("unsafe state file")
        raw = path.read_bytes()
        if raw == state_bytes: return
        try:
            value = json.loads(raw)
            validate_state(value, platform.boot_id, state["sessionId"] if state else None, state["revision"] if state else None)
            if state and value["revision"] == state["revision"] and value != state: raise ValueError("same revision changed state")
        except (ValueError, TypeError, KeyError) as error:
            emit("desktop-state-rejected", error=repr(error), rawBase64=base64.b64encode(raw).decode("ascii"))
            raise
        state_bytes, state = raw, value
        emit("desktop-state", state=value, desktopPid=desktop.pid if desktop else None, diagnosticStderrOffset=stderr_capture.captured if stderr_capture else None)
    def drain_stderr():
        nonlocal stderr_limited
        if stderr_source is None or stderr_limited: return
        while True:
            try: data = os.read(stderr_source.fileno(), 65536)
            except BlockingIOError: return
            if not data: return
            try:
                stderr_capture.append(data)
                if pointer_parser:
                    for receipt in pointer_parser.feed(data):
                        emit("desktop-pointer-receipt", bootId=platform.boot_id, sessionId=state["sessionId"] if state else None, desktopPid=desktop.pid, receipt=receipt)
            except LogLimitExceeded as error:
                stderr_limited = True
                emit("diagnostic-log-limit", limitBytes=error.limit, capturedBytes=error.captured, observedBytesAtLeast=error.observed)
                raise
    def stop(proc, label):
        if proc and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=5)
                emit("forced-process-stop", name=label, pid=proc.pid)
                raise RuntimeError("forced backend stop: " + label)
    try:
        subprocess.run(["/usr/lib/systemd/systemd-udevd", "--daemon"], check=True, timeout=10)
        command(["udevadm", "trigger", "--action=add"])
        command(["udevadm", "settle", "--timeout=10"])
        candidates = list(Path("/dev/virtio-ports").glob("org.mingos.evidence"))
        if not candidates:
            for entry in Path("/sys/class/virtio-ports").glob("vport*"):
                if (entry / "name").read_text().strip() == "org.mingos.evidence": candidates.append(Path("/dev") / entry.name)
        if not candidates: raise RuntimeError("virtio evidence channel missing")
        channel = candidates[0].open("w", buffering=1)
        platform = Platform(emit, LOG)
        emit("guest-start", kernel=list(os.uname()), osRelease=Path("/etc/os-release").read_text(), pid=1,
             bootId=platform.boot_id, commandLine=Path("/proc/cmdline").read_text())
        # Keep full dmesg in streamed logs without serializing one enormous serial JSON line.
        with (LOG / "kernel-dmesg.log").open("wb") as log:
            subprocess.run(["dmesg"], stdout=log, check=True, timeout=15)
        platform.prepare()
        platform.strict_before()
        diag_path = Path("/etc/mingos/input-diagnostics.json")
        diagnostic = json.loads(diag_path.read_text()) if diag_path.exists() else {}
        pointer_parser = PointerReceipt() if diagnostic.get("pointerHandshake") else None
        flush_logs()
        emit("drm-devices", cards=[str(p) for p in Path("/dev/dri").glob("*")])
        emit("input-devices", nodes=[str(p) for p in Path("/dev/input").glob("event*")],
             modules=Path("/proc/modules").read_text(), entropyAvail=Path("/proc/sys/kernel/random/entropy_avail").read_text().strip())
        if not list(Path("/dev/dri").glob("card*")): raise RuntimeError("virtio-gpu DRM card missing")
        runtime = Path("/run/mingos"); runtime.mkdir(mode=0o700)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY="wayland-mingos",
                   LIBSEAT_BACKEND="seatd", SEATD_SOCK="/run/seatd.sock", XDG_SESSION_TYPE="wayland")
        def launch(argv, name, desktop_debug=False):
            nonlocal stderr_source, stderr_capture
            output = (LOG / (name + ".log")).open("wb"); streams.append(output)
            extra = diagnostic.get("desktopEnv", {}) if desktop_debug else {}
            if extra and extra != {"WAYLAND_DEBUG": "client"}: raise RuntimeError("unexpected diagnostic desktop env")
            if extra:
                env_file = Path("/etc/mingos/desktop-diagnostic-env.json")
                env_sha = hashlib.sha256(env_file.read_bytes()).hexdigest()
                if env_sha != diagnostic["desktopEnvSha256"]: raise RuntimeError("diagnostic env hash mismatch")
                emit("desktop-diagnostic-env", env=extra, sha256=env_sha, profile=diagnostic["profile"], stderrLimitBytes=diagnostic["stderrLimitBytes"])
            proc = subprocess.Popen(argv, env=dict(env, **extra), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.PIPE if extra else output)
            if extra:
                stderr_source = proc.stderr; os.set_blocking(stderr_source.fileno(), False)
                target = (LOG / "desktop-wayland.stderr.log").open("wb"); streams.append(target)
                stderr_capture = BoundedLog(target, diagnostic["stderrLimitBytes"])
            emit("process-start", name=name, pid=proc.pid, argv=argv)
            return proc
        seat = launch(["seatd", "-u", "root", "-g", "root"], "seatd")
        deadline = time.monotonic() + 15
        while not Path(env["SEATD_SOCK"]).exists():
            if seat.poll() is not None or time.monotonic() >= deadline: raise RuntimeError("seatd socket not ready")
            time.sleep(0.1)
        if Path("/etc/mingos/input-diagnostics.json").exists():
            observer = launch(["python3", "-B", "/etc/mingos/evdev_observer.py", "--boot-id", platform.boot_id, "--duration-seconds", "900"], "evdev-observer-stderr")
        weston = launch(["weston", "--backend=drm", "--renderer=pixman", "--shell=kiosk-shell.so",
                         "--config=/etc/mingos/weston.ini", "--socket=wayland-mingos",
                         "--log=/run/evidence/weston.log", "--idle-time=0"], "weston-stderr")
        deadline = time.monotonic() + 30
        while not (runtime / "wayland-mingos").exists():
            if weston.poll() is not None or time.monotonic() >= deadline: raise RuntimeError("Weston DRM socket not ready")
            time.sleep(0.1)
        session = json.loads(Path("/etc/mingos/session.json").read_text())
        argv = ["/opt/mingos/ming-app-host", "desktop", "/opt/mingos/catalog.json", "--renderer", "cpu"] + session["extraArgs"]
        desktop = launch(argv, "desktop", desktop_debug=True)
        last_process_sample = 0
        while desktop.poll() is None:
            drain_stderr(); relay_state(); flush_logs()
            if observer and observer.poll() is not None: raise RuntimeError("virtual input observer exited during desktop")
            if diagnostic.get("readyCaptureSeconds") and state and state["phase"] == "ready" and ready_capture_deadline is None:
                ready_capture_deadline = time.monotonic() + diagnostic["readyCaptureSeconds"]
                emit("diagnostic-ready-capture-window", seconds=diagnostic["readyCaptureSeconds"], triggeredByRevision=state["revision"])
            if ready_capture_deadline is not None and time.monotonic() >= ready_capture_deadline:
                raise RuntimeError("diagnostic-only ready capture window completed without normal desktop exit")
            if time.monotonic() >= last_process_sample + 1:
                emit("process-table", processes=process_table()); last_process_sample = time.monotonic()
            if weston.poll() is not None: raise RuntimeError("Weston exited during desktop")
            time.sleep(0.1)
        drain_stderr()
        desktop_code = desktop.returncode
        relay_state()
        emit("desktop-exit", code=desktop_code)
        if desktop_code != 0: raise RuntimeError("desktop exit: " + str(desktop_code))
        active = [p for p in process_table() if "__worker" in p["argv"]]
        emit("desktop-workers-stopped", active=active)
        if active: raise RuntimeError("Kotlin workers still active; resource probe forbidden")
        stop(weston, "weston"); stop(seat, "seatd"); stop(observer, "input-observer")
        platform.strict_after()
    except Exception as caught:
        error = repr(caught)
        emit("guest-error", error=error, traceback=traceback.format_exc())
    finally:
        for proc, label in ((desktop, "desktop"), (weston, "weston"), (seat, "seatd"), (observer, "input-observer")):
            try: stop(proc, label)
            except Exception as caught:
                error = error or repr(caught)
        if platform:
            try: platform.finish_data(); data_synced = True
            except Exception as caught:
                error = error or repr(caught); emit("data-sync-error", error=repr(caught))
        try: drain_stderr()
        except Exception as caught:
            error = error or repr(caught); emit("diagnostic-stderr-error", error=repr(caught))
        if stderr_source is not None: stderr_source.close()
        for stream in streams: stream.close()
        if stderr_capture:
            path = LOG / "desktop-wayland.stderr.log"
            emit("desktop-diagnostic-stderr", bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), complete=not stderr_limited, limitBytes=stderr_capture.limit)
        flush_logs()
        emit("guest-result", status="normal" if error is None and desktop_code == 0 else "failed",
             desktopExitCode=desktop_code, error=error, dataSynced=data_synced,
             poweroffRequested=True, bootId=platform.boot_id if platform else None,
             diskUuid=platform.disk_uuid if platform else None,
             previousMarker=platform.previous if platform else None, marker=platform.marker if platform else None)
        emit("guest-stop")
        if channel: channel.close()
        subprocess.run(["/bin/busybox", "poweroff", "-f"], timeout=5)

if __name__ == "__main__": main()
