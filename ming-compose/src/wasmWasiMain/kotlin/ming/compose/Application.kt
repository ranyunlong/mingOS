package ming.compose

import androidx.compose.runtime.Composable
import ming.sdk.MingInput
import ming.sdk.MingText as SdkText

public class MingAppResult(val code: Int, val frames: Int)

/** Synchronous Ming input -> Snapshot -> Compose -> SDK4 loop; no asynchronous clock is implied. */
public object MingApplication {
    public fun run(content: @Composable () -> Unit): MingAppResult = runSynchronous(4096, content)

    public fun runPersistent(content: @Composable () -> Unit): MingAppResult = runSynchronous(null, content)

    private fun runSynchronous(eventLimit: Int?, content: @Composable () -> Unit): MingAppResult {
        val composition = MingComposition()
        val renderer = MingRenderer()
        var width = 1100
        var height = 760
        var frames = 0
        var status = 0
        fun draw(): Int {
            val result = renderer.render(composition.root, width, height)
            if (result == 0 && frames < Int.MAX_VALUE) frames++
            return result
        }
        var failure: Throwable? = null
        fun rememberFailure(next: Throwable) {
            val original = failure
            if (original == null) failure = next else if (next !== original) original.addSuppressed(next)
        }
        try {
            composition.setContent(content)
            status = draw()
            if (status == 0) {
                var closed = false
                var turns = 0
                while (eventLimit == null || turns < eventLimit) {
                    if (eventLimit != null) turns++
                    val event = MingInput.next()
                    if (event.type == 0) { closed = true; break }
                    var resized = false
                    when (event.type) {
                        1 -> {
                            val w = event.a.coerceIn(1, 4096)
                            val requestedHeight = event.b.coerceIn(1, 4096)
                            val h = if (w.toLong() * requestedHeight > 4_194_304) 4_194_304 / w else requestedHeight
                            if (w != width || h != height) {
                                width = w
                                height = h
                                resized = true
                            }
                        }
                        4 -> renderer.click(event.a, event.b)
                    }
                    val changed = composition.recompose()
                    if (resized || changed) {
                        status = draw()
                        if (status != 0) break
                    }
                }
                if (status == 0 && !closed) status = SdkText.LIMIT
            }
        } catch (caught: Throwable) { rememberFailure(caught) } finally {
            try { composition.dispose() } catch (caught: Throwable) { rememberFailure(caught) }
            try {
                val cleanup = renderer.dispose()
                if (status == 0 && cleanup < 0) status = cleanup
            } catch (caught: Throwable) { rememberFailure(caught) }
        }
        failure?.let { throw it }
        return MingAppResult(status, frames)
    }
}
