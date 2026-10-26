# Input SDK standalone probe

Fixed Kotlin 2.3.20. Includes the actual ../../ming-sdk Gradle project, not copied SDK logic. Only this probe's build/ming-sdk receives SDK outputs. Gradle user home is the existing project-local .cache/gradle; temporary JBR sockets and probe caches stay under this project. Builds use --offline and the existing root Gradle wrapper.

Commands (PowerShell from repository root; stage directories must be new):

~~~powershell
$env:JAVA_HOME='C:\Program Files\Android\Android Studio\jbr'
node probes/input-sdk/build.mjs my-green
node probes/input-sdk/contract.mjs my-green
node probes/input-sdk/build.mjs my-legacy --legacy
node probes/input-sdk/build.mjs my-pure --pure
node probes/input-sdk/verify.mjs another-run
~~~

verify.mjs <run-id> orchestrates green-<run-id>, legacy-<run-id> and pure-<run-id>, compares imports against legacy-baseline, executes both input and pure Kotlin/Wasm, and freezes SDK hashes. The completed run uses run-id verified. Re-running requires a new run ID because stage evidence is never overwritten. The initial execution harness failure (missing Kotlin runtime entropy import in the pure consumer double) is preserved in verification-first-failure.json. verification.json contains exact commands and artifact hashes. Input sources are snapshotted for each stage. compiler.wasm is unmodified compiler output; main.wasm uses only the repository's existing SDK adapter.

The RED stage was compiled before either new SDK file existed and failed on missing public APIs. No RED Wasm was produced. GREEN stages are real Kotlin/Wasm executions. Node supplies only ABI input bytes and validates the boundary; all codec/edit logic and assertions execute in Kotlin. No GUI/physical IME or paid calls are performed.
