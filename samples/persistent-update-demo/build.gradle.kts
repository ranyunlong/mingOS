plugins { kotlin("multiplatform"); id("org.jetbrains.kotlin.plugin.compose") }

val fixture = providers.gradleProperty("persistentUpdateFixture").getOrElse("old")
require(fixture in listOf("old", "new", "absent-write")) {
    "persistentUpdateFixture must be old, new or absent-write"
}
layout.buildDirectory.set(layout.projectDirectory.dir("build/$fixture"))

kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain {
            kotlin.setSrcDirs(listOf("src/$fixture/kotlin"))
            dependencies { implementation(project(":ming-compose")) }
        }
    }
}
composeCompiler {
    reportsDestination.set(layout.buildDirectory.dir("compose-reports"))
    metricsDestination.set(layout.buildDirectory.dir("compose-metrics"))
}
