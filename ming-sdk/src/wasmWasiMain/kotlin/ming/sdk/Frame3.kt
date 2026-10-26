@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)

package ming.sdk

import kotlin.math.roundToInt
import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

/**
 * SDK3 MGF2/layout1 builder. Commands retain painter order; clip replaces the current clip.
 * Geometry uses integer frame pixels, text uses a left pen and a downward-positive baseline.
 * Invalid input poisons this builder: submit returns -2 without calling the host.
 * Layouts must remain open until submit returns. Construct a new frame to replace its contents.
 */
class MingFrame3(private val width: Int, private val height: Int) {
    private val commands = ArrayList<Int>()
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

    fun rect(x: Int, y: Int, w: Int, h: Int, color: Int, radius: Int = 0) {
        if (!coordinate(x) || !coordinate(y) || !extent(w) || !extent(h) || radius !in 0..64) {
            status = MingText.INVALID; return
        }
        command(1, x, y, w, h, color, radius)
    }

    fun image(handle: Int, x: Int, y: Int, w: Int, h: Int) {
        if (handle <= 0 || !coordinate(x) || !coordinate(y) || !extent(w) || !extent(h)) {
            status = MingText.INVALID; return
        }
        command(2, handle, x, y, w, h)
    }

    fun clip(x: Int, y: Int, w: Int, h: Int) {
        if (!coordinate(x) || !coordinate(y) || w !in 0..1_000_000 || h !in 0..1_000_000) {
            status = MingText.INVALID; return
        }
        command(4, x, y, w, h, 0)
    }

    /** Finite pixels in +/-1,000,000, rounded to the nearest 1/64px after checking the range. */
    fun text(layout: TextLayout, x: Float, baselineY: Float, color: Int) {
        if (!x.isFinite() || !baselineY.isFinite() ||
            x !in -1_000_000f..1_000_000f || baselineY !in -1_000_000f..1_000_000f) {
            status = MingText.INVALID; return
        }
        textQ6(layout, (x * 64f).roundToInt(), (baselineY * 64f).roundToInt(), color)
    }

    /** Exact Q26.6 text coordinates; the host checks the session-local typed handle's lifetime. */
    fun textQ6(layout: TextLayout, xQ6: Int, baselineYQ6: Int, color: Int) {
        if (xQ6 !in -64_000_000..64_000_000 || baselineYQ6 !in -64_000_000..64_000_000) {
            status = MingText.INVALID; return
        }
        command(5, layout.handle, xQ6, baselineYQ6, color, 0)
    }

    fun submit(): Int {
        if (status != 0) return status
        // No legacy string pool: this builder draws immutable SDK3 layouts with op5.
        val size = 24 + commands.size * 4
        return withScopedMemoryAllocator { allocator ->
            val pointer = allocator.allocate(size)
            val header = intArrayOf(0x4d474632, 1, width, height, commands.size / 8, 0)
            for (i in header.indices) (pointer + i * 4).storeInt(header[i])
            for (i in commands.indices) (pointer + 24 + i * 4).storeInt(commands[i])
            submitFrame3(pointer.address.toInt(), size)
        }
    }
}

@WasmImport("ming_v3", "frame_submit")
private external fun submitFrame3(pointer: Int, byteLength: Int): Int
