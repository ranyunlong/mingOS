"""Shared validation and bounded QMP protocol; no Docker or guest startup on import."""
import hashlib
import json
import re
import socket
import time
from pathlib import Path, PurePosixPath

PERSISTENT_UPDATE_INSTALL_ROOT = "/data/mingos/install-root"
INSTALL_LIFECYCLE_INSTALL_ROOT = "/opt/mingos/install-root"
INSTALL_LIFECYCLE_EVENTS = "/opt/mingos/install-events.json"
INSTALL_LIFECYCLE_SOURCE = "/opt/mingos/install-source/app.json"
PERSISTENT_INSTALL_ROOT = "/data/mingos/install-root"
PERSISTENT_INSTALL_CATALOG = PERSISTENT_INSTALL_ROOT + "/catalog.json"
PERSISTENT_INSTALL_PROFILE = "persistent-install-pointer-ready-v1"
PERSISTENT_INSTALL_ARGS = ["--duration-ms", "0", "--isolation", "strict", "--data-root", "/data/mingos",
                           "--state-file", "/run/evidence/desktop-state.json",
                           "--qualification-receipts", PERSISTENT_INSTALL_PROFILE]
PERSISTENT_INSTALL_FIXTURE = {
    "helper": "/opt/mingos/prepare-persistent-install-fixture",
    "installRoot": PERSISTENT_INSTALL_ROOT,
    "initialWasm": "/opt/mingos/packages/initial/main.wasm",
    "sourceWasm": "/opt/mingos/packages/new/main.wasm",
    "sourceManifest": INSTALL_LIFECYCLE_SOURCE,
    "receipt": "/run/evidence/persistent-install-fixture.json",
}

PERSISTENT_UNINSTALL_PROFILE = "persistent-uninstall-pointer-ready-v1"
PERSISTENT_UNINSTALL_ARGS = ["--duration-ms", "0", "--isolation", "strict", "--data-root", "/data/mingos",
                             "--state-file", "/run/evidence/desktop-state.json",
                             "--qualification-receipts", PERSISTENT_UNINSTALL_PROFILE]
PERSISTENT_UNINSTALL_FIXTURE = {
    "helper": "/opt/mingos/prepare-persistent-uninstall-fixture",
    "installRoot": PERSISTENT_INSTALL_ROOT,
    "initialWasm": "/opt/mingos/packages/initial/main.wasm",
    "sourceWasm": "/opt/mingos/packages/new/main.wasm",
    "sourceManifest": INSTALL_LIFECYCLE_SOURCE,
    "receipt": "/run/evidence/persistent-uninstall-fixture.json",
}
PERSISTENT_PAYLOAD_PROFILES = ("persistent-install", "persistent-uninstall")

def persistent_uninstall_session_contract(review):
    if (review.get("persistentInstallState") != PERSISTENT_INSTALL_ROOT
            or review.get("catalogPath") != PERSISTENT_INSTALL_CATALOG
            or review.get("installSource") != INSTALL_LIFECYCLE_SOURCE
            or review.get("persistentUninstallFixture") != PERSISTENT_UNINSTALL_FIXTURE
            or review.get("extraArgs") != PERSISTENT_UNINSTALL_ARGS
            or any(key in review for key in ("installRoot", "installFixture", "updateFixture", "installEvents", "installEventsSha256",
                                            "persistentInstallFixture", "persistentInstallFixtureSha256"))):
        raise ValueError("persistent uninstall requires exact persistent paths/args without legacy fixture overrides")

def persistent_uninstall_boot_role(number):
    if type(number) is not int or number not in (1, 2):
        raise ValueError("persistent uninstall requires fixed boot number 1 or 2")
    return "initialize-first" if number == 1 else "open-uninstalled"

def persistent_session_contract(review):
    if (review.get("persistentInstallState") != PERSISTENT_INSTALL_ROOT
            or review.get("catalogPath") != PERSISTENT_INSTALL_CATALOG
            or review.get("installSource") != INSTALL_LIFECYCLE_SOURCE
            or review.get("persistentInstallFixture") != PERSISTENT_INSTALL_FIXTURE
            or review.get("extraArgs") != PERSISTENT_INSTALL_ARGS
            or any(key in review for key in ("installRoot", "installFixture", "updateFixture", "installEvents", "installEventsSha256"))):
        raise ValueError("persistent install requires exact persistent paths/args without legacy fixture overrides")

def persistent_boot_role(number):
    if type(number) is not int or number not in (1, 2):
        raise ValueError("persistent install requires fixed boot number 1 or 2")
    return "initialize-first" if number == 1 else "open-existing"

def persistent_input_hashes(review):
    hashes = review.get("inputScripts")
    if ("inputScriptSha256" in review or not isinstance(hashes, dict)
            or set(hashes) != {"boot1Sha256", "boot2Sha256"}
            or any(not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value) for value in hashes.values())
            or hashes["boot1Sha256"] == hashes["boot2Sha256"]):
        raise ValueError("persistent install requires two distinct reviewed input hashes without single-hash fallback")
    return hashes

def strict_json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError("duplicate JSON key")
            value[key] = item
        return value
    def invalid_constant(value):
        raise ValueError("non-JSON numeric constant: " + value)
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)

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
    install_root = review.get("installRoot")
    if install_root is not None:
        if (not isinstance(install_root, str) or not install_root.startswith("/")
                or "\0" in install_root or install_root.endswith("/")
                or any(part in ("", ".", "..") for part in install_root.split("/")[1:])):
            raise ValueError("invalid reviewed install root")
    if review.get("profile") == "data-text-update" and install_root != PERSISTENT_UPDATE_INSTALL_ROOT:
        raise ValueError("update profile requires persistent install root")
    if review.get("profile") == "install-lifecycle" and install_root != INSTALL_LIFECYCLE_INSTALL_ROOT:
        raise ValueError("install profile requires reviewed private install root")
    if review.get("profile") == "install-lifecycle" and review.get("installEvents") != INSTALL_LIFECYCLE_EVENTS:
        raise ValueError("install profile requires reviewed event path")
    if review.get("profile") == "install-lifecycle" and review.get("installSource") != INSTALL_LIFECYCLE_SOURCE:
        raise ValueError("install profile requires reviewed install source")
    if review.get("profile") in PERSISTENT_PAYLOAD_PROFILES:
        if review["profile"] == "persistent-uninstall": persistent_uninstall_session_contract(review)
        else: persistent_session_contract(review)
        persistent_input_hashes(review)
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

def validate_reviewed_input(review, path, boot_number=None):
    """Return frozen input bytes and their digest for both validation and use."""
    if review.get("profile") in PERSISTENT_PAYLOAD_PROFILES:
        if review["profile"] == "persistent-uninstall": persistent_uninstall_boot_role(boot_number)
        else: persistent_boot_role(boot_number)
        expected = persistent_input_hashes(review)["boot%dSha256" % boot_number]
        mismatch = "reviewed persistent input script hash mismatch for boot " + str(boot_number)
    else:
        if boot_number is not None:
            raise ValueError("boot number is reserved for persistent install")
        expected = review.get("inputScriptSha256")
        if expected is None and review.get("profile") in ("data-text", "data-text-update", "install-lifecycle", "data-text-private-probe", "data-text-public-probe"):
            raise ValueError("data-text input script review hash required")
        mismatch = "reviewed input script hash mismatch"
    if path is None:
        if expected is not None: raise ValueError(mismatch)
        return None, None
    with Path(path).open("rb") as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024: raise ValueError("input script exceeds 1 MiB bound")
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None and digest != expected: raise ValueError(mismatch)
    return raw, digest

def reviewed_input_spec(review, raw):
    if raw is None: return None
    persistent = review.get("profile") in PERSISTENT_PAYLOAD_PROFILES
    value = strict_json(raw) if persistent else json.loads(raw)
    if persistent and (not isinstance(value, dict) or set(value) != {"schemaVersion", "requireApps", "steps"}):
        raise ValueError("persistent input requires exact schemaVersion/requireApps/steps fields")
    return value

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

def qemu_command(bundle, out, paused=False, data_disk=None, input_diagnostics=False, boot_number=None, persistent_profile=None):
    if persistent_profile is not None and persistent_profile not in PERSISTENT_PAYLOAD_PROFILES:
        raise ValueError("unknown persistent payload profile")
    if persistent_profile is not None and boot_number is None:
        raise ValueError("persistent profile requires fixed boot number")
    role = None
    kernel_prefix = "persistent_uninstall" if persistent_profile == "persistent-uninstall" else "persistent_install"
    if boot_number is not None:
        role = persistent_uninstall_boot_role(boot_number) if persistent_profile == "persistent-uninstall" else persistent_boot_role(boot_number)
        if paused or not data_disk:
            raise ValueError("persistent install requires a running guest with persistent data disk")
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
                      + ((" mingos.data_uuid=" + data_disk["uuid"] + " mingos.data_serial=" + data_disk["serial"]) if data_disk else "")
                      + ((" mingos." + kernel_prefix + "_boot=" + str(boot_number) + " mingos." + kernel_prefix + "_role=" + role) if role else "")]

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
