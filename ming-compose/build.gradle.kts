plugins { kotlin("multiplatform"); id("org.jetbrains.kotlin.plugin.compose") }
val mingComposeFramedAppContract = providers.gradleProperty("mingComposeFramedAppContract").isPresent
val mingComposeRuntimeContract = providers.gradleProperty("mingComposeRuntimeContract").isPresent
val mingComposeDriverContract = providers.gradleProperty("mingComposeDriverContract").isPresent
val mingComposeContract = providers.gradleProperty("mingComposeContract").isPresent
val mingComposeRendererContract = providers.gradleProperty("mingComposeRendererContract").isPresent
require(listOf(mingComposeContract, mingComposeRendererContract, mingComposeDriverContract, mingComposeRuntimeContract, mingComposeFramedAppContract).count { it } <= 1) { "Choose one Compose contract source set" }
if (mingComposeDriverContract) layout.buildDirectory.set(layout.projectDirectory.dir("build/driver-contract"))
if (mingComposeRuntimeContract) layout.buildDirectory.set(layout.projectDirectory.dir("build/framed-runtime-contract"))
if (mingComposeFramedAppContract) layout.buildDirectory.set(layout.projectDirectory.dir("build/framed-app-contract"))
if (mingComposeRendererContract) layout.buildDirectory.set(layout.projectDirectory.dir("build/renderer-contract"))
if (mingComposeContract) layout.buildDirectory.set(layout.projectDirectory.dir("build/contract"))
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); if (mingComposeContract || mingComposeRendererContract || mingComposeDriverContract || mingComposeRuntimeContract || mingComposeFramedAppContract) binaries.executable() }
    sourceSets {
        wasmWasiMain {
            if (mingComposeDriverContract) kotlin.srcDir("tests/framed-driver")
            if (mingComposeRuntimeContract) kotlin.srcDir("tests/framed-runtime")
            if (mingComposeFramedAppContract) kotlin.srcDir("tests/framed-app")
            if (mingComposeContract) kotlin.srcDir("tests/contract")
            if (mingComposeRendererContract) kotlin.srcDir("tests/renderer-contract")
            dependencies {
                api(project(":ming-sdk"))
                api("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
                api("dev.ming.compose:runtime:0.1.0")
            }
        }
    }
}
composeCompiler {
    reportsDestination.set(layout.buildDirectory.dir("compose-reports"))
    metricsDestination.set(layout.buildDirectory.dir("compose-metrics"))
}
