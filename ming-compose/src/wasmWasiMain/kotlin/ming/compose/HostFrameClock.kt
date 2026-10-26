package ming.compose

import androidx.compose.runtime.MonotonicFrameClock
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine

/** Single-thread frame clock fed by the host, after advancing its dispatcher to the event time. */
internal class HostFrameClock(
    private val dispatcher: FrameDispatcher,
    private val maxWaiters: Int = 32,
) : MonotonicFrameClock {
    init { require(maxWaiters > 0) }
    private class Waiter(val deliver: (Long) -> Unit, val cancel: () -> Unit) { var active = true }
    private val waiters = mutableListOf<Waiter>()
    private var delivering: List<Waiter> = emptyList()
    private var closed = false
    private var sending = false
    private var lastFrame: Long? = null
    val pending: Int get() = waiters.size
    private fun closed() = CancellationException("HOST_FRAME_CLOCK_CLOSED")

    override suspend fun <R> withFrameNanos(onFrame: (Long) -> R): R = suspendCancellableCoroutine { continuation ->
        if (closed || dispatcher.isClosed) {
            continuation.cancel(closed())
            return@suspendCancellableCoroutine
        }
        if (waiters.size >= maxWaiters) {
            continuation.resumeWith(Result.failure(IllegalStateException("HOST_FRAME_WAITER_CAPACITY")))
            return@suspendCancellableCoroutine
        }
        val waiter = Waiter({ nanos ->
            if (continuation.isActive) continuation.resumeWith(runCatching { onFrame(nanos) })
        }, { continuation.cancel(closed()) })
        waiters.add(waiter)
        continuation.invokeOnCancellation { waiter.active = false; waiters.remove(waiter) }
    }

    fun sendFrame(nanos: Long): Boolean {
        if (closed || dispatcher.isClosed) throw closed()
        require(nanos >= 0 && (lastFrame == null || nanos >= lastFrame!!)) { "HOST_FRAME_TIME_BACKWARDS" }
        require(nanos == dispatcher.nowNanos) { "HOST_FRAME_EVENT_TIME_MISMATCH" }
        if (lastFrame == nanos) return false
        check(!sending) { "HOST_FRAME_REENTRANT_SEND" }
        lastFrame = nanos
        sending = true
        // New waiters belong to the next frame, including registrations inside onFrame.
        delivering = waiters.toList()
        waiters.clear()
        try {
            for (waiter in delivering) if (waiter.active) {
                waiter.active = false
                if (closed || dispatcher.isClosed) waiter.cancel() else waiter.deliver(nanos)
            }
        } finally { delivering = emptyList(); sending = false }
        return true
    }

    fun close() {
        if (closed) return
        closed = true
        val remaining = waiters.toList() + delivering
        waiters.clear()
        for (waiter in remaining) if (waiter.active) {
            waiter.active = false
            waiter.cancel()
        }
    }
}
