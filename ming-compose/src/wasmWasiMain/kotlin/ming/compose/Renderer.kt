package ming.compose

import androidx.compose.runtime.snapshots.Snapshot
import ming.sdk.MingFrame4
import ming.sdk.MingShadows
import ming.sdk.MingText as SdkText
import ming.sdk.ShadowStyle
import ming.sdk.TextLayout
import ming.sdk.TextMetrics
import ming.sdk.TextEditState
import ming.sdk.TextEditDisplay
import kotlin.math.abs

internal class MingLabel(val text: String, val emPx: Int, val layout: TextLayout, val metrics: TextMetrics, val missing: Boolean = false)
private class CapabilityFailure(val code: Int) : RuntimeException()
private class HitTarget(val x0: Int, val y0: Int, val x1: Int, val y1: Int, val action: () -> Unit)
internal data class FieldRect(val x: Int, val y: Int, val width: Int, val height: Int)
private class FieldPaint(
    val state: TextFieldState, val value: TextEditState, val display: TextEditDisplay,
    val label: MingLabel, val prefixes: Map<Int, MingLabel>, val placeholder: Boolean,
) {
    val selectionStart: Int get() = if (display.cursorStart >= 0) display.cursorStart else display.selectionStart
    val selectionEnd: Int get() = if (display.cursorEnd >= 0) display.cursorEnd else display.selectionEnd
    fun width(index: Int): Int = if (index == 0 || placeholder) 0 else checkNotNull(prefixes[index]).metrics.advanceQ6
}
private class FieldTarget(val paint: FieldPaint, val rect: FieldRect, val content: FieldRect, val scroll: Int) {
    val measured = mutableMapOf<Int, Int>()
}

/** Owns SDK handles. A failed frame preserves all previously published hit/caret geometry. */
internal class MingRenderer {
    private var labels: Map<MingNode, MingLabel> = emptyMap()
    private var fields: Map<MingNode, FieldPaint> = emptyMap()
    private var shadow: ShadowStyle? = null
    private var targets: List<HitTarget> = emptyList()
    private var fieldTargets: List<FieldTarget> = emptyList()
    private var drag: FieldTarget? = null
    private var disposed = false
    private var cleanupFailure = 0
    var focusedState: TextFieldState? = null
        private set
    var focusVersion: Long = 0
        private set
    var publishedCaret: FieldRect? = null
        private set
    var publishedFocus: TextFieldState? = null
        private set
    var focusRect: FieldRect? = null
        private set
    val textTargetCount: Int get() = fieldTargets.size

    private fun owned(labels: Map<MingNode, MingLabel>, fields: Map<MingNode, FieldPaint>): Set<MingLabel> =
        (labels.values + fields.values.flatMap { it.prefixes.values }).toSet()

    fun render(root: MingNode, width: Int, height: Int): Int {
        if (disposed || width !in 1..4096 || height !in 1..4096 || width.toLong() * height > 4_194_304) return SdkText.INVALID
        val staged = mutableListOf<MingLabel>()
        var stagedStyle: ShadowStyle? = null
        try {
            val leaves = mutableListOf<MingNode>()
            var count = 0
            fun collect(node: MingNode) {
                if (++count > 32) throw CapabilityFailure(SdkText.LIMIT)
                if (node.kind == NodeKind.Text || node.kind == NodeKind.Button || node.kind == NodeKind.TextField) leaves.add(node)
                node.children.forEach { collect(it) }
            }
            collect(root)
            val buttons = leaves.count { it.kind == NodeKind.Button }
            val fieldNodes = leaves.filter { it.kind == NodeKind.TextField }
            if (leaves.size > 16 || buttons > 8 || fieldNodes.size > 4 ||
                fieldNodes.map { it.fieldState }.toSet().size != fieldNodes.size ||
                width.toLong() * height * (1 + leaves.size + 2 * buttons) > 64_000_000) return SdkText.LIMIT
            val previousOwned = owned(labels, fields)
            fun obtain(node: MingNode, text: String, missing: Boolean): MingLabel {
                val candidates = listOfNotNull(labels[node]) + (fields[node]?.prefixes?.values ?: emptyList()) + staged
                return candidates.firstOrNull { it.text == text && it.emPx == node.emPx && it.missing == missing }
                    ?: createLabel(text, node.emPx, missing).also { staged.add(it) }
            }
            val next = linkedMapOf<MingNode, MingLabel>()
            val nextFields = linkedMapOf<MingNode, FieldPaint>()
            for (node in leaves) {
                if (node.kind != NodeKind.TextField) next[node] = obtain(node, node.text, false)
                else {
                    val value = checkNotNull(node.fieldValue)
                    val display = value.display()
                    val placeholder = display.text.isEmpty() && value.preedit == null
                    val label = obtain(node, if (placeholder) node.text else display.text, true)
                    next[node] = label
                    val positions = mutableSetOf(display.caret, display.selectionStart, display.selectionEnd)
                    if (node.fieldFocused && display.cursorStart >= 0) { positions.add(display.cursorStart); positions.add(display.cursorEnd) }
                    if (display.preeditStart >= 0) { positions.add(display.preeditStart); positions.add(display.preeditEnd) }
                    val prefixes = linkedMapOf<Int, MingLabel>()
                    for (index in positions) if (index > 0 && !placeholder) prefixes[index] =
                        if (index == display.text.length) label else obtain(node, display.text.substring(0, index), true)
                    nextFields[node] = FieldPaint(checkNotNull(node.fieldState), value, display, label, prefixes, placeholder)
                }
            }
            var style = shadow
            if (buttons > 0 && style == null) {
                val created = MingShadows.create(12, 6)
                style = created.style ?: throw CapabilityFailure(errorCode(created.code))
                stagedStyle = style
            }
            val placements = layoutMingTree(root, width, next)
            val nextTargets = mutableListOf<HitTarget>()
            val nextFieldTargets = mutableListOf<FieldTarget>()
            var nextCaret: FieldRect? = null
            var nextFocus: TextFieldState? = null
            var nextRect: FieldRect? = null
            val frame = MingFrame4(width, height)
            frame.rect(0, 0, width, height, 0xfff1f3f0.toInt())
            for (p in placements) {
                val node = p.node
                val label = checkNotNull(next[node])
                val button = node.kind == NodeKind.Button
                if (node.kind == NodeKind.TextField) {
                    val paint = checkNotNull(nextFields[node])
                    fun clipped(x: Int, y: Int, w: Int, h: Int): FieldRect? {
                        val x0 = x.coerceIn(0, width); val y0 = y.coerceIn(0, height)
                        val x1 = (x + w).coerceIn(x0, width); val y1 = (y + h).coerceIn(y0, height)
                        return if (x1 > x0 && y1 > y0) FieldRect(x0, y0, x1 - x0, y1 - y0) else null
                    }
                    val rect = clipped(p.x, p.y, p.width, p.height)
                    val content = clipped(p.x + 4, p.y + 2, (p.width - 8).coerceAtLeast(0), (p.height - 4).coerceAtLeast(0))
                    if (rect == null || content == null) continue
                    val caretQ6 = paint.width(paint.display.caret)
                    val innerWidth = content.width * 64
                    val oldScroll = fieldTargets.firstOrNull { it.paint.state === paint.state }?.scroll ?: 0
                    val total = if (paint.placeholder) 0 else label.metrics.advanceQ6
                    val scroll = oldScroll.coerceAtLeast(caretQ6 - (innerWidth - 64).coerceAtLeast(0))
                        .coerceAtMost(caretQ6).coerceIn(0, (total - innerWidth + 64).coerceAtLeast(0))
                    val target = FieldTarget(paint, rect, content, scroll)
                    nextFieldTargets.add(target)
                    frame.clip(rect.x, rect.y, rect.width, rect.height)
                    frame.rect(rect.x, rect.y, rect.width, rect.height, if (node.fieldFocused) 0xffd4e8df.toInt() else 0xffffffff.toInt())
                    frame.clip(content.x, content.y, content.width, content.height)
                    fun position(index: Int): Int = content.x * 64 + paint.width(index) - scroll
                    if (node.fieldFocused && paint.selectionStart != paint.selectionEnd) {
                        val left = position(paint.selectionStart) / 64
                        val right = (position(paint.selectionEnd) + 63) / 64
                        if (right > left) frame.rect(left, content.y, right - left, content.height, 0xffa9cfc0.toInt())
                    }
                    frame.textQ6(label.layout, content.x * 64 - scroll, p.baselineQ6, if (paint.placeholder) 0xff75837f.toInt() else node.color)
                    if (paint.display.preeditStart >= 0) {
                        val left = position(paint.display.preeditStart) / 64
                        val right = (position(paint.display.preeditEnd) + 63) / 64
                        if (right > left) frame.rect(left, content.y + content.height - 1, right - left, 1, node.color)
                    }
                    if (node.fieldFocused) {
                        val x = (position(paint.display.caret) / 64).coerceIn(content.x, content.x + content.width - 1)
                        nextCaret = FieldRect(x, content.y, 1, content.height)
                        nextFocus = paint.state
                        nextRect = nextCaret
                        frame.rect(x, content.y, 1, content.height, node.color)
                    }
                    frame.clip(0, 0, width, height)
                    continue
                }
                if (button) {
                    frame.shadow(checkNotNull(style), p.x, p.y + 4, p.width, p.height, 0x50304440)
                    frame.rectAA(p.x, p.y, p.width, p.height, 0xff245e55.toInt(), 12)
                    val action = node.onClick
                    val x0 = p.x.coerceIn(0, width); val y0 = p.y.coerceIn(0, height)
                    val x1 = (p.x + p.width).coerceIn(x0, width); val y1 = (p.y + p.height).coerceIn(y0, height)
                    if (action != null && x0 < x1 && y0 < y1) nextTargets.add(HitTarget(x0, y0, x1, y1, action))
                }
                frame.textQ6(label.layout, (p.x + if (button) 16 else 0) * 64, p.baselineQ6, node.color)
            }
            val submitted = frame.submit()
            if (submitted != 0) throw CapabilityFailure(errorCode(submitted))
            labels = next; fields = nextFields; targets = nextTargets; fieldTargets = nextFieldTargets
            publishedCaret = nextCaret; publishedFocus = nextFocus; focusRect = nextRect; shadow = style
            drag = drag?.let { old -> fieldTargets.firstOrNull { it.paint.state === old.paint.state } }
            if (focusedState != null && fieldTargets.none { it.paint.state === focusedState }) focus(null)
            val nextOwned = owned(next, nextFields)
            for (old in previousOwned) if (old !in nextOwned) close(old.layout)
            return 0
        } catch (failure: Throwable) {
            staged.forEach { close(it.layout) }
            stagedStyle?.let { close(it) }
            if (failure is CapabilityFailure) return failure.code
            throw failure
        }
    }

    fun click(x: Int, y: Int): Boolean {
        if (disposed) return false
        val hit = targets.lastOrNull { x >= it.x0 && x < it.x1 && y >= it.y0 && y < it.y1 } ?: return false
        Snapshot.withMutableSnapshot { hit.action() }
        return true
    }

    private fun focus(next: TextFieldState?) {
        if (focusedState === next) return
        Snapshot.withMutableSnapshot {
            focusedState?.let { it.isFocused = false; it.value = it.value.cancel() }
            focusedState = next
            next?.let { it.isFocused = true; it.value = it.value.cancel() }
        }
        if (focusVersion < Long.MAX_VALUE) focusVersion++
        drag = null
    }

    fun focusNext(reverse: Boolean): Boolean {
        if (disposed || fieldTargets.isEmpty()) return false
        val index = fieldTargets.indexOfFirst { it.paint.state === focusedState }
        val next = if (index < 0) { if (reverse) fieldTargets.lastIndex else 0 }
            else (index + if (reverse) fieldTargets.size - 1 else 1) % fieldTargets.size
        focus(fieldTargets[next].paint.state)
        return true
    }

    fun cancelInput(clearFocus: Boolean = false) {
        drag = null
        Snapshot.withMutableSnapshot { focusedState?.let { it.value = it.value.cancel() } }
        if (clearFocus) focus(null)
    }

    fun pointer(x: Int, y: Int, phase: Int, modifiers: Int): Int {
        if (disposed || phase == 0) return 0
        try {
            if (phase == 1) {
                val hit = fieldTargets.lastOrNull { x >= it.rect.x && x < it.rect.x + it.rect.width && y >= it.rect.y && y < it.rect.y + it.rect.height }
                if (hit == null) { focus(null); click(x, y); return 0 }
                val index = hitIndex(hit, x)
                focus(hit.paint.state)
                Snapshot.withMutableSnapshot {
                    val value = hit.paint.state.value
                    hit.paint.state.value = value.select(if (modifiers and 1 != 0) value.anchor else index, index)
                }
                drag = hit
            } else if (phase == 2 || phase == 3) {
                val hit = drag
                if (hit != null && x >= 0) {
                    val index = hitIndex(hit, x)
                    Snapshot.withMutableSnapshot { val value = hit.paint.state.value; hit.paint.state.value = value.select(value.anchor, index) }
                }
                if (phase == 3) drag = null
            }
            return 0
        } catch (failure: CapabilityFailure) { return failure.code }
    }

    private fun hitIndex(target: FieldTarget, x: Int): Int {
        val paint = target.paint
        val current = paint.state.value
        val changedText = current.text != paint.value.text
        val value = if (changedText) current else paint.value
        val display = if (changedText) current.display() else paint.display
        if (display.text.isEmpty()) return 0
        val text = display.text
        val measured = if (changedText) mutableMapOf<Int, Int>() else target.measured
        val boundaries = mutableListOf(0)
        var index = 0
        while (index < text.length) { index += if (text[index].isHighSurrogate()) 2 else 1; boundaries.add(index) }
        fun width(index: Int): Int {
            if (index == 0) return 0
            if (!changedText) paint.prefixes[index]?.let { return it.metrics.advanceQ6 }
            return measured.getOrPut(index) {
                val label = createLabel(text.substring(0, index), paint.label.emPx, true)
                try { label.metrics.advanceQ6 } finally { close(label.layout) }
            }
        }
        val wanted = ((x - target.content.x) * 64 + target.scroll).coerceAtLeast(0)
        var low = 0; var high = boundaries.lastIndex
        // Single-line LTR Chinese/Latin prefix advances, measured by SdkText, not character count.
        while (low < high) { val mid = (low + high) / 2; if (width(boundaries[mid]) < wanted) low = mid + 1 else high = mid }
        val right = boundaries[low]; val left = boundaries[(low - 1).coerceAtLeast(0)]
        val local = if (abs(width(left).toLong() - wanted) <= abs(width(right).toLong() - wanted)) left else right
        val composed = display.startOffset + local
        val preedit = value.preedit
        return if (preedit == null || composed <= value.selectionStart) composed
            else if (composed < value.selectionStart + preedit.text.length) value.selectionStart
            else composed - preedit.text.length + value.selectionEnd - value.selectionStart
    }

    fun dispose(): Int {
        if (disposed) return 0
        disposed = true
        focus(null)
        owned(labels, fields).forEach { close(it.layout) }
        shadow?.let { close(it) }
        labels = emptyMap(); fields = emptyMap(); shadow = null; targets = emptyList(); fieldTargets = emptyList()
        drag = null; publishedCaret = null; publishedFocus = null; focusRect = null
        val result = cleanupFailure; cleanupFailure = 0
        return result
    }

    private fun createLabel(text: String, emPx: Int, missing: Boolean): MingLabel {
        val created = SdkText.create(text, emPx.toFloat(), allowMissingGlyphs = missing)
        val layout = created.layout ?: throw CapabilityFailure(errorCode(created.code))
        val measured = layout.metrics()
        val metrics = measured.metrics
        if (measured.code != 0 || metrics == null) { close(layout); throw CapabilityFailure(errorCode(measured.code)) }
        return MingLabel(text, emPx, layout, metrics, missing)
    }
    private fun errorCode(code: Int): Int = if (code < 0) code else SdkText.INVALID
    private fun close(layout: TextLayout) { rememberClose(layout.close()) }
    private fun close(style: ShadowStyle) { rememberClose(style.close()) }
    private fun rememberClose(code: Int) { if (code < 0 && cleanupFailure == 0) cleanupFailure = code }
}
