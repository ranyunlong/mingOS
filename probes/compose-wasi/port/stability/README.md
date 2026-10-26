# Bounded WASI stability qualification

This is an independent test module, not a replacement for canonical counter-03. No canonical settings or Runtime source is edited. include.init.gradle includes the module and redirects every participating project's build output under stability/builds; the wrapper uses stability/.gradle and the existing project dependency cache.

From the workspace root:

```powershell
node probes/compose-wasi/port/stability/qualify.mjs gradle stability-compile-01 :stability:compileProductionExecutableKotlinWasmWasi --stacktrace
node probes/compose-wasi/port/stability/qualify.mjs exec stability-package-01 node probes/compose-wasi/port/stability/package.mjs
& '.\artifacts\compose-qualification\port\host-snapshot\ming-app-host.exe' run '.\artifacts\compose-qualification\port\stability\run-01\app.json' --timeout-ms 60000 --fuel 300000000
node probes/compose-wasi/port/stability/verify.mjs
```

Recorded results are under artifacts/compose-qualification/port/stability. Existing labels overwrite their own outputs when explicitly replayed, so preserve evidence before reusing them. The 100M fuel attempt failed at the resource budget; the 300M fuel attempt completed all16 lifetimes using195502045 fuel. This is bounded functional stability, not a performance or memory/GC qualification.

The stable integration baseline, parent replay and mandatory platform limitations remain in ../README.md and artifacts/compose-qualification/port/handoff.md.
