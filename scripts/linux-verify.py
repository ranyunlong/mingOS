#!/usr/bin/env python3
"""Native Linux acceptance using existing Wasm artifacts and Python's stdlib.

Exit zero means every requested path, prerequisite, build, test and evidence
check passed. Unsupported platforms leave pending evidence and exit nonzero.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import re
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid


class VerificationError(Exception):
    """An acceptance condition failed; never treat this as a skip/pass."""


APPS = {"read-file": (1, "samples/read-file/data/hello.txt"),
        "image-viewer": (2, "samples/image-viewer/data/coastal.png"),
        "text-demo": (3, None),
        "ui-demo": (4, None),
        "shadow-demo": (4, None),
        "compose-demo": (4, None),
        "animated-demo": (5, None)}
PATH_APPS = {"headless-sdk1": "read-file", "headless-sdk2": "image-viewer",
             "headless-sdk3": "text-demo", "headless-sdk4": "ui-demo",
             "wayland-sdk2": "image-viewer", "wayland-sdk3": "text-demo",
             "wayland-sdk4": "ui-demo", "headless-shadow": "shadow-demo",
             "wayland-shadow": "shadow-demo", "headless-compose": "compose-demo",
             "wayland-compose": "compose-demo", "headless-animated": "animated-demo",
             "wayland-animated": "animated-demo"}
PATHS = tuple(PATH_APPS)
FONT_ASSETS = ("assets/fonts/NotoSansSC-Regular.otf", "assets/fonts/OFL.txt",
               "assets/fonts/NOTICE.txt", "assets/fonts/font-pack.json")
CHECKS = ("compiler", "rustc", "cargo", "cc", "libraries", "build", "test")


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def integer(value, minimum=0, maximum=None):
    return type(value) is int and value >= minimum and (maximum is None or value <= maximum)


def file_record(path, base):
    path, base = Path(path).resolve(), Path(base).resolve()
    require(path.is_relative_to(base), f"File escapes its evidence/source root: {path}")
    require(path.is_file(), f"Missing file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": path.relative_to(base).as_posix(), "bytes": path.stat().st_size,
            "sha256": digest.hexdigest()}


def read_json(path):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid(value):
        raise VerificationError(f"Invalid JSON constant: {value}")
    path = Path(path)
    require(path.stat().st_size <= 4 * 1024 * 1024, f"JSON exceeds 4 MiB: {path}")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=invalid)


def project_configs(root):
    directories = [Path(root) / ".cargo"]
    directories.extend((Path(root) / "crates").rglob(".cargo"))
    return sorted(p for directory in directories for p in
                  (directory / "config", directory / "config.toml") if p.is_file())


def controlled_settings(environ):
    return {key: value for key, value in environ.items() if key in
            ("PATH", "CARGO_HOME", "RUSTUP_HOME", "RUSTUP_TOOLCHAIN", "MING_LINUX_TARGET_DIR",
             "CC", "CXX", "AR", "LD", "RANLIB", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS",
             "LD_PRELOAD", "LD_LIBRARY_PATH") or key.startswith(
                ("RUSTC", "RUSTDOC", "RUSTFLAGS", "CARGO_BUILD_", "CARGO_TARGET_", "CARGO_PROFILE_",
                 "CARGO_ENCODED_", "CC_", "CXX_", "AR_", "CFLAGS_", "HOST_CC", "TARGET_CC"))}


def config_reference(path, output, role):
    # Resolve the parent only, preserving a config symlink for lstat checks.
    path = Path(path).parent.resolve() / Path(path).name
    output = Path(output).resolve()
    if path.is_relative_to(output):
        return {"scope": "evidence", "path": path.relative_to(output).as_posix(), "role": role}
    return {"scope": "external", "style": "windows" if os.name == "nt" else "posix",
            "path": str(path), "role": role}


def config_path(reference, output):
    text = reference["path"]
    if reference["scope"] == "evidence":
        require(not PurePosixPath(text).is_absolute() and not PureWindowsPath(text).drive,
                "Cargo config evidence path must be relative")
        base = Path(output).resolve()
        path = base / text
        path = path.parent.resolve() / path.name
        require(path.is_relative_to(base), "Cargo config discovery path escapes evidence")
        return path
    require(reference["scope"] == "external", "Unknown Cargo config discovery scope")
    style = reference.get("style")
    require(style in ("windows", "posix"), "Unknown Cargo config path style")
    pure = PureWindowsPath(text) if style == "windows" else PurePosixPath(text)
    require(pure.is_absolute(), "External Cargo config path must be absolute")
    if (style == "windows") != (os.name == "nt"):
        # A Windows recheck cannot observe a Linux container's absolute paths.
        # Its final recorded absence is authoritative for those foreign paths.
        return None
    return Path(text)


def config_presence(reference, output):
    path = config_path(reference, output)
    if path is None:
        return None
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def check_cargo_configs(policy, output, stage):
    observations = [{"path": dict(ref), "present": config_presence(ref, output)}
                    for ref in policy["configDiscovery"]]
    policy["configChecks"].append({"stage": stage, "observations": observations})
    for observation in observations:
        require(observation["present"] is False,
                f"External Cargo config is present or unverified at {stage}: {observation['path']['path']}")


def validate_cargo_configs(policy, output):
    references = policy["configDiscovery"]
    require(isinstance(references, list) and references, "Missing Cargo configuration discovery record")
    home = [ref for ref in references if ref.get("role") == "cargo-home"]
    require(len(home) == 2 and {PureWindowsPath(ref["path"]).name for ref in home} == {"config", "config.toml"},
            "Missing chosen CARGO_HOME config discovery paths")
    history = policy["configChecks"]
    require(isinstance(history, list) and history and history[-1]["stage"] == "final",
            "Cargo configuration absence was not checked at workflow completion")
    for check in history:
        observations = check["observations"]
        require(len(observations) == len(references), "Incomplete Cargo config absence checkpoint")
        for reference, observation in zip(references, observations):
            require(observation["path"] == reference and observation["present"] is False,
                    "Cargo configuration presence or unknown state was recorded")
    for reference in references:
        present = config_presence(reference, output)
        require(present is not True, f"External Cargo config appeared after execution: {reference['path']}")


def cargo_environment(root, work, environ, output):
    """Isolate Cargo discovery and reject overrides that can replace tests/compiler."""
    inherited = controlled_settings(environ)
    allowed = {"PATH", "CARGO_HOME", "RUSTUP_HOME", "MING_LINUX_TARGET_DIR",
               "CARGO_TARGET_DIR", "CARGO_BUILD_TARGET", "CARGO_BUILD_JOBS"}
    for key in inherited:
        require(key in allowed or key.startswith("CARGO_PROFILE_"),
                f"Unsupported inherited compiler/wrapper/runner setting: {key}")
    dangerous = {"runner", "rustc", "rustc-wrapper", "rustc-workspace-wrapper", "rustdoc",
                 "rustflags", "rustdocflags", "linker", "env", "include", "alias"}
    def inspect_config(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                require(key not in dangerous, f"Unsupported Cargo configuration {key}: {path}")
                inspect_config(item, path)
    for path in project_configs(root):
        with path.open("rb") as stream:
            inspect_config(tomllib.load(stream), path)
    home = Path(environ.get("CARGO_HOME", str(Path(output) / "cargo-home"))).resolve()
    home.mkdir(parents=True, exist_ok=True)
    # Cargo discovers configuration from cwd ancestors and CARGO_HOME. Using a
    # temporary cwd outside the project prevents source/ancestor configs loading.
    # External config is rejected rather than silently trusting compiler/runners.
    references = [config_reference(home / name, output, "cargo-home") for name in ("config", "config.toml")]
    references.extend(config_reference(parent / ".cargo" / name, output, "cwd-ancestor")
                      for parent in [work, *work.parents] for name in ("config", "config.toml"))
    environ["CARGO_HOME"] = str(home)
    policy = {"isolatedWorkingDirectory": True, "invocationCwd": str(work), "cargoHome": str(home),
              "projectConfigPolicy": "fingerprinted, inspected for overrides, and not implicitly loaded",
              "inheritedSettings": inherited, "configDiscovery": references, "configChecks": []}
    check_cargo_configs(policy, output, "preflight")
    return policy

def collect_inputs(root):
    root = Path(root).resolve()
    records = {}
    try:
        for app, (sdk, grant) in APPS.items():
            manifest = root / "artifacts" / app / "app.json"
            data = read_json(manifest)
            require(data.get("schemaVersion") == 1 and data.get("sdkVersion") == sdk
                    and data.get("id") == "dev.ming." + app,
                    f"Unexpected manifest identity or SDK: {manifest}")
            # Acceptance uses existing packages, without rebuilding Kotlin.
            require(data.get("entry") == "main.wasm", f"Unexpected package entry: {manifest}")
            wasm = manifest.with_name("main.wasm")
            with wasm.open("rb") as stream:
                require(stream.read(8) == b"\0asm\x01\0\0\0", f"Invalid Wasm header: {wasm}")
            records[app + ".manifest"] = file_record(manifest, root)
            records[app + ".wasm"] = file_record(wasm, root)
            if grant is not None:
                records[app + ".grant"] = file_record(root / grant, root)
            build_info = manifest.with_name("build-info.json")
            if build_info.exists():
                require(read_json(build_info).get("packagedSha256") == records[app + ".wasm"]["sha256"],
                        f"Packaged Wasm SHA256 hash mismatch: {build_info}")
                records[app + ".build-info"] = file_record(build_info, root)
        # The embedded font is larger than the JSON limit; stream every asset
        # through file_record rather than reading the font as JSON or text.
        for relative in FONT_ASSETS:
            records[relative] = file_record(root / relative, root)
        for path in project_configs(root):
            records[path.relative_to(root).as_posix()] = file_record(path, root)
        for relative in ("Cargo.lock", "Cargo.toml", "rust-toolchain.toml",
                         "crates/ming-app-host/Cargo.toml", "scripts/verify-font-pack.py",
                         "tests/test_font_pack.py"):
            records[relative] = file_record(root / relative, root)
        for path in sorted((root / "crates/ming-app-host").rglob("*")):
            if path.is_file() and path.suffix in (".rs", ".wgsl"):
                records[path.relative_to(root).as_posix()] = file_record(path, root)
        # Fixture payloads and their independent oracle/provenance participate in
        # compilation and assertions even when their extension is not source code.
        for path in sorted((root / "crates/ming-app-host/tests/fixtures").rglob("*")):
            if path.is_file():
                records[path.relative_to(root).as_posix()] = file_record(path, root)
        for relative in ("scripts/linux/Dockerfile", "tests/test_linux_verify.py"):
            if (root / relative).is_file():
                records[relative] = file_record(root / relative, root)
        for path in sorted((root / "scripts").glob("linux-*")):
            if path.is_file():
                records[path.relative_to(root).as_posix()] = file_record(path, root)
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise VerificationError(f"Cannot fingerprint inputs: {error}") from error
    return records


def validate_outcome(data, *, app, renderer, window_system, grant, snapshot=True):
    """Validate host metadata after PNG writing; graphical paths request snapshots."""
    require(isinstance(data, dict), "Host stdout must be a JSON object")
    require(data.get("appId") == "dev.ming." + app, "Incorrect application identity")
    require(data.get("runtime") == "rust-wasmtime", "Expected native rust-wasmtime runtime")
    require(data.get("windowSystem") == window_system,
            f"Expected actual windowSystem={window_system}, got {data.get('windowSystem')!r}")
    presented = data.get("framesPresented")
    require(integer(presented), "Missing or invalid framesPresented")
    require("presentedFrame" in data, "Missing presentedFrame telemetry")
    presented_frame = data["presentedFrame"]
    if window_system == "wayland":
        require(presented >= 1, "Wayland must present at least one application frame")
        require(isinstance(presented_frame, dict), "Wayland requires a presentedFrame identity")
        for name in ("sequence", "width", "height"):
            require(integer(presented_frame.get(name), 1), f"Invalid presentedFrame.{name}")
    else:
        require(presented == 0, "Headless must not report presented frames")
        require(presented_frame is None, "Headless presentedFrame must be explicit null")
    require(integer(data.get("exitCode")) and data["exitCode"] == 0, "Guest exitCode must be zero")
    for name in ("requests", "fuelConsumed"):
        require(integer(data.get(name), 1), f"Invalid {name}")
    for name in ("durationMs", "entropyBytes", "bulkBytesRead", "framesSubmitted"):
        require(integer(data.get(name)), f"Invalid {name}")
    counts = data.get("inputCounts")
    require(isinstance(counts, list) and len(counts) == 9
            and all(integer(n) for n in counts), "Invalid inputCounts")
    events = data.get("events")
    require(isinstance(events, list) and all(isinstance(e, dict) and type(e.get("code")) is int
            and type(e.get("value")) is int for e in events), "Invalid events")
    presentation = ("wgpu-surface" if renderer == "gpu" else "softbuffer") if window_system == "wayland" else "none"
    require(data.get("presentationBackend") == presentation,
            f"Expected presentationBackend={presentation}; no fallback allowed")
    if renderer == "gpu":
        stats = data.get("gpuStats")
        require(isinstance(stats, dict), "Missing or invalid gpuStats")
        for name in ("imageUploads", "imageCacheCompactions", "imageUploadBytes", "sceneUploadBytes",
                     "readbackBytes", "renderSubmissions", "surfacePresentations",
                     "surfaceReconfigurations", "cachedImages"):
            require(integer(stats.get(name)), f"Invalid gpuStats.{name}")
        require(stats["renderSubmissions"] == data["framesSubmitted"],
                "GPU renderSubmissions differs from framesSubmitted")
        require(stats["surfacePresentations"] == presented,
                "GPU surfacePresentations differs from framesPresented")
        require(stats["surfaceReconfigurations"] >= 1 if window_system == "wayland"
                else stats["surfaceReconfigurations"] == 0, "Invalid GPU surfaceReconfigurations")
    if app == "read-file":
        expected = ([{"code": 1, "value": len(grant)}]
                    + [{"code": 2, "value": b} for b in grant]
                    + [{"code": 3, "value": -1}, {"code": 4, "value": 0}])
        require(events == expected, "SDK1 bytes, ungranted resource denial or close differs")
        require(data["framesSubmitted"] == 0 and data.get("frame") is None,
                "SDK1 unexpectedly reported a graphics frame")
        if renderer == "gpu":
            require(stats["readbackBytes"] == 0, "SDK1 must not read back GPU frames")
        return
    require(app in ("image-viewer", "text-demo", "ui-demo", "shadow-demo", "compose-demo", "animated-demo"), "Unknown acceptance application")
    sdk = f"SDK{APPS[app][0]}"
    require(data["framesSubmitted"] >= 1, f"{sdk} must submit at least one frame")
    if app == "animated-demo":
        require(data["framesSubmitted"] >= 2, "SDK5 animation must submit at least two frames")
        clock = data.get("frameClock")
        require(isinstance(clock, dict), "Missing or invalid frameClock")
        require(clock.get("source") == "host-monotonic-opportunity", "Invalid frameClock.source")
        for name in ("reads", "waits", "frames", "timers", "inputs", "closes", "cancels", "pending"):
            require(integer(clock.get(name), maximum=2**64 - 1), f"Invalid frameClock.{name}")
        require(clock["reads"] > 0 and clock["waits"] > 0 and clock["frames"] >= 2,
                "SDK5 host clock activity evidence missing")
        # Opportunities may pump due timers or be coalesced by rendering. They
        # are neither submitted/presented frame counts nor physical vSync/scanout.

    if app == "image-viewer":
        require(data["bulkBytesRead"] >= 32 and {"code": 21, "value": 32} in events,
                "SDK2 bulk-read evidence missing")
        require({"code": 40, "value": data["framesSubmitted"]} in events,
                "SDK2 completion frame count missing")
        require(not any(e["code"] in (5, 6, 9) for e in events), "SDK2 logged an application error")
    else:
        # text-demo/ui-demo: success logs only completion in finally; codes
        # 50..54 indicate layout, metrics, submit, close or event-budget errors.
        require(events == [{"code": 40, "value": data["framesSubmitted"]}],
                f"{sdk} completion frame count differs or application error logged")
        require(data["bulkBytesRead"] == 0, f"{sdk} must not bulk-read file resources")
    require(counts[0] >= 1, f"{sdk} close event missing")
    frame = data.get("frame")
    require(isinstance(frame, dict), f"Missing {sdk} frame summary")
    for name in ("width", "height"):
        require(integer(frame.get(name), 1, 4096), f"Invalid frame {name}")
    require(frame["width"] * frame["height"] <= 4194304, "Frame pixel limit exceeded")
    require(integer(frame.get("commands"), 1) and integer(frame.get("renderMicros")),
            "Invalid frame commands or timing")
    require(type(frame.get("sequence")) is int and frame["sequence"] == data["framesSubmitted"],
            "Frame sequence does not match submitted frames")
    if window_system == "wayland":
        # bridge.latest may coalesce submissions. The acceptance criterion is
        # the latest logical frame accepted by the presentation API, not scanout.
        require(all(presented_frame[name] == frame[name] for name in ("sequence", "width", "height")),
                "Wayland presentedFrame differs from the final submitted frame")
    backend = ("cpu-reference" if renderer == "cpu" else
               "wgpu-compute-surface" if window_system == "wayland" else "wgpu-compute-readback")
    require(frame.get("backend") == backend, f"Expected frame backend {backend}; no fallback allowed")
    if app in ("text-demo", "ui-demo", "shadow-demo", "compose-demo", "animated-demo"):
        require(integer(frame.get("textPrepareMicros"), maximum=frame["renderMicros"]),
                f"Invalid {sdk} textPrepareMicros or exceeds renderMicros")
        if renderer == "gpu":
            for name in ("glyphUploads", "glyphUploadBytes", "glyphCacheEvictions",
                         "glyphCacheCompactions", "glyphResidentBytes", "glyphEntries"):
                require(integer(stats.get(name)), f"Invalid gpuStats.{name}")
            require(all(stats[name] == 0 for name in
                        ("imageUploads", "imageUploadBytes", "imageCacheCompactions", "cachedImages")),
                    f"{sdk} must not upload or cache images")
            require(stats["sceneUploadBytes"] > 0, f"{sdk} GPU scene upload evidence missing")
            uploads, uploaded = stats["glyphUploads"], stats["glyphUploadBytes"]
            entries, resident = stats["glyphEntries"], stats["glyphResidentBytes"]
            # GPU coverage masks are nonempty, <=256x256, padded to 4 bytes.
            # Uploads are cumulative; cache entries/residency describe the last
            # committed frame. Reuse, eviction and compaction are all legal.
            require(uploads >= 1 and 4 * uploads <= uploaded <= 256 * 256 * uploads
                    and uploaded % 4 == 0 and uploaded <= data["framesSubmitted"] * 1024 * 1024,
                    f"Invalid {sdk} cumulative glyph uploads/bytes")
            require(integer(entries, 1, 4096) and entries <= uploads
                    and 4 * entries <= resident <= min(4 * 1024 * 1024, 256 * 256 * entries, uploaded)
                    and resident % 4 == 0, f"Invalid {sdk} glyph cache residency/entries")
            require(stats["glyphCacheEvictions"] <= uploads
                    and stats["glyphCacheCompactions"] <= data["framesSubmitted"],
                    f"Invalid {sdk} glyph cache eviction/compaction counts")
    if app in ("shadow-demo", "compose-demo", "animated-demo") and renderer == "gpu":
        for name in ("shadowCacheHits", "shadowCacheMisses", "shadowCacheEvictions",
                     "shadowGenerationWork", "shadowParameterUploadBytes",
                     "shadowResidentBytes", "shadowEntries"):
            require(integer(stats.get(name)), f"Invalid gpuStats.{name}")
        misses, hits = stats["shadowCacheMisses"], stats["shadowCacheHits"]
        entries, resident = stats["shadowEntries"], stats["shadowResidentBytes"]
        require(misses >= 1 and hits + misses <= 8 * data["framesSubmitted"],
                "Invalid SDK4 shadow cache misses/hits")
        require(integer(entries, 1, 8) and entries <= misses
                and stats["shadowCacheEvictions"] <= misses,
                "Invalid SDK4 shadow cache entries/evictions")
        require(stats["shadowParameterUploadBytes"] == 32 * misses,
                "SDK4 shadow parameter bytes must equal 32 * cache misses")
        require(4 * entries <= resident <= 2 * 1024 * 1024 and resident % 4 == 0,
                "Invalid SDK4 shadow cache resident bytes")
        # A miss costs at least a 1x1 mask and two blur=0 passes (3 work),
        # at most a 193x193 patch at r=64,b=16 (2,757,827 work).
        require(3 * misses <= stats["shadowGenerationWork"]
                <= min(2_757_827 * misses, 32_000_000 * data["framesSubmitted"]),
                "Invalid SDK4 shadow cold generation work")
    if renderer == "gpu":
        # Headless acceptance uses a fixed frame size and reads every frame;
        # a surface reads only the final frame, and only for an explicit PNG snapshot.
        readbacks = int(snapshot) if window_system == "wayland" else data["framesSubmitted"]
        expected_bytes = frame["width"] * frame["height"] * 4 * readbacks
        require(stats["readbackBytes"] == expected_bytes,
                f"Expected GPU readbackBytes={expected_bytes} after snapshot writing")


def wayland_environment(environ):
    env = dict(environ)
    runtime = Path(env.get("XDG_RUNTIME_DIR", ""))
    display = env.get("WAYLAND_DISPLAY", "")
    require(runtime.is_absolute() and runtime.is_dir(), "XDG_RUNTIME_DIR must be an existing absolute directory")
    require(bool(display), "WAYLAND_DISPLAY is required")
    address = Path(display)
    if not address.is_absolute():
        require(len(address.parts) == 1 and address.name not in (".", ".."), "Invalid relative WAYLAND_DISPLAY")
        address = runtime / address
    try:
        require(stat.S_ISSOCK(address.stat().st_mode), f"Wayland path is not a Unix socket: {address}")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(address))
    except (OSError, AttributeError) as error:
        raise VerificationError(f"Cannot connect to Wayland socket {address}: {error}") from error
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_SOCKET", None)  # Never let an inherited FD override the probed pathname.
    env["WAYLAND_DISPLAY"] = str(address)
    env["WINIT_UNIX_BACKEND"] = "wayland"  # Best effort; actual host field is authoritative.
    return env, {"runtimeDir": str(runtime), "socket": str(address), "socketVerified": True,
                 "connectionVerified": True, "forcedBackend": "wayland"}


def run_command(argv, *, cwd, env, timeout, output, name):
    """Bound a whole Linux process group; stream logs to disk instead of pipes."""
    started = time.monotonic()
    output = Path(output)
    stdout, stderr = output / (name + ".stdout.log"), output / (name + ".stderr.log")
    record = {"name": name, "argv": [str(a) for a in argv], "cwd": str(cwd),
              "timeoutSeconds": timeout, "returnCode": None, "timedOut": False, "error": None}
    with stdout.open("wb") as out, stderr.open("wb") as err:
        try:
            child = subprocess.Popen(record["argv"], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=err, close_fds=True,
                                     start_new_session=(os.name == "posix"))
            try:
                record["returnCode"] = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record["timedOut"] = True
                if os.name == "posix":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    child.kill()
                record["returnCode"] = child.wait(timeout=5)
        except OSError as error:
            record["error"] = str(error)
    record["durationSeconds"] = round(time.monotonic() - started, 6)
    record["stdout"], record["stderr"] = file_record(stdout, output), file_record(stderr, output)
    return record


def checked(record):
    require(type(record.get("returnCode")) is int and record["returnCode"] == 0
            and record.get("timedOut") is False and record.get("error") is None,
            f"{record.get('name')}: exit={record.get('returnCode')} timeout={record.get('timedOut')} "
            f"error={record.get('error')}; see stdout/stderr logs")


def verify_record(record, base):
    require(isinstance(record, dict), "Missing file fingerprint")
    require(file_record(Path(base) / record["path"], base) == record, f"Hash/size changed: {record.get('path')}")


def verify_command(record, output):
    checked(record)
    for stream in ("stdout", "stderr"):
        verify_record(record[stream], output)


def snapshot_size(path):
    with Path(path).open("rb") as stream:
        header = stream.read(33)
        stream.seek(-12, 2)
        end = stream.read()
    require(len(header) == 33 and header[:16] == b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
            and end == b"\0\0\0\0IEND\xaeB`\x82", "Snapshot is not a complete PNG")
    return struct.unpack(">II", header[16:24])


def path_app(name):
    require(name in PATH_APPS, f"Unknown acceptance path: {name}")
    return PATH_APPS[name]


def requested_paths(mode):
    require(mode in ("headless", "wayland", "all"), f"Unknown acceptance mode: {mode}")
    return PATHS if mode == "all" else tuple(name for name in PATHS if name.startswith(mode + "-"))


def validate_report(report, root, output):
    """Recheck persisted evidence, including raw stdout and current source hashes.

    This is an integrity/acceptance check, not a cryptographic attestation of a
    remote system. A fixture report is never proof of native execution.
    """
    errors = []
    try:
        require(report.get("schemaVersion") == 1 and report.get("status") == "passed", "Run did not pass")
        require(report.get("system", {}).get("name") == "Linux", "Native Linux execution missing")
        require(report.get("renderer") in ("cpu", "gpu"), "Invalid renderer")
        profile = report.get("buildProfile")
        require(profile in ("debug", "release"), "Missing or invalid buildProfile")
        expected_paths = requested_paths(report["mode"])
        require(report.get("inputs") == collect_inputs(root), "Input provenance hashes changed or missing")
        verify_record(report["binary"], output)
        checks = report["checks"]
        require([c.get("name") for c in checks] == list(CHECKS), "Missing/out-of-order build/test prerequisite evidence")
        for command in checks:
            verify_command(command, output)
        verify_record(report["compiler"]["file"], output)
        policy = report["cargoPolicy"]
        require(policy.get("isolatedWorkingDirectory") is True, "Cargo configuration was not isolated")
        validate_cargo_configs(policy, output)
        require(checks[1]["argv"][0] == report["compiler"]["path"] and
                policy["effectiveSettings"]["RUSTC"] == report["compiler"]["path"] and
                policy["effectiveSettings"]["CARGO_BUILD_RUSTC"] == report["compiler"]["path"],
                "Cargo compiler differs from rustc preflight")
        rust = (Path(output) / checks[1]["stdout"]["path"]).read_text(encoding="utf-8")
        channel = toolchain(root)
        target = native_target(rust, channel)
        require(report.get("nativeTarget") == target, "Recorded native target differs from compiler")
        # Compare recorded paths without requiring the original cache or output
        # directory to still exist (evidence can be moved or rechecked on Windows).
        source_root = PurePosixPath(report["sourceRoot"].replace("\\", "/"))
        common = ["--locked", "--manifest-path", str(source_root / "Cargo.toml"),
                  "-p", "ming-app-host", "--target", target]
        if report["renderer"] == "gpu":
            common.extend(["--features", "gpu"])
        if profile == "release":
            common.append("--release")
        for kind, command in zip(("build", "test"), checks[-2:]):
            expected = ["cargo", "+" + channel, kind, *common]
            if kind == "build":
                expected.extend(["--bin", "ming-app-host"])
            require([arg.replace("\\", "/") for arg in command["argv"]] == expected,
                    f"{kind} command differs from selected build profile/target/features")
        origin = report["binaryBuild"]
        target_dir = PurePosixPath(policy["effectiveSettings"]["CARGO_TARGET_DIR"].replace("\\", "/"))
        require(PurePosixPath(origin["sourcePath"].replace("\\", "/"))
                == target_dir / target / profile / "ming-app-host",
                "Host binary source differs from selected build profile/target")
        copy_name = "ming-app-host-release" if profile == "release" else "ming-app-host"
        require(report["binary"]["path"] == copy_name
                and PurePosixPath(origin["copiedPath"].replace("\\", "/")).name == copy_name
                and all(origin[field] == report["binary"][field] for field in ("bytes", "sha256")),
                "Host binary copy/profile/source fingerprint mismatch")
        for name in expected_paths:
            path = report["paths"][name]
            require(path["status"] == "passed", f"{name}: no successful execution")
            verify_command(path["command"], output)
            raw = read_json(Path(output) / path["command"]["stdout"]["path"])
            require(raw == path["outcome"], f"{name}: raw stdout differs from saved outcome")
            app = path_app(name)
            window = "wayland" if name.startswith("wayland-") else "headless"
            argv = path["command"]["argv"]
            require(isinstance(argv, list) and len(argv) >= 3 and all(isinstance(a, str) for a in argv),
                    "Invalid host command arguments")
            require(argv[0] == origin["copiedPath"]
                    and argv[1] == ("view" if window == "wayland" else "run")
                    and PurePosixPath(argv[2].replace("\\", "/")) == source_root / "artifacts" / app / "app.json",
                    f"{name}: command binary or application differs from recorded evidence")
            if app in ("compose-demo", "animated-demo"):
                for flag, value in (("--timeout-ms", "60000"), ("--fuel", "300000000")):
                    require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv)
                            and argv[argv.index(flag) + 1] == value,
                            f"{app} requires explicit {flag} {value}")
                require(not any(a.startswith(("--fuel=", "--timeout-ms=")) for a in argv)
                        and path["command"]["timeoutSeconds"] == 65,
                        "Compose/animated host/process budget differs from 60000ms/300M fuel")
                require(integer(raw.get("fuelConsumed"), 1, 300_000_000), "Compose/animated fuel exceeds explicit budget")
            validate_outcome(raw, app=app, renderer=report["renderer"], window_system=window,
                             grant=(Path(root) / APPS[app][1]).read_bytes() if APPS[app][1] is not None else None)
            if app != "read-file":
                verify_record(path["snapshot"], output)
                require(snapshot_size(Path(output) / path["snapshot"]["path"])
                        == (raw["frame"]["width"], raw["frame"]["height"]), "Snapshot dimensions differ")
            if window == "wayland":
                session = path.get("session", {})
                require(session.get("socketVerified") is True and session.get("connectionVerified") is True,
                        "Wayland socket connection evidence missing")
        require(not report.get("errors"), "Run has recorded errors")
    except (VerificationError, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append(str(error))
    return errors


def toolchain(root):
    with (Path(root) / "rust-toolchain.toml").open("rb") as stream:
        channel = tomllib.load(stream)["toolchain"]["channel"]
    require(re.fullmatch(r"\d+\.\d+\.\d+", channel) is not None, "Exact stable Rust toolchain required")
    return channel


def native_target(rust_stdout, channel):
    host = re.search(r"^host: ([a-zA-Z0-9_-]+)$", rust_stdout, re.M)
    release = re.search(r"^release: (\S+)$", rust_stdout, re.M)
    require(release is not None and release[1] == channel, f"Rust toolchain must be exactly {channel}")
    require(host is not None and "-linux-" in host[1], "Rust host must target native Linux")
    return host[1]


def save_report(report, output):
    temporary = Path(output) / "evidence.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(Path(output) / "evidence.json")


def verify(root, output, *, mode="headless", renderer="cpu", build_profile="debug", command_timeout=900,
           run_timeout=60, auto_close_ms=15000, environ=None, runner=run_command):
    require(build_profile in ("debug", "release"), "Invalid build profile")
    root, output = Path(root).resolve(), Path(output).resolve()
    require(not output.exists(), f"Output must be a fresh directory: {output}")
    output.mkdir(parents=True)
    env = dict(os.environ if environ is None else environ)
    report = {"schemaVersion": 1, "status": "pending", "mode": mode, "renderer": renderer,
              "buildProfile": build_profile,
              "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "sourceRoot": str(root), "system": {"name": platform.system(),
              "release": platform.release(), "machine": platform.machine(), "python": platform.python_version()},
              "sessionEnvironment": {k: env.get(k) for k in
                                     ("XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "DISPLAY")},
              "inheritedBuildSettings": controlled_settings(env),
              "inputs": {}, "checks": [], "paths": {n: {"status": "pending", "reason": "Not requested"}
              for n in PATHS}, "errors": []}
    for name in requested_paths(mode):
        report["paths"][name]["reason"] = "Not yet executed"
    save_report(report, output)
    temporary_work = None
    try:
        report["inputs"] = collect_inputs(root)
        if report["system"]["name"] != "Linux":
            report["errors"].append("Native Linux unavailable; build, test, headless and Wayland execution pending")
            return report
        channel = toolchain(root)
        temporary_work = tempfile.TemporaryDirectory(prefix="ming-linux-cargo-")
        work = Path(temporary_work.name).resolve()
        report["cargoPolicy"] = cargo_environment(root, work, env, output)
        env["RUSTUP_AUTO_INSTALL"] = "0"  # A missing pinned toolchain fails; do not install globally.
        env["CARGO_TARGET_DIR"] = env.get("MING_LINUX_TARGET_DIR", str(output / "target"))
        # A caller can put Linux outputs on its dedicated cache volume. Evidence
        # retains the binary separately so later cache changes cannot forge a pass.
        env.pop("CARGO_BUILD_TARGET", None)
        def execute(name, argv, environment=env, timeout=command_timeout):
            if name in ("cargo", "build", "test"):
                check_cargo_configs(report["cargoPolicy"], output, "before-" + name)
            command = runner([str(a) for a in argv], cwd=work if name in CHECKS else root, env=environment,
                             timeout=timeout, output=output, name=name)
            return command
        command = execute("compiler", ["rustup", "which", "--toolchain", channel, "rustc"])
        report["checks"].append(command)
        checked(command)
        compiler = Path((output / command["stdout"]["path"]).read_text(encoding="utf-8").strip())
        require(compiler.is_absolute() and compiler.is_file(), "rustup did not resolve an absolute installed compiler")
        import shutil
        shutil.copy2(compiler, output / "toolchain-rustc")
        report["compiler"] = {"path": str(compiler), "file": file_record(output / "toolchain-rustc", output)}
        env["RUSTC"] = env["CARGO_BUILD_RUSTC"] = str(compiler)
        report["cargoPolicy"]["effectiveSettings"] = controlled_settings(env)
        save_report(report, output)
        for name, args in (("rustc", [str(compiler), "-vV"]),
                           ("cargo", ["cargo", "+" + channel, "--version"]),
                           ("cc", ["cc", "--version"]),
                           ("libraries", ["pkg-config", "--modversion", "wayland-client", "xkbcommon",
                                          "x11", "xcursor", "xi", "xrandr"])):
            command = execute(name, args)
            report["checks"].append(command)
            save_report(report, output)
            checked(command)
            if name == "rustc":
                target = native_target((output / command["stdout"]["path"]).read_text(encoding="utf-8"), channel)
                report["nativeTarget"] = target
        common = ["--locked", "--manifest-path", str(root / "Cargo.toml"), "-p", "ming-app-host", "--target", target]
        if renderer == "gpu":
            common.extend(["--features", "gpu"])
        if build_profile == "release":
            common.append("--release")
        for name in ("build", "test"):
            args = ["cargo", "+" + channel, name, *common]
            if name == "build":
                args.extend(["--bin", "ming-app-host"])
            command = execute(name, args)
            report["checks"].append(command)
            save_report(report, output)
            checked(command)
            check_cargo_configs(report["cargoPolicy"], output, "after-" + name)
        built = Path(env["CARGO_TARGET_DIR"]) / target / build_profile / "ming-app-host"
        # Keep an immutable-for-this-run copy beside evidence, independent of cache location.
        import shutil
        source_record = file_record(built, built.parent)
        binary = output / ("ming-app-host-release" if build_profile == "release" else "ming-app-host")
        shutil.copy2(built, binary)
        report["binary"] = file_record(binary, output)
        require(all(report["binary"][field] == source_record[field] for field in ("bytes", "sha256")),
                "Copied host binary differs from selected build output")
        report["binaryBuild"] = {"sourcePath": str(built), "copiedPath": str(binary),
                                 "bytes": source_record["bytes"], "sha256": source_record["sha256"]}
        for name in requested_paths(mode):
            path = report["paths"][name]
            app = path_app(name)
            window = "wayland" if name.startswith("wayland-") else "headless"
            child_env = dict(env)
            try:
                if window == "wayland":
                    child_env, path["session"] = wayland_environment(child_env)
                else:
                    for key in ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
                        child_env.pop(key, None)
                app_timeout = 60 if app in ("compose-demo", "animated-demo") else run_timeout
                args = [binary, "view" if window == "wayland" else "run",
                        root / "artifacts" / app / "app.json",
                        "--renderer", renderer, "--timeout-ms", str(int(app_timeout * 1000))]
                if app in ("compose-demo", "animated-demo"):
                    args.extend(["--fuel", "300000000"])
                if APPS[app][1] is not None:
                    args.extend(["--grant", "1=" + str(root / APPS[app][1])])
                if window == "wayland":
                    args.extend(["--auto-close-ms", str(auto_close_ms)])
                if app != "read-file":
                    args.extend(["--snapshot", output / (name + ".png")])
                path["command"] = execute(name, args, child_env, app_timeout + 5)
                checked(path["command"])
                data = read_json(output / path["command"]["stdout"]["path"])
                path["outcome"] = data
                validate_outcome(data, app=app, renderer=renderer, window_system=window,
                                 grant=(root / APPS[app][1]).read_bytes() if APPS[app][1] is not None else None)
                if app != "read-file":
                    path["snapshot"] = file_record(output / (name + ".png"), output)
                    require(snapshot_size(output / path["snapshot"]["path"])
                            == (data["frame"]["width"], data["frame"]["height"]), "Snapshot dimensions differ")
                path.update(status="passed", reason="Checked process exit, actual host outcome and saved evidence")
            except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
                path.update(status="failed", reason=str(error))
                report["errors"].append(f"{name}: {error}")
            save_report(report, output)
        check_cargo_configs(report["cargoPolicy"], output, "final")
        report["status"] = "failed" if report["errors"] else "passed"
        if report["status"] == "passed":
            report["errors"].extend(validate_report(report, root, output))
            if report["errors"]:
                report["status"] = "failed"
    except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
        report["status"] = "failed"
        report["errors"].append(str(error))
    finally:
        if "cargoPolicy" in report:
            try:
                check_cargo_configs(report["cargoPolicy"], output, "final")
            except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
                report["status"] = "failed"
                if str(error) not in report["errors"]:
                    report["errors"].append(str(error))
        if temporary_work is not None:
            temporary_work.cleanup()
        report["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        save_report(report, output)
    return report


def positive_seconds(value):
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected a positive whole number of seconds") from error
    if not 1 <= result <= 7200:
        raise argparse.ArgumentTypeError("Seconds must be between 1 and 7200")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("headless", "wayland", "all"), default="headless")
    parser.add_argument("--renderer", choices=("cpu", "gpu"), default="cpu")
    parser.add_argument("--build-profile", choices=("debug", "release"), default="debug")
    parser.add_argument("--output", type=Path, help="Fresh evidence directory (must not exist)")
    parser.add_argument("--command-timeout", type=positive_seconds, default=900)
    parser.add_argument("--run-timeout", type=positive_seconds, default=60)
    parser.add_argument("--auto-close-ms", type=int, default=15000)
    parser.add_argument("--validate", type=Path, metavar="EVIDENCE_JSON",
                        help="Recheck saved evidence and source hashes without running Linux commands")
    args = parser.parse_args(argv)
    if not 1 <= args.run_timeout <= 60:
        parser.error("--run-timeout must be between 1 and 60 seconds (host headless limit)")
    if not 100 <= args.auto_close_ms < args.run_timeout * 1000:
        parser.error("--auto-close-ms must be at least 100 and smaller than --run-timeout")
    root = Path(__file__).resolve().parents[1]
    try:
        if args.validate:
            errors = validate_report(read_json(args.validate), root, args.validate.resolve().parent)
            print(json.dumps({"status": "failed" if errors else "passed", "errors": errors}, indent=2))
            return 1 if errors else 0
        output = args.output or root / ".cache/linux-verification" / (time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8])
        report = verify(root, output, mode=args.mode, renderer=args.renderer,
                        build_profile=args.build_profile,
                        command_timeout=args.command_timeout, run_timeout=args.run_timeout,
                        auto_close_ms=args.auto_close_ms)
        print(json.dumps(report, indent=2, ensure_ascii=True))
        return 0 if report["status"] == "passed" else 1
    except (VerificationError, OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "failed", "errors": [str(error)]}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
