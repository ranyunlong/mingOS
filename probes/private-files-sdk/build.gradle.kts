plugins { kotlin("multiplatform") version "2.3.20" }
project(":ming-sdk") { layout.buildDirectory.set(rootProject.layout.projectDirectory.dir("build/ming-sdk")) }
kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain {
            if (providers.gradleProperty("contract").orNull == "authorized") kotlin.setSrcDirs(listOf("authorized"))
            if (providers.gradleProperty("contract").orNull == "large") kotlin.setSrcDirs(listOf("large"))
            if (providers.gradleProperty("contract").orNull == "typed" || providers.gradleProperty("contract").orNull == "private-kernel") kotlin.setSrcDirs(listOf("typed"))
            if (providers.gradleProperty("contract").orNull == "typed-large") kotlin.setSrcDirs(listOf("typed-large"))
            if (providers.gradleProperty("contract").orNull == "typed-directory") kotlin.setSrcDirs(listOf("typed-directory"))
            if (providers.gradleProperty("contract").orNull == "typed-denied") kotlin.setSrcDirs(listOf("typed-denied"))
            if (providers.gradleProperty("contract").orNull == "typed-malformed") kotlin.setSrcDirs(listOf("typed-malformed"))
            if (providers.gradleProperty("contract").orNull == "public") kotlin.setSrcDirs(listOf("public"))
            if (providers.gradleProperty("contract").orNull == "public-large") kotlin.setSrcDirs(listOf("public-large"))
            if (providers.gradleProperty("contract").orNull == "public-denied") kotlin.setSrcDirs(listOf("public-denied"))
            dependencies { implementation(project(":ming-sdk")) }
        }
    }
}
