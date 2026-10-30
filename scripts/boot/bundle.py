#!/usr/bin/env python3
"""Assemble a RAM-root boot bundle in a task volume, only after payload/session review."""
import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from bootlib import checked_path, checked_session, sha256, validate_kernel_config
from infra import validate_fault_injection
from isolation_profiles import verify_review_and_files
from payload_profiles import verify_data_text, verify_data_text_private_probe, verify_data_text_public_probe, verify_public_probe
from wayland_receipt import POINTER_SYNC_POLICY

MODULES = ("virtio_pci", "virtio_gpu", "virtio_input", "virtio_console", "virtio_rng", "evdev", "virtio_blk", "ext4")


def fault_config_from_review(review):
    fault_config = review.get("faultInjection")
    if fault_config is None:
        return None
    return validate_fault_injection(fault_config)


def install_fault_injection(root, scripts, fault_config):
    if fault_config is None:
        return None
    fault_config = validate_fault_injection(fault_config)
    target = Path(root) / "etc/mingos"
    target.mkdir(parents=True, exist_ok=True)
    (target / "fault-injection.json").write_text(json.dumps(fault_config, sort_keys=True, separators=(",", ":")))
    shutil.copyfile(Path(scripts) / "fault_injector.py", target / "fault_injector.py")
    return fault_config


def verify_payload(payload, review):
    checked_session(review, payload)
    profile = review.get("profile", "desktop")
    if profile not in ("desktop", "data-text", "data-text-private-probe", "data-text-public-probe", "public-probe"):
        raise ValueError("unknown reviewed payload profile")
    if any(item.is_symlink() for item in payload.rglob("*")):
        raise ValueError("frozen payload must contain no symlinks")
    with (payload / "ming-app-host").open("rb") as source:
        header = source.read(4)
    if header != b"\x7fELF":
        raise ValueError("Linux ELF release host required")
    catalog = json.loads((payload / "catalog.json").read_text())
    if profile == "data-text":
        return verify_data_text(payload, review, catalog)
    if profile == "data-text-private-probe":
        return verify_data_text_private_probe(payload, review, catalog)
    if profile == "data-text-public-probe":
        return verify_data_text_public_probe(payload, review, catalog)
    if profile == "public-probe":
        return verify_public_probe(payload, review, catalog)
    apps = catalog["apps"]
    if len(apps) != 2 or set(catalog["autostart"]) != {app["id"] for app in apps}:
        raise ValueError("reviewed catalog must autostart both real Kotlin apps")
    ids = set()
    for app in apps:
        manifest = checked_path(payload, app["manifest"])
        package = manifest.parent
        data = json.loads(manifest.read_text())
        entry = checked_path(package, data["entry"])
        receipt = json.loads((package / "build-receipt.json").read_text())
        if receipt["status"] != "passed" or receipt["manifestSha256"] != sha256(manifest) or receipt["packagedSha256"] != sha256(entry):
            raise ValueError("unverified or changed Kotlin package: " + str(manifest))
        if entry.read_bytes()[:8] != b"\0asm\x01\0\0\0":
            raise ValueError("real Wasm package required")
        compiler = package / "compiler.wasm"
        if receipt["rawSha256"] != sha256(compiler):
            raise ValueError("raw Kotlin compiler output does not match receipt")
        ids.add(data["id"])
    if ids != {"dev.ming.compose-demo", "dev.ming.animated-demo"}:
        raise ValueError("expected real Compose and Motion packages")
    return catalog

class Rootfs:
    def __init__(self, root):
        self.root = root
        self.copied = set()
        for name in ("usr/bin", "usr/sbin", "usr/lib", "usr/lib64", "etc/mingos", "opt/mingos",
                     "proc", "sys", "dev", "run", "tmp"):
            (root / name).mkdir(parents=True, exist_ok=True)
        for name, target in (("bin", "usr/bin"), ("sbin", "usr/sbin"), ("lib", "usr/lib"), ("lib64", "usr/lib64")):
            (root / name).symlink_to(target, target_is_directory=True)

    def copy(self, source):
        source = Path(source)
        if not source.exists():
            raise FileNotFoundError(source)
        dest = self.root / source.relative_to("/")
        if source.is_dir():
            for entry in source.iterdir():
                if entry.name not in ("__pycache__", "test", "tests"):
                    self.copy(entry)
            return
        if str(source) in self.copied:
            return
        self.copied.add(str(source))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            # usr-merge aliases can lead to the same destination through /lib and /usr/lib.
            if not dest.exists() and not dest.is_symlink():
                dest.symlink_to(os.readlink(source))
            self.copy(source.resolve())
            return
        if not dest.exists():
            shutil.copy2(source, dest)
        self.copy_dependencies(source)

    def copy_dependencies(self, source):
        source = Path(source)
        with source.open("rb") as stream:
            elf = stream.read(4) == b"\x7fELF"
        if elf:
            # Weston plugins refer to its private libexec_weston, loaded by the host executable.
            # Resolve that installed private directory when checking plugin dependency closure.
            search = "/usr/lib/x86_64-linux-gnu/weston:/usr/lib/x86_64-linux-gnu/libweston-14"
            linked = subprocess.run(["ldd", str(source)], capture_output=True, text=True,
                                    env=dict(os.environ, LD_LIBRARY_PATH=search))
            if "not found" in linked.stdout:
                raise RuntimeError("unresolved ELF dependency: " + str(source) + "\n" + linked.stdout)
            for dependency in re.findall(r"(?:=>\s+)?(/[^\s(]+)", linked.stdout):
                self.copy(dependency)

def copy_runtime(system):
    # Kiosk boot needs no demos, GL renderer, RDP/VNC, PipeWire, or desktop shell.
    paths = ("/usr/bin/weston", "/usr/lib/x86_64-linux-gnu/weston/kiosk-shell.so",
             "/usr/lib/x86_64-linux-gnu/weston/libexec_weston.so.0",
             "/usr/lib/x86_64-linux-gnu/libweston-14/drm-backend.so",
             "/usr/sbin/blkid", "/usr/sbin/seatd", "/usr/lib/systemd/systemd-udevd", "/usr/bin/udevadm",
             "/usr/lib/udev", "/etc/udev", "/usr/share/X11/xkb", "/usr/share/libinput",
             "/etc/os-release", "/usr/bin/python3", "/usr/bin/busybox", "/usr/sbin/modprobe",
             "/usr/lib/python%d.%d" % sys.version_info[:2])
    for name in paths:
        system.copy(name)
    for name in ("/etc/udev/hwdb.bin", "/usr/lib/udev/hwdb.bin"):
        if Path(name).exists():
            system.copy(name)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--isolation-probes", type=Path)
    parser.add_argument("--isolation-profile", choices=("legacy-v1", "source-v2"), default="legacy-v1")
    parser.add_argument("--input-diagnostics", action="store_true")
    parser.add_argument("--wayland-client-diagnostics", action="store_true")
    parser.add_argument("--pointer-handshake-diagnostics", action="store_true")
    args = parser.parse_args()
    if args.isolation_profile != "legacy-v1" and not args.isolation_probes:
        raise ValueError("non-legacy isolation profile requires --isolation-probes")
    review = json.loads(args.review.read_text())
    fault_config = fault_config_from_review(review)
    catalog = verify_payload(args.payload, review)  # before any large filesystem write
    if args.input_diagnostics and (review.get("profile") not in ("data-text", "data-text-private-probe", "data-text-public-probe", "public-probe") or not args.isolation_probes):
        raise ValueError("input diagnostics requires reviewed data-text with strict probes")
    if args.wayland_client_diagnostics and not args.input_diagnostics:
        raise ValueError("Wayland client logging requires diagnostic-only input bundle")
    if args.pointer_handshake_diagnostics and not args.wayland_client_diagnostics:
        raise ValueError("pointer handshake requires instrumented Wayland client receipts")
    qualification = review.get("instrumentedQualification") is True
    if qualification and (not args.pointer_handshake_diagnostics or review.get("instrumentedProfile") != "data-text-pointer-ready-v1" or review.get("inputSynchronization") != POINTER_SYNC_POLICY):
        raise ValueError("explicit reviewed instrumented pointer qualification policy required")
    if qualification:
        expected_instrumentation = {"desktopEnv":{"WAYLAND_DEBUG":"client"}, "stderrLimitBytes":8388608,
            "observerSha256":sha256(Path(__file__).with_name("evdev_observer.py")), "observerMaxEvents":4096,
            "observerMaxSeconds":900, "exclusiveGrab":False, "readyCaptureSeconds":None}
        if review.get("instrumentation") != expected_instrumentation:
            raise ValueError("reviewed instrumentation does not match actual bounded guest configuration")
    probe_contract = None
    if args.isolation_probes:
        probe_contract = verify_review_and_files(args.isolation_profile, review, args.isolation_probes)
    args.output.mkdir(parents=True, exist_ok=False)
    root = args.output / "rootfs"
    root.mkdir()
    system = Rootfs(root)
    scripts = Path(__file__).resolve().parent
    kernels = list(Path("/boot").glob("vmlinuz-*"))
    if len(kernels) != 1:
        raise ValueError("exactly one installed task Linux kernel required")
    release = kernels[0].name.removeprefix("vmlinuz-")
    config = Path("/boot") / ("config-" + release)
    settings = config.read_text()
    validate_kernel_config(settings)
    copy_runtime(system)
    system.copy_dependencies(args.payload / "ming-app-host")
    for applet in ("sh", "mount", "umount", "mkdir", "sleep", "poweroff", "dmesg", "cat", "grep", "id", "uname", "chmod"):
        (root / "usr/bin" / applet).symlink_to("busybox")
    for module in MODULES:
        deps = subprocess.check_output(["modprobe", "--set-version", release, "--show-depends", module], text=True)
        for filename in re.findall(r"^insmod\s+(\S+)", deps, re.M):
            system.copy(filename)
    modules = Path("/usr/lib/modules") / release
    for name in ("modules.builtin", "modules.builtin.modinfo", "modules.order"):
        system.copy(modules / name)
    subprocess.run(["depmod", "-b", str(root), release], check=True)
    (root / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/sh\n")
    (root / "etc/group").write_text("root:x:0:\nvideo:x:44:\ninput:x:104:\n")
    (root / "etc/nsswitch.conf").write_text("passwd: files\ngroup: files\nhosts: files\n")
    (root / "etc/hostname").write_text("mingos-dev\n")
    shutil.copytree(args.payload, root / "opt/mingos", dirs_exist_ok=True)
    (root / "opt/mingos/ming-app-host").chmod(0o755)
    for name in ("init", "guest.py", "guest_platform.py", "infra.py", "bounded_log.py", "wayland_receipt.py", "worker_resources.py", "weston.ini"):
        target = root / ("init" if name == "init" else "etc/mingos/" + name)
        shutil.copyfile(scripts / name, target)
    if review.get("profile") in ("data-text-public-probe", "public-probe"):
        (root / "etc/mingos/public-storage-seed.json").write_text(json.dumps({
            "schemaVersion": 1,
            "profile": "data-text-public-probe",
            "packageIds": ["dev.ming.public-kernel-probe"],
        }, sort_keys=True, separators=(",", ":")))
    install_fault_injection(root, scripts, fault_config)
    if args.input_diagnostics:
        shutil.copyfile(scripts / "evdev_observer.py", root / "etc/mingos/evdev_observer.py")
        env = {"WAYLAND_DEBUG": "client"} if args.wayland_client_diagnostics else {}
        env_file = root / "etc/mingos/desktop-diagnostic-env.json"
        env_file.write_text(json.dumps(env, sort_keys=True, separators=(",", ":")))
        (root / "etc/mingos/input-diagnostics.json").write_text(json.dumps({
            "diagnosticOnly": not qualification, "profile": "input-pointer-ready-v1" if args.pointer_handshake_diagnostics else ("input-wayland-client-v1" if env else "input-transport-v1"),
            "evdevSha256": sha256(scripts / "evdev_observer.py"), "desktopEnv": env,
            "desktopEnvSha256": sha256(env_file), "stderrLimitBytes": 8 * 1024 * 1024,
            "readyCaptureSeconds": 15 if env and not qualification else None, "pointerHandshake": args.pointer_handshake_diagnostics}))
    if args.isolation_probes:
        for source_name, destination in (("mingos-isolation-probe", "usr/local/bin/mingos-isolation-probe"), ("vm-prepare.sh", "usr/local/libexec/mingos-isolation-prepare.sh")):
            target = root / destination; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(args.isolation_probes / source_name, target); target.chmod(0o755)
        system.copy_dependencies(args.isolation_probes / "mingos-isolation-probe")
        (root / "etc/mingos/probe.json").write_text(json.dumps({
            "schemaVersion": 1,
            "profile": probe_contract["name"],
            "sha256": probe_contract["artifactSha256"],
        }, sort_keys=True))
    (root / "init").chmod(0o755)
    (root / "etc/mingos/session.json").write_bytes(args.review.read_bytes())
    locks = Path("/opt/mingos-boot/locks")
    shutil.copytree(locks, args.output / "locks", ignore=shutil.ignore_patterns("apt-install.log"))
    if (locks / "apt-install.log").exists():
        with (locks / "apt-install.log").open("rb") as source, gzip.open(args.output / "locks/apt-install.log.gz", "wb") as target:
            shutil.copyfileobj(source, target)
    shutil.copyfile(config, args.output / "kernel.config")
    shutil.copyfile(kernels[0], args.output / "vmlinuz")
    # No device nodes, loop mounts, host chroot, or privileged operations.
    for item in [root, *root.rglob("*")]:
        os.utime(item, (0, 0), follow_symlinks=False)
    entries = sorted(["."] + [str(item.relative_to(root)) for item in root.rglob("*")])
    with (args.output / "initramfs.cpio.gz").open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            proc = subprocess.Popen(["cpio", "--null", "--create", "--format=newc", "--owner=0:0", "--reproducible"],
                                    cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            # cpio reads the complete path list before output can fill its pipe.
            import threading
            def feed():
                proc.stdin.write("\0".join(entries).encode() + b"\0")
                proc.stdin.close()
            thread = threading.Thread(target=feed)
            thread.start()
            shutil.copyfileobj(proc.stdout, compressed)
            thread.join()
            if proc.wait() != 0:
                raise RuntimeError("cpio archive creation failed")
    manifest = {"schemaVersion": 1, "infraApiVersion": 1, "inputDiagnostics": args.input_diagnostics, "diagnosticOnly": args.input_diagnostics and not qualification, "instrumentedQualification": qualification, "diagnosticProfile": ("input-pointer-ready-v1" if args.pointer_handshake_diagnostics else ("input-wayland-client-v1" if args.wayland_client_diagnostics else "input-transport-v1")) if args.input_diagnostics else None, "payloadProfile": review.get("profile", "desktop"), "isolationProfile": probe_contract["name"] if probe_contract else None, "strictProbeSha256": probe_contract["artifactSha256"] if probe_contract else None, "faultInjection": fault_config, "autostart": catalog["autostart"], "bootAttempted": False, "kernelRelease": release,
                "reviewSha256": sha256(args.review), "sessionReview": review,
                "sha256": {name: sha256(args.output / name) for name in ("vmlinuz", "initramfs.cpio.gz")},
                "payloadSha256": {str(p.relative_to(args.payload)): sha256(p) for p in args.payload.rglob("*") if p.is_file()},
                "scriptSha256": {name: sha256(scripts / name) for name in (("bundle.py", "isolation_profiles.py", "payload_profiles.py", "evdev_observer.py", "bounded_log.py", "wayland_receipt.py", "worker_resources.py", "bootlib.py", "guest.py", "guest_platform.py", "infra.py", "init", "weston.ini") + (("fault_injector.py",) if fault_config else ()))}}
    (args.output / "bundle.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
