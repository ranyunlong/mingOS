/*
 * Derived from kotlinx-coroutines-core-wasm-wasi 1.9.0,
 * wasmWasiMain/internal/CoroutineExceptionHandlerImpl.kt.
 * Modified for MingOS deterministic qualification: no WASI fd_write/proc_exit.
 * Unhandled fallback emits a failure marker and throws explicitly. It is not a
 * production abort implementation. Every qualified coroutine must use the owned
 * error sink; any 1980 marker makes the qualification fail.
 * Original source and license are retained unchanged under ../upstream/.
 */
@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
package kotlinx.coroutines.internal
import kotlin.wasm.WasmImport
@WasmImport("ming_v1", "log") private external fun unhandled(code: Int, value: Int): Int
internal actual fun propagateExceptionFinalResort(exception: Throwable) {
    unhandled(1980, 1)
    throw UnsupportedOperationException("PROBE_UNHANDLED_EXCEPTION_REQUIRES_HOST_ABORT", exception)
}
