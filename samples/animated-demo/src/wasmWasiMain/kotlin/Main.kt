import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import kotlinx.coroutines.delay
import ming.compose.MingButton
import ming.compose.MingColumn
import ming.compose.MingFramedApplication
import ming.compose.MingText
import ming.sdk.MingLog

private const val INITIAL_ALPHA = 32
private const val FADE_RGB = 0x245e55
private const val START_DELAY_MS = 80L
private const val DURATION_NS = 400_000_000L
private const val MAX_FRAME_STEPS = 64

fun main() {
    val result = MingFramedApplication.runPersistent {
        var generation by remember { mutableStateOf(0) }
        var alpha by remember { mutableStateOf(INITIAL_ALPHA) }
        var completed by remember { mutableStateOf(false) }

        LaunchedEffect(generation) {
            alpha = INITIAL_ALPHA
            completed = false
            delay(START_DELAY_MS)
            val started = withFrameNanos { it }
            check(started >= 0L) { "Negative frame timestamp" }
            var previous = started
            var reachedEnd = false
            for (step in 0 until MAX_FRAME_STEPS) {
                val now = withFrameNanos { it }
                check(now >= previous) { "Frame clock moved backwards" }
                previous = now
                val elapsed = (now - started).coerceAtMost(DURATION_NS)
                alpha = INITIAL_ALPHA + ((255 - INITIAL_ALPHA).toLong() * elapsed / DURATION_NS).toInt()
                if (elapsed == DURATION_NS) {
                    reachedEnd = true
                    break
                }
            }
            check(reachedEnd) { "Animation exhausted its bounded frame steps" }
            // No completion write in finally: a canceled generation must not publish a final state.
            alpha = 255
            completed = true
        }

        MingColumn(padding = 24, gap = 12) {
            MingText("MingOS · 帧动画", emPx = 24)
            MingButton("重新播放") { generation++ }
            MingText("渐显文字", emPx = 32, color = (alpha shl 24) or FADE_RGB)
            MingText(if (completed) "动画完成" else "动画进行中")
        }
    }
    MingLog.event(if (result.code == 0) 40 else 60, if (result.code == 0) result.frames else result.code)
}
