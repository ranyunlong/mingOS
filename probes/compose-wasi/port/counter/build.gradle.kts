plugins { kotlin("multiplatform"); id("org.jetbrains.kotlin.plugin.compose") }
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain.dependencies { implementation(project(":runtime")); implementation(project(":platform")) }
    }
}

composeCompiler {
    reportsDestination.set(layout.buildDirectory.dir("compose-reports"))
    metricsDestination.set(layout.buildDirectory.dir("compose-metrics"))
}
