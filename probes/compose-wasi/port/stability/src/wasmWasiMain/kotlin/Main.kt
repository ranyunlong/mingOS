@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
package stability

import androidx.compose.runtime.*
import androidx.compose.runtime.snapshots.Snapshot
import kotlin.coroutines.EmptyCoroutineContext
import kotlin.wasm.WasmImport

@WasmImport("ming_v1", "log") private external fun log(code: Int, value: Int): Int
private fun event(code: Int, value: Int) { check(log(code, value) == 0) }
private const val LIFETIMES = 16
private const val UPDATES = 64

private class Node(val kind: String, var text: String = "") { val children = mutableListOf<Node>() }
private class TreeApplier(root: Node) : AbstractApplier<Node>(root) {
    var removed = 0
    var moved = 0
    var clears = 0
    override fun insertTopDown(index: Int, instance: Node) { current.children.add(index, instance) }
    override fun insertBottomUp(index: Int, instance: Node) { }
    override fun remove(index: Int, count: Int) { current.children.remove(index, count); removed += count }
    override fun move(from: Int, to: Int, count: Int) { current.children.move(from, to, count); moved++ }
    override fun onClear() { root.children.clear(); clears++ }
}
private class Token(val kind: String) : RememberObserver {
    var remembered = 0
    var forgotten = 0
    var abandoned = 0
    override fun onRemembered() { remembered++ }
    override fun onForgotten() { forgotten++ }
    override fun onAbandoned() { abandoned++ }
}
private class Observations {
    lateinit var count: MutableState<Int>
    lateinit var visible: MutableState<Boolean>
    lateinit var order: MutableState<List<Int>>
    lateinit var parentToken: Token
    lateinit var detailToken: Token
    val itemTokens = mutableMapOf<Int, Token>()
    val tokens = mutableListOf<Token>()
    var committed = 0
    var parentEnters = 0
    var parentDisposals = 0
    var detailEnters = 0
    var detailDisposals = 0
    var itemEnters = 0
    var itemDisposals = 0
    fun token(kind: String): Token = Token(kind).also { tokens.add(it) }
}

@Composable private fun Counter(obs: Observations) {
    val count = remember { mutableStateOf(0) }
    val visible = remember { mutableStateOf(true) }
    val order = remember { mutableStateOf(listOf(1, 2, 3)) }
    obs.count = count; obs.visible = visible; obs.order = order
    obs.parentToken = remember { obs.token("parent") }
    SideEffect { obs.committed++ }
    DisposableEffect(Unit) { obs.parentEnters++; onDispose { obs.parentDisposals++ } }
    ComposeNode<Node, TreeApplier>(factory = { Node("counter") }, update = { set(count.value.toString()) { text = it } })
    if (visible.value) {
        obs.detailToken = remember { obs.token("detail") }
        DisposableEffect(Unit) { obs.detailEnters++; onDispose { obs.detailDisposals++ } }
        ComposeNode<Node, TreeApplier>(factory = { Node("detail") }, update = { set("visible") { text = it } })
    }
    for (id in order.value) key(id) {
        obs.itemTokens[id] = remember { obs.token("item-" + id) }
        DisposableEffect(id) { obs.itemEnters++; onDispose { obs.itemDisposals++ } }
        ComposeNode<Node, TreeApplier>(factory = { Node("item-" + id) }, update = { set("item-" + id + ":" + count.value) { text = it } })
    }
}

private fun recompose(composition: ControlledComposition, modified: Set<Any>, expected: Boolean = true) {
    composition.recordModificationsOf(modified)
    val snapshot = Snapshot.takeMutableSnapshot(readObserver = { composition.recordReadOf(it) }, writeObserver = { composition.recordWriteOf(it) })
    val changed = try { snapshot.enter { composition.recompose() }.also { snapshot.apply().check() } } finally { snapshot.dispose() }
    check(changed == expected)
    if (changed) { composition.applyChanges(); composition.applyLateChanges(); composition.changesApplied() }
}
private fun assertTree(root: Node, obs: Observations, counter: Node, itemNodes: Map<Int, Node>, itemTokens: Map<Int, Token>, parentToken: Token) {
    val expected = mutableListOf("counter")
    if (obs.visible.value) expected.add("detail")
    expected.addAll(obs.order.value.map { "item-" + it })
    check(root.children.map { it.kind } == expected)
    check(root.children.first() === counter && counter.text == obs.count.value.toString())
    check(obs.parentToken === parentToken && parentToken.remembered == 1 && parentToken.forgotten == 0)
    for (id in 1..3) {
        val node = root.children.single { it.kind == "item-" + id }
        check(node === itemNodes[id] && node.text == "item-" + id + ":" + obs.count.value)
        check(obs.itemTokens[id] === itemTokens[id])
        check(obs.itemTokens[id]!!.remembered == 1 && obs.itemTokens[id]!!.forgotten == 0)
    }
}

fun main() {
    var updates = 0; var removals = 0; var remounts = 0; var reorders = 0
    var commits = 0; var disposedParents = 0; var disposedDetails = 0; var disposedItems = 0
    var forgottenTokens = 0; var moves = 0; var noChangePasses = 0; var checksum = 0
    repeat(LIFETIMES) { lifetime ->
        val root = Node("root"); val obs = Observations(); val applier = TreeApplier(root)
        val recomposer = Recomposer(EmptyCoroutineContext)
        val composition = ControlledComposition(applier, recomposer)
        try {
            composition.setContent { Counter(obs) }
            val counter = root.children.first(); val parentToken = obs.parentToken
            val itemNodes = (1..3).associateWith { id -> root.children.single { it.kind == "item-" + id } }
            val itemTokens = obs.itemTokens.toMap()
            val count = obs.count; val visible = obs.visible; val order = obs.order
            assertTree(root, obs, counter, itemNodes, itemTokens, parentToken)
            for (step in 1..UPDATES) {
                Snapshot.withMutableSnapshot { count.value = step }
                recompose(composition, setOf(count)); updates++; checksum += count.value
                check(obs.count === count && obs.visible === visible && obs.order === order)
                if (step % 4 == 0) {
                    val oldDetail = obs.detailToken
                    Snapshot.withMutableSnapshot { visible.value = !visible.value }
                    recompose(composition, setOf(visible))
                    if (visible.value) { remounts++; check(obs.detailToken !== oldDetail && obs.detailToken.remembered == 1) }
                    else { removals++; check(oldDetail.forgotten == 1) }
                }
                if (step % 8 == 0) {
                    Snapshot.withMutableSnapshot { order.value = order.value.reversed() }
                    recompose(composition, setOf(order)); reorders++
                }
                assertTree(root, obs, counter, itemNodes, itemTokens, parentToken)
                check(obs.parentDisposals == 0 && obs.itemDisposals == 0)
            }
            val beforeNoChange = obs.committed
            Snapshot.withMutableSnapshot { count.value = count.value }
            recompose(composition, emptySet(), expected = false)
            check(obs.committed == beforeNoChange); noChangePasses++
            check(obs.committed == 89 && obs.parentEnters == 1 && obs.detailEnters == 9 && obs.detailDisposals == 8 && obs.itemEnters == 3)
        } finally { composition.dispose(); recomposer.cancel() }
        check(composition.isDisposed && root.children.isEmpty())
        check(obs.parentDisposals == 1 && obs.detailDisposals == 9 && obs.itemDisposals == 3)
        check(obs.tokens.size == 13)
        check(obs.tokens.all { it.remembered == 1 && it.forgotten == 1 && it.abandoned == 0 })
        val clears = applier.clears
        repeat(3) { composition.dispose() }
        check(applier.clears == clears && obs.parentDisposals == 1 && obs.detailDisposals == 9 && obs.itemDisposals == 3)
        Snapshot.withMutableSnapshot { obs.count.value = -1 }
        check(root.children.isEmpty() && obs.parentDisposals == 1 && obs.detailDisposals == 9)
        check(applier.removed == 8 && applier.moved > 0)
        commits += obs.committed; disposedParents += obs.parentDisposals; disposedDetails += obs.detailDisposals; disposedItems += obs.itemDisposals
        forgottenTokens += obs.tokens.sumOf { it.forgotten }; moves += applier.moved
        event(600, lifetime + 1)
    }
    check(updates == 1024 && removals == 128 && remounts == 128 && reorders == 128)
    check(commits == 1424 && disposedParents == 16 && disposedDetails == 144 && disposedItems == 48 && forgottenTokens == 208)
    check(noChangePasses == 16 && checksum == 33280)
    event(601, updates); event(602, removals); event(603, remounts); event(604, reorders)
    event(605, commits); event(606, disposedParents); event(607, disposedDetails); event(608, disposedItems)
    event(609, forgottenTokens); event(610, moves); event(611, noChangePasses); event(612, checksum)
    event(699, 1)
}
