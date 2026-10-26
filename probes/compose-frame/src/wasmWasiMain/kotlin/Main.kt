@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlinx.coroutines.InternalCoroutinesApi::class)
package frameprobe

import androidx.compose.runtime.*
import androidx.compose.runtime.snapshots.Snapshot
import kotlinx.coroutines.*
import ming.compose.port.WasiDiagnostics
import kotlin.coroutines.EmptyCoroutineContext
import kotlin.wasm.WasmImport

@WasmImport("ming_v1", "log") private external fun log(code: Int, value: Int): Int
private var failed = 0
private var passed = 0
private fun event(code: Int, value: Int) { check(log(code, value) == 0) }
private fun case(id: Int, ok: Boolean, detail: String = ""): Boolean {
    event(id, if (ok) 1 else 0)
    if (ok) passed++ else { failed++; WasiDiagnostics.writeLine("case=" + id + " " + detail) }
    return ok
}
private fun pump(loop: ManualLoop) { repeat(4) { loop.drain(); Snapshot.sendApplyNotifications() }; loop.drain() }
private fun frame(loop: ManualLoop, clock: ManualClock, nanos: Long) { loop.advanceTo(nanos); check(clock.sendFrame(nanos)); pump(loop) }

private fun schedulerCases() {
    val loop = ManualLoop()
    val marks = mutableListOf<Int>()
    val job = loop.scope.launch { marks.add(1); delay(10); marks.add(2) }
    loop.drain(); check(marks == listOf(1) && loop.pendingTimers == 1)
    loop.advanceTo(9_000_000); check(marks == listOf(1))
    loop.advanceTo(10_000_000); case(101, marks == listOf(1,2) && job.isCompleted && loop.pendingTimers == 0)
    case(102, !loop.advanceTo(10_000_000) && marks.size == 2)
    var cleanup = 0; var late = 0
    val cancelled = loop.scope.launch { try { delay(100); late++ } finally { cleanup++ } }
    loop.drain(); check(loop.pendingTimers == 1); cancelled.cancel(); loop.drain()
    loop.advanceTo(200_000_000); case(103, cancelled.isCancelled && cleanup == 1 && late == 0 && loop.pendingTimers == 0)
    var timedOut = false
    val timeout = loop.scope.launch { try { withTimeout(5) { delay(50) } } catch (_: TimeoutCancellationException) { timedOut = true } }
    loop.drain(); loop.advanceTo(205_000_000); case(104, timedOut && timeout.isCompleted && loop.pendingTimers == 0)
    var zero = 0; loop.scope.launch { delay(0); zero++ }; loop.drain(); case(105, zero == 1 && loop.pendingTimers == 0)
    var rejectedBackwards = false; try { loop.advanceTo(1) } catch (_: IllegalArgumentException) { rejectedBackwards = true }; check(rejectedBackwards)
    loop.close(); case(109, loop.pendingTasks == 0 && loop.pendingTimers == 0 && loop.rootJob.isCompleted)

    val bounded = ManualLoop(maxQueue = 4, maxTimers = 2)
    var ran = 0
    repeat(4) { bounded.dispatch(EmptyCoroutineContext, Runnable { ran++ }) }
    var overflow = false; try { bounded.dispatch(EmptyCoroutineContext, Runnable { ran++ }) } catch (_: QueueCapacity) { overflow = true }
    bounded.drain(); case(106, overflow && ran == 4)
    val pending = List(3) { bounded.scope.launch { delay(100) } }; bounded.drain()
    case(107, bounded.pendingTimers == 2 && bounded.errors.size == 1 && pending.last().isCancelled)
    bounded.dispatch(EmptyCoroutineContext, Runnable { error("expected-direct-error") })
    bounded.dispatch(EmptyCoroutineContext, Runnable { ran++ }); bounded.drain()
    case(108, bounded.errors.size == 2 && ran == 5)
    bounded.close(); check(bounded.pendingTimers == 0 && bounded.pendingTasks == 0)
}

private fun frameCases() {
    val loop = ManualLoop(); val clock = ManualClock(loop)
    val seen = mutableListOf<Long>()
    // An actual coroutine context element supplies the clock.
    val actual = loop.scope.launch(clock) { seen.add(withFrameNanos { it }); seen.add(withFrameNanos { it }) }
    loop.drain(); check(clock.pending == 1)
    loop.advanceTo(10); check(clock.sendFrame(10)); loop.drain(); case(201, seen == listOf(10L) && clock.pending == 1)
    case(202, !clock.sendFrame(10) && seen == listOf(10L) && clock.pending == 1)
    var backward = false; try { clock.sendFrame(9) } catch (_: IllegalArgumentException) { backward = true }; case(203, backward && clock.pending == 1)
    loop.advanceTo(20); check(clock.sendFrame(20)); loop.drain(); case(204, seen == listOf(10L,20L) && actual.isCompleted)
    var called = 0; val cancel = loop.scope.launch(clock) { withFrameNanos { called++ } }
    loop.drain(); cancel.cancel(); loop.drain(); loop.advanceTo(30); check(clock.sendFrame(30)); loop.drain(); case(205, called == 0 && clock.pending == 0)
    val capped = ManualClock(loop, maxWaiters = 2)
    val waiters = List(3) { loop.scope.launch(capped) { withFrameNanos { } } }; loop.drain()
    case(206, capped.pending == 2 && waiters.last().isCancelled && loop.errors.size == 1)
    capped.close(); loop.drain(); check(capped.pending == 0)
    var finallyClosed = 0
    loop.scope.launch(clock) { try { withFrameNanos { called++ } } finally { finallyClosed++ } }; loop.drain()
    clock.close(); loop.drain(); var closeRejected = false; try { clock.sendFrame(40) } catch (_: ClockClosed) { closeRejected = true }
    case(207, finallyClosed == 1 && closeRejected && called == 0 && clock.pending == 0)
    loop.close()
}

private class Node(var text: String = "") { val children = mutableListOf<Node>() }
private class TreeApplier(root: Node) : AbstractApplier<Node>(root) {
    override fun insertTopDown(index: Int, instance: Node) { current.children.add(index, instance) }
    override fun insertBottomUp(index: Int, instance: Node) { }
    override fun remove(index: Int, count: Int) { current.children.remove(index, count) }
    override fun move(from: Int, to: Int, count: Int) { current.children.move(from,to,count) }
    override fun onClear() { root.children.clear() }
}
private class Observation {
    lateinit var state: MutableState<Int>
    lateinit var mode: MutableState<Int>
    var token: Any? = null; var remembered = 0; var commits = 0; var starts = 0; var finishes = 0; var cancelledDelay = 0; var cancelledFrame = 0
    val effectFrames = mutableListOf<Long>()
}
@Composable private fun Counter(obs: Observation) {
    val state = remember { mutableStateOf(0) }; val mode = remember { mutableStateOf(0) }; val token = remember { obs.remembered++; Any() }
    obs.state = state; obs.mode = mode; obs.token = token
    SideEffect { obs.commits++ }
    LaunchedEffect(mode.value) {
        obs.starts++
        try {
            when (mode.value) {
                0 -> { withFrameNanos { obs.effectFrames.add(it) }; state.value = 1; delay(5); state.value = 2; withFrameNanos { obs.effectFrames.add(it) }; state.value = 3 }
                1 -> try { delay(100); error("cancelled delay resumed") } finally { obs.cancelledDelay++ }
                2 -> try { withFrameNanos { error("cancelled frame callback ran") } } finally { obs.cancelledFrame++ }
                else -> error("expected-effect-error")
            }
        } finally { obs.finishes++ }
    }
    ComposeNode<Node,TreeApplier>(factory = { Node() }, update = { set(state.value.toString()) { text = it } })
}
private fun composeCases() {
    val loop = ManualLoop(); val clock = ManualClock(loop)
    val recomposer = Recomposer(loop.context + clock)
    case(301, recomposer.effectCoroutineContext[MonotonicFrameClock] !== clock)
    val runner = loop.scope.launch(if (OMIT_PARENT_CLOCK) EmptyCoroutineContext else clock) { recomposer.runRecomposeAndApplyChanges() }
    pump(loop)
    if (!case(302, loop.errors.isEmpty() && runner.isActive, loop.errors.joinToString { it.message ?: "unknown" })) {
        check(OMIT_PARENT_CLOCK && loop.errors.any { it.message?.contains("MonotonicFrameClock") == true })
        recomposer.cancel(); runner.cancel(); clock.close(); loop.close(); return
    }
    val root = Node(); val obs = Observation(); val composition = Composition(TreeApplier(root),recomposer)
    try {
        composition.setContent { Counter(obs) }; pump(loop)
        val node = root.children.single(); val state = obs.state; val token = obs.token
        case(303, node.text == "0" && obs.starts == 1 && obs.effectFrames.isEmpty() && clock.pending == 1)
        frame(loop,clock,16_000_000); case(304, obs.effectFrames == listOf(16_000_000L) && state.value == 1 && loop.pendingTimers == 1)
        frame(loop,clock,20_000_000); case(305, node.text == "1" && state.value == 1)
        loop.advanceTo(21_000_000); pump(loop); check(state.value == 2)
        frame(loop,clock,24_000_000); frame(loop,clock,28_000_000)
        case(306, node.text == "3" && root.children.single() === node && obs.state === state && obs.token === token && obs.remembered == 1 && obs.finishes == 1)
        val commits = obs.commits; Snapshot.withMutableSnapshot { state.value = 3 }; pump(loop)
        loop.advanceTo(30_000_000); check(clock.sendFrame(30_000_000)); pump(loop); case(307, obs.commits == commits)
        Snapshot.withMutableSnapshot { obs.mode.value = 1 }; pump(loop); frame(loop,clock,32_000_000); check(loop.pendingTimers == 1)
        Snapshot.withMutableSnapshot { obs.mode.value = 2 }; pump(loop); frame(loop,clock,36_000_000)
        case(308, obs.cancelledDelay == 1 && loop.pendingTimers == 0 && obs.starts == 3)
        composition.dispose(); recomposer.cancel(); runner.cancel(); pump(loop)
        case(309, obs.cancelledFrame == 1 && obs.finishes == 3 && clock.pending == 0 && loop.pendingTimers == 0)
        composition.dispose(); case(310, root.children.isEmpty() && obs.finishes == 3 && runner.isCompleted)
    } finally { composition.dispose(); recomposer.cancel(); runner.cancel(); clock.close(); loop.close() }
    check(loop.errors.isEmpty())
}
private fun closeAndErrorCases() {
    val loop = ManualLoop(); val clock = ManualClock(loop); var cleaned = 0; var late = 0
    loop.scope.launch { try { delay(100) ; late++ } finally { cleaned++ } }
    loop.scope.launch(clock) { try { withFrameNanos { late++ } } finally { cleaned++ } }
    loop.drain(); loop.close(); case(312, cleaned == 2 && late == 0 && loop.pendingTimers == 0 && loop.pendingTasks == 0 && clock.pending == 0)
    val after = loop.scope.launch { late++ }; loop.drain(); check(after.isCancelled && late == 0)
    var dispatchRejected = false; try { loop.dispatch(EmptyCoroutineContext,Runnable { late++ }) } catch (_: LoopClosed) { dispatchRejected = true }; check(dispatchRejected)
    clock.close()
    val errors = ManualLoop(); val parent = ManualClock(errors); val recomposer = Recomposer(errors.context)
    val runner = errors.scope.launch(parent) { recomposer.runRecomposeAndApplyChanges() }; pump(errors)
    val root = Node(); val obs = Observation(); val composition = Composition(TreeApplier(root),recomposer)
    composition.setContent { obs.mode = remember { mutableStateOf(3) }; LaunchedEffect(Unit) { error("expected-effect-error") } }
    pump(errors)
    case(311, errors.errors.isNotEmpty() && errors.errors.all { it.message?.contains("expected-effect-error") == true })
    composition.dispose(); recomposer.cancel(); runner.cancel(); parent.close(); errors.close()
    check(errors.pendingTasks == 0 && errors.pendingTimers == 0)
}
fun main() {
    schedulerCases(); frameCases(); composeCases(); if (!OMIT_PARENT_CLOCK) closeAndErrorCases()
    event(990, passed); event(991, failed); event(999, if (failed == 0) 1 else 0)
}
