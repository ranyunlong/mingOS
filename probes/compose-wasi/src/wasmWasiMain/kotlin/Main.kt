import androidx.compose.runtime.AbstractApplier
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ComposeNode
import androidx.compose.runtime.ControlledComposition
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.Recomposer
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.snapshots.Snapshot
import kotlin.coroutines.EmptyCoroutineContext

// Qualification candidate only: WASI dependency resolution currently prevents compilation.
// No substitute annotation/runtime, browser target, or production MingSDK integration.
private class Node(var text: String = "") {
    val children = mutableListOf<Node>()
}

private class TreeApplier(root: Node) : AbstractApplier<Node>(root) {
    override fun insertTopDown(index: Int, instance: Node) { current.children.add(index, instance) }
    override fun insertBottomUp(index: Int, instance: Node) { }
    override fun remove(index: Int, count: Int) { current.children.remove(index, count) }
    override fun move(from: Int, to: Int, count: Int) { current.children.move(from, to, count) }
    override fun onClear() { root.children.clear() }
}

private class Observations {
    lateinit var counter: MutableState<Int>
    var remembered: Any? = null
    var compositions = 0
    var disposed = 0
}

@Composable
private fun Counter(observations: Observations) {
    val counter = remember { mutableStateOf(0) }
    val token = remember { Any() }
    observations.counter = counter
    observations.remembered = token
    observations.compositions++
    DisposableEffect(Unit) { onDispose { observations.disposed++ } }
    ComposeNode<Node, TreeApplier>(
        factory = { Node() },
        update = { set("count=" + counter.value) { text = it } },
    )
}

fun main() {
    val root = Node()
    val observations = Observations()
    val recomposer = Recomposer(EmptyCoroutineContext)
    val composition = ControlledComposition(TreeApplier(root), recomposer)
    try {
        composition.setContent { Counter(observations) }
        check(root.children.single().text == "count=0")
        val remembered = observations.remembered
        val counter = observations.counter
        val initialNode = root.children.single()
        val initialCompositions = observations.compositions

        Snapshot.withMutableSnapshot { counter.value = 1 }
        // Synchronous qualification drives the official ControlledComposition API directly.
        // Scheduler/event-loop qualification remains a separate future port requirement.
        composition.recordModificationsOf(setOf(counter))
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
        check(changed)
        composition.applyChanges()
        composition.applyLateChanges()
        composition.changesApplied()
        check(root.children.single().text == "count=1")
        check(root.children.single() === initialNode)
        check(observations.counter === counter)
        check(observations.remembered === remembered)
        check(observations.compositions > initialCompositions)
        check(observations.disposed == 0)
    } finally {
        composition.dispose()
        recomposer.cancel()
    }
    check(root.children.isEmpty())
    check(observations.disposed == 1)
    // Deliberately no println/extra host imports. Success is normal program return.
}
