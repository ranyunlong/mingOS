plugins { kotlin("multiplatform") }
kotlin {
    compilerOptions { optIn.add("kotlin.contracts.ExperimentalContracts") }
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs() }
    sourceSets {
        commonMain {
            kotlin.srcDir(rootDir.resolve("upstream/collection/commonMain"))
            dependencies { api(project(":annotation")) }
        }
        wasmWasiMain {
            kotlin.srcDir(rootDir.resolve("upstream/collection/nonJvmMain"))
            kotlin.srcDir(rootDir.resolve("upstream/collection/webMain"))
        }
    }
}
