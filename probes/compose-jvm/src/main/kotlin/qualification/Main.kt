package qualification

import androidx.compose.runtime.AbstractApplier
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ComposeNode
import androidx.compose.runtime.ControlledComposition
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.Recomposer
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshots.Snapshot
import kotlin.coroutines.EmptyCoroutineContext

private class Node(val kind: String, var text: String = "") {
    val children = mutableListOf<Node>()
}

private class TreeApplier(root: Node) : AbstractApplier<Node>(root) {
    var removals = 0
        private set
    var clears = 0
        private set
    override fun insertTopDown(index: Int, instance: Node) { current.children.add(index, instance) }
    override fun insertBottomUp(index: Int, instance: Node) { }
    override fun remove(index: Int, count: Int) {
        current.children.remove(index, count)
        removals += count
    }
    override fun move(from: Int, to: Int, count: Int) { current.children.move(from, to, count) }
    override fun onClear() { root.children.clear(); clears++ }
}

private class Observations {
    lateinit var counter: MutableState<Int>
    lateinit var visible: MutableState<Boolean>
    var remembered: Any? = null
    var rememberInitializations = 0
    var compositions = 0
    var committed = 0
    var parentEnters = 0
    var parentDisposals = 0
    var childEnters = 0
    var childDisposals = 0
}

@Composable
private fun Counter(observations: Observations) {
    val counter = remember { mutableStateOf(0) }
    val visible = remember { mutableStateOf(true) }
    val token = remember { observations.rememberInitializations++; Any() }
    observations.counter = counter
    observations.visible = visible
    observations.remembered = token
    observations.compositions++
    SideEffect { observations.committed++ }
    DisposableEffect(Unit) {
        observations.parentEnters++
        onDispose { observations.parentDisposals++ }
    }
    ComposeNode<Node, TreeApplier>(
        factory = { Node("counter") },
        update = { set("count=" + counter.value) { text = it } },
    )
    if (visible.value) {
        DisposableEffect(Unit) {
            observations.childEnters++
            onDispose { observations.childDisposals++ }
        }
        ComposeNode<Node, TreeApplier>(
            factory = { Node("detail") },
            update = { set("removable") { text = it } },
        )
    }
}

private fun recompose(composition: ControlledComposition, modified: Set<Any>) {
    composition.recordModificationsOf(modified)
    val snapshot = Snapshot.takeMutableSnapshot(
        readObserver = { composition.recordReadOf(it) },
        writeObserver = { composition.recordWriteOf(it) },
    )
    val changed = try {
        val result = snapshot.enter { composition.recompose() }
        snapshot.apply().check()
        result
    } finally {
        snapshot.dispose()
    }
    check(changed) { "Invalidated composition produced no changes" }
    composition.applyChanges()
    composition.applyLateChanges()
    composition.changesApplied()
}

fun main() {
    val root = Node("root")
    val observations = Observations()
    val applier = TreeApplier(root)
    val recomposer = Recomposer(EmptyCoroutineContext)
    val composition = ControlledComposition(applier, recomposer)
    var initial = false
    var rememberedStateChanged = false
    var nodeRemoval = false
    var disposal = false
    try {
        composition.setContent { Counter(observations) }
        check(root.children.map { it.kind } == listOf("counter", "detail"))
        check(root.children[0].text == "count=0")
        check(observations.rememberInitializations == 1)
        check(observations.parentEnters == 1 && observations.childEnters == 1)
        check(observations.committed == 1)
        initial = true
        println("COMPOSE_PHASE {\"phase\":\"initial\",\"counter\":0,\"nodes\":2}")
        val token = observations.remembered
        val counter = observations.counter
        val visible = observations.visible
        val counterNode = root.children[0]
        val detailNode = root.children[1]
        val initialCompositions = observations.compositions

        Snapshot.withMutableSnapshot { counter.value = 1 }
        recompose(composition, setOf(counter))
        check(root.children[0] === counterNode && root.children[1] === detailNode)
        check(root.children[0].text == "count=1")
        check(observations.counter === counter && observations.visible === visible)
        check(observations.remembered === token && observations.rememberInitializations == 1)
        check(observations.compositions > initialCompositions && observations.committed == 2)
        check(observations.parentDisposals == 0 && observations.childDisposals == 0)
        rememberedStateChanged = true
        println("COMPOSE_PHASE {\"phase\":\"state-recomposition\",\"counter\":1,\"sameNode\":true,\"sameRememberedToken\":true}")

        Snapshot.withMutableSnapshot { visible.value = false }
        recompose(composition, setOf(visible))
        check(root.children.size == 1 && root.children.single() === counterNode)
        check(applier.removals == 1)
        check(observations.childDisposals == 1 && observations.parentDisposals == 0)
        check(observations.rememberInitializations == 1 && observations.committed == 3)
        nodeRemoval = true
        println("COMPOSE_PHASE {\"phase\":\"node-removal\",\"nodes\":1,\"childDisposals\":1}")
    } finally {
        composition.dispose()
        recomposer.cancel()
    }
    check(composition.isDisposed)
    check(root.children.isEmpty() && applier.clears > 0)
    val clearsAfterDispose = applier.clears
    check(observations.parentDisposals == 1 && observations.childDisposals == 1)
    composition.dispose()
    check(applier.clears == clearsAfterDispose && observations.parentDisposals == 1 && observations.childDisposals == 1)
    disposal = true
    println("COMPOSE_PHASE {\"phase\":\"composition-dispose\",\"nodes\":0,\"parentDisposals\":1,\"childDisposals\":1,\"secondDisposeIdempotent\":true}")
    println("COMPOSE_APPLIER {\"clearCallsAfterDispose\":" + clearsAfterDispose + "}")
    val runtimeVersion = Recomposer::class.java.protectionDomain.codeSource.location.toString()
    val applicationJar = Node::class.java.protectionDomain.codeSource.location.toString()
    println("COMPOSE_ORIGIN application=" + applicationJar + " runtime=" + runtimeVersion)
    check(initial && rememberedStateChanged && nodeRemoval && disposal)
    println("COMPOSE_JVM_RESULT {\"schemaVersion\":1,\"compile\":\"passed\",\"run\":\"passed\",\"initial\":true,\"rememberMutableStateRecomposition\":true,\"nodeRemoval\":true,\"compositionDispose\":true,\"disposableEffectParentDisposals\":" + observations.parentDisposals + ",\"disposableEffectChildDisposals\":" + observations.childDisposals + ",\"rememberInitializations\":" + observations.rememberInitializations + ",\"committedCompositions\":" + observations.committed + ",\"gui\":false,\"sdkOnlyIsolationQualified\":false,\"performanceQualified\":false}")
}
