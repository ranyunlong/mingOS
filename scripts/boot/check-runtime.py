#!/usr/bin/env python3
"""Exercise rootfs file/dependency copying in capped tmpfs. No payload, archive, or boot."""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from bundle import Rootfs, copy_runtime
from bootlib import validate_kernel_config

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"bootAttempted": False, "payloadUsed": False, "archiveCreated": False}
    try:
        config = next(Path("/boot").glob("config-*"))
        validate_kernel_config(config.read_text())
        with tempfile.TemporaryDirectory(prefix="mingos-runtime-", dir="/tmp") as temporary:
            root = Path(temporary)
            runtime = Rootfs(root)
            copy_runtime(runtime)
            report["copiedSourcePaths"] = len(runtime.copied)
            regular = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
            report["regularFiles"] = len(regular)
            report["regularBytes"] = sum(p.stat().st_size for p in regular)
            report["kernelConfig"] = config.name
            report["status"] = "runtime-copy-passed-not-booted"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = repr(error)
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(report["status"] == "failed")

if __name__ == "__main__":
    raise SystemExit(main())
