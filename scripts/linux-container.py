#!/usr/bin/env python3
"""Isolated Docker/Weston acceptance. No Docker socket or host devices in container."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

spec = importlib.util.spec_from_file_location("linux_verify", Path(__file__).with_name("linux-verify.py"))
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)


def docker_commands(root, output, run_id, build_profile="debug"):
    acceptance.require(build_profile in ("debug", "release"), "Invalid build profile")
    root, output = Path(root).resolve(), Path(output).resolve()
    acceptance.require(output.is_relative_to(root / "artifacts") and output.name.startswith("linux-"),
                       "Container output must be artifacts/linux-<run-id> under this project")
    acceptance.require(run_id.isalnum(), "Run identifier must be alphanumeric")
    name = "mingos-linux-task3-" + run_id
    image = "mingos-linux-task3:" + run_id
    volume = name + "-cache"
    return {
        "image": image, "container": name, "volume": volume,
        "build": ["docker", "build", "--label", "mingos.acceptance=linux-task3", "--tag", image,
                  "--file", str(root / "scripts/linux/Dockerfile"), str(root / "scripts/linux")],
        "create": ["docker", "create", "--name", name, "--label", "mingos.acceptance=linux-task3", "--init",
                   "--cpus", "2", "--memory", "8g", "--pids-limit", "512", "--cap-drop", "ALL",
                   "--security-opt", "no-new-privileges", "--workdir", "/workspace",
                   "--mount", "type=bind,source=" + str(root) + ",target=/workspace,readonly",
                   "--mount", "type=bind,source=" + str(output) + ",target=/evidence",
                   "--mount", "type=volume,source=" + volume + ",target=/ming-cache",
                   "--env", "CARGO_HOME=/ming-cache/cargo", "--env", "CARGO_TARGET_DIR=/ming-cache/target",
                   "--env", "MING_LINUX_TARGET_DIR=/ming-cache/target", "--env", "RUSTUP_HOME=/usr/local/rustup",
                   "--env", "CARGO_BUILD_JOBS=2", "--env", "CARGO_PROFILE_DEV_DEBUG=0",
                   "--env", "CARGO_PROFILE_TEST_DEBUG=0", image, "python3",
                   "/workspace/scripts/linux-container.py", "--inside", "--output", "/evidence",
                   "--build-profile", build_profile],
    }


def check_all_features(root, output, environ, report, *, timeout, build_profile="debug", runner=acceptance.run_command):
    acceptance.require(build_profile in ("debug", "release") and report.get("buildProfile") == build_profile,
                       "All-features check build profile differs from host report")
    env = dict(environ)
    with tempfile.TemporaryDirectory(prefix="ming-linux-check-") as directory:
        work = Path(directory).resolve()
        policy = acceptance.cargo_environment(root, work, env, output)
        env["RUSTC"] = env["CARGO_BUILD_RUSTC"] = report["compiler"]["path"]
        env["RUSTUP_AUTO_INSTALL"] = "0"
        policy["effectiveSettings"] = acceptance.controlled_settings(env)
        args = ["cargo", "+" + acceptance.toolchain(root), "check", "--locked", "--all-features", "--all-targets",
                "--target", report["nativeTarget"], "--manifest-path", str(Path(root) / "Cargo.toml")]
        if build_profile == "release":
            args.append("--release")
        acceptance.check_cargo_configs(policy, output, "before-cargo-check")
        record = runner(args, cwd=work, env=env, timeout=timeout, output=output, name="linux-all-features-check")
        acceptance.check_cargo_configs(policy, output, "final")
        return {"command": record, "cargoPolicy": policy, "buildProfile": build_profile}

def inside(output, mode, renderer, command_timeout, build_profile="debug"):
    output = Path(output)
    env = dict(os.environ)
    tooling = {"status": "pending", "buildProfile": build_profile, "checks": [], "compositor": {
        "kind": "Weston headless backend with Pixman software renderer",
        "physicalGpuVerified": False, "physicalDisplayVerified": False}}
    root = Path(__file__).resolve().parents[1]
    weston = None
    log = None
    try:
        for name, argv in (("shell-syntax", ["sh", "-n", str(root / "scripts/linux-verify.sh")]),
                           ("fixture-tests", ["python3", "-B", "-m", "unittest", "discover", "-s", "tests",
                                              "-p", "test_linux_verify.py", "-v"]),
                           ("font-fixtures", ["python3", "-B", "-S", "-m", "unittest", "discover", "-s", "tests",
                                              "-p", "test_font_pack.py", "-v"]),
                           ("font-pack", ["python3", "-B", "-S", str(root / "scripts/verify-font-pack.py")]),
                           ("weston-version", ["weston", "--version"])):
            check = acceptance.run_command(argv, cwd=root, env=env,
                                           timeout=command_timeout if name == "fixture-tests" else 60,
                                           output=output, name=name)
            tooling["checks"].append(check)
            acceptance.checked(check)
        if mode in ("all", "wayland"):
            runtime = Path(tempfile.mkdtemp(prefix="mingos-wayland-"))
            runtime.chmod(0o700)
            env.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY="mingos-task3")
            # Deliberately dirty inherited variables prove the verifier clears them.
            env.update(DISPLAY=":999", WAYLAND_SOCKET="999")
            args = ["weston", "--backend=headless-backend.so", "--use-pixman", "--width=1280", "--height=960",
                    "--socket=mingos-task3", "--no-config", "--idle-time=0", "--log=" + str(output / "weston.log")]
            tooling["compositor"]["argv"] = args
            log = (output / "weston-stderr.log").open("wb")
            weston = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                      start_new_session=True, close_fds=True)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and weston.poll() is None:
                try:
                    acceptance.wayland_environment(env)
                    tooling["compositor"]["socketReady"] = True
                    break
                except acceptance.VerificationError:
                    time.sleep(0.1)
            if not tooling["compositor"].get("socketReady"):
                tooling["compositor"]["error"] = "Weston socket did not become ready; inspect weston.log"
        report = acceptance.verify(root, output / "run", mode=mode, renderer=renderer,
                                   command_timeout=command_timeout, environ=env, auto_close_ms=15000,
                                   build_profile=build_profile)
        if report["status"] == "passed":
            tooling["allFeaturesCheck"] = check_all_features(root, output, env, report, timeout=command_timeout,
                                                            build_profile=build_profile)
            acceptance.checked(tooling["allFeaturesCheck"]["command"])
            errors = acceptance.validate_report(report, root, output / "run")
            acceptance.require(not errors, "; ".join(errors))
        tooling["status"] = report["status"]
        print(json.dumps({"status": report["status"], "evidence": "/evidence/run/evidence.json",
                          "errors": report["errors"], "paths": {n: p["status"] for n, p in report["paths"].items()}}, indent=2))
        return 0 if report["status"] == "passed" else 1
    except (OSError, acceptance.VerificationError) as error:
        tooling.update(status="failed", error=str(error))
        print(json.dumps({"status": "failed", "errors": [str(error)]}), file=sys.stderr)
        return 1
    finally:
        if weston is not None:
            weston.terminate()
            try:
                weston.wait(timeout=5)
            except subprocess.TimeoutExpired:
                weston.kill()
                weston.wait(timeout=5)
            tooling["compositor"]["exitCodeAfterStop"] = weston.returncode
        if log is not None:
            log.close()
        (output / "tooling-evidence.json").write_text(json.dumps(tooling, indent=2) + "\n", encoding="utf-8")


def ensure_stopped(name, *, cwd, env, output, runner=acceptance.run_command):
    """Verify ownership once; subsequent operations use the immutable Docker ID."""
    acceptance.require(name.startswith("mingos-linux-task3-") and
                       all(c.isalnum() or c == "-" for c in name), "Refusing unowned container name")
    result = {"container": name, "stopped": False, "stateVerified": False, "commands": []}
    inspection_errors = (OSError, ValueError, KeyError, IndexError, TypeError,
                         AttributeError, acceptance.VerificationError)
    def command(label, argv, timeout):
        record = runner(argv, cwd=cwd, env=env, timeout=timeout, output=output, name=label)
        result["commands"].append(record)
        return record
    def inspect(label, reference, *, initial=False):
        result["stateVerified"] = False
        result.pop("state", None)
        record = command(label, ["docker", "inspect", reference], 15)
        acceptance.checked(record)
        data = acceptance.read_json(Path(output) / record["stdout"]["path"])[0]
        identity = data.get("Id")
        acceptance.require(isinstance(identity, str) and len(identity) == 64 and
                           all(c in "0123456789abcdef" for c in identity), "Missing immutable container ID")
        if initial:
            acceptance.require(data.get("Name") == "/" + name and
                               data.get("Config", {}).get("Labels", {}).get("mingos.acceptance") == "linux-task3",
                               "Container identity/ownership label mismatch; no stop/kill issued")
            result["containerId"] = identity
        else:
            acceptance.require(identity == result["containerId"], "Immutable container ID mismatch")
        state = data["State"]
        acceptance.require(type(state.get("Running")) is bool, "Container running state is unknown")
        result["state"] = state
        result["lastKnownState"] = state
        result["stateVerified"] = True
        return state["Running"]
    try:
        running = inspect("cleanup-inspect-before", name, initial=True)
        identity = result["containerId"]
        if running:
            result["stateVerified"] = False
            result.pop("state", None)
            try:
                command("cleanup-stop", ["docker", "stop", "--time", "5", identity], 15)
                running = inspect("cleanup-inspect-after-stop", identity)
            except inspection_errors as error:
                # Ownership was already established. Unknown daemon state must
                # not prevent a bounded kill of that same immutable container.
                result["postStopError"] = str(error)
                running = True
        if running:
            try:
                command("cleanup-kill", ["docker", "kill", "--signal", "KILL", identity], 10)
            except inspection_errors as error:
                result["killError"] = str(error)
            running = inspect("cleanup-inspect-final", identity)
        result["stopped"] = not running
        if running:
            result["error"] = "Owned container still running after bounded stop/kill"
    except inspection_errors as error:
        result["stateVerified"] = False
        result.pop("state", None)
        result["error"] = str(error)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mode", choices=("headless", "wayland", "all"), default="all")
    parser.add_argument("--renderer", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--build-profile", choices=("debug", "release"), default="debug")
    parser.add_argument("--command-timeout", type=acceptance.positive_seconds, default=1800)
    args = parser.parse_args(argv)
    if args.inside:
        return inside(args.output or Path("/evidence"), args.mode, args.renderer, args.command_timeout,
                      build_profile=args.build_profile)
    root = Path(__file__).resolve().parents[1]
    run_id = uuid.uuid4().hex[:12]
    output = (args.output or root / "artifacts" / ("linux-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + run_id)).resolve()
    plan = docker_commands(root, output, run_id, build_profile=args.build_profile)
    acceptance.require(not output.exists(), "Container output must be a fresh directory")
    output.mkdir(parents=True)
    plan["create"].extend(["--mode", args.mode, "--renderer", args.renderer,
                           "--command-timeout", str(args.command_timeout)])
    env = dict(os.environ)
    host = {"status": "pending", "buildProfile": args.build_profile, "resources": {k: plan[k] for k in ("image", "container", "volume")}, "checks": []}
    def run(name, argv, timeout=1800):
        record = acceptance.run_command(argv, cwd=root, env=env, timeout=timeout, output=output, name=name)
        host["checks"].append(record)
        (output / "docker-evidence.json").write_text(json.dumps(host, indent=2) + "\n", encoding="utf-8")
        acceptance.checked(record)
    creation_attempted = False
    try:
        run("docker-info", ["docker", "info", "--format", "{{json .}}"], 30)
        run("docker-build", plan["build"])
        run("docker-image", ["docker", "image", "inspect", plan["image"]], 30)
        creation_attempted = True
        run("docker-create", plan["create"], 60)
        run("docker-start", ["docker", "start", "--attach", plan["container"]], 7200)
        run("docker-container", ["docker", "inspect", plan["container"]], 30)
        report = acceptance.read_json(output / "run/evidence.json")
        acceptance.require(report.get("buildProfile") == args.build_profile,
                           "Container host report build profile differs from requested profile")
        errors = acceptance.validate_report(report, root, output / "run")
        acceptance.require(not errors, "; ".join(errors))
        host["status"] = "passed"
    except (OSError, acceptance.VerificationError, KeyboardInterrupt) as error:
        host.update(status="failed", error=str(error))
    finally:
        if creation_attempted:
            host["cleanup"] = ensure_stopped(plan["container"], cwd=root, env=env, output=output)
            if not host["cleanup"]["stopped"]:
                host.update(status="failed", error=host["cleanup"].get("error", "Container cleanup failed"))
        # Keep only this run's labeled, stopped resources for diagnosis. No pruning.
        (output / "docker-evidence.json").write_text(json.dumps(host, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": host["status"], "output": str(output), "resources": host["resources"],
                      "error": host.get("error")}, indent=2))
    return 0 if host["status"] == "passed" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, acceptance.VerificationError) as error:
        print(json.dumps({"status": "failed", "errors": [str(error)]}), file=sys.stderr)
        sys.exit(1)
