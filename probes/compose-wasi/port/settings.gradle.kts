pluginManagement { repositories { mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { mavenCentral(); google() } }
rootProject.name = "mingos-compose-wasi-official-source-port"
include(":identity")
include(":platform")
include(":annotation", ":runtime-annotation", ":collection", ":runtime", ":counter")
include(":diagnostics")
include(":clock-check")
