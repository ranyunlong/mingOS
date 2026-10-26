@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)

package ming.sdk

import kotlin.math.roundToInt
import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

/** SDK4 frame builder; text uses the same immutable, session-local SDK3 layouts. */
class MingFrame4(private val width: Int, private val height: Int) {
    private val commands = ArrayList<Int>()
    private var aaCount = 0
    private var shadowCount = 0
    private var status = if (width in 1..4096 && height in 1..4096 &&
        width.toLong() * height <= 4_194_304) 0 else MingText.INVALID

    private fun coordinate(value: Int): Boolean = value in -1_000_000..1_000_000
    private fun extent(value: Int): Boolean = value in 1..1_000_000

    private fun command(op: Int, a: Int, b: Int, c: Int, d: Int, e: Int,
        f: Int = 0, g: Int = 0) {
        if (status != 0) return
        if (commands.size / 8 >= 256) { status = MingText.INVALID; return }
        commands.add(op); commands.add(a); commands.add(b); commands.add(c)
        commands.add(d); commands.add(e); commands.add(f); commands.add(g)
    }

    /** Existing op1 hard-edge rounded rectangle semantics, unchanged. */
    fun rect(x: Int, y: Int, w: Int, h: Int, color: Int, radius: Int = 0) {
        if (status != 0) return
        if (!coordinate(x) || !coordinate(y) || !extent(w) || !extent(h) || radius !in 0..64) {
            status = MingText.INVALID; return
        }
        command(1, x, y, w, h, color, radius)
    }

    /** Op6: 4x4 coverage AA. The host clamps radius to min(w/2, h/2).
     * Every AA command counts, including transparent, clipped and radius-zero rectangles.
     */
    fun rectAA(x: Int, y: Int, w: Int, h: Int, color: Int, radius: Int = 0) {
        if (status != 0) return
        if (!coordinate(x) || !coordinate(y) || !extent(w) || !extent(h) || radius !in 0..64) {
            status = MingText.INVALID; return
        }
        if (aaCount >= 16) { status = MingText.LIMIT; return }
        aaCount++
        command(6, x, y, w, h, color, radius)
    }

    /** Op7: full blurred shape in painter order; draw before the panel it shadows.
     * Transparent and clipped draws still count toward the eight-shadow frame limit.
     * The host validates the immutable style's session and lifetime at submission.
     */
    fun shadow(style: ShadowStyle, x: Int, y: Int, w: Int, h: Int, color: Int) {
        if (status != 0) return
        if (!coordinate(x) || !coordinate(y) || !extent(w) || !extent(h)) {
            status = MingText.INVALID; return
        }
        if (shadowCount >= 8) { status = MingText.LIMIT; return }
        shadowCount++
        command(7, style.handle, x, y, w, h, color)
    }

    fun image(handle: Int, x: Int, y: Int, w: Int, h: Int) {
        if (status != 0) return
        if (handle <= 0 || !coordinate(x) || !coordinate(y) || !extent(w) || !extent(h)) {
            status = MingText.INVALID; return
        }
        command(2, handle, x, y, w, h)
    }

    /** Replaces the current half-open integer clip; this is not a clip stack. */
    fun clip(x: Int, y: Int, w: Int, h: Int) {
        if (status != 0) return
        if (!coordinate(x) || !coordinate(y) || w !in 0..1_000_000 || h !in 0..1_000_000) {
            status = MingText.INVALID; return
        }
        command(4, x, y, w, h, 0)
    }

    /** Finite pixels in +/-1,000,000, rounded to nearest 1/64px after checking the range. */
    fun text(layout: TextLayout, x: Float, baselineY: Float, color: Int) {
        if (status != 0) return
        if (!x.isFinite() || !baselineY.isFinite() ||
            x !in -1_000_000f..1_000_000f || baselineY !in -1_000_000f..1_000_000f) {
            status = MingText.INVALID; return
        }
        textQ6(layout, (x * 64f).roundToInt(), (baselineY * 64f).roundToInt(), color)
    }

    /** The host remains authoritative for the immutable layout handle's lifetime. */
    fun textQ6(layout: TextLayout, xQ6: Int, baselineYQ6: Int, color: Int) {
        if (status != 0) return
        if (xQ6 !in -64_000_000..64_000_000 || baselineYQ6 !in -64_000_000..64_000_000) {
            status = MingText.INVALID; return
        }
        command(5, layout.handle, xQ6, baselineYQ6, color, 0)
    }

    /** Local errors persist and never call the host. Host rejection leaves this builder reusable. */
    fun submit(): Int {
        if (status != 0) return status
        val size = 24 + commands.size * 4
        return withScopedMemoryAllocator { allocator ->
            val pointer = allocator.allocate(size)
            // MGF2/layout1: same 24-byte header, eight i32 words per command, no string pool.
            val header = intArrayOf(0x4d474632, 1, width, height, commands.size / 8, 0)
            for (i in header.indices) (pointer + i * 4).storeInt(header[i])
            for (i in commands.indices) (pointer + 24 + i * 4).storeInt(commands[i])
            submitFrame4(pointer.address.toInt(), size)
        }
    }
}

@WasmImport("ming_v4", "frame_submit")
private external fun submitFrame4(pointer: Int, length: Int): Int
