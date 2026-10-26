plugins { kotlin("multiplatform"); id("org.jetbrains.kotlin.plugin.compose") }
kotlin {
    compilerOptions {
        freeCompilerArgs.addAll("-XXLanguage:+AllowAnyAsAnActualTypeForExpectInterface", "-XXLanguage:+JsAllowImplementingFunctionInterface")
    }
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs() }
    sourceSets {
        val commonMain by getting {
            kotlin.srcDir(rootDir.resolve("upstream/runtime/commonMain"))
            dependencies {
                api(project(":runtime-annotation"))
                api(project(":collection"))
                api("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
                implementation("org.jetbrains.kotlinx:atomicfu:0.27.0")
            }
        }
        // Keep OptionalExpectation annotations in intermediate common source sets.
        val nonAndroidMain by creating {
            dependsOn(commonMain)
            kotlin.srcDir(rootDir.resolve("upstream/runtime/nonAndroidMain"))
        }
        val nonJvmMain by creating {
            dependsOn(nonAndroidMain)
            kotlin.srcDir(rootDir.resolve("upstream/runtime/nonJvmMain"))
            // Platform diagnostics use the existing Ming event ABI, not WASI stdout.
            kotlin.exclude("**/internal/Utils.nonJvm.kt")
        }
        val singleThreadMain by creating {
            dependsOn(nonJvmMain)
            // Upstream web actuals are Kotlin-only: thread identity, synchronization,
            // and a strong-reference weak-reference fallback (experimental limitation).
            kotlin.srcDir(rootDir.resolve("upstream/runtime/webMain"))
        }
        val wasmWasiMain by getting {
            dependsOn(singleThreadMain)
            dependencies { implementation(project(":platform")) }
        }
    }
}
