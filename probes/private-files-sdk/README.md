# Private Files SDK Probe

The probe compiles a real Kotlin/Wasm application using `MingPrivateFiles.call`. The Linux host run checks that an ungranted application is denied and invalid SDK inputs do not emit RPCs. `contract.mjs` uses a controlled parent response to exercise the success path; it does not test real user authorization.

From this directory, build offline using the repository Gradle cache:

```sh
GRADLE_USER_HOME=../../.cache/gradle java -classpath ../../gradle/wrapper/gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain --offline --no-daemon compileProductionExecutableKotlinWasmWasi
node package.mjs
```

`contract.mjs` needs Docker, the `rust:latest` image, and a Linux `ming-app-host` binary in the target directory. Set `MING_HOST_TARGET` to that Cargo target directory if it is not `/private/tmp/mingos-private-file-target-20261029`, then run `node contract.mjs`.

To build the separate authorized probe, use `-Pcontract=authorized` in the Gradle command and run `node package.mjs authorized`. On Linux, run the explicitly ignored integration test with `MING_PRIVATE_KOTLIN_WASM` set to the resulting absolute `build/run-authorized/main.wasm` path:

```sh
MING_PRIVATE_KOTLIN_WASM="$PWD/build/run-authorized/main.wasm" cargo test --manifest-path ../../Cargo.toml -p ming-app-host --no-default-features --features desktop --test private_files_kotlin authorized_kotlin_workers_write_read_and_remain_isolated -- --ignored
```

The authorized and large manifests declare `privateStorage: true`; the declaration does not itself grant permission. The test parent explicitly grants two test identities and directly forwards worker calls to `PrivateFileRpc`. It exercises real Kotlin workers and filesystem storage, not the desktop's authorization UI or supervisor dispatch loop.

There is also an ignored supervisor unit test that runs the authorized probe through the actual supervisor dispatch loop. On Linux, build the host binary with `--no-default-features --features desktop`, then set `MING_TEST_PRIVATE_MANIFEST` to the absolute `build/run-authorized/app.json` path and `MING_TEST_PRIVATE_WORKER` to the absolute path of that binary. Run the test named `private_file_supervisor_runs_authorized_kotlin_and_rejects_mismatched_identity` with `--lib -- --ignored`. The worker override exists only in test builds; authorization in this test is explicit test setup, not a user interface decision.

For the 131,064-byte chunked upload probe, build with `-Pcontract=large` and run `node package.mjs large`. On Linux, set `MING_PRIVATE_LARGE_WASM` to the resulting absolute `build/run-large/main.wasm` path and run the ignored integration test `authorized_kotlin_worker_writes_large_file_in_chunks`. For the supervisor test, set `MING_TEST_PRIVATE_LARGE_MANIFEST` to `build/run-large/app.json` and `MING_TEST_PRIVATE_WORKER` to the host binary, then run the ignored library test `private_file_supervisor_runs_large_kotlin_upload`. Both explicitly grant a test identity; neither exercises real desktop authorization.

Two additional ignored Linux library tests exercise the desktop controller with real workers: `storage_prompt_approval_launches_real_kotlin_worker` uses `MING_TEST_PRIVATE_MANIFEST` and `MING_TEST_PRIVATE_WORKER`; `offscreen_declared_storage_starts_without_implicit_approval` uses `MING_TEST_PRIVATE_DENIED_WASM` pointing to `build/run/main.wasm` and the same worker variable. The first simulates presentation and mouse input in the controller; it does not validate a real window or physical input.

The public shared-directory probes use `MingPublicFiles` and the `ming_public_files_v2` import. Build them with `-Pcontract=public`, `-Pcontract=public-large`, or `-Pcontract=public-denied`, then run `node package.mjs public`, `node package.mjs public-large`, or `node package.mjs public-denied`. The ignored `public_files_kotlin` tests verify shared access, directory operations, and 131,064-byte chunked writes with `MING_PUBLIC_TYPED_WASM` and `MING_PUBLIC_TYPED_LARGE_WASM`.

Desktop controller tests use the same probes with `MING_PUBLIC_TYPED_WASM`, `MING_PUBLIC_DENIED_WASM`, and `MING_TEST_PRIVATE_WORKER`. They simulate the presented prompt and matching button press/release, then verify approval grants a persistent public session and denial starts the worker with public calls returning `DENIED`. These tests still do not qualify a physical window, hardware GPU, or kernel bundle.
