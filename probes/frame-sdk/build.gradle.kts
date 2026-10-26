plugins { kotlin("multiplatform") version "2.3.20" }

// Compile the real SDK project while keeping every task output inside this standalone probe.
project(":ming-sdk") {
    layout.buildDirectory.set(rootProject.layout.projectDirectory.dir("build/ming-sdk"))
}
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain.dependencies { implementation(project(":ming-sdk")) }
    }
}
