plugins { kotlin("multiplatform") }
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs() }
    sourceSets {
        commonMain {
            kotlin.srcDir(rootDir.resolve("upstream/annotation/commonMain"))
            
        }
        wasmWasiMain {
            kotlin.srcDir(rootDir.resolve("upstream/annotation/nonJvmMain"))
        }
    }
}
