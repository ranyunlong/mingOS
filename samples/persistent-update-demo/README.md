# Persistent update fixture

These are three independent SDK6 Kotlin/Compose applications. `old` reads
`note.txt` and displays `0.1.0`; `new` reads `state/note.txt` and displays
`0.2.0`. Each requires exactly `alpha` before rendering and waits for Close.
The new manifest declares `ensureDir(state)` followed by
`renameFile(note.txt, state/note.txt)` from `0.1.0`.

`absent-write` is a separate `0.2.0` application without a migration. It writes
`note.txt=alpha`, requires confirmed directory sync, reads the bytes back, then
renders and waits for Close. The Host tests use it with an originally absent
private namespace/files directory.

Build with the existing local Gradle dependencies:

```sh
node scripts/build-kotlin.mjs persistent-update-demo old
node scripts/build-kotlin.mjs persistent-update-demo new
node scripts/build-kotlin.mjs persistent-update-demo absent-write
node --test samples/persistent-update-demo/tests/runtime.test.mjs
```

The build command always passes `--offline` and
`-PpersistentUpdateFixture=old|new|absent-write`. Gradle selects only
`src/<variant>/kotlin` and writes to `build/<variant>`; packaged artifacts are
in `artifacts/persistent-update-demo/<variant>/`. Each output contains the
unsigned manifest, raw `compiler.wasm`, adapted `main.wasm`, `build-info.json`,
compiler logs, and a build receipt. The Host helper signs the exact packaged
bytes with the corresponding version and migration manifest.

The build records source hashes for this fixture, SDK, Compose, ported runtime,
coroutines, Gradle configuration/wrapper and adapter. It verifies those inputs
remain unchanged, reflects every import and its i32 signature, and permits
only the existing `random_get` to `ming_v2.random_fill` adaptation.

Diagnostic events are `9500` (read result), `9501` (write result), `9502`
(directory sync, `1` required), `9510` (application result), and `9511`
(submitted frame count). These do not replace the parent Host's lifecycle
evidence.

The Node tests execute the real compiled modules with synthetic private-file,
rendering, input, and Close capabilities. They check distinct layouts, exact
content, sync refusal, resource cleanup and cancellation. Real files,
transactions, process reaping, recovery and reboot qualification are verified
by the separate Rust/Host tests.
