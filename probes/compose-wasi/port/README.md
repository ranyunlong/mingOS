# Official-source Compose WASI qualification port

Current result: genuine Compose compiler 2.3.20 + official Runtime 1.10.0 source compiles to wasmWasi and the counter executes in the real Ming host4 with Ming-only packaged imports. This is an experimental qualification, not production Compose support.

The root of this standalone project is an aggregator. The actual application entry is counter/src/wasmWasiMain/kotlin/Main.kt. It uses official @Composable, remember, mutableStateOf, ComposeNode, AbstractApplier, ControlledComposition, Snapshot and DisposableEffect APIs. The early root src/ smoke file is not an active application target.

Modules: official annotation 1.9.1, runtime-annotation 1.10.0, collection 1.5.0 and Runtime 1.10.0 sources; platform (pinned identity + bounded diagnostics); counter; independent identity, diagnostics and clock-check qualifications. Upstream sources and Apache licenses are preserved under upstream/. Common algorithms are unchanged.

Actual recorded successful build:

```powershell
node probes/compose-wasi/port/qualify.mjs gradle resume-counter-compile-03 :counter:compileProductionExecutableKotlinWasmWasi :clock-check:compileProductionExecutableKotlinWasmWasi :diagnostics:compileProductionExecutableKotlinWasmWasi --stacktrace
node probes/compose-wasi/port/qualify.mjs exec resume-counter-package-03 node probes/compose-wasi/port/package.mjs counter-03 counter
node probes/compose-wasi/port/host-run.mjs
node probes/compose-wasi/port/verify-port.mjs
```

These commands reuse existing labels and will replace their associated evidence; preserve the evidence directory before intentionally replaying them. Every recorded command is independently reproducible from its *.command.json arguments. The wrapper is invoked with -p probes/compose-wasi/port; project cache stays under this probe, while the existing .cache/gradle is reused. JDK21 is selected explicitly.

package.mjs invokes the existing unchanged root adapter: the only accepted runtime rewrite is random_get -> ming_v2.random_fill. Unsupported WASI imports fail; no whitelist is broadened. host-run.mjs snapshots the current real Rust host executable under the allowed evidence directory, hashes it before execution and runs the retained counter Wasm with a 60-second per-run budget. The default 5-second budget was insufficient for one cold load; performance is not qualified.

The initial fd_write blocker and the combined counter+suspend-clock variant are retained separately as counter-01/ and counter-02/. The final runnable artifact is counter-03/. A diagnostic platform actual uses the existing ming_v1.log event ABI with reversible UTF-8 framing, an explicit 2048-byte line limit and host-error checks. The default frame clock explicitly throws unless a suitable clock is supplied; clock-check verifies the rejection rather than pretending a clock exists. Runtime's upstream web weak-reference fallback is strong: only explicit slot cleanup is qualified, not true weak-reference GC. Guest execution is constrained to a single thread.

See ../../../artifacts/compose-qualification/port/report.md, result.json, source-audit.json, patches/ and intrinsics-proposal.md. The historical qualification and port-v1 evidence, as well as the completed JVM comparison, remain unchanged. No root SDK, Rust, AA, scripts or sample sources are modified by this port.
