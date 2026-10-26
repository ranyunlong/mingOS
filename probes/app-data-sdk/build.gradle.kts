plugins { kotlin("multiplatform") version "2.3.20" }
project(":ming-sdk") { layout.buildDirectory.set(rootProject.layout.projectDirectory.dir("build/ming-sdk")) }
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain {
            kotlin.setSrcDirs(listOf(when (providers.gradleProperty("contract").orNull) { "legacy" -> "legacy"; "size-only" -> "size-only"; else -> "src/wasmWasiMain/kotlin" }))
            dependencies { implementation(project(":ming-sdk")) }
        }
    }
}
