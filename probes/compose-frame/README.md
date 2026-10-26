# 6D deterministic Compose coroutine/frame qualification

This independent wasmWasi sidecar uses real Compose compiler2.3.20, a frozen notice-version official Runtime1.10.0 port KLIB and genuine coroutines1.9 source APIs. It does not modify root SDK/host/ming-compose/port or prior evidence.

The tests call actual Recomposer.runRecomposeAndApplyChanges and LaunchedEffect. Recomposition is performed by Recomposer, never repeated manual ControlledComposition.recompose calls. Test main explicitly advances ManualLoop time and ManualClock frames, and flushes Snapshot apply notifications. This is not a production HostFrame/native presentation clock.

Important condition: the unmodified published coroutines1.9 KLIB retains forbidden WASI platform fallbacks even with a custom dispatcher. The passing profile includes official-source coroutines with two platform actual replacements that fail explicitly for missing default services, plus four annotation-array compatibility copies for Kotlin2.3. Upstream archive files and license are retained under coroutines-port/upstream; six review patches and source provenance are in the evidence directory. Default dispatcher/runBlocking/suspend-main/platform abort are not implemented by this profile.

The final Mode.kt is GREEN. From the workspace root:

```powershell
node probes/compose-frame/qualify.mjs gradle frame-green-rebuild :compileProductionExecutableKotlinWasmWasi :qualificationMetadata --offline --stacktrace
node probes/compose-frame/qualify.mjs exec frame-green-package node probes/compose-frame/package.mjs frame-green-replay
node probes/compose-frame/run-host.mjs frame-green-replay
node probes/compose-frame/qualify.mjs exec frame-green-evaluation node probes/compose-frame/evaluate.mjs frame-green-replay
node probes/compose-frame/verify.mjs
```

The original frozen passing case is artifacts/compose-frame-qualification/green-parent-clock; its existing app.json can be directly replayed using artifacts/compose-frame-qualification/host/ming-app-host.exe run <app.json> --timeout-ms60000 --fuel300000000 (pass the options as separate arguments). Use fresh labels to preserve evidence. Budget120000ms is not used or supported.

RED evidence is red-adapted: constructor-only clock wiring caused actual case302 failure and the captured missing-MonotonicFrameClock diagnostic. Host exits0 to preserve structured test output, while the evaluator exits1. Only Mode.kt true->false fixes the runner context; all shared source/build input differences are recorded in red-green-diff.json. GREEN executes28 cases with no default-service fallback markers.

Queues/timers/frame awaiters/errors and pump steps are bounded. Close/cancellation, delay/timeout, duplicate/backward time, same-value state, real remembered state/node identity and LaunchedEffect errors are covered. Underlying Runtime strong weakref fallback, single-thread assumption and unstable pinned identity remain. No GUI/input, physical mouse, native frame timestamp, performance or production SDK qualification is claimed.

Result/report/provenance/import types/command logs are under artifacts/compose-frame-qualification. Minimum future capabilities are documented in hostframe-abi-proposal.md; no proposed ABI was implemented in root.
