plugins { kotlin("multiplatform") }
kotlin {
 @OptIn(org.jetbrains.kotlin.gradle.ExperimentalWasmDsl::class)
 wasmWasi { nodejs(); binaries.executable() }
 sourceSets { wasmWasiMain.dependencies { implementation(project(":ming-sdk")) } }
}
