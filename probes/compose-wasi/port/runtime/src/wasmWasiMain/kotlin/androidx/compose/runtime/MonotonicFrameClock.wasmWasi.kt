package androidx.compose.runtime

/** This port has no clock intrinsic. Callers must supply an explicit host frame clock. */
@Deprecated("MonotonicFrameClocks are not globally applicable across platforms. Use an appropriate local clock.")
public actual val DefaultMonotonicFrameClock: MonotonicFrameClock = object : MonotonicFrameClock {
    override suspend fun <R> withFrameNanos(onFrame: (Long) -> R): R =
        throw UnsupportedOperationException("MING_WASI_FRAME_CLOCK_REQUIRED: supply an explicit MonotonicFrameClock")
}
