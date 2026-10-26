package persistentprobe

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.withFrameNanos
import kotlinx.coroutines.awaitCancellation
import ming.compose.MingColumn
import ming.compose.MingFramedApplication
import ming.compose.MingText
import ming.sdk.MingFrames
import ming.sdk.MingLog

/** Probe-only modes: 1 persistent ticker, 2 persistent idle, 3 old bounded negative control. */
fun runProbe(mode: Int) {
    val ticks = mutableStateOf(0)
    var starts = 0
    var finalized = 0
    var disposed = 0
    var firstFrame = -1L
    var lastFrame = -1L
    val start = MingFrames.monotonicNanos()
    MingLog.event(9200, mode)
    val content: @Composable () -> Unit = {
        DisposableEffect(Unit) { onDispose { disposed++; MingLog.event(9208, disposed) } }
        LaunchedEffect(Unit) {
            starts++
            try {
                if (mode == 2) awaitCancellation()
                else while (true) {
                    val now = withFrameNanos { it }
                    if (firstFrame < 0) firstFrame = now
                    lastFrame = now
                    if (ticks.value < Int.MAX_VALUE) ticks.value++
                    if (ticks.value == 5000) MingLog.event(9201, ticks.value)
                }
            } finally { finalized++; MingLog.event(9207, finalized) }
        }
        MingColumn { MingText(if (mode == 2) "persistent idle" else "ticks=" + ticks.value) }
    }
    val result = if (mode == 3) MingFramedApplication.run(content)
        else MingFramedApplication.runPersistent(content)
    val end = MingFrames.monotonicNanos()
    val elapsed = if (start.code == 0 && end.code == 0) end.nanos!! - start.nanos!! else -1L
    MingLog.event(9202, ticks.value)
    MingLog.event(9203, result.frames)
    MingLog.event(9204, finalized)
    MingLog.event(9205, result.code)
    MingLog.event(9206, disposed)
    MingLog.event(9209, starts)
    fun words(lowCode: Int, value: Long) {
        MingLog.event(lowCode, value.toInt())
        MingLog.event(lowCode + 1, (value ushr 32).toInt())
    }
    words(9210, elapsed)
    words(9212, firstFrame)
    words(9214, lastFrame)
    MingLog.event(9216, start.code)
    MingLog.event(9217, end.code)
}
