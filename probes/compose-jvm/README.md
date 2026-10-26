# Real Compose JVM qualification

This standalone JVM probe pins JDK 21, Kotlin and Compose compiler 2.3.20, and the official org.jetbrains.compose.runtime:runtime:1.10.0 dependency. It uses an in-memory AbstractApplier tree and genuine compiler-transformed @Composable code. It opens no GUI and does not change the production runtime.

The program validates initial nodes, remembered mutable state changing from 0 to 1, recomposition with stable remembered and node identities, conditional node removal, child DisposableEffect cleanup, Composition.dispose, parent DisposableEffect cleanup, and a second idempotent dispose. It deliberately drives the official ControlledComposition API synchronously; this is not a GUI/event-loop or scheduling benchmark.

From the workspace root:

```powershell
node probes/compose-jvm/qualify.mjs env
node probes/compose-jvm/qualify.mjs gradle jvm-compile-02 qualificationArtifacts --stacktrace --info
node probes/compose-jvm/execute.mjs jvm-compile-02 02
node probes/compose-jvm/verify.mjs
```

The existing root wrapper and cached Gradle 8.14.5 distribution are used with -p probes/compose-jvm. Project cache and outputs are local to this probe; the existing project .cache/gradle dependency cache is reused. COMPOSE_PROBE_JAVA_HOME can select another JDK 21 installation; the qualified installation is Android Studio JBR 21.0.6.

qualificationArtifacts retains the real application JAR, classfiles, resolved runtime/compiler dependency hashes and Gradle variant selections in artifacts/compose-jvm-qualification. execute.mjs executes java -cp using that JAR and its resolved official dependencies, then runs javap and jar --list. It does not run the application from a classes directory. Every compile/run command retains independent stdout/stderr and an exit-code/hash receipt.

result.json separates compilation, actual execution and lowering verification. report.md explains the result and limits. The first run's overly strict Applier.clear count assertion and its original JAR/source are retained in attempt-01/. Original WASI qualification and port directories are preserved; wasi-checkpoint.json captures the paused implementation state.

This qualification demonstrates the Compose programming model on JVM only. It does not establish SDK-only isolation, a JVM sandbox, native component integration, performance, Android/ART equivalence, or a production runtime migration.
