#!/usr/bin/env python3
"""Build only the task tools image; never builds a payload or boots a guest."""
import argparse
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

TAG = "mingos-boot-tools:20261025-trixie13"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    # Docker Desktop's current backing VHDX is on C:, same drive as this workspace.
    free = shutil.disk_usage(Path.cwd()).free
    if free < 1800 * 1024**2:
        raise SystemExit("BUILD_BLOCKED: less than 1800 MiB free on workspace/Docker backing drive")
    expected = "sha256:e91ad5a3eae8964aab759b9db30997a49be1a57fb97e63529f59fa5b16bb2722"
    actual = subprocess.run(["docker", "image", "inspect", "mingos-linux-task3:b89a9dc7cdce",
                             "--format", "{{.Id}}"], capture_output=True, text=True, check=True)
    if actual.stdout.strip() != expected:
        raise SystemExit("BUILD_BLOCKED: task base tag does not match immutable expected image ID")
    args.output.mkdir(parents=True, exist_ok=False)
    context = Path(__file__).resolve().parent
    inputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (context / "Dockerfile.tools", context / "packages.lock")}
    argv = ["docker", "build", "--pull=false", "--progress=plain", "--network=default",
            "--file", str(context / "Dockerfile.tools"), "--tag", TAG, str(context)]
    report = {"argv": argv, "inputSha256": inputs, "freeBytesBefore": free, "bootAttempted": False}
    started = time.monotonic()
    with (args.output / "build.stdout.log").open("wb") as out, (args.output / "build.stderr.log").open("wb") as err:
        try:
            proc = subprocess.run(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL, timeout=1800)
            report["exitCode"] = proc.returncode
        except subprocess.TimeoutExpired:
            # Do not prune or delete builder state: preserve logs for diagnosis.
            report["exitCode"] = 124
    report["elapsedSeconds"] = time.monotonic() - started
    report["freeBytesAfter"] = shutil.disk_usage(Path.cwd()).free
    if report["exitCode"] == 0:
        proc = subprocess.run(["docker", "image", "inspect", TAG, "--format", "{{.Id}}"],
                              capture_output=True, text=True, check=True)
        report["imageId"] = proc.stdout.strip()
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return report["exitCode"]

if __name__ == "__main__":
    raise SystemExit(main())
