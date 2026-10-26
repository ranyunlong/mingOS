plugins { kotlin("multiplatform") version "2.3.20" }

val contract = providers.gradleProperty("shadowContract").isPresent
if (contract) layout.buildDirectory.set(layout.projectDirectory.dir("build/contract"))
if (rootProject.name == "shadow-demo") {
    project(":ming-sdk").layout.buildDirectory.set(layout.projectDirectory.dir("build/sdk"))
}

kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain {
            if (contract) kotlin.setSrcDirs(listOf("tests/kotlin"))
            dependencies { implementation(project(":ming-sdk")) }
        }
    }
}
