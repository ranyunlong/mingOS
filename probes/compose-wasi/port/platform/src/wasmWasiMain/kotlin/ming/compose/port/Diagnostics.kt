@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
package ming.compose.port

import kotlin.wasm.WasmImport

/** Experimental bounded UTF-8 lines over the existing integer Ming event ABI. */
object WasiDiagnostics {
    const val MAX_LINE_BYTES = 2048
    fun writeLine(text: String) {
        // Reject long input before concatenation/encoding; at most 6142 UTF-8 bytes
        // can be allocated from 2047 UTF-16 code units plus newline.
        require(text.length < MAX_LINE_BYTES) { "MING_DIAGNOSTIC_LINE_LIMIT: UTF-16 length " + text.length }
        val bytes = (text + "\n").encodeToByteArray()
        require(bytes.size <= MAX_LINE_BYTES) { "MING_DIAGNOSTIC_LINE_LIMIT: " + bytes.size }
        emit(1100, bytes.size)
        var index = 0
        while (index < bytes.size) {
            var word = 0
            for (offset in 0 until 4) {
                if (index + offset < bytes.size) {
                    word = word or ((bytes[index + offset].toInt() and 255) shl (offset * 8))
                }
            }
            emit(1101, word)
            index += 4
        }
        emit(1102, bytes.size)
    }
    private fun emit(code: Int, value: Int) {
        val result = log(code, value)
        check(result == 0) { "MING_DIAGNOSTIC_HOST_REJECTED: " + result }
    }
}
@WasmImport("ming_v1", "log")
private external fun log(code: Int, value: Int): Int
