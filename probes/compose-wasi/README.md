# Compose WASI qualification probe

Pinned candidate: Kotlin/Compose compiler 2.3.20, Compose Runtime 1.10.0, Gradle 8.14.5, JDK 21.

**Current result: blocked at authentic WASI dependency variant resolution. The counter has not compiled or run.**

This standalone project has only a wasmWasi application target. It imports the official Compose APIs, including AbstractApplier, remember, mutableStateOf and DisposableEffect. Main.kt is a qualification candidate for an in-memory counter, identity-preserving recomposition, and disposal. It does not define a replacement Compose annotation or runtime.

From the MingOS workspace root on the qualified Windows environment:

```powershell
node probes/compose-wasi/qualify.mjs env
node probes/compose-wasi/qualify.mjs gradle compiler-artifacts qualifyCompilerArtifacts --stacktrace
node probes/compose-wasi/qualify.mjs gradle platform-dependencies-final qualifyPlatformDependencies --stacktrace
node probes/compose-wasi/qualify.mjs gradle compile-counter-final compileKotlinWasmWasi compileProductionExecutableKotlinWasmWasi --stacktrace
node probes/compose-wasi/inspect.mjs
node probes/compose-wasi/verify-evidence.mjs
```

The dependency and compile commands currently return exit 1, intentionally preserving the real failure. The compiler-artifact task returns exit 0 and proves availability only. Each run writes command arguments, input hashes, independent stdout/stderr and exit status to artifacts/compose-qualification. Reusing the same label replaces that label's receipt; use a new label to retain historical runs.

qualify.mjs invokes the existing cached root wrapper with -p probes/compose-wasi, a probe-local project cache, and the existing project-local .cache/gradle user home. It selects Android Studio JBR 21; COMPOSE_PROBE_JAVA_HOME may point at another JDK 21 installation. No root project is included. Generated build and project-cache output stays in this probe, apart from normal shared Gradle dependency-cache writes.

fetch accepts explicit authoritative artifact URLs and records bytes/hash/HTTP status under the evidence directory, namespaced by host and full Maven path. Canonical downloaded inputs are listed in download-hashes.json. extract-sources.ps1 preserves official source archives and their original Apache license files under the evidence directory for inspection only; none is a compiler input. inspect-klib.ps1 reads the official Wasm-JS KLIB manifest without applying it to the WASI target.

See ../../artifacts/compose-qualification/report.md and result.json for findings, scope, source-port inventory, and the distinction between resolution, compilation and execution.
