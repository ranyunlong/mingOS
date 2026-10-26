@file:OptIn(kotlinx.coroutines.InternalCoroutinesApi::class)
package frameprobe

import kotlinx.coroutines.*
import kotlin.coroutines.*

class QueueCapacity : IllegalStateException("PROBE_QUEUE_CAPACITY")
class TimerCapacity : IllegalStateException("PROBE_TIMER_CAPACITY")
class LoopClosed : CancellationException("PROBE_LOOP_CLOSED")

/** Deterministic qualification driver. Main supplies all time; no OS clock or native loop. */
class ManualLoop(
    private val maxQueue: Int = 128,
    private val maxTimers: Int = 64,
    private val maxPumpSteps: Int = 4096,
    private val maxErrors: Int = 16,
) : CoroutineDispatcher(), Delay {
    private val queue = ArrayDeque<Runnable>()
    private val alarms = mutableListOf<Alarm>()
    val errors = mutableListOf<Throwable>()
    val rootJob: CompletableJob = SupervisorJob()
    private val handler = object : AbstractCoroutineContextElement(CoroutineExceptionHandler), CoroutineExceptionHandler {
        override fun handleException(context: CoroutineContext, exception: Throwable) { record(exception) }
    }
    val context: CoroutineContext = rootJob + this + handler
    val scope: CoroutineScope = CoroutineScope(context)
    var nowNanos: Long = 0
        private set
    var isClosed = false
        private set
    private var closing = false
    private var draining = false
    private var nextId = 0L
    val pendingTasks: Int get() = queue.size
    val pendingTimers: Int get() = alarms.size

    private inner class Alarm(val id: Long, val at: Long, val block: Runnable, val cancel: (() -> Unit)?) : DisposableHandle {
        var active = true
        override fun dispose() { if (active) { active = false; alarms.remove(this) } }
        fun fire() { if (active) { dispose(); block.run() } }
    }
    private fun record(failure: Throwable) {
        check(errors.size < maxErrors) { "PROBE_ERROR_CAPACITY" }
        errors.add(failure)
    }
    override fun isDispatchNeeded(context: CoroutineContext): Boolean = true
    override fun dispatch(context: CoroutineContext, block: Runnable) {
        if (isClosed && !closing) {
            val job = context[Job] ?: throw LoopClosed()
            job.cancel(LoopClosed())
        }
        if (queue.size >= maxQueue) throw QueueCapacity()
        queue.addLast(block)
    }
    private fun deadline(millis: Long): Long {
        require(millis >= 0)
        val delta = if (millis > Long.MAX_VALUE / 1_000_000L) Long.MAX_VALUE else millis * 1_000_000L
        return if (delta > Long.MAX_VALUE - nowNanos) Long.MAX_VALUE else nowNanos + delta
    }
    private fun schedule(millis: Long, block: Runnable, cancelled: (() -> Unit)? = null): Alarm {
        if (isClosed) throw LoopClosed()
        if (alarms.size >= maxTimers) throw TimerCapacity()
        return Alarm(++nextId, deadline(millis), block, cancelled).also { alarms.add(it) }
    }
    override fun scheduleResumeAfterDelay(timeMillis: Long, continuation: CancellableContinuation<Unit>) {
        if (isClosed) { continuation.cancel(LoopClosed()); return }
        val alarm = schedule(timeMillis, Runnable { if (continuation.isActive) continuation.resume(Unit) }, { continuation.cancel(LoopClosed()) })
        continuation.invokeOnCancellation { alarm.dispose() }
    }
    override fun invokeOnTimeout(timeMillis: Long, block: Runnable, context: CoroutineContext): DisposableHandle = schedule(timeMillis, block)
    fun drain() {
        check(!draining) { "PROBE_REENTRANT_PUMP" }
        draining = true
        var steps = 0
        try {
            while (true) {
                val action: Runnable = if (queue.isNotEmpty()) queue.removeFirst() else {
                    val alarm = alarms.filter { it.active && it.at <= nowNanos }.minWithOrNull(compareBy<Alarm> { it.at }.thenBy { it.id }) ?: break
                    Runnable { alarm.fire() }
                }
                check(++steps <= maxPumpSteps) { "PROBE_PUMP_STEP_LIMIT" }
                try { action.run() } catch (failure: Throwable) { record(failure) }
            }
        } finally { draining = false }
    }
    fun advanceTo(nanos: Long): Boolean {
        if (isClosed) throw LoopClosed()
        require(nanos >= nowNanos) { "PROBE_TIME_BACKWARDS" }
        val changed = nanos != nowNanos
        nowNanos = nanos
        drain()
        return changed
    }
    fun close() {
        if (isClosed) return
        isClosed = true; closing = true
        try {
            rootJob.cancel(LoopClosed())
            val remaining = alarms.toList()
            remaining.forEach { it.dispose(); it.cancel?.invoke() }
            drain()
            check(queue.isEmpty() && alarms.isEmpty()) { "PROBE_CLOSE_PENDING_WORK" }
        } finally { closing = false }
    }
}
