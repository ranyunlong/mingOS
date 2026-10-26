pluginManagement { repositories { mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { mavenCentral(); google() } }
rootProject.name = "mingos-compose-frame-qualification"
includeBuild("coroutines-port") {
    dependencySubstitution { substitute(module("org.jetbrains.kotlinx:kotlinx-coroutines-core")).using(project(":kotlinx-coroutines-core")) }
}
