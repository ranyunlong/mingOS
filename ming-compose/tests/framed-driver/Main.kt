@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlinx.coroutines.InternalCoroutinesApi::class)
package ming.compose

import androidx.compose.runtime.*
import androidx.compose.runtime.snapshots.Snapshot
import kotlinx.coroutines.*
import kotlin.coroutines.EmptyCoroutineContext
import kotlin.wasm.WasmImport

@WasmImport("ming_v1", "log") private external fun log(code: Int, value: Int): Int
private fun verify(id: Int, ok: Boolean) { check(log(id, if (ok) 1 else 0) == 0); check(ok) { "DRIVER_CASE_$id" } }
private inline fun <reified T : Throwable> rejects(block: () -> Unit): Boolean = try { block(); false } catch (e: Throwable) { if (e is T) true else throw e }
private fun pump(d: FrameDispatcher) { repeat(4) { d.drain(); Snapshot.sendApplyNotifications() }; d.drain(); d.throwIfFailed() }
private fun pulse(d: FrameDispatcher, c: HostFrameClock, t: Long) { d.advanceTo(t); check(c.sendFrame(t)); pump(d) }

private fun timeCases() {
    var fresh = 0L
    val d = FrameDispatcher(0L, { fresh })
    val marks = mutableListOf<Int>()
    val job = d.scope.launch { marks.add(1); delay(10); marks.add(2) }
    d.drain()
    verify(4001, marks == listOf(1) && d.pendingTimers == 1 && d.nextDeadlineNanos == 10_000_000L)
    d.advanceTo(9_999_999); check(marks == listOf(1))
    d.advanceTo(10_000_000)
    verify(4002, marks == listOf(1, 2) && job.isCompleted && d.nextDeadlineNanos == null)
    verify(4003, !d.advanceTo(10_000_000) && rejects<IllegalArgumentException> { d.advanceTo(-1) } && rejects<IllegalArgumentException> { d.advanceTo(9) } && d.nowNanos == 10_000_000L)
    fresh = 13_000_000
    var delayed = false
    d.scope.launch { delay(5); delayed = true }; d.drain()
    val clock = HostFrameClock(d)
    verify(4004, d.nowNanos == 10_000_000L && d.nextDeadlineNanos == 18_000_000L && clock.sendFrame(10_000_000))
    d.advanceTo(17_999_999); check(!delayed); d.advanceTo(18_000_000)
    verify(4005, delayed && d.pendingTimers == 0)
    var zero = 0
    d.scope.launch { delay(0); zero++ }; d.drain()
    var directZero = 0
    d.invokeOnTimeout(0, Runnable { directZero++ }, d.context); d.drain()
    verify(4006, zero == 1 && directZero == 1 && d.pendingTimers == 0)
    var timedOut = false; var finallyCount = 0
    d.scope.launch { try { withTimeout(5) { try { delay(50) } finally { finallyCount++ } } } catch (_: TimeoutCancellationException) { timedOut = true } }
    d.drain(); d.advanceTo(23_000_000)
    verify(4007, timedOut && finallyCount == 1 && d.pendingTimers == 0)
    var unreachable = 0; var cancelledFinally = 0
    val cancelled = d.scope.launch { try { delay(20); unreachable++ } finally { cancelledFinally++ } }
    d.drain(); cancelled.cancel(); d.drain(); d.advanceTo(100_000_000)
    verify(4008, cancelled.isCancelled && cancelledFinally == 1 && unreachable == 0 && d.pendingTimers == 0)
    var removed = 0
    val handle = d.invokeOnTimeout(1, Runnable { removed++ }, d.context)
    handle.dispose(); handle.dispose(); d.advanceTo(200_000_000)
    verify(4009, removed == 0 && d.pendingTimers == 0)
    clock.close(); d.close(); d.close(); d.throwIfFailed()
    verify(4010, d.isClosed && d.rootJob.isCompleted && d.pendingTasks == 0 && d.pendingTimers == 0)

    val huge = FrameDispatcher(Long.MAX_VALUE - 2, { Long.MAX_VALUE - 1 })
    var end = 0
    huge.invokeOnTimeout(Long.MAX_VALUE, Runnable { end++ }, huge.context)
    verify(4011, huge.nextDeadlineNanos == Long.MAX_VALUE && huge.nowNanos == Long.MAX_VALUE - 2)
    huge.advanceTo(Long.MAX_VALUE - 1); check(end == 0); huge.advanceTo(Long.MAX_VALUE)
    verify(4012, end == 1); huge.close()
    val infinite = FrameDispatcher(0, { 0 })
    val forever = infinite.scope.launch { delay(Long.MAX_VALUE); error("infinite delay returned") }
    infinite.drain(); forever.cancel(); infinite.drain()
    verify(4013, forever.isCancelled && infinite.pendingTimers == 0); infinite.close()
    verify(4014, rejects<IllegalArgumentException> { FrameDispatcher(-1, { 0 }) })
    val invalidProvider = FrameDispatcher(0, { -1 })
    verify(4015, rejects<IllegalArgumentException> { invalidProvider.invokeOnTimeout(1, Runnable {}, invalidProvider.context) } && invalidProvider.pendingTimers == 0)
    invalidProvider.close()
    var provider = 3_000_000L
    val timeoutFresh = FrameDispatcher(1_000_000, { provider }); var timeoutHit = 0
    timeoutFresh.invokeOnTimeout(2, Runnable { timeoutHit++ }, timeoutFresh.context)
    verify(4016, timeoutFresh.nowNanos == 1_000_000L && timeoutFresh.nextDeadlineNanos == 5_000_000L)
    timeoutFresh.advanceTo(4_999_999); check(timeoutHit == 0); timeoutFresh.advanceTo(5_000_000); check(timeoutHit == 1)
    var timeoutZero = false
    timeoutFresh.scope.launch { try { withTimeout(0) { error("zero timeout body") } } catch (_: TimeoutCancellationException) { timeoutZero = true } }; timeoutFresh.drain()
    verify(4017, timeoutZero && timeoutFresh.pendingTimers == 0 && rejects<IllegalArgumentException> { timeoutFresh.invokeOnTimeout(-1, Runnable {}, timeoutFresh.context) })
    provider = 0; val fifo = mutableListOf<Int>()
    timeoutFresh.invokeOnTimeout(1, Runnable { fifo.add(1) }, timeoutFresh.context)
    timeoutFresh.invokeOnTimeout(1, Runnable { fifo.add(2) }, timeoutFresh.context)
    timeoutFresh.advanceTo(6_000_000)
    verify(4018, fifo == listOf(1, 2)); timeoutFresh.close()
}

private fun boundsAndCloseCases() {
    val queue = FrameDispatcher(0, { 0 })
    var count = 0
    repeat(128) { queue.dispatch(EmptyCoroutineContext, Runnable { count++ }) }
    verify(4101, queue.pendingTasks == 128 && rejects<IllegalStateException> { queue.dispatch(EmptyCoroutineContext, Runnable { count++ }) })
    queue.drain(); verify(4102, count == 128 && queue.pendingTasks == 0); queue.close()
    val timers = FrameDispatcher(0, { 0 })
    val handles = (0 until 64).map { timers.invokeOnTimeout(1, Runnable { count++ }, timers.context) }
    verify(4103, timers.pendingTimers == 64 && rejects<IllegalStateException> { timers.invokeOnTimeout(1, Runnable {}, timers.context) })
    handles.forEach { it.dispose() }; timers.close(); check(timers.pendingTimers == 0)
    val ids = FrameDispatcher(0, { 0 }, initialTimerId = Long.MAX_VALUE - 1)
    ids.invokeOnTimeout(1, Runnable {}, ids.context)
    verify(4104, rejects<IllegalStateException> { ids.invokeOnTimeout(1, Runnable {}, ids.context) } && ids.pendingTimers == 1); ids.close()
    val steps = FrameDispatcher(0, { 0 }, maxPumpSteps = 4)
    var spins = 0
    lateinit var again: Runnable
    again = Runnable { spins++; steps.dispatch(EmptyCoroutineContext, again) }
    steps.dispatch(EmptyCoroutineContext, again); steps.drain()
    verify(4105, spins == 4 && steps.isClosed && steps.pendingTasks == 0 && rejects<IllegalStateException> { steps.throwIfFailed() })
    val nested = FrameDispatcher(0, { 0 }); val order = mutableListOf<Int>()
    nested.dispatch(EmptyCoroutineContext, Runnable {
        order.add(1); nested.dispatch(EmptyCoroutineContext, Runnable { order.add(3) }); nested.drain(); order.add(2)
        check(rejects<IllegalStateException> { nested.advanceTo(1) })
    })
    nested.drain(); verify(4106, order == listOf(1, 2, 3) && nested.nowNanos == 0L); nested.close()
    val closed = FrameDispatcher(0, { 0 }); var clean = 0; var body = 0
    closed.scope.launch { try { delay(100); body++ } finally { clean++ } }; closed.drain()
    closed.dispatch(EmptyCoroutineContext, Runnable { body++ }); closed.close()
    val late = closed.scope.launch { body++ }; closed.drain(); closed.close()
    verify(4107, clean == 1 && body == 0 && late.isCancelled && closed.rootJob.isCompleted && closed.pendingTasks == 0 && closed.pendingTimers == 0)
    verify(4108, rejects<CancellationException> { closed.dispatch(EmptyCoroutineContext, Runnable { body++ }) } && rejects<CancellationException> { closed.advanceTo(0) })
    val reclose = FrameDispatcher(0, { 0 }); var cleaned = 0
    reclose.scope.launch { try { delay(100) } finally { cleaned++ } }; reclose.drain()
    reclose.scope.launch { reclose.close() }; reclose.drain()
    verify(4109, reclose.isClosed && cleaned == 1 && reclose.rootJob.isCompleted && reclose.pendingTasks == 0 && reclose.pendingTimers == 0)
    val overload = FrameDispatcher(0, { 0 }, maxQueue = 2); var overloadBody = 0
    overload.scope.launch { overloadBody++ }; overload.scope.launch { overloadBody++ }
    val rejected = overload.scope.launch { overloadBody += 100 }
    overload.drain()
    verify(4112, rejected.isCancelled && rejected.isCompleted && overloadBody == 2 && overload.pendingTasks == 0 && rejects<IllegalStateException> { overload.throwIfFailed() }); overload.close()
    val crowded = FrameDispatcher(0, { 0 }); var crowdedCleanup = 0; var crowdedBody = 0
    repeat(64) { crowded.scope.launch { try { delay(100) } finally { crowdedCleanup++ } } }; crowded.drain()
    repeat(128) { crowded.scope.launch { crowdedBody++ } }
    crowded.close()
    verify(4113, crowdedCleanup == 64 && crowdedBody == 0 && crowded.pendingTasks == 0 && crowded.pendingTimers == 0 && crowded.rootJob.isCompleted); crowded.throwIfFailed()
    val errors = FrameDispatcher(0, { 0 }); val original = IllegalArgumentException("original-async-failure")
    errors.scope.launch { throw original }; errors.drain()
    var same = false; try { errors.throwIfFailed() } catch (e: Throwable) { same = e === original }
    verify(4110, same); errors.throwIfFailed(); errors.close()
    val capped = FrameDispatcher(0, { 0 }, maxErrors = 2)
    val first = IllegalStateException("first"); val second = IllegalStateException("second")
    capped.dispatch(EmptyCoroutineContext, Runnable { throw first }); capped.dispatch(EmptyCoroutineContext, Runnable { throw second }); capped.dispatch(EmptyCoroutineContext, Runnable { error("third") }); capped.drain()
    var one = false; var two = false
    try { capped.throwIfFailed() } catch (e: Throwable) { one = e === first }; try { capped.throwIfFailed() } catch (e: Throwable) { two = e === second }
    verify(4111, one && two && capped.isClosed && rejects<IllegalStateException> { capped.throwIfFailed() } && capped.pendingTasks == 0); capped.close()
}

private fun clockCases() {
    val d = FrameDispatcher(0, { 0 }); val c = HostFrameClock(d)
    val times = mutableListOf<Long>()
    d.scope.launch(c) { repeat(2) { withFrameNanos { times.add(it) } } }; d.drain()
    d.advanceTo(1); check(c.sendFrame(1)); d.drain()
    verify(4201, times == listOf(1L) && c.pending == 1 && !c.sendFrame(1))
    d.advanceTo(2); check(c.sendFrame(2)); d.drain()
    verify(4202, times == listOf(1L, 2L) && c.pending == 0)
    verify(4203, rejects<IllegalArgumentException> { c.sendFrame(-1) } && rejects<IllegalArgumentException> { c.sendFrame(1) } && rejects<IllegalArgumentException> { c.sendFrame(3) })
    var callback = 0; var cleaned = 0
    val cancelled = d.scope.launch(c) { try { withFrameNanos { callback++ } } finally { cleaned++ } }; d.drain(); cancelled.cancel(); d.drain()
    verify(4204, cleaned == 1 && callback == 0 && c.pending == 0)
    d.scope.launch(c) { withFrameNanos { t ->
        times.add(t); d.scope.launch(c, start = CoroutineStart.UNDISPATCHED) { withFrameNanos { times.add(it) } }
    } }; d.drain(); d.advanceTo(4); c.sendFrame(4); d.drain()
    verify(4205, times == listOf(1L, 2L, 4L) && c.pending == 1 && !c.sendFrame(4))
    d.advanceTo(5); c.sendFrame(5); d.drain(); verify(4206, times.last() == 5L && c.pending == 0)
    val small = HostFrameClock(d, 1); var capacity = false
    d.scope.launch(small) { withFrameNanos { callback++ } }; d.drain()
    d.scope.launch(small) { try { withFrameNanos { callback++ } } catch (_: IllegalStateException) { capacity = true } }; d.drain()
    verify(4207, capacity && small.pending == 1); small.close(); d.drain(); check(small.pending == 0)
    d.scope.launch(c) { try { withFrameNanos { callback++ } } finally { cleaned++ } }; d.drain(); c.close(); c.close(); d.drain()
    val after = d.scope.launch(c) { withFrameNanos { callback++ } }; d.drain()
    verify(4208, cleaned == 2 && callback == 0 && c.pending == 0 && after.isCancelled && rejects<CancellationException> { c.sendFrame(5) })
    val closingBatch = HostFrameClock(d); var second = 0; var secondFinally = 0
    d.scope.launch(closingBatch) { withFrameNanos { closingBatch.close() } }
    d.scope.launch(closingBatch) { try { withFrameNanos { second++ } } finally { secondFinally++ } }; d.drain()
    d.advanceTo(6); closingBatch.sendFrame(6); d.drain()
    verify(4209, second == 0 && secondFinally == 1 && closingBatch.pending == 0)
    val callbackFailure = IllegalArgumentException("original-frame-failure"); var exactFailure = false
    val exceptionClock = HostFrameClock(d)
    d.scope.launch(exceptionClock) { try { withFrameNanos { throw callbackFailure } } catch (e: Throwable) { exactFailure = e === callbackFailure } }; d.drain()
    d.advanceTo(7); exceptionClock.sendFrame(7); d.drain()
    verify(4210, exactFailure && exceptionClock.pending == 0); exceptionClock.close()
    val fullClock = HostFrameClock(d); var closedCleanup = 0
    repeat(32) { d.scope.launch(fullClock) { try { withFrameNanos { callback++ } } finally { closedCleanup++ } } }; d.drain()
    var overflow = false
    d.scope.launch(fullClock) { try { withFrameNanos { callback++ } } catch (_: IllegalStateException) { overflow = true } }; d.drain()
    verify(4211, fullClock.pending == 32 && overflow)
    d.close(); fullClock.close(); d.throwIfFailed()
    verify(4212, closedCleanup == 32 && callback == 0 && fullClock.pending == 0 && d.pendingTasks == 0 && d.rootJob.isCompleted)
}

private class TestNode(var text: String = "") { val children = mutableListOf<TestNode>() }
private class TestApplier(root: TestNode) : AbstractApplier<TestNode>(root) {
    override fun insertTopDown(index: Int, instance: TestNode) { current.children.add(index, instance) }
    override fun insertBottomUp(index: Int, instance: TestNode) {}
    override fun remove(index: Int, count: Int) { current.children.remove(index, count) }
    override fun move(from: Int, to: Int, count: Int) { current.children.move(from, to, count) }
    override fun onClear() { root.children.clear() }
}
private class Observed {
    lateinit var state: MutableState<Int>; lateinit var mode: MutableState<Int>; lateinit var visible: MutableState<Boolean>
    var token: Any? = null; var remembers = 0; var starts = 0; var finishes = 0; var disposed = 0; var commits = 0
    val frames = mutableListOf<Long>()
}
@Composable private fun Counter(o: Observed) {
    val state = remember { mutableStateOf(0) }; val mode = remember { mutableStateOf(0) }; val visible = remember { mutableStateOf(true) }
    o.state = state; o.mode = mode; o.visible = visible; o.token = remember { o.remembers++; Any() }
    SideEffect { o.commits++ }
    DisposableEffect(Unit) { onDispose { o.disposed++ } }
    LaunchedEffect(mode.value) {
        o.starts++
        try {
            when (mode.value) {
                0 -> { withFrameNanos { o.frames.add(it) }; state.value = 1; delay(5); state.value = 2; withFrameNanos { o.frames.add(it) }; state.value = 3 }
                1 -> delay(100)
                2 -> withFrameNanos { error("disposed frame callback ran") }
                else -> error("real-effect-failure")
            }
        } finally { o.finishes++ }
    }
    if (visible.value) ComposeNode<TestNode, TestApplier>(factory = { TestNode() }, update = { set(state.value.toString()) { text = it } })
}
private fun compositionCases() {
    val d = FrameDispatcher(0, { 0 }); val c = HostFrameClock(d)
    val r = Recomposer(d.context + c)
    verify(4301, r.effectCoroutineContext[MonotonicFrameClock] !== c)
    val runner = d.scope.launch(c) { r.runRecomposeAndApplyChanges() }; pump(d)
    val root = TestNode(); val o = Observed(); val composition = Composition(TestApplier(root), r)
    try {
        composition.setContent { Counter(o) }; pump(d)
        val node = root.children.single(); val state = o.state; val token = o.token
        verify(4302, runner.isActive && node.text == "0" && o.starts == 1 && c.pending == 1)
        pulse(d, c, 16_000_000); verify(4303, o.frames == listOf(16_000_000L) && state.value == 1 && d.nextDeadlineNanos == 21_000_000L)
        pulse(d, c, 20_000_000); verify(4304, node.text == "1")
        d.advanceTo(21_000_000); pump(d); check(state.value == 2)
        pulse(d, c, 24_000_000); pulse(d, c, 28_000_000)
        verify(4305, node.text == "3" && root.children.single() === node && o.state === state && o.token === token && o.remembers == 1 && o.finishes == 1)
        val commits = o.commits; Snapshot.withMutableSnapshot { state.value = 3 }; pump(d); pulse(d, c, 30_000_000)
        verify(4306, o.commits == commits)
        Snapshot.withMutableSnapshot { o.mode.value = 1 }; pump(d); pulse(d, c, 32_000_000); check(d.pendingTimers == 1)
        Snapshot.withMutableSnapshot { o.visible.value = false }; pump(d); pulse(d, c, 34_000_000)
        verify(4308, root.children.isEmpty() && o.disposed == 0)
        Snapshot.withMutableSnapshot { o.mode.value = 2 }; pump(d); pulse(d, c, 36_000_000)
        verify(4307, o.starts == 3 && o.finishes == 2 && d.pendingTimers == 0)
        composition.dispose(); composition.dispose(); r.cancel(); runner.cancel(); pump(d)
        pulse(d, c, 40_000_000)
        verify(4309, o.disposed == 1 && o.finishes == 3 && c.pending == 0 && runner.isCompleted && d.pendingTimers == 0)
    } finally { composition.dispose(); r.cancel(); runner.cancel(); c.close(); d.close() }
    d.throwIfFailed()
    val errors = FrameDispatcher(0, { 0 }); val clock = HostFrameClock(errors); val recomposer = Recomposer(errors.context)
    val running = errors.scope.launch(clock) { recomposer.runRecomposeAndApplyChanges() }; pump(errors)
    val observed = Observed(); val content = Composition(TestApplier(TestNode()), recomposer)
    content.setContent { Counter(observed) }; pump(errors)
    Snapshot.withMutableSnapshot { observed.mode.value = 3 }; pump(errors)
    errors.advanceTo(1); clock.sendFrame(1); errors.drain(); Snapshot.sendApplyNotifications(); errors.drain()
    var captured = false
    try { errors.throwIfFailed() } catch (e: IllegalStateException) { captured = e.message == "real-effect-failure" }
    verify(4310, captured && observed.finishes >= 1)
    content.dispose(); recomposer.cancel(); running.cancel(); clock.close(); errors.close()
}
fun main() {
    try {
        timeCases(); boundsAndCloseCases(); clockCases(); compositionCases()
        check(log(4999, 1) == 0)
    } catch (failure: Throwable) {
        val message = failure.message ?: "unknown driver failure"
        check(log(4900, message.length) == 0)
        message.forEach { check(log(4901, it.code) == 0) }
        check(log(4999, 0) == 0)
    }
}
