@file:OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
import org.jetbrains.kotlin.gradle.dsl.KotlinMultiplatformExtension
plugins {
    kotlin("multiplatform") version "2.3.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.3.20" apply false
}
val contract = providers.gradleProperty("textContract").orElse("text").get()
subprojects {
    afterEvaluate { layout.buildDirectory.set(rootProject.layout.projectDirectory.dir("build/" + name)) }
}
project(":ming-compose") {
    afterEvaluate {
        if (contract != "sample") {
            extensions.configure<KotlinMultiplatformExtension> {
                wasmWasi { binaries.executable() }
                sourceSets.getByName("wasmWasiMain").kotlin.setSrcDirs(listOf(
                    project.file("src/wasmWasiMain/kotlin"),
                    if (contract == "text") rootProject.file("kotlin") else project.file("tests/" + contract)
                ))
            }
        }
    }
}
