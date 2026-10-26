pluginManagement { repositories { mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositories { mavenCentral(); google() } }
rootProject.name = "ming-app-data-sdk-contract"
include(":ming-sdk")
project(":ming-sdk").projectDir = file("../../ming-sdk")
