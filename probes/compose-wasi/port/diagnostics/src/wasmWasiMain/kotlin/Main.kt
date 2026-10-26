@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
import ming.compose.port.WasiDiagnostics
import kotlin.wasm.WasmImport
@WasmImport("ming_v1", "log") private external fun event(code: Int, value: Int): Int
fun main() {
    WasiDiagnostics.writeLine("Ming 诊断 · error")
    WasiDiagnostics.writeLine("x".repeat(WasiDiagnostics.MAX_LINE_BYTES - 1))
    var rejected = false
    try { WasiDiagnostics.writeLine("x".repeat(WasiDiagnostics.MAX_LINE_BYTES)) }
    catch (failure: IllegalArgumentException) { rejected = failure.message?.startsWith("MING_DIAGNOSTIC_LINE_LIMIT:") == true }
    check(rejected)
    event(1199, 1)
}
