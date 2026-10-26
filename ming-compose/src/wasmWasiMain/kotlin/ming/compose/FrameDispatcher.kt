@file:OptIn(kotlinx.coroutines.InternalCoroutinesApi::class)

package ming.compose

import kotlinx.coroutines.CancellableContinuation
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableJob
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineExceptionHandler
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Delay
import kotlinx.coroutines.DisposableHandle
import kotlinx.coroutines.Job
import kotlinx.coroutines.Runnable
import kotlinx.coroutines.SupervisorJob
import kotlin.coroutines.CoroutineContext
import kotlin.coroutines.resume

/**
 * Single-thread dispatcher owned and pumped by the host event loop. No platform timer is used.
 * [nowNanos] is the last delivered event time; fresh scheduling reads never advance it.
 * Limits fail explicitly. A failed pump/error buffer shuts down instead of leaving runnable work.
 */
internal class FrameDispatcher(
    initialNowNanos: Long,
    private val nowProvider: () -> Long,
    private val maxQueue: Int = 128,
    private val maxTimers: Int = 64,
    private val maxErrors: Int = 16,
    private val maxPumpSteps: Int = 4096,
    initialTimerId: Long = 0,
) : CoroutineDispatcher(), Delay {
    init {
        require(initialNowNanos >= 0 && initialTimerId >= 0)
        require(maxQueue > 0 && maxTimers > 0 && maxErrors > 0 && maxPumpSteps > 0)
    }

    private class Task(val context: CoroutineContext, val block: Runnable)
    private val queue = ArrayDeque<Task>()
    private val timers = mutableListOf<Timer>()
    private val errors = ArrayDeque<Throwable>()
    private var errorsOverflowed = false
    private var draining = false
    private var nextTimerId = initialTimerId

    val rootJob: CompletableJob = SupervisorJob()
    private val handler = CoroutineExceptionHandler { _, failure -> record(failure) }
    val context: CoroutineContext = rootJob + this + handler
    val scope: CoroutineScope = CoroutineScope(context)

    var nowNanos: Long = initialNowNanos
        private set
    var isClosed: Boolean = false
        private set
    val pendingTasks: Int get() = queue.size
    val pendingTimers: Int get() = timers.size
    val nextDeadlineNanos: Long? get() = timers.minOfOrNull { it.deadline }

    private fun closed() = CancellationException("FRAME_DISPATCHER_CLOSED")
    private fun record(failure: Throwable) {
        // An exception handler must never itself throw into unsupported platform handlers.
        if (errors.size < maxErrors) errors.addLast(failure) else errorsOverflowed = true
    }

    // Returning false would enter coroutines' platform unconfined EventLoop. This WASI
    // variant deliberately has no platform EventLoop, including for cancellation cleanup.
    override fun isDispatchNeeded(context: CoroutineContext): Boolean = true

    private var cancellationDepth = 0
    private fun runCancelled(task: Task) {
        val job = task.context[Job] ?: return
        job.cancel(closed())
        if (cancellationDepth >= maxQueue) {
            record(IllegalStateException("FRAME_CANCELLATION_DEPTH_LIMIT"))
            return
        }
        cancellationDepth++
        try { task.block.run() } catch (failure: Throwable) { record(failure) }
        finally { cancellationDepth-- }
    }

    override fun dispatch(context: CoroutineContext, block: Runnable) {
        val task = Task(context, block)
        if (isClosed) {
            val job = context[Job] ?: throw closed()
            job.cancel(closed())
            // Only cancelled continuations can run here. Free one slot if cancellation
            // fans out while the pump is already active; raw callbacks never run on close.
            if (queue.size >= maxQueue) runCancelled(queue.removeFirst())
            queue.addLast(task)
            drain()
            return
        }
        if (queue.size >= maxQueue) {
            val failure = IllegalStateException("FRAME_QUEUE_CAPACITY")
            if (context[Job] == null) throw failure
            record(failure)
            // A rejected coroutine must complete cancellation, without invoking the
            // unsupported unconfined path or executing a new DEFAULT launch's body.
            runCancelled(task)
            return
        }
        queue.addLast(task)
    }

    private inner class Timer(
        val id: Long,
        val deadline: Long,
        val block: Runnable,
        val cancel: (() -> Unit)?,
    ) : DisposableHandle {
        private var active = true
        override fun dispose() {
            if (active) { active = false; timers.remove(this) }
        }
        fun fire() {
            if (active) { dispose(); block.run() }
        }
    }

    private fun schedule(millis: Long, block: Runnable, cancel: (() -> Unit)? = null): Timer {
        if (isClosed) throw closed()
        require(millis >= 0) { "FRAME_NEGATIVE_DELAY" }
        check(timers.size < maxTimers) { "FRAME_TIMER_CAPACITY" }
        check(nextTimerId < Long.MAX_VALUE) { "FRAME_TIMER_ID_EXHAUSTED" }
        val fresh = nowProvider()
        require(fresh >= 0) { "FRAME_NEGATIVE_MONOTONIC_TIME" }
        val base = maxOf(nowNanos, fresh)
        val delta = if (millis > Long.MAX_VALUE / 1_000_000L) Long.MAX_VALUE else millis * 1_000_000L
        val deadline = if (delta > Long.MAX_VALUE - base) Long.MAX_VALUE else base + delta
        return Timer(++nextTimerId, deadline, block, cancel).also { timers.add(it) }
    }

    override fun scheduleResumeAfterDelay(timeMillis: Long, continuation: CancellableContinuation<Unit>) {
        if (isClosed) { continuation.cancel(closed()); return }
        val timer = schedule(timeMillis, Runnable { if (continuation.isActive) continuation.resume(Unit) }, { continuation.cancel(closed()) })
        continuation.invokeOnCancellation { timer.dispose() }
    }

    override fun invokeOnTimeout(timeMillis: Long, block: Runnable, context: CoroutineContext): DisposableHandle =
        schedule(timeMillis, block)

    /** Nested drains are coalesced into the active pump, never recursively executed. */
    fun drain() {
        if (draining) return
        draining = true
        var steps = 0
        try {
            while (true) {
                if (errorsOverflowed && !isClosed) close()
                if (!isClosed && steps >= maxPumpSteps) {
                    val due = nextDeadlineNanos?.let { it <= nowNanos } == true
                    if (queue.isEmpty() && !due) break
                    record(IllegalStateException("FRAME_PUMP_STEP_LIMIT"))
                    close()
                }
                val action = if (queue.isNotEmpty()) {
                    val task = queue.removeFirst()
                    if (isClosed) Runnable { runCancelled(task) } else task.block
                } else {
                    if (isClosed) break
                    val timer = timers.filter { it.deadline <= nowNanos }
                        .minWithOrNull(compareBy<Timer> { it.deadline }.thenBy { it.id }) ?: break
                    Runnable { timer.fire() }
                }
                steps++
                try { action.run() } catch (failure: Throwable) { record(failure) }
            }
        } finally { draining = false }
    }

    fun advanceTo(nanos: Long): Boolean {
        if (isClosed) throw closed()
        require(nanos >= 0 && nanos >= nowNanos) { "FRAME_TIME_BACKWARDS" }
        check(!draining) { "FRAME_REENTRANT_ADVANCE" }
        val changed = nanos != nowNanos
        nowNanos = nanos
        drain()
        return changed
    }

    /** Consume failures one at a time, preserving the original Throwable identity. */
    fun throwIfFailed() {
        if (errors.isNotEmpty()) throw errors.removeFirst()
        if (errorsOverflowed) {
            errorsOverflowed = false
            error("FRAME_ERROR_CAPACITY: additional failures could not be retained")
        }
    }

    /**
     * Idempotent cancellation, including calls from a running callback. Already queued coroutine
     * continuations run only cancellation cleanup; plain callbacks and timers are discarded.
     * Cleanup is bounded by the existing queue (at most maxQueue); closed dispatch cannot add work.
     */
    fun close() {
        if (isClosed) return
        isClosed = true
        rootJob.cancel(closed())
        val remaining = timers.toList()
        remaining.forEach { timer -> timer.dispose(); timer.cancel?.invoke() }
        repeat(queue.size) {
            val task = queue.removeFirst()
            val job = task.context[Job]
            if (job != null) queue.addLast(task)
        }
        drain()
    }
}
