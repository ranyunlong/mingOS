@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)

package ming.sdk

import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

/** SDK3 exposes only the host's pinned, regular builtin font. */
enum class BuiltinFont(internal val id: Int) {
    BUILTIN_FONT1(1)
}

/** A successful result has code 0; failures carry the host's negative error code. */
class TextLayoutResult internal constructor(val code: Int, val layout: TextLayout?)
class TextMetricsResult internal constructor(val code: Int, val metrics: TextMetrics?)

/** The first nine words are exact Q26.6 values in frame pixels, relative to the pen/baseline. */
data class TextMetrics(
    val advanceQ6: Int,
    val ascentQ6: Int,
    val descentQ6: Int,
    val gapQ6: Int,
    val lineHeightQ6: Int,
    val inkLeftQ6: Int,
    val inkTopQ6: Int,
    val inkRightQ6: Int,
    val inkBottomQ6: Int,
    val glyphCount: Int,
    val statusFlags: Int,
    val fontRevision: Int,
) {
    val advance: Float get() = advanceQ6 / 64f
    val ascent: Float get() = ascentQ6 / 64f
    val descent: Float get() = descentQ6 / 64f
    val gap: Float get() = gapQ6 / 64f
    val lineHeight: Float get() = lineHeightQ6 / 64f
    val inkLeft: Float get() = inkLeftQ6 / 64f
    val inkTop: Float get() = inkTopQ6 / 64f
    val inkRight: Float get() = inkRightQ6 / 64f
    val inkBottom: Float get() = inkBottomQ6 / 64f
    val hasMissingGlyphs: Boolean get() = statusFlags and 1 != 0
}

/**
 * Immutable, session-local shaped text. Keep it alive through frame submission and close explicitly.
 * Closing again, measuring after close, or submitting a closed layout returns a host error.
 * The raw handle is intentionally internal to the SDK; close does not mutate this value.
 */
class TextLayout internal constructor(internal val handle: Int) {
    fun metrics(): TextMetricsResult = withScopedMemoryAllocator { allocator ->
        val pointer = allocator.allocate(48)
        val result = textLayoutMetrics(handle, pointer.address.toInt(), 48)
        if (result != 48) {
            TextMetricsResult(if (result < 0) result else MingText.INVALID, null)
        } else {
            TextMetricsResult(0, TextMetrics(
                pointer.loadInt(), (pointer + 4).loadInt(), (pointer + 8).loadInt(),
                (pointer + 12).loadInt(), (pointer + 16).loadInt(), (pointer + 20).loadInt(),
                (pointer + 24).loadInt(), (pointer + 28).loadInt(), (pointer + 32).loadInt(),
                (pointer + 36).loadInt(), (pointer + 40).loadInt(), (pointer + 44).loadInt(),
            ))
        }
    }

    fun close(): Int = textLayoutClose(handle)
}

object MingText {
    const val INVALID: Int = -2
    const val LIMIT: Int = -4
    const val MISSING_GLYPH: Int = -5
    const val UNSUPPORTED: Int = -6

    /** Single-line LTR Chinese/Latin text; em sizes must be 8..96px in exact quarter-pixel steps. */
    fun create(
        text: String,
        emSize: Float,
        font: BuiltinFont = BuiltinFont.BUILTIN_FONT1,
        allowMissingGlyphs: Boolean = false,
    ): TextLayoutResult {
        if (!emSize.isFinite() || emSize !in 8f..96f || emSize * 4f % 1f != 0f) {
            return TextLayoutResult(INVALID, null)
        }
        return createQ6(text, (emSize * 64f).toInt(), font, allowMissingGlyphs)
    }

    /** Exact Q26.6 counterpart to create; the host remains authoritative for text and resource limits. */
    fun createQ6(
        text: String,
        emSizeQ6: Int,
        font: BuiltinFont = BuiltinFont.BUILTIN_FONT1,
        allowMissingGlyphs: Boolean = false,
    ): TextLayoutResult {
        if (emSizeQ6 !in 512..6144 || emSizeQ6 % 16 != 0 || text.length > 512) {
            return TextLayoutResult(INVALID, null)
        }
        val bytes = text.encodeToByteArray()
        if (bytes.size > 512) return TextLayoutResult(INVALID, null)
        return withScopedMemoryAllocator { allocator ->
            val pointer = allocator.allocate(bytes.size.coerceAtLeast(1))
            for (i in bytes.indices) (pointer + i).storeByte(bytes[i])
            val handle = textLayoutCreate(pointer.address.toInt(), bytes.size, font.id, emSizeQ6,
                if (allowMissingGlyphs) 1 else 0)
            if (handle > 0) TextLayoutResult(0, TextLayout(handle))
            else TextLayoutResult(if (handle < 0) handle else INVALID, null)
        }
    }
}

@WasmImport("ming_v3", "text_layout_create")
private external fun textLayoutCreate(pointer: Int, byteLength: Int, font: Int, emSizeQ6: Int, flags: Int): Int
@WasmImport("ming_v3", "text_layout_metrics")
private external fun textLayoutMetrics(handle: Int, pointer: Int, capacity: Int): Int
@WasmImport("ming_v3", "text_layout_close")
private external fun textLayoutClose(handle: Int): Int
