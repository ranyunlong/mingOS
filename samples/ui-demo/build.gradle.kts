plugins { kotlin("multiplatform") }

val uiDemoContract = providers.gradleProperty("uiDemoContract").isPresent
if (uiDemoContract) layout.buildDirectory.set(layout.projectDirectory.dir("build/sdk-contract"))

kotlin {
    @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
    wasmWasi { nodejs(); binaries.executable() }
    sourceSets {
        wasmWasiMain {
            if (uiDemoContract) kotlin.setSrcDirs(listOf("tests"))
            dependencies { implementation(project(":ming-sdk")) }
        }
    }
}

if (uiDemoContract) {
    tasks.register<Copy>("stageSdkContract") {
        dependsOn("compileProductionExecutableKotlinWasmWasi")
        from(layout.buildDirectory.file("compileSync/wasmWasi/main/productionExecutable/kotlin/mingos-ui-demo.wasm"))
        into(layout.projectDirectory.dir("build/sdk-contract-artifact"))
        rename { "sdk-contract.wasm" }
    }
}
