@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
import androidx.compose.runtime.runWasiDefaultClockQualification
import kotlin.wasm.WasmImport
@WasmImport("ming_v1", "log") private external fun event(code: Int, value: Int): Int
fun main() { check(runWasiDefaultClockQualification() == 1); event(223, 1) }
