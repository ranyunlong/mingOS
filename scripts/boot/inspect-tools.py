#!/usr/bin/env python3
"""Inspect tools/kernel and copy package provenance, without loading a kernel."""
import argparse
import hashlib
import gzip
import json
import shutil
import subprocess
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"bootAttempted": False, "commands": [], "debs": {}}
    locks = Path("/opt/mingos-boot/locks")
    shutil.copytree(locks, args.output / "locks", ignore=shutil.ignore_patterns("apt-install.log"))
    raw_log = locks / "apt-install.log"
    if raw_log.exists():
        with raw_log.open("rb") as source, gzip.open(args.output / "locks/apt-install.log.gz", "wb") as target:
            shutil.copyfileobj(source, target)
    for line in (locks / "download.sha256").read_text().splitlines():
        expected, name = line.split(maxsplit=1)
        archive = Path("/opt/mingos-boot/debs") / name.removeprefix("./")
        with archive.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != expected:
            raise ValueError("archive hash mismatch: " + name)
        report["debs"][archive.name] = {"sha256": actual, "bytes": archive.stat().st_size}
    commands = {
        "qemu-version": ["qemu-system-x86_64", "--version"],
        "qemu-help": ["qemu-system-x86_64", "--help"],
        "virtio-vga-help": ["qemu-system-x86_64", "-device", "virtio-vga,help"],
        "seatd-help": ["seatd", "-h"],
        "weston-help": ["weston", "--help"],
        "guest-syntax": ["/bin/sh", "-n", "/src/init"],
        "kernel-files": ["sh", "-c", "ls -l /boot; cat /boot/config-*"],
        "modules": ["sh", "-c", "set -eu; release=$(basename /boot/vmlinuz-* | cut -c9-); for module in virtio_pci virtio_gpu virtio_input virtio_console; do modprobe --set-version \"$release\" --show-depends \"$module\"; done"],
        "package-paths": ["dpkg-query", "-L", "weston", "libweston-14-0", "seatd", "udev"],
        "python": ["/usr/bin/python3", "-c", "import sys; print(sys.version); print(sys.executable)"],
    }
    for name, argv in commands.items():
        proc = subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        (args.output / (name + ".stdout.log")).write_bytes(proc.stdout)
        (args.output / (name + ".stderr.log")).write_bytes(proc.stderr)
        report["commands"].append({"name": name, "argv": argv, "exitCode": proc.returncode})
    report["status"] = "passed" if all(item["exitCode"] == 0 for item in report["commands"]) else "failed"
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(report["status"] != "passed")

if __name__ == "__main__":
    raise SystemExit(main())
