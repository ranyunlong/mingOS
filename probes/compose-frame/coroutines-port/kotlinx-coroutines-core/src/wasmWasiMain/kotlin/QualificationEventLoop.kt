/*
 * Derived from kotlinx-coroutines-core-wasm-wasi 1.9.0, wasmWasiMain/EventLoop.kt.
 * Modified for MingOS deterministic qualification: remove WASI clock/poll and
 * exported-function-exit loop hooks. Default execution/time services fail explicitly;
 * the sidecar must inject its genuine bounded CoroutineDispatcher and Delay.
 * Original source and license are retained unchanged under ../upstream/.
 */
package kotlinx.coroutines

import kotlin.coroutines.CoroutineContext
import kotlin.wasm.WasmImport

@OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
@WasmImport("ming_v1", "log") private external fun missingService(code: Int, value: Int): Int
private fun missing(service: String): Nothing {
    missingService(1981, 1)
    throw UnsupportedOperationException("PROBE_EXPLICIT_DISPATCHER_AND_CLOCK_REQUIRED: " + service)
}
internal actual fun createEventLoop(): EventLoop = missing("createEventLoop")
internal actual fun nanoTime(): Long = missing("nanoTime")
internal actual object DefaultExecutor : EventLoopImplBase() {
    override fun shutdown(): Unit = missing("shutdown")
    override fun invokeOnTimeout(timeMillis: Long, block: Runnable, context: CoroutineContext): DisposableHandle = missing("default timeout")
    actual override fun enqueue(task: Runnable): Unit = missing("default enqueue")
}
internal actual abstract class EventLoopImplPlatform : EventLoop() {
    protected actual fun unpark(): Unit = missing("unpark")
    protected actual fun reschedule(now: Long, delayedTask: EventLoopImplBase.DelayedTask): Unit = missing("reschedule")
}
internal actual inline fun platformAutoreleasePool(crossinline block: () -> Unit) = block()
internal actual fun createDefaultDispatcher(): CoroutineDispatcher = DefaultExecutor

// Suspend-main entry uses this in upstream CoroutineRunner; unsupported here.
internal fun runEventLoop(): Unit = missing("runEventLoop")
