plugins { kotlin("multiplatform") }
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
}
kotlin.sourceSets.getByName("wasmWasiMain").dependencies { implementation(project(":platform")) }
