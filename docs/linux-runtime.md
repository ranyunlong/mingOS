# Linux native acceptance

This entry point builds the Rust host and executes the **existing** SDK1
read-file, SDK2 image-viewer, SDK3 text-demo and SDK4 ui-demo/shadow-demo/compose-demo and SDK5 animated-demo Wasm packages. Python 3.11+ and its standard
library are sufficient for the verifier. Neither Node, Kotlin/Gradle nor a
package-manager install is part of Linux acceptance. CPU is the default.

SDK3验收补充（2026-10-24）：text-demo不传文件grant，检查无bulk文件读取、完成帧数、关闭输入、textPrepareMicros与renderMicros关系。GPU模式还检查独立glyph atlas的上传/容量计数与零图像上传；Wayland模式仍以实际windowSystem和呈现计数为准。输入指纹包含四个内置字体资产和字体验证工具/测试；Docker tooling另运行stdlib字体资产验证。当前新阶段证据将写入artifacts/linux-typography-20261024-reviewed；首轮artifacts/linux-typography-20261024-final在补查无text配置Clippy时由任务主动停止（exit143），其失败结果与归属核验/清理记录保留，不能当作通过；下方早期记录均保留其历史源码状态，不能替代新阶段结果。

SDK4补充（2026-10-24）：ui-demo不传文件grant，正常日志与字体示例同为完成帧计数；新增headless-sdk4和wayland-sdk4。当前AA阶段独立结果将保存在artifacts/linux-ui-aa-20261024-reviewed；源码和包需冻结后执行。SDK3及更早目录保留各自历史状态，不能用旧指纹验证新源码。

第6B项阴影补充（2026-10-24）：新增headless-shadow与wayland-shadow，使用artifacts/shadow-demo的真实Kotlin/Wasm包，不替代原七路径。指纹新增整个tests/fixtures目录，包含A8样本及oracle/provenance；GPU可用时检查阴影cache、32B/miss参数上传和glyph统计。当前独立Linux结果写入artifacts/linux-shadow-20261024，现已通过9路径、Linux154项Rust测试和131项夹具，容器exit0并停止；跨平台像素对照见shadow-verification.md。

第6C项补充（2026-10-25）：新增headless-compose、wayland-compose，保留既有9路径。真实Compose包使用60000ms/300M fuel，未放宽宿主上限。新增--build-profile debug|release，默认debug；本轮显式release，构建、测试、全功能检查、二进制来源和报告绑定同一profile。首轮artifacts/linux-compose-20261025保留为失败记录：11路径本体通过，但旧外层断言错误要求快速启动的SDK2/3必须呈现两次。现增加presentedFrame遥测，按呈现API接受的最后帧序号/逻辑尺寸核对最终提交帧；保留resize与两次提交检查，允许中间帧合并。新结果artifacts/linux-compose-20261025-reviewed已完整通过：11路径、release Rust155、Linux162fixture、字体42、release全功能检查；二次重验完成，容器exit0且停止。该遥测不代表物理scanout。

第6D项补充（2026-10-25）：新增SDK5 headless-animated与wayland-animated，共13路径。真实帧/协程动画使用60000ms/300M fuel；frameClock机会独立于提交/呈现。当前177项夹具已在Windows执行（176通过/既有Socket skip1），Linux必须177通过无skip。artifacts/linux-animated-20261025已完整通过：13路径、release Rust183、Linux177fixtures无skip、字体42、全features/alltargets与保存证据重验，容器exit0且停止；最终动画PNG与Windows CPU/GPU一致。

## Native Linux prerequisites

Use the exact installed Rust version in `rust-toolchain.toml` (1.99.1), Cargo,
a C compiler, pkg-config and development libraries for Wayland, xkbcommon,
X11, Xcursor, Xi and Xrandr. The desktop host also needs the corresponding
runtime libraries. Provision these yourself or use the isolated container
below; the verification script does not install global tools. It sets
`RUSTUP_AUTO_INSTALL=0` so a missing exact toolchain fails explicitly.

From any working directory:

```sh
sh /path/to/MingOS/scripts/linux-verify.sh --mode headless \
  --output /path/to/MingOS/artifacts/linux-native-headless
```

The output directory must not already exist. It contains a checkpointed
`evidence.json`, raw stdout/stderr for every command, a copy and hash of the
native executable, and SDK2/SDK3/SDK4 PNG snapshots. Output paths may contain spaces.
The script invokes Cargo with `+1.99.1`, `--locked`, `--target <rustc-host>`
and the workspace manifest path, and checks the actual rustc release/host.
The verifier resolves the absolute installed compiler with `rustup which`,
checks that compiler directly, fingerprints a copy, and sets both `RUSTC` and
`CARGO_BUILD_RUSTC` to the same path for Cargo. It then calls the host executable
directly so application stdout is clean JSON.
Cargo builds and tests use the normal default features, without an X display.

Cargo commands run from a fresh temporary directory outside the project.
Project `.cargo/config` and `.cargo/config.toml` files (including crate-local
ones) are fingerprinted; runner/compiler/wrapper/flag/linker/env/include
settings are rejected and project configs are not implicitly loaded. The
verifier rejects external Cargo configuration in the chosen `CARGO_HOME` or
isolated working-directory ancestors. Without an explicit clean `CARGO_HOME`,
it creates one in the evidence directory. Existing Cargo caches may be used
only if they contain no config files. Absence is rechecked before every Cargo
invocation, after build/test, and at workflow completion; each checkpoint is
saved. Config creation during the run fails acceptance. Evidence-local discovery
paths are relative, so evidence remains movable. Revalidation checks accessible
external paths and uses recorded final absence for foreign paths (for example,
Linux container paths when revalidating on Windows).

Inherited `CARGO_TARGET_*_RUNNER` settings cannot bypass test execution.
Compiler, wrapper, Rust flags, linker and C compiler overrides also fail
explicitly. The report records relevant inherited/effective build settings
without collecting registry tokens. Target selection is explicitly controlled;
`CARGO_BUILD_TARGET` is ignored in favor of the verified native host. This
keeps Cargo's actual compiler consistent with the rustc preflight. Clear the
reported unsupported setting and rerun instead of accepting a weakened check.
The default target directory belongs to this evidence run, keeping Linux and
Windows build outputs separate. To use a **Linux-only** cache, explicitly set
`MING_LINUX_TARGET_DIR=/some/linux/cache/target`. Do not point it at the Windows
`.cache/rust-target`. `CARGO_HOME` may also point at a dedicated Linux cache.
Build/prerequisite/test commands default to a 900-second timeout each; host
workers default to 60 seconds. `--command-timeout` adjusts the former.
`--run-timeout` accepts 1–60 seconds; the outer host-process deadline includes
five seconds of cleanup allowance. Linux timeouts kill the entire process
group, including worker children. Logs stream to files rather than pipes.

SDK1 acceptance checks every granted file byte, denial of ungranted resource 2,
and successful close. SDK2 requires a bulk read, successful frame completion,
a valid frame summary, matching PNG dimensions, and backend `cpu-reference`
with the default CPU renderer. Headless paths require
`presentationBackend: "none"`. Both require native runtime, zero guest/process exit, and actual
`windowSystem: "headless"`. An application that merely exits zero but reports
its own load/render failure does not pass.

## Forced Wayland

Run in a real Wayland session with an existing absolute `XDG_RUNTIME_DIR` and
`WAYLAND_DISPLAY` naming a Unix socket:

```sh
sh scripts/linux-verify.sh --mode all --auto-close-ms 15000 \
  --output artifacts/linux-native-all
```

`--mode headless` requests SDK1/2/3/4 headless paths plus shadow-demo/compose-demo/animated-demo. `--mode wayland`
requests the SDK2/3/4 viewers plus shadow-demo/compose-demo/animated-demo. `--mode all` requests all thirteen. Build/test
prerequisites run in every mode. Unrequested paths remain **pending** and
never count as Wayland passes. Each requested application path has its own
status and saved evidence, so a failed Wayland session cannot mask a
successful headless run or vice versa.

The verifier checks that the display path is actually a Unix socket and
successfully connects with a two-second deadline. It then removes `DISPLAY`
**and `WAYLAND_SOCKET`** from the viewer environment. In particular, an inherited
Wayland file descriptor cannot silently replace the socket that was checked.
It sets `WINIT_UNIX_BACKEND=wayland` as a best-effort hint and passes the
verified socket path in `WAYLAND_DISPLAY`. The decisive evidence is the host's
actual winit `windowSystem: "wayland"` result, plus successful execution and a
snapshot, and `framesPresented >= 1` (counted by the host only after
successful presentation of an application frame). Headless paths must report
`framesPresented: 0`. A socket, session environment or headless result alone is insufficient.
The viewer auto-closes after 15 seconds by default; this can be adjusted below
the host timeout. Missing/unconnectable sockets and X11/missing backend results
fail the requested path.

`--renderer gpu` is explicit and adds Cargo's `gpu` feature to build/test.
SDK2 frame and presentation backends must match the selected path:

| Renderer / path | `frame.backend` | `presentationBackend` |
| --- | --- | --- |
| CPU headless `run` | `cpu-reference` | `none` |
| CPU native `view` (Wayland acceptance) | `cpu-reference` | `softbuffer` |
| GPU headless `run` | `wgpu-compute-readback` | `none` |
| GPU native `view` (Wayland acceptance) | `wgpu-compute-surface` | `wgpu-surface` |

CPU fallback or a headless GPU backend reported for the native GPU viewer
fails validation. CPU outcomes need no `gpuStats`. GPU outcomes require all
nine counters as nonnegative integers (booleans are rejected): `imageUploads`,
`imageCacheCompactions`, `imageUploadBytes`, `sceneUploadBytes`, `readbackBytes`,
`renderSubmissions`, `surfacePresentations`, `surfaceReconfigurations`, and
`cachedImages`. Render submissions must match `framesSubmitted`, and surface
presentations must match `framesPresented`. Headless runs must have zero
surface reconfigurations; a presenting GPU viewer must have at least one.
SDK1 has no frame and must have zero GPU readback bytes.

Native GPU `view` without `--snapshot` must report `readbackBytes: 0`.
The verifier explicitly requests `--snapshot` for the SDK2/SDK3/SDK4 paths, including
`view`: its GPU surface outcome must therefore report exactly
`frame.width * frame.height * 4` readback bytes, once for the final PNG,
regardless of how many frames were submitted or presented. Host metadata is
collected **after PNG writing**, so a stale zero counter fails acceptance.
Headless GPU rendering reads back every submitted frame; for the fixed-size
image-viewer sample, the required total is
`framesSubmitted * frame.width * frame.height * 4`. Writing its PNG uses the
existing CPU pixels and adds no readback. The validator's `snapshot=False`
policy covers native GPU view without a snapshot; the verifier CLI continues
to request snapshots.

Raw and parsed GPU metadata are preserved. Regular Cargo tests do not
establish ignored hardware parity tests, and GPU scene readback does not
establish native GPU surface presentation. The default container below
exposes no GPU device and makes no hardware-GPU claim.

## Isolated Docker / software Wayland

The Docker Desktop engine must already be available. Run on Windows or Linux:

```sh
python -B scripts/linux-container.py --mode all \
  --output artifacts/linux-20261024-task3
```

The runner uses a fresh named image, container and Cargo cache volume for each
invocation. Only this project (read-only), its evidence subdirectory
(read/write), and the fresh Linux cache are mounted. No host Docker socket,
home directory, display socket or GPU device is passed through. Unrelated
containers, volumes and images are not changed or pruned. Resource names and
exact commands are saved in `docker-evidence.json` for diagnosis and optional
manual cleanup. After an attach timeout, error, interruption or normal exit,
the runner inspects the exact run-owned labeled container, stops it with a
bounded grace period, kills it if still running or the post-stop state cannot
be inspected, and records final state. After initial exact ownership verification,
all stop/kill/inspect operations use the verified immutable Docker container ID.
A failed final inspection leaves state unverified and cleanup failed; initial
ownership failure never issues stop or kill. Failure to prove it stopped is a failure. Images and stopped containers are
deliberately retained; unrelated containers never receive stop/kill.

The Dockerfile uses official Rust 1.99.1 pinned to image digest
`sha256:931c2c0531a8b143e044f0f3914dfe802f15bb3d1955f0b657e9359327ac2ffc`.
APT development/runtime packages are installed inside the purpose-specific
image. Debian package versions are resolved at build time; retain the saved
build log and resulting image ID for reproduction. No tools are installed on
the host. Build network access and crates.io access are required.

Inside the container: `CARGO_HOME=/ming-cache/cargo`,
`CARGO_TARGET_DIR=/ming-cache/target`, `MING_LINUX_TARGET_DIR=/ming-cache/target`,
`RUSTUP_HOME=/usr/local/rustup`; Cargo jobs are limited to 2 and Rust debug
symbols are disabled to bound memory/storage. The container has a two-CPU,
8-GiB memory limit. The verifier still checks `--locked` and the native target.

The runner executes shell syntax and all Python fixture tests on Linux, starts
Weston with its **headless backend and Pixman software renderer**, then runs the
native verifier. After native acceptance passes, it also runs
`cargo +1.99.1 check --locked --all-features --all-targets` for the native target,
using the same compiler and configuration isolation. This checks Linux/Vulkan
compilation only. It deliberately supplies invalid inherited `DISPLAY` and
`WAYLAND_SOCKET` values, which the verifier must discard. Weston and native
host logs, Docker image/container inspection, unit-test results, source/app
hashes, outcomes and PNGs are saved under the evidence directory. A passing
Wayland path here establishes an actual native Wayland client talking to a
software compositor inside Linux on Docker Desktop's WSL2 kernel. Physical
Linux display/hardware GPU, a desktop user's session, and native Linux strong
isolation remain separate acceptance work.

## Recheck evidence and fixture tests

```sh
python -B -m unittest discover -s tests -p test_linux_verify.py -v
python -B scripts/linux-verify.py --validate artifacts/linux-20261024-task3-reviewed-v2/run/evidence.json
```

Fixture tests need no Linux, Rust, Node, compositor or GPU. The real Unix-socket
test skips only if Python lacks `AF_UNIX` (as on this Windows interpreter).
On Linux it runs against a real temporary listening socket, including failure
after the socket closes. CLI validation may run on Windows: it rechecks the
native run logs, compiler/executable/snapshot fingerprints, raw outcomes and
current input fingerprints. Docker and supplemental tooling commands have
separate records in `docker-evidence.json` and `tooling-evidence.json`.
Moving the evidence directory is supported. Changing source, verifier, Rust
tests, manifests, grants or Wasm artifacts intentionally makes old evidence
fail revalidation. Evidence is an integrity record, not signed attestation.

Exit zero means all requested paths and prerequisite checks passed. Pending,
failed, timed-out, malformed, missing or changed evidence returns nonzero.
On a non-Linux host the native entry point writes **pending** evidence and
returns nonzero without running Cargo or inventing a Linux/Wayland success.

## Frozen-source GPU surface stage (2026-10-24)

The parent confirmed the source freeze after review fixes and formatting.
Final passing evidence is at
`artifacts/linux-gpu-surface-20261024-final/`. Read its `summary.json` and
`commands.md` for results and exact commands. The first capture at
`artifacts/linux-gpu-surface-20261024/` is superseded: all three CPU paths
passed, but input drift during the parent's final boundary fix correctly
failed overall acceptance. Its outcomes were preserved; no final acceptance
claim is based on it. The earlier evidence below also describes a historical
source state. The evidence directory contains
the run-specific `run-frozen-linux.py` orchestration and `frozen-inputs.json`
with the source/input capture. Source and verifier files remain unchanged
after capture.

Recheck the Docker engine and reuse only this task's image and Linux cache
recorded by `artifacts/linux-20261024-task3-reviewed-v2/reuse-resources.json`,
preserving its ownership chain. The project mount remains read-only; the
fresh evidence mount and task-owned Linux cache are writable. No unrelated
Docker resources are used or pruned.

This run requires CPU headless SDK1/SDK2 and forced Wayland SDK2 with at
least two application presentations, all 60 Linux fixture tests without
skips, and the locked native all-features/all-targets compilation check.
Revalidate the resulting current-source evidence on Windows using
`D:\python\python.exe`; Windows fixtures expect 60 run, 59 passed and the
one `AF_UNIX` skip. Exact Docker/Cargo/host commands, exit codes, logs and
fingerprints are retained with the evidence.

Linux GPU hardware is unavailable for this stage: CPU/software Wayland
acceptance and GPU compilation cannot establish Linux GPU surface execution.
The `surfaceFormat` and `presentationUploadBytes` helper metadata may be
preserved without extending the core verifier policy. Kotlin/SDK artifacts
remain unchanged.

Final validation completed on **2026-10-24 at 06:53:22 UTC
(14:53:22 Asia/Shanghai)**, after the final GPU-session idle-device-loss
boundary fix. The capture, native evidence and final Windows revalidation
agree on the current source/input fingerprints. Documentation and evidence
artifacts are outside the input collector and may be updated after capture;
Rust, Cargo, Kotlin, verifier and test sources were not edited after capture.

| Final check | Actual result |
| --- | --- |
| Linux Python 3.13.5 fixtures | 60 passed, no skips; exit 0 |
| Windows Python fixtures | 60 run, 59 passed, 1 expected `AF_UNIX` skip; exit 0 |
| Native default build and tests | build passed; 42 tests passed, 0 failed, 0 ignored |
| SDK1 CPU headless | `windowSystem=headless`, 0 submitted, 0 presented, presentation `none` |
| SDK2 CPU headless | 1 submitted, 0 presented, `cpu-reference`, presentation `none` |
| SDK2 CPU forced Wayland | 2 submitted, 2 presented, `cpu-reference`, presentation `softbuffer` |
| Locked Linux all-features/all-targets check | passed; compilation only |
| Windows current-source evidence revalidation | passed, no errors; exit 0 |
| Owned Docker container | exit 0, `Running=false`, `OOMKilled=false`; stopped state verified |

The final run used Docker engine 29.8.0, the existing task-owned image
`mingos-linux-task3:b89a9dc7cdce` with immutable ID
`sha256:e91ad5a3eae8964aab759b9db30997a49be1a57fb97e63529f59fa5b16bb2722`,
and volume `mingos-linux-task3-b89a9dc7cdce-cache`. The final container was
`mingos-linux-task3-4e5d1c7a8008`. Its mount inspection confirms read-only
source and only the run evidence and owned cache writable. Ownership-chain
fingerprints are in the final `reuse-resources.json`; exact Docker commands
and cleanup inspection are in `docker-evidence.json`.

Exact entry commands from the project root on Windows:

```powershell
& D:\python\python.exe -B artifacts/linux-gpu-surface-20261024-final/run-frozen-linux.py
& D:\python\python.exe -B -m unittest discover -s tests -p test_linux_verify.py -v
& D:\python\python.exe -B scripts/linux-verify.py --validate artifacts/linux-gpu-surface-20261024-final/run/evidence.json
```

The run-specific orchestration imports the existing container/verifier
helpers, verifies the cached image/volume ownership, then runs a fresh
container with `--mode all --renderer cpu --command-timeout 1800`.
It uses the same pinned compiler and isolated Cargo controls documented
above. `commands.md` includes the exact Docker, Cargo, fixture and native
application argv; the JSON records preserve each command's working directory,
limits, stdout/stderr fingerprints and exit status.

Both final SDK2 PNGs are 1100×760 with SHA256
`2520ef2f8404195f29158117128f5f6245d0d8641ec25ed7a6440a0f76e2a70a`.
SDK1/SDK2 Wasm SHA256 remain
`83c17b2df1bf7872f3fd8fa5bf64450eaaf33425df08678219d1ebdaf6306c49`
and `2eb888508c0fb4ac0a27df4bdbe8dfff1b402c46c1318dff56b6297c5988de21`.
Weston 14.0.2 used its headless backend with Pixman inside native Linux on
Docker Desktop's WSL2 kernel. This validates native CPU Wayland presentation,
not a physical Linux display or Linux GPU surface execution. The parent's
Windows GPU QA is a separate result.

## Earlier recorded execution (historical source state)

Validation completed on **2026-10-24 at 05:54:36 UTC (13:54:36 Asia/Shanghai)**.
Historical evidence: `artifacts/linux-20261024-task3-reviewed-v2/`.

| Check | Actual result |
| --- | --- |
| Python fixtures, Windows 3.13.12 | 52 run; 51 passed, 1 `AF_UNIX` skip; exit 0 |
| Python fixtures, native Linux | 52 passed, no skips; exit 0 |
| POSIX shell syntax, Linux and Git Bash | exit 0 |
| Locked native Rust default build | exit 0 |
| Locked native Rust default tests | 41 passed, 0 failed, 0 ignored; exit 0 |
| SDK1 read-file headless | passed; `windowSystem=headless`, 0 presented |
| SDK2 image-viewer headless | passed; `windowSystem=headless`, 1 submitted, 0 presented |
| SDK2 forced Wayland viewer | passed; actual `windowSystem=wayland`, 2 submitted, **2 presented** |
| Locked Linux all-features/all-targets compilation | exit 0 |
| Real Docker attach-timeout cleanup probe | attach CLI timed out; owned daemon container stopped; final `Running=false`, exit 137, `OOMKilled=false` |
| Native evidence revalidation on Windows | passed; no errors; exit 0 |

The final run reused only this task's own purpose-specific image and Linux
Cargo cache to avoid a second dependency download. The source remained
read-only. `reuse-resources.json` records the ownership chain back to the first
run. Exact Docker argv, image inspection, container state, command deadlines,
stdout/stderr hashes and exit codes are retained. The main acceptance container
finished with exit 0, `Running=false`, `OOMKilled=false`; its cleanup inspection
is saved in `docker-evidence.json`. The separate timeout probe retained its
own before/after inspection and stop command under `timeout-probe/`.

Exact Rust commands inside Linux (Cargo cwd is isolated, not `/workspace`):

```sh
cargo +1.99.1 build --locked --manifest-path /workspace/Cargo.toml \
  -p ming-app-host --target x86_64-unknown-linux-gnu --bin ming-app-host
cargo +1.99.1 test --locked --manifest-path /workspace/Cargo.toml \
  -p ming-app-host --target x86_64-unknown-linux-gnu
cargo +1.99.1 check --locked --all-features --all-targets \
  --target x86_64-unknown-linux-gnu --manifest-path /workspace/Cargo.toml
```

The compiler used for both preflight and Cargo was
`/usr/local/rustup/toolchains/1.99.1-x86_64-unknown-linux-gnu/bin/rustc`.
The verifier recorded inherited/effective compiler/Cargo settings and a hash of
the compiler copy. Native host process wall times were 0.872197 seconds for
SDK1, 3.731267 seconds for SDK2 headless and 15.710787 seconds for the Wayland
viewer (including its intentional 15-second auto-close interval). These are
host-process wall times, not GPU or presentation-latency measurements.

Preserved Wasm SHA256:

- SDK1: `83c17b2df1bf7872f3fd8fa5bf64450eaaf33425df08678219d1ebdaf6306c49`
- SDK2: `2eb888508c0fb4ac0a27df4bdbe8dfff1b402c46c1318dff56b6297c5988de21`

Both native SDK2 snapshots are 1100×760 with SHA256
`2520ef2f8404195f29158117128f5f6245d0d8641ec25ed7a6440a0f76e2a70a`.
The image-viewer frame backend is `cpu-reference`. The Wayland PNG was visually
inspected. Weston reports its headless backend and Pixman renderer; no physical
GPU/device/display was exposed or validated by this Linux run.

Earlier attempts are retained without rewriting their outcomes:
`artifacts/linux-20261024-task3/` failed overall with input-provenance drift
while shared Rust/verifier edits were active; its per-path results are saved.
`artifacts/linux-20261024-task3-final/` passed at its then-current source state
and is superseded by the reviewed run after later source/harness fixes.
`artifacts/linux-20261024-windows-pending/` records the native entry point's
honest non-Linux pending result (exit 1). Use the **reviewed** evidence directory
for that historical source state; it does not validate the current GPU surface
stage, and older provenance is intentionally not patched.

The preceding reviewed run in `artifacts/linux-20261024-task3-reviewed/` is
preserved and superseded by `linux-20261024-task3-reviewed-v2` after the two
residual review fixes. New regressions prove that failed post-stop inspection
still attempts kill by the initially verified immutable container ID, and that
unknown final state cannot pass cleanup. Config drift regressions create Cargo
config files after build, after test, after application execution, and before
revalidation; each is rejected. Windows revalidation of that historical Linux
report passed at its recorded source state without dereferencing foreign Linux
absolute discovery paths.
