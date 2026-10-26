pluginManagement { repositories { mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { mavenCentral() } }
rootProject.name = "frame-live"
include(":ming-sdk")
project(":ming-sdk").projectDir = file("../../ming-sdk")
