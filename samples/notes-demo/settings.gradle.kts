pluginManagement {
    repositories { gradlePluginPortal(); mavenCentral() }
    plugins {
        kotlin("multiplatform") version "2.3.20"
        id("org.jetbrains.kotlin.plugin.compose") version "2.3.20"
    }
}
dependencyResolutionManagement { repositories { mavenCentral() } }
rootProject.name = "notes-demo"
include(":ming-sdk", ":ming-compose")
project(":ming-sdk").projectDir = file("../../ming-sdk")
project(":ming-compose").projectDir = file("../../ming-compose")
includeBuild("../../probes/compose-wasi/port") {
    dependencySubstitution { substitute(module("dev.ming.compose:runtime")).using(project(":runtime")) }
}
includeBuild("../../probes/compose-frame/coroutines-port") {
    dependencySubstitution { substitute(module("org.jetbrains.kotlinx:kotlinx-coroutines-core")).using(project(":kotlinx-coroutines-core")) }
}
