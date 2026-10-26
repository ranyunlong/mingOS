@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)

package ming.sdk

import kotlin.wasm.WasmImport

/** Experimental SDK v0: positive handles, negative errors; no host paths. */
object MingFiles {
    const val DENIED: Int = -1
    fun open(resourceId: Int): Int = hostOpen(resourceId)
    fun length(handle: Int): Int = hostLength(handle)
    fun readByte(handle: Int, offset: Int): Int = hostReadByte(handle, offset)
    fun close(handle: Int): Int = hostClose(handle)
}

object MingLog {
    fun event(code: Int, value: Int): Int = hostLog(code, value)
}

@WasmImport("ming_v1", "file_open")
private external fun hostOpen(resourceId: Int): Int

@WasmImport("ming_v1", "file_length")
private external fun hostLength(handle: Int): Int

@WasmImport("ming_v1", "file_read_byte")
private external fun hostReadByte(handle: Int, offset: Int): Int

@WasmImport("ming_v1", "file_close")
private external fun hostClose(handle: Int): Int

@WasmImport("ming_v1", "log")
private external fun hostLog(code: Int, value: Int): Int
