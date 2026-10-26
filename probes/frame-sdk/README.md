# HostFrame SDK boundary qualification

This standalone probe compiles the real `ming-sdk` project with Kotlin 2.3.20. Its SDK build directory is redirected to `probes/frame-sdk/build/ming-sdk`; root Gradle/settings and the legacy SDK build directory are not modified.

The executable is a packet-fixture contract. Node supplies only the host-boundary doubles and records request/output memory, arguments and exact responses. It does not implement a product clock and is not native/Wasmtime, monotonic scheduling, vsync, or presentation acceptance. The fixture Wasm expects deliberately malformed responses and must not be treated as a native-host smoke test.

The public API is in `ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/FrameClock.kt`:

```kotlin
MingFrames.monotonicNanos(): ClockReadResult
MingFrames.wait(wantFrame: Boolean, deadlineNs: Long? = null, nonblocking: Boolean = false): FrameWaitResult
```

`ClockReadResult` has `code: Int`, `nanos: Long?`. `FrameWaitResult` has `code: Int`, `event: MingFrameEvent?`. Event fields are `kind: MingFrameKind`, `nowNanos: Long`, `frameSequence: Long`, `input: InputEvent?`; the enum values are FRAME, TIMER, INPUT, CLOSE, CANCEL. Enum ordinals are not ABI wire numbers. Non-INPUT events have null input after all four payload words are validated as zero. All objects are detached values and own no host handles.

Clock success is code 0 with a value. Wait success is code 0 with an event; pending is code 0 with null event. Negative host codes are preserved. Invalid local negative deadlines make no host call. Invalid lengths/packets map to -2 with no event. All timestamp/sequence values must fit nonnegative Long; FRAME additionally requires a positive sequence. This stateless wrapper does not track or generate cross-call clock/sequence ordering.

Use a new artifact stage name for each build (existing stages are not overwritten):

```powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
node probes/frame-sdk/build.mjs my-stage
node probes/frame-sdk/contract.mjs my-stage
```

For launch failures caused by the sandbox denying Java, use the normal explicit escalation mechanism. The RED snapshot under `artifacts/frame-sdk/red` was built before FrameClock.kt existed; it is historical evidence and the scripts do not restore or roll back source files. The `red-launch` receipt is an EPERM launch failure, not the compiler RED.

Coverage: 16 clock calls and 58 wait calls, four flag combinations, exact 8/16/48 byte buffers, little-endian and Long.MAX_VALUE, pending/error no-output handling, local negative deadlines, five event kinds, INPUT types 1..7, malformed lengths/version/kind/time/sequence/reserved/payload, and recovery after errors. Native-host raw pointer overlap/unaligned requests, real time/fairness, SDK5 gating, RPC and host budgets remain parent-owned native tests. This SDK and double impose no native-alignment or disjoint-buffer restriction.

Build receipts freeze the source files, exact command and log hashes. Build info records real compiler and adapted Wasm hashes and typed imports. Only the existing random_get -> ming_v2.random_fill adapter is used; every other WASI capability remains rejected.
