#!/usr/bin/env python3
"""Exactly two fresh QEMU processes, one immutable bundle and one persistent owned disk."""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from infra import InputPlan, validate_cold_pair
from bootlib import PERSISTENT_PAYLOAD_PROFILES, reviewed_input_spec, sha256, strict_json, validate_bundle, validate_reviewed_input

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True); p.add_argument("--review", type=Path, required=True)
    p.add_argument("--work-root", type=Path, required=True); p.add_argument("--data-manifest", required=True)
    p.add_argument("--output", type=Path, required=True); p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--input-script", type=Path)
    p.add_argument("--boot1-input-script", type=Path)
    p.add_argument("--boot2-input-script", type=Path)
    args = p.parse_args()
    identity = sha256(args.bundle / "bundle.json")
    manifest = validate_bundle(args.bundle, sha256(args.review))
    raw_review = args.review.read_text()
    review = json.loads(raw_review)
    persistent = review.get("profile") in PERSISTENT_PAYLOAD_PROFILES
    frozen_inputs = {}
    if persistent:
        review = strict_json(raw_review)
        if args.input_script is not None:
            raise ValueError("persistent install cannot use a single input script")
        if review != manifest.get("sessionReview") or review.get("reviewed") is not True or not review.get("reviewer"):
            raise ValueError("main-thread review absent/changed")
        inputs = {1: args.boot1_input_script, 2: args.boot2_input_script}
        for number, source in inputs.items():
            frozen_inputs[number], _ = validate_reviewed_input(review, source, boot_number=number)
            pointer = manifest.get("diagnosticProfile") == "input-pointer-ready-v1"
            InputPlan(reviewed_input_spec(review, frozen_inputs[number]), args.output / ("boot-%d.inputs.jsonl" % number),
                      args.timeout, split_transactions=pointer, pointer_handshake=pointer,
                      persistent_update_gate=review.get("profile") == "persistent-update")
    else:
        if args.boot1_input_script is not None or args.boot2_input_script is not None:
            raise ValueError("per-boot input scripts are reserved for persistent install")
        inputs = {1: args.input_script, 2: args.input_script}
    args.output.mkdir(parents=True, exist_ok=False)
    if persistent:
        for number, raw in frozen_inputs.items():
            path = args.output / ("boot-%d.input.json" % number)
            with path.open("xb") as output: output.write(raw)
            path.chmod(0o400)
            inputs[number] = path
    reports = []
    try:
        for number in (1, 2):
            if sha256(args.bundle / "bundle.json") != identity: raise ValueError("bundle changed between boots")
            argv = [sys.executable, "-B", str(Path(__file__).with_name("vm.py")), "--bundle", str(args.bundle),
                    "--review", str(args.review), "--work-root", str(args.work_root), "--data-manifest", args.data_manifest,
                    "--output", str(args.output / ("boot-" + str(number))), "--timeout", str(args.timeout)]
            if persistent:
                argv += ["--boot-number", str(number)]
            if inputs[number]: argv += ["--input-script", str(inputs[number])]
            if manifest.get("diagnosticProfile") == "input-pointer-ready-v1": argv += ["--diagnostic-split-input", "--diagnostic-pointer-handshake"]
            with (args.output / ("boot-%d.stdout.log" % number)).open("wb") as out, (args.output / ("boot-%d.stderr.log" % number)).open("wb") as err:
                result = subprocess.run(argv, stdout=out, stderr=err, timeout=args.timeout + 45)
            reports.append(json.loads((args.output / ("boot-%d/result.json" % number)).read_text()))
            if result.returncode: raise RuntimeError("cold boot failed; preserve disk/evidence; no retry or format")
            if persistent and (reports[-1].get("bootNumber") != number
                               or reports[-1].get("inputScriptSha256") != review["inputScripts"]["boot%dSha256" % number]):
                raise ValueError("cold boot number/input receipt mismatch")
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
