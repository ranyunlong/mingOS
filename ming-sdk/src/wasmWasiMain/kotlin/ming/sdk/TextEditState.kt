package ming.sdk

/** Preedit cursor range is UTF-16 on scalar boundaries; -1/-1 hides it. */
data class TextPreedit(val text: String, val cursorStart: Int = -1, val cursorEnd: Int = -1) {
    init {
        require(editUtf8Length(text) in 0..512) { "Preedit must be scalar text within 512 UTF-8 bytes" }
        require(validEditCursor(text, cursorStart, cursorEnd)) { "Invalid preedit cursor range" }
    }
}

/**
 * Layout-ready window. All positions except startOffset/fullLength are local UTF-16 indices.
 * preeditStart/End and cursorStart/End are -1 when absent/hidden/outside the window.
 * Clipped ranges may be shorter than their full display ranges. No layout handle is owned.
 */
data class TextEditDisplay(
    val text: String,
    val caret: Int,
    val selectionStart: Int,
    val selectionEnd: Int,
    val preeditStart: Int,
    val preeditEnd: Int,
    val cursorStart: Int,
    val cursorEnd: Int,
    val startOffset: Int,
    val fullLength: Int,
)

/**
 * Immutable single-line editing value for Chinese/Latin and Unicode scalar-safe navigation.
 * This is not full UAX grapheme segmentation. committed and preedit each have a 512-byte budget.
 * Methods reject invalid/oversized edits by returning this exact value, retaining existing text.
 * Construction rejects invalid invariants. No Compose state, Host call or clipboard is used.
 */
data class TextEditState(
    val text: String = "",
    val anchor: Int = text.length,
    val caret: Int = anchor,
    val preedit: TextPreedit? = null,
) {
    init {
        require(editUtf8Length(text) in 0..512) { "Committed text must be scalar text within 512 UTF-8 bytes" }
        require(editBoundary(text, anchor) && editBoundary(text, caret)) { "Invalid selection boundary" }
    }

    val selectionStart: Int get() = minOf(anchor, caret)
    val selectionEnd: Int get() = maxOf(anchor, caret)
    val selectedText: String get() = text.substring(selectionStart, selectionEnd)

    /** Replace selection atomically. Empty text can delete selection; ABI COMMIT itself is nonempty. */
    fun commit(value: String): TextEditState {
        val size = editUtf8Length(value)
        if (size !in 0..512) return this
        val next = text.substring(0, selectionStart) + value + text.substring(selectionEnd)
        if (editUtf8Length(next) > 512) return this
        val end = selectionStart + value.length
        return changed(next, end, end, null)
    }

    /** Preedit never changes committed text or selection. Empty text clears composition. */
    fun preedit(value: String, cursorStart: Int = -1, cursorEnd: Int = -1): TextEditState {
        if (editUtf8Length(value) !in 0..512 || !validEditCursor(value, cursorStart, cursorEnd)) return this
        val next = if (value.isEmpty()) null else TextPreedit(value, cursorStart, cursorEnd)
        return changed(text, anchor, caret, next)
    }

    fun cancel(): TextEditState = if (preedit == null) this else changed(text, anchor, caret, null)

    /** Explicit selection or keyboard navigation ends composition, while retaining committed text. */
    fun select(anchor: Int, caret: Int = anchor): TextEditState {
        if (!editBoundary(text, anchor) || !editBoundary(text, caret)) return this
        return changed(text, anchor, caret, null)
    }

    /**
     * Keys use SDK6 ABI IDs (1 Backspace, 2 Delete, 3/4 Left/Right, 5/6 Home/End, 8 Escape,
     * 10 A). Only keydown edits. Enter/Tab, Up/Down, clipboard keys and other shortcuts are
     * left to the field wrapper. Ctrl+A is supported; Ctrl word movement/deletion is not.
     */
    fun key(key: Int, down: Boolean = true, modifiers: Int = 0): TextEditState {
        if (!down || modifiers and (1 or 2).inv() != 0) return this
        if (modifiers and 2 != 0) return if (key == 10) select(0, text.length) else this
        val shift = modifiers and 1 != 0
        return when (key) {
            1, 2 -> {
                if (selectionStart != selectionEnd) commit("")
                else {
                    val from = if (key == 1) editPrevious(text, caret) else caret
                    val to = if (key == 2) editNext(text, caret) else caret
                    if (from == to) this else changed(text.removeRange(from, to), from, from, null)
                }
            }
            3, 4, 5, 6 -> {
                val target = when (key) {
                    3 -> if (!shift && anchor != caret) selectionStart else editPrevious(text, caret)
                    4 -> if (!shift && anchor != caret) selectionEnd else editNext(text, caret)
                    5 -> 0
                    else -> text.length
                }
                select(if (shift) anchor else target, target)
            }
            8 -> cancel()
            else -> this
        }
    }

    /** Frame/epoch routing is the wrapper's responsibility; this value applies content only. */
    fun apply(event: TextInputEvent): TextEditState = when (event.kind) {
        TextInputKind.COMMIT -> commit(event.text)
        TextInputKind.PREEDIT -> preedit(event.text, event.cursorStart, event.cursorEnd)
        TextInputKind.CANCEL -> cancel()
        TextInputKind.KEY -> key(event.a, event.b == 1, event.modifiers)
        TextInputKind.POINTER -> this
    }

    /**
     * A scalar-safe window of at most 512 UTF-8 bytes around the visible caret. Preedit replaces
     * selection only in this display string. When its cursor is hidden, caret sits at preedit end.
     * startOffset maps local positions back to the full composed display, not committed text.
     */
    fun display(): TextEditDisplay {
        val composition = preedit
        val start = selectionStart
        val end = selectionEnd
        val full = if (composition == null) text else text.substring(0, start) + composition.text + text.substring(end)
        val compositionEnd = if (composition == null) -1 else start + composition.text.length
        val visibleCaret = if (composition == null) caret else start +
            (if (composition.cursorEnd < 0) composition.text.length else composition.cursorEnd)
        var left = visibleCaret
        var right = visibleCaret
        var budget = 512
        // Alternate sides to retain useful context. Every step consumes a complete scalar.
        while (true) {
            var advanced = false
            if (left > 0) {
                val next = editPrevious(full, left)
                val cost = editUtf8Length(full.substring(next, left))
                if (cost <= budget) { left = next; budget -= cost; advanced = true }
            }
            if (right < full.length) {
                val next = editNext(full, right)
                val cost = editUtf8Length(full.substring(right, next))
                if (cost <= budget) { right = next; budget -= cost; advanced = true }
            }
            if (!advanced) break
        }
        fun local(index: Int) = index.coerceIn(left, right) - left
        val intersects = composition != null && start <= right && compositionEnd >= left
        val cursorVisible = composition != null && composition.cursorStart >= 0 &&
            start + composition.cursorEnd >= left && start + composition.cursorStart <= right
        return TextEditDisplay(
            full.substring(left, right), visibleCaret - left,
            if (composition == null) local(start) else visibleCaret - left,
            if (composition == null) local(end) else visibleCaret - left,
            if (intersects) local(start) else -1,
            if (intersects) local(compositionEnd) else -1,
            if (cursorVisible) local(start + composition.cursorStart) else -1,
            if (cursorVisible) local(start + composition.cursorEnd) else -1,
            left, full.length,
        )
    }

    private fun changed(text: String, anchor: Int, caret: Int, preedit: TextPreedit?): TextEditState =
        if (this.text == text && this.anchor == anchor && this.caret == caret && this.preedit == preedit) this
        else TextEditState(text, anchor, caret, preedit)
}

private fun validEditCursor(text: String, start: Int, end: Int): Boolean =
    (start == -1 && end == -1) || (start >= 0 && start <= end && editBoundary(text, start) && editBoundary(text, end))

private fun editBoundary(text: String, index: Int): Boolean = index in 0..text.length &&
    !(index > 0 && index < text.length && text[index - 1].isHighSurrogate() && text[index].isLowSurrogate())

private fun editPrevious(text: String, index: Int): Int = when {
    index == 0 -> 0
    index >= 2 && text[index - 1].isLowSurrogate() -> index - 2
    else -> index - 1
}

private fun editNext(text: String, index: Int): Int = when {
    index == text.length -> index
    text[index].isHighSurrogate() -> index + 2
    else -> index + 1
}

/** -1 rejects unpaired UTF-16 surrogates, rather than silently encoding U+FFFD. */
private fun editUtf8Length(text: String): Int {
    var bytes = 0
    var index = 0
    while (index < text.length) {
        val ch = text[index++]
        when {
            ch.isHighSurrogate() -> {
                if (index == text.length || !text[index++].isLowSurrogate()) return -1
                bytes += 4
            }
            ch.isLowSurrogate() -> return -1
            ch.code >= 0x800 -> bytes += 3
            ch.code >= 0x80 -> bytes += 2
            else -> bytes++
        }
        if (bytes > 512) return bytes
    }
    return bytes
}
