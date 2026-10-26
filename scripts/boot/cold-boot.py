#!/usr/bin/env python3
"""Exactly two fresh QEMU processes, one immutable bundle and one persistent owned disk."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from infra import validate_cold_pair
from bootlib import sha256, validate_bundle

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True); p.add_argument("--review", type=Path, required=True)
    p.add_argument("--work-root", type=Path, required=True); p.add_argument("--data-manifest", required=True)
    p.add_argument("--output", type=Path, required=True); p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--input-script", type=Path)
    args = p.parse_args()
    identity = sha256(args.bundle / "bundle.json")
    manifest = validate_bundle(args.bundle, sha256(args.review))
    args.output.mkdir(parents=True, exist_ok=False)
    reports = []
    try:
        for number in (1, 2):
            if sha256(args.bundle / "bundle.json") != identity: raise ValueError("bundle changed between boots")
            argv = [sys.executable, "-B", str(Path(__file__).with_name("vm.py")), "--bundle", str(args.bundle),
                    "--review", str(args.review), "--work-root", str(args.work_root), "--data-manifest", args.data_manifest,
                    "--output", str(args.output / ("boot-" + str(number))), "--timeout", str(args.timeout)]
            if args.input_script: argv += ["--input-script", str(args.input_script)]
            if manifest.get("diagnosticProfile") == "input-pointer-ready-v1": argv += ["--diagnostic-split-input", "--diagnostic-pointer-handshake"]
            with (args.output / ("boot-%d.stdout.log" % number)).open("wb") as out, (args.output / ("boot-%d.stderr.log" % number)).open("wb") as err:
                result = subprocess.run(argv, stdout=out, stderr=err, timeout=args.timeout + 45)
            reports.append(json.loads((args.output / ("boot-%d/result.json" % number)).read_text()))
            if result.returncode: raise RuntimeError("cold boot failed; preserve disk/evidence; no retry or format")
        validate_cold_pair(*reports)
        report = {"status": "same-bundle-disk-cold-boot-verified", "bootIds": [x["bootId"] for x in reports],
                  "instrumentedProfile": manifest.get("diagnosticProfile"), "bundleIdentity": identity, "diskUuid": reports[0]["diskUuid"],
                  "markers": [x["marker"] for x in reports], "finalSystemAccepted": False}
    except Exception as error:
        report = {"status": "failed", "error": repr(error), "finalSystemAccepted": False}
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return int(report["status"] == "failed")

if __name__ == "__main__": raise SystemExit(main())
