@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)
package ming.sdk

import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

/** code=0 has a value; negative codes have no value. Nanoseconds are monotonic, not wall time. */
class ClockReadResult(val code: Int, val nanos: Long?)

enum class MingFrameKind { FRAME, TIMER, INPUT, CLOSE, CANCEL }

/** Detached event data; it owns no host resource and needs no close operation. */
class MingFrameEvent internal constructor(
    val kind: MingFrameKind,
    val nowNanos: Long,
    val frameSequence: Long,
    val input: InputEvent?,
)

/** code=0 with event=null means pending; code=0 with an event means successful delivery. */
class FrameWaitResult(val code: Int, val event: MingFrameEvent?)

/** SDK5 controlled host time/frame opportunities. A FRAME is not proof of presentation or vsync. */
object MingFrames {
    const val INVALID: Int = -2
    const val IO: Int = -3
    const val LIMIT: Int = -4

    fun monotonicNanos(): ClockReadResult = withScopedMemoryAllocator { allocator ->
        val output = allocator.allocate(8)
        val code = clockMonotonicNs(output.address.toInt(), 8)
        if (code != 8) {
            ClockReadResult(if (code < 0) code else INVALID, null)
        } else {
            val nanos = wordsToLong(output.loadInt(), (output + 4).loadInt())
            if (nanos < 0) ClockReadResult(INVALID, null) else ClockReadResult(0, nanos)
        }
    }

    /**
     * Request one bounded host wait. Null deadline means no timer; any negative deadline is a
     * local error and makes no host call. Pending/error output buffers are never decoded.
     * Cross-call time/sequence ordering is enforced by the session-owned host clock.
     */
    fun wait(
        wantFrame: Boolean,
        deadlineNs: Long? = null,
        nonblocking: Boolean = false,
    ): FrameWaitResult {
        if (deadlineNs != null && deadlineNs < 0) return FrameWaitResult(INVALID, null)
        return withScopedMemoryAllocator { allocator ->
            val request = allocator.allocate(16)
            val output = allocator.allocate(48)
            val flags = (if (wantFrame) 1 else 0) or (if (nonblocking) 2 else 0)
            val deadline = deadlineNs ?: -1L
            request.storeInt(1)
            (request + 4).storeInt(flags)
            (request + 8).storeInt(deadline.toInt())
            (request + 12).storeInt((deadline ushr 32).toInt())
            val code = waitFrame(request.address.toInt(), 16, output.address.toInt(), 48)
            if (code != 48) {
                return@withScopedMemoryAllocator FrameWaitResult(if (code <= 0) code else INVALID, null)
            }
            if (output.loadInt() != 1 || (output + 40).loadInt() != 0 || (output + 44).loadInt() != 0) {
                return@withScopedMemoryAllocator FrameWaitResult(INVALID, null)
            }
            val kind = when ((output + 4).loadInt()) {
                1 -> MingFrameKind.FRAME
                2 -> MingFrameKind.TIMER
                3 -> MingFrameKind.INPUT
                4 -> MingFrameKind.CLOSE
                5 -> MingFrameKind.CANCEL
                else -> return@withScopedMemoryAllocator FrameWaitResult(INVALID, null)
            }
            val now = wordsToLong((output + 8).loadInt(), (output + 12).loadInt())
            val sequence = wordsToLong((output + 16).loadInt(), (output + 20).loadInt())
            if (now < 0 || sequence < 0 || (kind == MingFrameKind.FRAME && sequence == 0L)) {
                return@withScopedMemoryAllocator FrameWaitResult(INVALID, null)
            }
            val type = (output + 24).loadInt()
            val a = (output + 28).loadInt()
            val b = (output + 32).loadInt()
            val c = (output + 36).loadInt()
            val input = if (kind == MingFrameKind.INPUT) {
                if (type !in 1..7) return@withScopedMemoryAllocator FrameWaitResult(INVALID, null)
                InputEvent(type, a, b, c)
            } else {
                if (type != 0 || a != 0 || b != 0 || c != 0) {
                    return@withScopedMemoryAllocator FrameWaitResult(INVALID, null)
                }
                null
            }
            FrameWaitResult(0, MingFrameEvent(kind, now, sequence, input))
        }
    }
}

// Wasm memory is little-endian. Avoid signed extension of the low word and do not require
// a native pointer alignment or a platform-specific 64-bit load/store API.
private fun wordsToLong(low: Int, high: Int): Long =
    (low.toLong() and 0xffffffffL) or (high.toLong() shl 32)

@WasmImport("ming_frame_v1", "clock_monotonic_ns")
private external fun clockMonotonicNs(outputPointer: Int, capacity: Int): Int

@WasmImport("ming_frame_v1", "wait")
private external fun waitFrame(requestPointer: Int, requestLength: Int, outputPointer: Int, capacity: Int): Int
