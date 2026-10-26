@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)

package ming.sdk

import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

enum class TextInputKind { COMMIT, PREEDIT, KEY, POINTER, CANCEL }

object TextInputModifiers {
    const val SHIFT: Int = 1
    const val CONTROL: Int = 2
    const val ALT: Int = 4
    const val SUPER: Int = 8
}

/** Detached SDK6 data. a/b/c retain the ABI values; preedit cursors below use UTF-16. */
class TextInputEvent internal constructor(
    val kind: TextInputKind,
    val sequence: Long,
    val epoch: Long,
    val frameSequence: Long,
    val a: Int,
    val b: Int,
    val c: Int,
    val modifiers: Int,
    val text: String,
    /** -1/-1 means hidden (also used for non-preedit events). */
    val cursorStart: Int,
    val cursorEnd: Int,
)

/** code=0: event or pending null; negative code: no event. No host resource is owned. */
class TextInputResult internal constructor(val code: Int, val event: TextInputEvent?)

/** SDK6 input capability. Unused entrypoints are eliminated from legacy consumers by Wasm DCE. */
object MingTextInput {
    const val INVALID: Int = -2
    const val MAX_TEXT_BYTES: Int = 512
    const val PACKET_CAPACITY: Int = 568

    /** Pending/error buffers are never read, even if the Host left a previous valid packet there. */
    fun poll(): TextInputResult = withScopedMemoryAllocator { allocator ->
        val output = allocator.allocate(PACKET_CAPACITY)
        val count = inputPoll(output.address.toInt(), PACKET_CAPACITY)
        if (count <= 0) TextInputResult(count, null)
        else if (count !in 56..PACKET_CAPACITY) TextInputResult(INVALID, null)
        else decode(ByteArray(count) { (output + it).loadByte() })
    }

    /**
     * Pure decoder for one complete little-endian packet. length excludes any unused capacity.
     * Checks packet format, not cross-call ordering; the session Host owns sequence/epoch freshness.
     */
    fun decode(bytes: ByteArray, length: Int = bytes.size): TextInputResult {
        fun invalid() = TextInputResult(INVALID, null)
        if (length !in 56..PACKET_CAPACITY || length > bytes.size) return invalid()
        fun word(offset: Int): Int = (bytes[offset].toInt() and 255) or
            ((bytes[offset + 1].toInt() and 255) shl 8) or
            ((bytes[offset + 2].toInt() and 255) shl 16) or
            ((bytes[offset + 3].toInt() and 255) shl 24)
        fun wide(offset: Int): Long = (word(offset).toLong() and 0xffffffffL) or
            (word(offset + 4).toLong() shl 32)
        if (word(0) != 1 || word(52) != 0) return invalid()
        val kind = when (word(4)) {
            1 -> TextInputKind.COMMIT
            2 -> TextInputKind.PREEDIT
            3 -> TextInputKind.KEY
            4 -> TextInputKind.POINTER
            5 -> TextInputKind.CANCEL
            else -> return invalid()
        }
        val sequence = wide(8)
        val epoch = wide(16)
        val frame = wide(24)
        if (sequence <= 0 || epoch <= 0 || frame < 0 || (frame == 0L && kind != TextInputKind.CANCEL)) return invalid()
        val a = word(32)
        val b = word(36)
        val c = word(40)
        val modifiers = word(44)
        val textLength = word(48)
        if (modifiers and 15.inv() != 0 || textLength !in 0..MAX_TEXT_BYTES || length != 56 + textLength) return invalid()
        val text = strictInputUtf8(bytes, 56, length) ?: return invalid()
        var cursorStart = -1
        var cursorEnd = -1
        when (kind) {
            TextInputKind.COMMIT -> if (a != 0 || b != 0 || c != 0 || textLength == 0) return invalid()
            TextInputKind.PREEDIT -> {
                if (c != 0) return invalid()
                if (a != -1 || b != -1) {
                    if (a < 0 || b < a || b > textLength) return invalid()
                    cursorStart = inputByteCursor(text, a)
                    cursorEnd = inputByteCursor(text, b)
                    if (cursorStart < 0 || cursorEnd < 0) return invalid()
                }
            }
            TextInputKind.KEY -> if (textLength != 0 || a !in 1..15 || b !in 0..1 || c !in 0..1) return invalid()
            TextInputKind.POINTER -> {
                val inside = a in 0..4095 && b in 0..4095
                if (textLength != 0 || (!inside && !(a == -1 && b == -1)) || c !in 0..3) return invalid()
            }
            TextInputKind.CANCEL -> if (textLength != 0 || a !in 1..4 || b != 0 || c != 0) return invalid()
        }
        return TextInputResult(0, TextInputEvent(kind, sequence, epoch, frame, a, b, c, modifiers, text, cursorStart, cursorEnd))
    }

    /**
     * Focuses the parent-owned latest presented source. No app ID, frame override or path is accepted.
     * The parent must recheck actual source bounds; these are only the ABI's 4096px limits.
     * Inactive rectangles are normalized to zero regardless of supplied coordinates.
     */
    fun textFocus(active: Boolean, x: Int = 0, y: Int = 0, width: Int = 0, height: Int = 0): Int {
        if (active && (x !in 0..4095 || y !in 0..4095 || width !in 1..4096 || height !in 1..4096 ||
                width > 4096 - x || height > 4096 - y)) return INVALID
        return withScopedMemoryAllocator { allocator ->
            val request = allocator.allocate(24)
            request.storeInt(1)
            (request + 4).storeInt(if (active) 1 else 0)
            (request + 8).storeInt(if (active) x else 0)
            (request + 12).storeInt(if (active) y else 0)
            (request + 16).storeInt(if (active) width else 0)
            (request + 20).storeInt(if (active) height else 0)
            inputTextFocus(request.address.toInt(), 24)
        }
    }
}

/** Reject overlong, surrogate, truncated and out-of-range sequences without replacement characters. */
private fun strictInputUtf8(bytes: ByteArray, start: Int, end: Int): String? {
    val out = StringBuilder()
    var i = start
    while (i < end) {
        val first = bytes[i++].toInt() and 255
        val count: Int
        var scalar: Int
        val minimum: Int
        when (first) {
            in 0..0x7f -> { count = 0; scalar = first; minimum = 0 }
            in 0xc2..0xdf -> { count = 1; scalar = first and 31; minimum = 0x80 }
            in 0xe0..0xef -> { count = 2; scalar = first and 15; minimum = 0x800 }
            in 0xf0..0xf4 -> { count = 3; scalar = first and 7; minimum = 0x10000 }
            else -> return null
        }
        if (i + count > end) return null
        repeat(count) {
            val next = bytes[i++].toInt() and 255
            if (next !in 0x80..0xbf) return null
            scalar = (scalar shl 6) or (next and 63)
        }
        if (scalar < minimum || scalar in 0xd800..0xdfff || scalar > 0x10ffff) return null
        if (scalar <= 0xffff) out.append(scalar.toChar())
        else {
            val supplementary = scalar - 0x10000
            out.append((0xd800 + (supplementary ushr 10)).toChar())
            out.append((0xdc00 + (supplementary and 1023)).toChar())
        }
    }
    return out.toString()
}

private fun inputByteCursor(text: String, byteIndex: Int): Int {
    var bytes = 0
    var index = 0
    while (index < text.length && bytes < byteIndex) {
        val ch = text[index]
        bytes += when { ch.isHighSurrogate() -> 4; ch.code >= 0x800 -> 3; ch.code >= 0x80 -> 2; else -> 1 }
        index += if (ch.isHighSurrogate()) 2 else 1
    }
    return if (bytes == byteIndex) index else -1
}

@WasmImport("ming_input_v1", "poll")
private external fun inputPoll(outputPointer: Int, capacity: Int): Int

@WasmImport("ming_input_v1", "text_focus")
private external fun inputTextFocus(requestPointer: Int, length: Int): Int
