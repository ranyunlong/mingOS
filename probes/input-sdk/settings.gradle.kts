pluginManagement { repositories { mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { mavenCentral(); google() } }
rootProject.name = "ming-input-sdk-contract"
include(":ming-sdk")
project(":ming-sdk").projectDir = file("../../ming-sdk")
