package ming.compose

import androidx.compose.runtime.AbstractApplier

internal enum class NodeKind { Root, Column, Text, Button, TextField }

internal class MingNode(val kind: NodeKind) {
    val children: MutableList<MingNode> = mutableListOf()
    var text: String = ""
    var fieldState: TextFieldState? = null
    var fieldValue: ming.sdk.TextEditState? = null
    var fieldFocused: Boolean = false
    var onClick: (() -> Unit)? = null
    var emPx: Int = 18
    var color: Int = 0xff203b37.toInt()
    var gap: Int = 12
    var padding: Int = 0
}

/** Only the applier mutates the tree; SDK resource ownership belongs to the renderer. */
internal class MingApplier(root: MingNode) : AbstractApplier<MingNode>(root) {
    // Count includes root. Composer may insert the replacement before deleting the old tree,
    // including across applyChanges/applyLateChanges, so the transient bound is separate.
    private var nodeCount = 1

    override fun insertTopDown(index: Int, instance: MingNode) {
        val inserted = instance.subtreeSize()
        check(nodeCount + inserted <= 64) { "MingComposition exceeds 64 transient nodes including root" }
        current.children.add(index, instance)
        nodeCount += inserted
    }

    // Called only after the whole composition turn, never from onEndChanges: late changes
    // may still remove old nodes after a normal change batch has ended.
    fun checkCommittedTree() {
        check(nodeCount <= 32) { "MingComposition exceeds 32 nodes including root" }
    }

    override fun insertBottomUp(index: Int, instance: MingNode) = Unit

    override fun remove(index: Int, count: Int) {
        val children = current.children
        var removed = 0
        for (i in index until index + count) removed += children[i].subtreeSize()
        children.remove(index, count)
        nodeCount -= removed
    }

    override fun move(from: Int, to: Int, count: Int) {
        current.children.move(from, to, count)
    }

    override fun onClear() {
        root.children.clear()
        nodeCount = 1
    }

    private fun MingNode.subtreeSize(): Int = 1 + children.sumOf { it.subtreeSize() }
}
