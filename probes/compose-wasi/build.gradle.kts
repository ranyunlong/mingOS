import java.security.MessageDigest

plugins {
    kotlin("multiplatform") version "2.3.20"
    id("org.jetbrains.kotlin.plugin.compose") version "2.3.20"
}

kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi {
        nodejs()
        binaries.executable()
    }
    sourceSets {
        wasmWasiMain.dependencies {
            implementation("org.jetbrains.compose.runtime:runtime:1.10.0")
        }
    }
}

// Diagnostic only: preserve the actual WASI compile attributes for every dependency.
val platformCoordinates = listOf(
    "org.jetbrains.compose.runtime:runtime:1.10.0",
    "androidx.compose.runtime:runtime:1.10.0",
    "androidx.collection:collection:1.5.0",
    "androidx.compose.runtime:runtime-annotation:1.10.0",
    "org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0",
    "org.jetbrains.kotlinx:atomicfu:0.27.0",
)
val platformProbes = platformCoordinates.mapIndexed { index, coordinate ->
    configurations.create("qualificationDependency" + index) {
        isCanBeResolved = true
        isCanBeConsumed = false
        val wasiAttributes = configurations.getByName("wasmWasiCompileClasspath").attributes
        attributes {
            wasiAttributes.keySet().forEach { key ->
                @Suppress("UNCHECKED_CAST")
                val typedKey = key as org.gradle.api.attributes.Attribute<Any>
                attribute(typedKey, wasiAttributes.getAttribute(typedKey)!!)
            }
        }
        dependencies.add(project.dependencies.create(coordinate))
    }
}

tasks.register("qualifyPlatformDependencies") {
    doLast {
        val results = platformProbes.mapIndexed { index, configuration ->
            try {
                configuration.resolve()
                val artifacts = configuration.resolvedConfiguration.resolvedArtifacts.map { artifact ->
                    mapOf("coordinate" to artifact.moduleVersion.id.toString(), "file" to artifact.file.absolutePath,
                        "sha256" to MessageDigest.getInstance("SHA-256")
                            .digest(artifact.file.readBytes()).joinToString("") { "%02x".format(it) })
                }
                println("WASI RESOLVED: " + platformCoordinates[index])
                mapOf("coordinate" to platformCoordinates[index], "resolved" to true, "artifacts" to artifacts)
            } catch (failure: Exception) {
                val error = generateSequence(failure as Throwable?) { it.cause }.joinToString("\n") { it.message ?: it.javaClass.name }
                println("WASI BLOCKED: " + platformCoordinates[index] + "\n" + error)
                mapOf("coordinate" to platformCoordinates[index], "resolved" to false, "error" to error)
            }
        }
        val report = rootDir.resolve("../../artifacts/compose-qualification/platform-dependencies.json")
        report.writeText(groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(results)) + "\n")
        if (results.any { it["resolved"] == false }) throw GradleException("WASI platform dependency qualification has blockers; see " + report)
    }
}


tasks.register("qualifyCompilerArtifacts") {
    doLast {
        val pluginConfigurations = configurations.filter {
            it.isCanBeResolved && it.name.contains("compilerPlugin", ignoreCase = true) &&
                it.name.contains("WasmWasi", ignoreCase = true)
        }
        check(pluginConfigurations.isNotEmpty()) { "No WASI compiler plugin configurations registered" }
        val results = pluginConfigurations.map { configuration ->
            configuration.resolve()
            mapOf("configuration" to configuration.name, "artifacts" to configuration.resolvedConfiguration.resolvedArtifacts.map { artifact ->
                mapOf("coordinate" to artifact.moduleVersion.id.toString(), "file" to artifact.file.absolutePath,
                    "sha256" to MessageDigest.getInstance("SHA-256").digest(artifact.file.readBytes())
                        .joinToString("") { "%02x".format(it) })
            })
        }
        val report = rootDir.resolve("../../artifacts/compose-qualification/compiler-artifacts.json")
        report.writeText(groovy.json.JsonOutput.prettyPrint(groovy.json.JsonOutput.toJson(results)) + "\n")
        println(report.readText())
    }
}
