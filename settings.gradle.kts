pluginManagement { repositories { gradlePluginPortal(); mavenCentral() } }
dependencyResolutionManagement { repositories { mavenCentral() } }
rootProject.name = "mingos"
include(":ming-sdk", ":read-file")
project(":read-file").projectDir = file("samples/read-file")

include(":image-viewer")
project(":image-viewer").projectDir = file("samples/image-viewer")

include(":text-demo")
project(":text-demo").projectDir = file("samples/text-demo")

include(":ui-demo")
project(":ui-demo").projectDir = file("samples/ui-demo")

include(":shadow-demo")
project(":shadow-demo").projectDir = file("samples/shadow-demo")


include(":ming-compose")
includeBuild("probes/compose-wasi/port") {
    dependencySubstitution {
        substitute(module("dev.ming.compose:runtime")).using(project(":runtime"))
    }
}

include(":compose-demo")
project(":compose-demo").projectDir = file("samples/compose-demo")

includeBuild("probes/compose-frame/coroutines-port") {
    dependencySubstitution {
        substitute(module("org.jetbrains.kotlinx:kotlinx-coroutines-core")).using(project(":kotlinx-coroutines-core"))
    }
}

include(":animated-demo")
project(":animated-demo").projectDir = file("samples/animated-demo")

include(":text-entry-demo")
project(":text-entry-demo").projectDir = file("samples/text-entry-demo")
