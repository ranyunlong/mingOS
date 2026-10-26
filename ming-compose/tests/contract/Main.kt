@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
package ming.compose

import androidx.compose.runtime.Composable
import androidx.compose.runtime.ComposeNode
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.RememberObserver
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.key
import androidx.compose.runtime.movableContentOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshots.Snapshot
import kotlin.wasm.WasmImport

@WasmImport("ming_v1", "log")
private external fun event(code: Int, value: Int): Int

@Composable
private fun Leaf(label: String) {
    ComposeNode<MingNode, MingApplier>(
        factory = { MingNode(NodeKind.Text) },
        update = { set(label) { text = it } },
    )
}

@Composable
private fun Branch(content: @Composable () -> Unit) {
    ComposeNode<MingNode, MingApplier>(
        factory = { MingNode(NodeKind.Column) },
        update = {},
        content = content,
    )
}

private class Lifetime : RememberObserver {
    var remembered = 0
    var forgotten = 0
    var abandoned = 0
    override fun onRemembered() { remembered++ }
    override fun onForgotten() { forgotten++ }
    override fun onAbandoned() { abandoned++ }
}

private fun expectClosed(composition: MingComposition) {
    check(composition.isDisposed)
    check(!composition.hasSnapshotObserver)
    check(composition.root.children.isEmpty())
    val probe = mutableStateOf(0)
    Snapshot.notifyObjectsInitialized()
    probe.value = 1
    Snapshot.sendApplyNotifications()
    check(!composition.hasPendingSnapshotChanges) { "Disposed session still receives Snapshot notifications" }
    composition.dispose()
    check(runCatching { composition.setContent { Leaf("closed") } }.exceptionOrNull() is IllegalStateException)
    check(runCatching { composition.recompose() }.exceptionOrNull() is IllegalStateException)
}

private fun stateAndRemember() {
    val composition = MingComposition()
    check(!composition.isDisposed && composition.hasSnapshotObserver)
    check(composition.root.kind == NodeKind.Root)
    check(!composition.recompose())
    var state = mutableStateOf(-1)
    var remembered: Any? = null
    var initializations = 0
    var runs = 0
    var commits = 0
    composition.setContent {
        state = remember { mutableStateOf(0) }
        remembered = remember { initializations++; Any() }
        runs++
        Leaf("count=${state.value}")
        SideEffect { commits++ }
    }
    val node = composition.root.children.single()
    val token = remembered
    val stateIdentity = state
    check(node.text == "count=0" && node.kind == NodeKind.Text)
    check(initializations == 1 && commits == 1)
    event(201, 1)

    // Plain global writes are delivered by recompose's sendApplyNotifications.
    state.value = 1
    check(composition.recompose())
    check(composition.root.children.single() === node && node.text == "count=1")
    check(remembered === token && state === stateIdentity && initializations == 1 && commits == 2)
    event(202, 1)
    event(203, 1)
    val unchangedRuns = runs
    state.value = 1
    check(!composition.recompose())
    check(runs == unchangedRuns && commits == 2)
    check(!composition.recompose())
    event(204, 1)

    // Explicit mutable snapshots also notify automatically, without a caller changes set.
    Snapshot.withMutableSnapshot { state.value = 2 }
    check(composition.recompose() && node.text == "count=2")
    state.value = 3
    state.value = 4
    check(composition.recompose() && node.text == "count=4" && commits == 4)
    event(205, 1)
    val unrelated = mutableStateOf(0)
    unrelated.value = 1
    val beforeUnrelated = runs
    check(!composition.recompose() && runs == beforeUnrelated)
    event(206, 1)
    composition.dispose()
    expectClosed(composition)
    Snapshot.withMutableSnapshot { state.value = 99 }
    Snapshot.sendApplyNotifications()
    check(commits == 4 && node.text == "count=4")
    event(207, 1)
}

private fun keyedMovesAndRemoval() {
    val composition = MingComposition()
    val order = mutableStateOf(listOf("a", "b", "c"))
    val alive = mutableMapOf<String, Lifetime>()
    var disposals = 0
    composition.setContent {
        for (id in order.value) key(id) {
            remember { Lifetime().also { alive[id] = it } }
            DisposableEffect(Unit) { onDispose { disposals++ } }
            // A keyed group emits two nodes, so real Compose exercises block moves.
            Leaf("$id-0")
            Leaf("$id-1")
        }
    }
    val original = composition.root.children.toList()
    check(original.map { it.text } == listOf("a-0", "a-1", "b-0", "b-1", "c-0", "c-1"))
    order.value = listOf("c", "a", "b")
    check(composition.recompose())
    check(composition.root.children == listOf(original[4], original[5], original[0], original[1], original[2], original[3]))
    order.value = listOf("b", "c", "a")
    check(composition.recompose())
    check(composition.root.children == listOf(original[2], original[3], original[4], original[5], original[0], original[1]))
    check(alive.values.all { it.remembered == 1 && it.forgotten == 0 && it.abandoned == 0 })
    check(disposals == 0)
    event(208, 1)
    order.value = listOf("b", "a")
    check(composition.recompose())
    check(composition.root.children == listOf(original[2], original[3], original[0], original[1]))
    check(alive.getValue("c").forgotten == 1 && disposals == 1)
    event(209, 1)
    composition.dispose()
    expectClosed(composition)
    check(alive.values.all { it.remembered == 1 && it.forgotten == 1 && it.abandoned == 0 })
    check(disposals == 3)
    composition.dispose()
    check(disposals == 3)
    event(210, 1)
}

private fun replacementAndSessionIsolation() {
    val first = MingComposition()
    val second = MingComposition()
    val shared = mutableStateOf(0)
    val lifetime = Lifetime()
    var effectDisposals = 0
    first.setContent {
        remember { lifetime }
        DisposableEffect(Unit) { onDispose { effectDisposals++ } }
        Leaf("first=${shared.value}")
    }
    second.setContent { Leaf("second=${shared.value}") }
    shared.value = 1
    check(first.recompose() && second.recompose())
    check(first.root.children.single().text == "first=1")
    check(second.root.children.single().text == "second=1")
    first.setContent { Branch { Leaf("replacement=${shared.value}") } }
    check(lifetime.remembered == 1 && lifetime.forgotten == 1 && effectDisposals == 1)
    check(first.root.children.single().kind == NodeKind.Column)
    event(211, 1)
    first.dispose()
    expectClosed(first)
    shared.value = 2
    check(second.recompose() && second.root.children.single().text == "second=2")
    second.dispose()
    expectClosed(second)
    event(212, 1)
}

private fun writesDuringCompositionAndApply() {
    val composition = MingComposition()
    val source = mutableStateOf(0)
    val followUp = mutableStateOf(0)
    var commits = 0
    composition.setContent {
        val value = source.value
        Leaf("$value:${followUp.value}")
        SideEffect {
            commits++
            if (followUp.value != value) followUp.value = value
        }
    }
    source.value = 1
    check(composition.recompose())
    check(composition.root.children.single().text == "1:0")
    // Writes from applyChanges belong to the following synchronous pump.
    check(composition.recompose())
    check(composition.root.children.single().text == "1:1")
    check(!composition.recompose() && commits == 3)
    event(213, 1)
    composition.dispose()
    expectClosed(composition)

    val writing = MingComposition()
    val trigger = mutableStateOf(0)
    val written = mutableStateOf(-1)
    writing.setContent {
        val value = trigger.value
        written.value = value
        Leaf("${written.value}")
    }
    check(written.value == 0 && writing.root.children.single().text == "0")
    trigger.value = 1
    check(writing.recompose())
    check(written.value == 1 && writing.root.children.single().text == "1")
    writing.dispose()
    expectClosed(writing)
    event(214, 1)
}

private fun capacityAndSubtreeRemoval() {
    val composition = MingComposition()
    val nested = mutableStateOf(true)
    val ids = mutableStateOf((0 until 31).toList())
    composition.setContent {
        if (nested.value) {
            Branch { repeat(30) { Leaf("nested-$it") } }
        } else {
            for (id in ids.value) key(id) { Leaf("flat-$id") }
        }
    }
    check(composition.root.children.single().children.size == 30)
    event(215, 1)
    nested.value = false
    check(composition.recompose() && composition.root.children.size == 31)
    val nodes = composition.root.children.toList()
    ids.value = ids.value.reversed()
    check(composition.recompose() && composition.root.children == nodes.reversed())
    ids.value = (0 until 15).toList()
    check(composition.recompose() && composition.root.children.size == 15)
    ids.value = (0 until 31).toList()
    check(composition.recompose() && composition.root.children.size == 31)
    event(216, 1)
    ids.value = (0 until 32).toList()
    val error = runCatching { composition.recompose() }.exceptionOrNull()
    check(error is IllegalStateException && error.message?.contains("32") == true)
    expectClosed(composition)
    event(217, 1)
}

private fun failureCleanup() {
    val oversized = MingComposition()
    val lifetime = Lifetime()
    val error = runCatching {
        oversized.setContent {
            remember { lifetime }
            repeat(32) { Leaf("over-$it") }
        }
    }.exceptionOrNull()
    check(error is IllegalStateException && error.message?.contains("32") == true)
    expectClosed(oversized)
    check(lifetime.remembered + lifetime.abandoned == 1 && lifetime.forgotten == lifetime.remembered)
    event(218, 1)

    val broken = MingComposition()
    val abandoned = Lifetime()
    val deliberate = IllegalArgumentException("contract composition failure")
    val thrown = runCatching {
        broken.setContent {
            remember { abandoned }
            throw deliberate
        }
    }.exceptionOrNull()
    check(thrown === deliberate)
    expectClosed(broken)
    check(abandoned.abandoned == 1 && abandoned.remembered == 0 && abandoned.forgotten == 0)
    event(219, 1)

    val disposalFailure = MingComposition()
    val disposeError = IllegalArgumentException("contract dispose failure")
    disposalFailure.setContent {
        DisposableEffect(Unit) { onDispose { throw disposeError } }
        Leaf("cleanup")
    }
    check(runCatching { disposalFailure.dispose() }.exceptionOrNull() === disposeError)
    expectClosed(disposalFailure)
    val healthy = MingComposition()
    val state = mutableStateOf(0)
    healthy.setContent { Leaf("healthy=${state.value}") }
    state.value = 1
    check(healthy.recompose() && healthy.root.children.single().text == "healthy=1")
    healthy.dispose()
    expectClosed(healthy)
    event(220, 1)
}


private fun disjointReplacementAtCapacity() {
    val session = MingComposition()
    val ids = mutableStateOf((0 until 31).toList())
    session.setContent { for (id in ids.value) key(id) { Leaf("id=$id") } }
    val original = session.root.children.toList()
    check(original.size == 31)
    ids.value = (31 until 46).toList()
    val replacement = runCatching { session.recompose() }
    event(221, if (replacement.isSuccess) 1 else 0)
    check(replacement.getOrDefault(false)) {
        "CBR-001: legal disjoint shrink rejected: ${replacement.exceptionOrNull()?.message}"
    }
    check(!session.isDisposed && session.root.children.map { it.text } == (31 until 46).map { "id=$it" })
    check(session.root.children.none { it in original })

    ids.value = (100 until 131).toList()
    check(session.recompose() && session.root.children.size == 31)
    val full = session.root.children.toList()
    ids.value = (200 until 231).toList()
    check(session.recompose() && !session.isDisposed)
    check(session.root.children.map { it.text } == (200 until 231).map { "id=$it" })
    check(session.root.children.none { it in full })
    event(222, 1)

    // Initial-content replacement uses Recomposer.composeInitial, not the manual recompose path.
    session.setContent { for (id in 300 until 331) key(id) { Leaf("set=$id") } }
    check(!session.isDisposed && session.root.children.map { it.text } == (300 until 331).map { "set=$it" })
    session.dispose()
    expectClosed(session)
    event(223, 1)

    val nested = MingComposition()
    val generation = mutableStateOf(0)
    nested.setContent {
        key(generation.value) {
            Branch { repeat(30) { Leaf("${generation.value}:$it") } }
        }
    }
    val oldBranch = nested.root.children.single()
    check(oldBranch.children.size == 30)
    generation.value = 1
    check(nested.recompose() && !nested.isDisposed)
    val newBranch = nested.root.children.single()
    check(newBranch !== oldBranch && newBranch.children.map { it.text } == (0 until 30).map { "1:$it" })
    nested.dispose()
    expectClosed(nested)
    event(224, 1)

    val oversized = MingComposition()
    val failure = runCatching { oversized.setContent { repeat(64) { Leaf("transient-$it") } } }.exceptionOrNull()
    check(failure is IllegalStateException && failure.message?.contains("64") == true)
    expectClosed(oversized)
    event(225, 1)

    // Stage the actual Applier callbacks across two change batches: the removal happens only
    // in the second batch. This guards against incorrectly checking the committed quota in
    // onEndChanges, which can be called before Runtime applyLateChanges.
    val root = MingNode(NodeKind.Root)
    val applier = MingApplier(root)
    repeat(31) { applier.insertTopDown(it, MingNode(NodeKind.Text)) }
    applier.onBeginChanges()
    repeat(32) { applier.insertTopDown(31 + it, MingNode(NodeKind.Text)) }
    applier.onEndChanges()
    check(root.children.size == 63) // Root + 63 = bounded transient maximum 64.
    check(runCatching { applier.checkCommittedTree() }.exceptionOrNull() is IllegalStateException)
    applier.onBeginChanges()
    applier.remove(0, 32)
    applier.onEndChanges()
    check(root.children.size == 31)
    applier.checkCommittedTree()
    applier.clear()
    check(root.children.isEmpty())
    event(226, 1)
}


private fun initialLateChangesQuota() {
    val valid = MingComposition()
    var normalPhaseNodes = -1
    val movable = movableContentOf { repeat(31) { Leaf("late-$it") } }
    valid.setContent {
        movable()
        SideEffect { normalPhaseNodes = valid.root.children.size }
    }
    // Runtime applies initial movable-content insertions in its late change list. This SideEffect
    // runs after the normal list, so zero here and 31 after setContent prove both phases occurred.
    check(normalPhaseNodes == 0)
    check(!valid.isDisposed && valid.root.children.size == 31)
    valid.dispose()
    expectClosed(valid)

    val invalid = MingComposition()
    var normalPhaseOverflowNodes = -1
    val tooMany = movableContentOf { repeat(32) { Leaf("late-over-$it") } }
    val failure = runCatching {
        invalid.setContent {
            tooMany()
            SideEffect { normalPhaseOverflowNodes = invalid.root.children.size }
        }
    }.exceptionOrNull()
    check(normalPhaseOverflowNodes == 0)
    check(failure is IllegalStateException && failure.message?.contains("32") == true)
    expectClosed(invalid)
    event(227, 1)
}

fun main() {
    event(200, 1) // Entry trace separates pre-main startup/JIT from contract execution.
    stateAndRemember()
    keyedMovesAndRemoval()
    replacementAndSessionIsolation()
    writesDuringCompositionAndApply()
    capacityAndSubtreeRemoval()
    failureCleanup()
    disjointReplacementAtCapacity()
    initialLateChangesQuota()
    event(299, 1)
}
