package ming.compose

internal class MingPlacement(
    val node: MingNode, val x: Int, val y: Int, val width: Int, val height: Int, val baselineQ6: Int,
)

/** The first bridge supports integer vertical layout; it does not pretend to be Compose UI Layout. */
internal fun layoutMingTree(root: MingNode, width: Int, labels: Map<MingNode, MingLabel>): List<MingPlacement> {
    val result = mutableListOf<MingPlacement>()
    fun visit(node: MingNode, x: Int, y: Int, available: Int): Int {
        if (node.kind == NodeKind.Root || node.kind == NodeKind.Column) {
            val padding = if (node.kind == NodeKind.Root) 0 else node.padding
            val gap = if (node.kind == NodeKind.Root) 0 else node.gap
            var bottom = y + padding
            val childWidth = (available - 2 * padding).coerceAtLeast(1)
            node.children.forEachIndexed { index, child ->
                if (index > 0) bottom += gap
                bottom += visit(child, x + padding, bottom, childWidth)
            }
            return bottom - y + padding
        }
        val metrics = checkNotNull(labels[node]).metrics
        val lineHeight = ((metrics.lineHeightQ6 + 63) / 64).coerceAtLeast(1)
        val button = node.kind == NodeKind.Button
        val field = node.kind == NodeKind.TextField
        val height = when { button -> (lineHeight + 20).coerceAtLeast(40); field -> (lineHeight + 12).coerceAtLeast(32); else -> lineHeight }
        val baseline = y * 64 + metrics.ascentQ6 + if (button || field) (height * 64 - metrics.lineHeightQ6) / 2 else 0
        result.add(MingPlacement(node, x, y, available, height, baseline))
        return height
    }
    visit(root, 0, 0, width)
    return result
}
