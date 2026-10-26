plugins { kotlin("multiplatform") version "2.3.20" }
project(":ming-sdk").layout.buildDirectory.set(layout.projectDirectory.dir("build/sdk"))
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets { wasmWasiMain.dependencies { implementation(project(":ming-sdk")) } }
}
