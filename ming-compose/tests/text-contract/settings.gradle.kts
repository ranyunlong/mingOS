pluginManagement { repositories { gradlePluginPortal(); mavenCentral() } }
dependencyResolutionManagement { repositories { mavenCentral() } }
rootProject.name = "ming-text-field-contract"
include(":ming-sdk", ":ming-compose", ":text-entry-demo")
project(":ming-sdk").projectDir = file("../../../ming-sdk")
project(":ming-compose").projectDir = file("../../../ming-compose")
project(":text-entry-demo").projectDir = file("../../../samples/text-entry-demo")
includeBuild("../../../probes/compose-wasi/port") {
    dependencySubstitution { substitute(module("dev.ming.compose:runtime")).using(project(":runtime")) }
}
includeBuild("../../../probes/compose-frame/coroutines-port") {
    dependencySubstitution { substitute(module("org.jetbrains.kotlinx:kotlinx-coroutines-core")).using(project(":kotlinx-coroutines-core")) }
}
