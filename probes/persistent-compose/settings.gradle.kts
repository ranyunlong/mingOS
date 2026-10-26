pluginManagement { repositories { gradlePluginPortal(); mavenCentral() } }
dependencyResolutionManagement { repositories { mavenCentral() } }
rootProject.name = "ming-persistent-compose-probe"
include(":ming-sdk", ":ming-compose")
project(":ming-sdk").projectDir = file("../../ming-sdk")
project(":ming-compose").projectDir = file("../../ming-compose")
includeBuild("../compose-wasi/port") { dependencySubstitution { substitute(module("dev.ming.compose:runtime")).using(project(":runtime")) } }
includeBuild("../compose-frame/coroutines-port") { dependencySubstitution { substitute(module("org.jetbrains.kotlinx:kotlinx-coroutines-core")).using(project(":kotlinx-coroutines-core")) } }
