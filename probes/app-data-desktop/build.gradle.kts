@file:OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
plugins {
    kotlin("multiplatform") version "2.3.20"
    id("org.jetbrains.kotlin.plugin.compose") version "2.3.20"
}
layout.buildDirectory.set(layout.projectDirectory.dir("build/appdata"))
subprojects { afterEvaluate { layout.buildDirectory.set(rootProject.layout.projectDirectory.dir("build/deps/" + name)) } }
kotlin {
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets { wasmWasiMain.dependencies { implementation(project(":ming-compose")) } }
}
composeCompiler {
    reportsDestination.set(layout.buildDirectory.dir("compose-reports"))
    metricsDestination.set(layout.buildDirectory.dir("compose-metrics"))
}
