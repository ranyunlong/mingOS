package androidx.compose.runtime

import androidx.compose.runtime.internal.currentThreadId
import androidx.compose.runtime.internal.currentThreadName
import androidx.compose.runtime.snapshots.SnapshotWeakSet
import kotlin.coroutines.Continuation
import kotlin.coroutines.EmptyCoroutineContext
import kotlin.coroutines.startCoroutine

/** Probe-only platform checks; these do not claim true weak references or concurrent support. */
public fun runWasiPlatformQualification(): IntArray {
    check(currentThreadId() == 0L && currentThreadName() == "main")
    val references = SnapshotWeakSet<Any>()
    repeat(512) { check(references.add(Any())) }
    check(references.size == 512 && references.values.count { it != null } == 512)
    val allocatedSlots = references.values.size
    references.removeIf { true }
    check(references.size == 0 && references.values.all { it == null })
    // Upstream web WeakReference holds one strong reference per wrapper until it is dropped.
    // This proves explicit removal releases all wrapper slots, not automatic GC behavior.
    return intArrayOf(512, references.size, allocatedSlots)
}

/** Separate suspend boundary probe; it may retain additional coroutine runtime imports. */
public fun runWasiDefaultClockQualification(): Int {
    var completed = false
    var failure: Throwable? = null
    val clockProbe: suspend () -> Unit = {
        @Suppress("DEPRECATION")
        DefaultMonotonicFrameClock.withFrameNanos { }
    }
    clockProbe.startCoroutine(object : Continuation<Unit> {
        override val context = EmptyCoroutineContext
        override fun resumeWith(result: Result<Unit>) {
            completed = true
            failure = result.exceptionOrNull()
        }
    })
    check(completed && failure is UnsupportedOperationException)
    check(failure?.message?.startsWith("MING_WASI_FRAME_CLOCK_REQUIRED:") == true)
    return 1
}
