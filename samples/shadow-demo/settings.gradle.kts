pluginManagement { repositories { gradlePluginPortal(); mavenCentral() } }
dependencyResolutionManagement { repositories { mavenCentral() } }
rootProject.name = "shadow-demo"
include(":ming-sdk")
project(":ming-sdk").projectDir = file("../../ming-sdk")
