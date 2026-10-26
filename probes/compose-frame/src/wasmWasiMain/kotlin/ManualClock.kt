package frameprobe

import androidx.compose.runtime.MonotonicFrameClock
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class ClockClosed : CancellationException("PROBE_FRAME_CLOCK_CLOSED")
class FrameCapacity : IllegalStateException("PROBE_FRAME_AWAITER_CAPACITY")
/** Real suspending frame-clock contract driven explicitly by test main, not native presentation. */
class ManualClock(private val loop: ManualLoop, private val maxWaiters: Int = 32) : MonotonicFrameClock {
    private class Waiter(val deliver: (Long) -> Unit, val cancel: () -> Unit) { var active = true }
    private val waiters = mutableListOf<Waiter>()
    private var closed = false
    private var lastFrame: Long? = null
    val pending: Int get() = waiters.size
    override suspend fun <R> withFrameNanos(onFrame: (Long) -> R): R = suspendCancellableCoroutine { continuation ->
        if (closed || loop.isClosed) { continuation.cancel(ClockClosed()); return@suspendCancellableCoroutine }
        if (waiters.size >= maxWaiters) { continuation.resumeWithException(FrameCapacity()); return@suspendCancellableCoroutine }
        val waiter = Waiter({ nanos ->
            if (continuation.isActive) {
                try { continuation.resume(onFrame(nanos)) } catch (failure: Throwable) { continuation.resumeWithException(failure) }
            }
        }, { continuation.cancel(ClockClosed()) })
        waiters.add(waiter)
        continuation.invokeOnCancellation { waiter.active = false; waiters.remove(waiter) }
    }
    fun sendFrame(nanos: Long): Boolean {
        if (closed || loop.isClosed) throw ClockClosed()
        require(nanos >= 0 && (lastFrame == null || nanos >= lastFrame!!)) { "PROBE_FRAME_TIME_BACKWARDS" }
        require(nanos == loop.nowNanos) { "PROBE_FRAME_TIME_NOT_MANUALLY_ADVANCED" }
        if (lastFrame == nanos) return false
        lastFrame = nanos
        val batch = waiters.toList(); waiters.clear()
        for (waiter in batch) if (waiter.active) { waiter.active = false; waiter.deliver(nanos) }
        return true
    }
    fun close() {
        if (closed) return
        closed = true
        val batch = waiters.toList(); waiters.clear()
        for (waiter in batch) if (waiter.active) { waiter.active = false; waiter.cancel() }
    }
}
