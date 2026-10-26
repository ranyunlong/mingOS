plugins { kotlin("multiplatform") }
group = "org.jetbrains.kotlinx"
version = "1.9.0"
kotlin {
    compilerOptions {
        optIn.addAll("kotlinx.coroutines.InternalCoroutinesApi", "kotlinx.coroutines.ExperimentalCoroutinesApi", "kotlinx.coroutines.DelicateCoroutinesApi", "kotlinx.coroutines.ObsoleteCoroutinesApi", "kotlinx.coroutines.FlowPreview", "kotlin.ExperimentalStdlibApi", "kotlin.contracts.ExperimentalContracts", "kotlin.time.ExperimentalTime", "kotlin.ExperimentalUnsignedTypes", "kotlin.experimental.ExperimentalTypeInference", "kotlin.ExperimentalMultiplatform")
        freeCompilerArgs.add("-Xexpect-actual-classes")
    }
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs() }
    sourceSets {
        val commonMain by getting {
            kotlin.srcDir(rootDir.resolve("upstream/commonMain"))
            kotlin.exclude("CompletableDeferred.kt", "CompletableJob.kt", "Deferred.kt", "Job.kt")
            dependencies { implementation("org.jetbrains.kotlinx:atomicfu:0.27.0") }
        }
        val jsAndWasmSharedMain by creating {
            dependsOn(commonMain)
            kotlin.srcDir(rootDir.resolve("upstream/jsAndWasmSharedMain"))
        }
        val wasmWasiMain by getting {
            dependsOn(jsAndWasmSharedMain)
            kotlin.srcDir(rootDir.resolve("upstream/wasmWasiMain"))
            kotlin.exclude("EventLoop.kt", "internal/CoroutineExceptionHandlerImpl.kt")
        }
    }
}
