#!/usr/bin/env python3
"""Reviewed one-shot SIGKILL injector for a strict guest worker diagnostic."""
import argparse
import json
import os
import signal
import stat
import time
from pathlib import Path

from infra import validate_fault_injection, validate_fault_receipt, validate_state


def read_json(path, limit=262144):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise ValueError("unsafe JSON input")
    return json.loads(path.read_bytes())


def resolve_target(config, state, proc_root=Path("/proc")):
    validate_fault_injection(config)
    validate_state(state, state.get("bootId"))
    app = next((item for item in state["apps"] if item["app"] == config["app"]), None)
    if app is None or app["generation"] != config["generation"] or app["state"] not in ("running", "ready"):
        return None
    pid = app.get("workerPid")
    if not isinstance(pid, int) or pid < 2 or pid == os.getpid():
        return None
    proc = Path(proc_root) / str(pid)
    try:
        cgroup = (proc / "cgroup").read_text(encoding="ascii")
        cmdline = (proc / "cmdline").read_bytes()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    expected_cgroup = "/mingos/app-%d-g-%d" % (config["app"], config["generation"])
    if cgroup != "0::" + expected_cgroup + "\n":
        return None
    argv = [item for item in cmdline.split(b"\0") if item]
    if len(argv) < 2 or not argv[0].endswith(b"/ming-app-host") or argv[1] != b"__worker":
        return None
    return {"pid": pid, "cgroup": expected_cgroup, "revision": state["revision"]}


def write_receipt(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def completed_receipt(process, config, receipt_path):
    exit_code = process.poll()
    if exit_code is None:
        return None
    if exit_code != 0:
        raise RuntimeError("fault injector exit: " + str(exit_code))
    return validate_fault_receipt(read_json(receipt_path, 16384), config)


def inject(config_path, state_path, receipt_path, proc_root=Path("/proc")):
    config = validate_fault_injection(read_json(config_path, 16384))
    deadline = time.monotonic() + config["maxWaitSeconds"]
    last_error = None
    while time.monotonic() < deadline:
        try:
            target = resolve_target(config, read_json(state_path), proc_root)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
            last_error = repr(error)
            target = None
        if target is not None:
            os.kill(target["pid"], signal.SIGKILL)
            receipt = dict(config, **target, status="sent", monotonicNs=time.monotonic_ns())
            write_receipt(receipt_path, receipt)
            return receipt
        time.sleep(0.05)
    raise TimeoutError("fault target not observed: " + str(last_error))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    args = parser.parse_args()
    try:
        print(json.dumps(inject(args.config, args.state_file, args.receipt, args.proc_root), sort_keys=True))
    except Exception as error:
        print(json.dumps({"status": "failed", "error": repr(error)}, sort_keys=True))
        raise


if __name__ == "__main__":
    main()
