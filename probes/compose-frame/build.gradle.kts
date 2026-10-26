import java.security.MessageDigest
import groovy.json.JsonOutput
plugins {
    kotlin("multiplatform") version "2.3.20"
    id("org.jetbrains.kotlin.plugin.compose") version "2.3.20"
}
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain.dependencies {
            implementation(files("libs/ported/runtime", "libs/ported/platform", "libs/ported/runtime-annotation", "libs/ported/collection", "libs/ported/annotation"))
            implementation("org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0")
            implementation("org.jetbrains.kotlinx:atomicfu:0.27.0")
        }
    }
}
composeCompiler {
    reportsDestination.set(layout.buildDirectory.dir("compose-reports"))
    metricsDestination.set(layout.buildDirectory.dir("compose-metrics"))
}
tasks.register("qualificationMetadata") {
    dependsOn("compileProductionExecutableKotlinWasmWasi")
    doLast {
        val records = listOf("wasmWasiCompileClasspath", "wasmWasiRuntimeClasspath", "kotlinCompilerPluginClasspathWasmWasiMain").associateWith { name ->
            configurations.getByName(name).resolvedConfiguration.resolvedArtifacts.map { artifact ->
                val files = if (artifact.file.isDirectory) artifact.file.walkTopDown().filter { it.isFile }.sortedBy { it.relativeTo(artifact.file).invariantSeparatorsPath }.map { file ->
                    mapOf("path" to file.relativeTo(artifact.file).invariantSeparatorsPath, "bytes" to file.length(), "sha256" to MessageDigest.getInstance("SHA-256").digest(file.readBytes()).joinToString("") { "%02x".format(it) })
                }.toList() else emptyList()
                val digestBytes = if (artifact.file.isDirectory) JsonOutput.toJson(files).toByteArray(Charsets.UTF_8) else artifact.file.readBytes()
                mapOf("coordinate" to artifact.moduleVersion.id.toString(), "file" to artifact.file.absolutePath,
                    "kind" to if (artifact.file.isDirectory) "directory" else "file", "files" to files,
                    "sha256" to MessageDigest.getInstance("SHA-256").digest(digestBytes).joinToString("") { "%02x".format(it) })
            }
        }
        rootDir.resolve("../../artifacts/compose-frame-qualification/resolved-dependencies.json").writeText(JsonOutput.prettyPrint(JsonOutput.toJson(records)) + "\n")
    }
}
