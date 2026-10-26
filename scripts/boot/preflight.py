#!/usr/bin/env python3
"""Read-only host/image probes; optional apt resolution is confined to tmpfs."""
import argparse
import datetime
import json
import shutil
import subprocess
from pathlib import Path

IMAGE = "sha256:e91ad5a3eae8964aab759b9db30997a49be1a57fb97e63529f59fa5b16bb2722"
CACHE = "mingos-linux-task3-b89a9dc7cdce-cache"
PACKAGES = ["qemu-system-x86", "cpio", "busybox-static", "linux-image-amd64", "udev"]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resolve-packages", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"timestampUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "image": IMAGE, "cacheReadOnly": CACHE, "bootAttempted": False,
              "commands": [], "tools": {name: shutil.which(name) for name in
                                         ("docker", "wsl", "qemu-system-x86_64", "qemu-img", "python")},
              "workspaceDisk": dict(zip(("total", "used", "free"), shutil.disk_usage(Path.cwd())))}
    def run(name, command, timeout=60):
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, timeout=timeout, check=False)
            code, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as error:
            code, stdout, stderr = 124, error.stdout or b"", error.stderr or b""
        (args.output / (name + ".stdout.log")).write_bytes(stdout)
        (args.output / (name + ".stderr.log")).write_bytes(stderr)
        report["commands"].append({"name": name, "argv": command, "exitCode": code})
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        return code
    run("docker-version", ["docker", "version", "--format", "{{json .}}"])
    run("docker-info", ["docker", "info", "--format", "{{json .DockerRootDir}} {{json .DriverStatus}}"])
    run("image", ["docker", "image", "inspect", IMAGE, "--format",
                  "ID={{.Id}} OS={{.Os}} ARCH={{.Architecture}} SIZE={{.Size}}"])
    run("cache", ["docker", "volume", "inspect", CACHE])
    run("docker-disk", ["docker", "system", "df"])
    run("wsl", ["wsl", "--list", "--verbose"])
    base = ["docker", "run", "--rm", "--pull", "never", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m"]
    probe = """cat /etc/os-release; rustc --version; weston --version
command -v qemu-system-x86_64 || true
command -v cpio || true
df -h / /cache
ls -l /dev/kvm 2>/dev/null || true
find /cache -maxdepth 4 -name ming-app-host -type f -printf '%p %s bytes\\n'
cat /etc/apt/sources.list.d/debian.sources
"""
    run("container", base + ["--network", "none", "--mount",
        "type=volume,source=" + CACHE + ",target=/cache,readonly", IMAGE, "sh", "-c", probe])
    run("weston-help", base + ["--network", "none", IMAGE, "weston", "--help"])
    if args.resolve_packages:
        # No package installation: lists and apt temporary files live in capped RAM mounts.
        script = """set -eu
apt-get -o APT::Sandbox::User=root update
apt-cache policy """ + " ".join(PACKAGES) + """
apt-get -o APT::Sandbox::User=root --simulate --no-install-recommends install """ + " ".join(PACKAGES) + """
apt-get -o APT::Sandbox::User=root --print-uris --yes --download-only --no-install-recommends install """ + " ".join(PACKAGES)
        run("apt-resolution", base + ["--tmpfs", "/var/lib/apt/lists:rw,size=192m",
            "--tmpfs", "/var/cache/apt:rw,size=16m", "--tmpfs", "/tmp:rw,size=32m",
            IMAGE, "sh", "-c", script], 300)
    print(json.dumps(report, indent=2))
    return int(any(item["exitCode"] for item in report["commands"]))

if __name__ == "__main__":
    raise SystemExit(main())
