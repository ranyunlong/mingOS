plugins { kotlin("multiplatform") }

// Opt-in contract executable; its outputs never replace the actual demo or package.
val textDemoContract = providers.gradleProperty("textDemoContract").isPresent
if (textDemoContract) layout.buildDirectory.set(layout.projectDirectory.dir("build/sdk-contract"))

kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain {
            if (textDemoContract) kotlin.setSrcDirs(listOf("tests"))
            dependencies { implementation(project(":ming-sdk")) }
        }
    }
}

if (textDemoContract) {
    // Gradle removes old outputs when the same compilation changes build directories.
    // Stage the probe like the production packager so a later demo build preserves it.
    tasks.register<Copy>("stageSdkContract") {
        dependsOn("compileProductionExecutableKotlinWasmWasi")
        from(layout.buildDirectory.file("compileSync/wasmWasi/main/productionExecutable/kotlin/mingos-text-demo.wasm"))
        into(layout.projectDirectory.dir("build/sdk-contract-artifact"))
        rename { "sdk-contract.wasm" }
    }
}
