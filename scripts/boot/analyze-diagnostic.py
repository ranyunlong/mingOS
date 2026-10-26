#!/usr/bin/env python3
"""Cross-check saved kernel/DRM/native app evidence; never certifies a final OS."""
import argparse
import json
import re
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--decoded", type=Path, required=True)
    parser.add_argument("--bundle-manifest", type=Path)
    parser.add_argument("--payload", type=Path)
    args = parser.parse_args()
    if bool(args.bundle_manifest) != bool(args.payload):
        parser.error("--bundle-manifest and --payload must be used together")
    serial = (args.run / "kernel-serial.log").read_text(errors="replace")
    records = [json.loads(line) for line in (args.decoded / "events.jsonl").read_text().splitlines()]
    weston = (args.decoded / "weston.log").read_text(errors="replace") if (args.decoded / "weston.log").exists() else ""
    desktop = (args.decoded / "desktop.log").read_text(errors="replace") if (args.decoded / "desktop.log").exists() else ""
    outcome = None
    decoder = json.JSONDecoder()
    for candidate in re.finditer(r"(?m)^\{", desktop):
        try:
            value, _ = decoder.raw_decode(desktop[candidate.start():])
            if isinstance(value, dict) and value.get("runtime") == "ming-desktop":
                outcome = value
        except ValueError:
            pass
    starts = [x for x in records if x["type"] == "guest-start"]
    exits = [x for x in records if x["type"] == "desktop-exit"]
    tables = [x for x in records if x["type"] == "process-table"]
    instances = outcome.get("instances", []) if outcome else []
    workers = {x["workerPid"] for x in instances if x.get("workerPid") is not None}
    concurrent = any(workers and workers <= {p["pid"] for p in table["processes"]} for table in tables)
    parents = {int(match.group(1)) for table in tables for process in table["processes"]
               if process["pid"] in workers
               for match in [re.search(r"(?m)^PPid:\s*(\d+)", process["status"])] if match}
    app_ids = {x.get("packageId") for x in instances}
    identities = {}
    if args.bundle_manifest:
        from bootlib import checked_path, sha256
        from bundle import verify_payload
        manifest = json.loads(args.bundle_manifest.read_text())
        catalog = verify_payload(args.payload, manifest["sessionReview"])
        for name, digest in manifest["payloadSha256"].items():
            if sha256(checked_path(args.payload, name)) != digest:
                raise ValueError("staged payload differs from boot bundle: " + name)
        for app in catalog["apps"]:
            path = checked_path(args.payload, app["manifest"])
            identities[app["id"]] = {"packageId": json.loads(path.read_text())["id"],
                                     "manifestSha256": sha256(path)}
    ready_apps = {item["app"] for item in instances
                  if item.get("firstReadyMs") is not None and (item.get("presentedSourceSequence") or 0) > 0}
    identified_ready = {identities[app]["packageId"] for app in ready_apps if app in identities}
    report = {
        "scope": "authorized 7C 15-second bounded kernel/DRM diagnostic only",
        "finalSystemAccepted": False,
        "kernelRunning": "Linux version 6.12.113+deb13-amd64" in serial and "Run /init as init process" in serial and bool(starts),
        "kernelBootId": starts[0]["bootId"] if starts else None,
        "virtioGpuInitialized": "Initialized virtio_gpu" in serial,
        "crngInitialized": "random: crng init done" in serial,
        "drmPixmanKiosk": all(text in weston for text in ("using /dev/dri/card0", "Using Pixman renderer", "Output 'Virtual-1' enabled", "kiosk-shell.so")),
        "inputDevices": [x for x in records if x["type"] == "input-devices"],
        "desktopProcessStarts": [x for x in records if x["type"] == "process-start" and x["name"] == "desktop"],
        "guestErrors": [x for x in records if x["type"] == "guest-error"],
        "desktopExitCodes": [x["code"] for x in exits],
        "desktopOutcome": outcome,
        "runtimePackageIdsAvailable": all(item.get("packageId") is not None for item in instances) if instances else False,
        "verifiedBundlePackageIdentity": identities,
        "bothApplicationWindowsReady": {1, 2} <= ready_apps,
        "bothVerifiedKotlinApplicationsReady": {"dev.ming.compose-demo", "dev.ming.animated-demo"} <= identified_ready,
        "appShutdownErrors": {item["app"]: item.get("error") for item in instances},
        "applicationNaturalCompletionClaimed": False,
        "reportedWorkerPids": sorted(workers),
        "workersObservedTogetherInProc": len(workers) >= 2 and concurrent,
        "observedWorkerParentPids": sorted(parents),
        "nativePresentedFrames": outcome.get("framesPresented", 0) if outcome else 0,
        "nativeWaylandSoftbuffer": bool(outcome and outcome.get("windowSystem") == "wayland" and outcome.get("presentationBackend") == "softbuffer" and outcome.get("offscreenFrames") == 0),
        "cleanGuestPowerdown": "reboot: Power down" in serial,
        "qmpScreendumps": sorted(p.name for p in args.run.glob("scanout-*.ppm")),
        "scanoutVisualReviewRequired": True,
        "kernelOomMessages": [line for line in serial.splitlines() if re.search(r"(?i)out of memory:|oom-kill:|killed process \d", line)],
        "nonfatalLocaleWarning": "couldn't find a Compose file for locale" in desktop,
    }
    (args.run / "diagnostic-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in ("desktopOutcome", "inputDevices", "qmpScreendumps")}, indent=2))

if __name__ == "__main__":
    main()
