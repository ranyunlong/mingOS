"""Shared validation and bounded QMP protocol; no Docker or guest startup on import."""
import hashlib
import json
import socket
import time
from pathlib import Path, PurePosixPath

def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()

def checked_path(base, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("invalid payload path")
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("payload path escapes root")
    target = (Path(base) / relative).resolve()
    if not target.is_relative_to(Path(base).resolve()):
        raise ValueError("payload symlink escapes root")
    return target

def checked_session(review, payload):
    if review.get("reviewed") is not True or not review.get("reviewer"):
        raise ValueError("main-thread session/payload review is pending")
    for name, field in (("ming-app-host", "hostSha256"), ("catalog.json", "catalogSha256")):
        if review.get(field) != sha256(payload / name):
            raise ValueError("review hash mismatch: " + name)
    mode, extra = review.get("sessionMode"), review.get("extraArgs")
    if mode not in ("bounded", "long-lived") or not isinstance(extra, list):
        raise ValueError("explicit sessionMode and extraArgs required")
    if any(not isinstance(arg, str) or "\0" in arg for arg in extra):
        raise ValueError("invalid session argument")
    if any(arg.split("=", 1)[0] in ("--backend", "--renderer", "--snapshot", "--events") for arg in extra):
        raise ValueError("session cannot override native CPU presentation or inject test events")
    if mode == "bounded":
        if review.get("profile") == "public-probe":
            expected = ["--duration-ms", "15000", "--isolation", "strict", "--data-root", "/data/mingos",
                        "--state-file", "/run/evidence/desktop-state.json"]
            if extra != expected:
                raise ValueError("bounded public probe review requires exact data-root/isolation/state args")
        elif len(extra) != 2 or extra[0] != "--duration-ms" or not extra[1].isdigit() or not 1 <= int(extra[1]) <= 60000:
            raise ValueError("bounded review requires --duration-ms 1..60000")
    elif not extra or not review.get("budgetEvidence"):
        raise ValueError("long-lived session requires exact main-thread args and budgetEvidence")
    return review

def validate_reviewed_input(review, path):
    expected = review.get("inputScriptSha256")
    if expected is None:
        if review.get("profile") in ("data-text", "data-text-private-probe", "data-text-public-probe"):
            raise ValueError("data-text input script review hash required")
        return
    if path is None or sha256(path) != expected:
        raise ValueError("reviewed input script hash mismatch")

def validate_kernel_config(settings):
    flags = dict(line.split("=", 1) for line in settings.splitlines() if line.startswith("CONFIG_") and "=" in line)
    for name in ("DEVTMPFS", "RD_GZIP", "SERIAL_8250_CONSOLE", "CGROUPS", "MEMCG", "CGROUP_SCHED", "CFS_BANDWIDTH", "CGROUP_PIDS", "SECCOMP_FILTER", "NAMESPACES"):
        if flags.get("CONFIG_" + name) != "y":
            raise ValueError("kernel prerequisite missing: " + name)
    for name in ("DRM_VIRTIO_GPU", "VIRTIO_CONSOLE", "VIRTIO_INPUT", "VIRTIO_PCI", "HW_RANDOM_VIRTIO", "INPUT_EVDEV", "EXT4_FS", "VIRTIO_BLK"):
        if flags.get("CONFIG_" + name) not in ("y", "m"):
            raise ValueError("kernel prerequisite missing: " + name)

def validate_bundle(bundle, review_digest):
    manifest = json.loads((bundle / "bundle.json").read_text())
    if manifest.get("reviewSha256") != review_digest:
        raise ValueError("bundle/session review mismatch")
    for name in ("vmlinuz", "initramfs.cpio.gz"):
        if manifest["sha256"].get(name) != sha256(bundle / name):
            raise ValueError("bundle hash mismatch: " + name)
    return manifest

def qemu_command(bundle, out, paused=False, data_disk=None, input_diagnostics=False):
    command = ["qemu-system-x86_64", "-machine", "q35", "-accel", "tcg,thread=multi",
               "-cpu", "max", "-smp", "2", "-m", "2048", "-nodefaults", "-no-reboot",
               "-display", "none", "-vga", "none", "-nic", "none",
               "-device", "virtio-vga,xres=1280,yres=800",
               "-object", "rng-random,id=mingrng,filename=/dev/urandom",
               "-device", "virtio-rng-pci,rng=mingrng",
               "-device", "virtio-keyboard-pci", "-device", "virtio-tablet-pci",
               "-device", "virtio-serial-pci", "-chardev", "file,id=evidence,path=" + str(out / "guest.jsonl"),
               "-device", "virtserialport,chardev=evidence,name=org.mingos.evidence",
               "-serial", "file:" + str(out / "kernel-serial.log"),
               "-qmp", "unix:" + str(out / "qmp.sock") + ",server=on,wait=off"]
    if input_diagnostics:
        command += ["-trace", "enable=input_event_*,file=" + str(out / "qemu-input.trace")]
    if data_disk:
        command += ["-blockdev", json.dumps({"driver": "file", "filename": data_disk["imagePath"], "node-name": "ming-data-file"}),
                    "-blockdev", json.dumps({"driver": "raw", "file": "ming-data-file", "node-name": "ming-data"}),
                    "-device", "virtio-blk-pci,drive=ming-data,serial=" + data_disk["serial"]]
    if paused:
        return command + ["-S"]
    return command + ["-kernel", str(bundle / "vmlinuz"), "-initrd", str(bundle / "initramfs.cpio.gz"),
                      "-append", "console=tty0 console=ttyS0,115200 rdinit=/init panic=-1"
                      + ((" mingos.data_uuid=" + data_disk["uuid"] + " mingos.data_serial=" + data_disk["serial"]) if data_disk else "")]

class Qmp:
    def __init__(self, path, timeout=15, transcript_path=None):
        self.transcript = []
        self.transcript_path = transcript_path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.sock.connect(str(path)); break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    self.sock.close(); raise TimeoutError("QMP socket not ready")
                time.sleep(0.1)
        self.sock.settimeout(5)
        self.stream = self.sock.makefile("rwb")
        self.sequence = 0
        self.read()
        self.execute("qmp_capabilities")

    def note(self, direction, message):
        self.transcript.append(message)
        if self.transcript_path:
            from infra import append_record
            append_record(self.transcript_path, {"direction": direction, "message": message})

    def read(self):
        line = self.stream.readline(1024 * 1024)
        if not line:
            raise RuntimeError("QMP closed unexpectedly")
        value = json.loads(line)
        self.note("receive", value)
        return value

    def execute(self, command, arguments=None):
        self.sequence += 1
        request = {"execute": command, "id": self.sequence}
        if arguments is not None: request["arguments"] = arguments
        self.note("send", request)
        self.stream.write(json.dumps(request).encode() + b"\n"); self.stream.flush()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = self.read()
            if response.get("id") == self.sequence:
                if "error" in response: raise RuntimeError("QMP error: " + json.dumps(response["error"]))
                return response.get("return")
        raise TimeoutError("QMP command deadline: " + command)

    def drain_closed(self):
        self.sock.settimeout(0.5)
        try:
            while True: self.read()
        except (OSError, RuntimeError, ValueError):
            pass

    def close(self):
        self.stream.close(); self.sock.close()
