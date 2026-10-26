import java.security.MessageDigest
import java.io.File
import groovy.json.JsonOutput

plugins {
    kotlin("jvm") version "2.3.20"
    id("org.jetbrains.kotlin.plugin.compose") version "2.3.20"
    application
}

kotlin { jvmToolchain(21) }
application { mainClass.set("qualification.MainKt") }
dependencies { implementation("org.jetbrains.compose.runtime:runtime:1.10.0") }

tasks.jar { archiveBaseName.set("compose-jvm-counter") }

tasks.register("qualificationArtifacts") {
    dependsOn(tasks.jar)
    doLast {
        val out = rootDir.resolve("../../artifacts/compose-jvm-qualification")
        out.mkdirs()
        fun digest(file: File): String = MessageDigest.getInstance("SHA-256")
            .digest(file.readBytes()).joinToString("") { "%02x".format(it) }
        fun artifacts(configuration: org.gradle.api.artifacts.Configuration) =
            configuration.resolvedConfiguration.resolvedArtifacts.map { artifact ->
                mapOf("coordinate" to artifact.moduleVersion.id.toString(),
                    "file" to artifact.file.absolutePath, "bytes" to artifact.file.length(),
                    "sha256" to digest(artifact.file))
            }
        val jar = tasks.jar.get().archiveFile.get().asFile
        val retainedJar = out.resolve(jar.name)
        jar.copyTo(retainedJar, overwrite = true)
        val classRoot = layout.buildDirectory.dir("classes/kotlin/main").get().asFile
        val classes = classRoot.walkTopDown().filter { it.isFile && it.extension == "class" }.map { file ->
            val rel = file.relativeTo(classRoot).invariantSeparatorsPath
            val retained = out.resolve("classfiles/" + rel)
            retained.parentFile.mkdirs()
            file.copyTo(retained, overwrite = true)
            mapOf("path" to rel, "bytes" to file.length(), "sha256" to digest(file))
        }.toList()
        val compiler = configurations.filter {
            it.isCanBeResolved && it.name.contains("compilerPlugin", ignoreCase = true)
        }.map { configuration -> mapOf("configuration" to configuration.name, "artifacts" to artifacts(configuration)) }
        val runtime = configurations.getByName("runtimeClasspath")
        val report = mapOf(
            "applicationJar" to mapOf("file" to retainedJar.absolutePath, "sha256" to digest(retainedJar), "bytes" to retainedJar.length()),
            "mainClass" to application.mainClass.get(),
            "runtimeClasspath" to artifacts(runtime),
            "compilerPluginConfigurations" to compiler,
            "classes" to classes,
            "components" to runtime.incoming.resolutionResult.allComponents.map { component ->
                mapOf("id" to component.id.displayName, "variants" to component.variants.map { variant ->
                    mapOf("name" to variant.displayName, "attributes" to variant.attributes.keySet().associate { key ->
                        @Suppress("UNCHECKED_CAST")
                        val typed = key as org.gradle.api.attributes.Attribute<Any>
                        key.name to variant.attributes.getAttribute(typed).toString()
                    })
                })
            },
        )
        val text = JsonOutput.prettyPrint(JsonOutput.toJson(report)) + "\n"
        out.resolve("artifacts.json").writeText(text)
        println(text)
    }
}
