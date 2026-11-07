plugins { kotlin("multiplatform"); id("org.jetbrains.kotlin.plugin.compose") }
val notesContract = providers.gradleProperty("notesContract").isPresent
if (notesContract) layout.buildDirectory.set(layout.projectDirectory.dir("build/contract"))
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain {
            if (notesContract) { kotlin.exclude("Main.kt"); kotlin.srcDir("tests/kotlin") }
            dependencies { implementation(project(":ming-compose")) }
        }
    }
}
composeCompiler {
    reportsDestination.set(layout.buildDirectory.dir("compose-reports"))
    metricsDestination.set(layout.buildDirectory.dir("compose-metrics"))
}
