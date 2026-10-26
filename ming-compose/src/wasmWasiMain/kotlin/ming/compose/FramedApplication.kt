package ming.compose

import androidx.compose.runtime.Composable
import ming.sdk.MingFrameKind
import ming.sdk.MingFrames

private class FrameHostFailure(val code: Int) : RuntimeException("MING_HOST_FRAME_ERROR: $code")
private fun hostNanos(): Long {
    val read = MingFrames.monotonicNanos()
    return read.nanos?.takeIf { read.code == 0 }
        ?: throw FrameHostFailure(if (read.code < 0) read.code else MingFrames.INVALID)
}

/** SDK5 asynchronous Compose entry; the existing synchronous SDK4 entry remains unchanged. */
public object MingFramedApplication {
    public fun run(content: @Composable () -> Unit): MingAppResult = runFramed(4096, content = content)

    public fun runPersistent(content: @Composable () -> Unit): MingAppResult = runFramed(null, content = content)
}

/** One cleanup owner for SDK5/6; callbacks themselves introduce no input namespace dependency. */
internal fun runFramed(
    eventLimit: Int?,
    renderer: MingRenderer = MingRenderer(),
    onInput: ((ming.sdk.InputEvent?) -> Int)? = null,
    afterDraw: (() -> Int)? = null,
    cleanup: (() -> Int)? = null,
    onViewportChanged: (() -> Unit)? = null,
    content: @Composable () -> Unit,
): MingAppResult {
    var owned: FramedComposition? = null
    var failure: Throwable? = null
    var status = 0
    var frames = 0
    var width = 1100
    var height = 760
    var layoutDirty = true
    var paintedRevision = -1L
    fun rememberFailure(next: Throwable) {
        val original = failure
        if (original == null) failure = next else if (next !== original) original.addSuppressed(next)
    }
    try {
        val runtime = FramedComposition(hostNanos(), ::hostNanos)
        owned = runtime
        runtime.setContent(content)
        fun draw(): Int {
            val result = renderer.render(runtime.root, width, height)
            if (result == 0) {
                if (frames < Int.MAX_VALUE) frames++
                paintedRevision = runtime.revision
                layoutDirty = false
                val hint = afterDraw?.invoke() ?: 0
                if (hint != 0) throw FrameHostFailure(hint)
            }
            return result
        }
        status = draw()
        if (status == 0) {
            var closed = false
            var turns = 0
            while (eventLimit == null || turns < eventLimit) {
                if (eventLimit != null) turns++
                runtime.pump()
                val inputStatus = onInput?.invoke(null) ?: 0
                if (inputStatus != 0) throw FrameHostFailure(inputStatus)
                runtime.pump()
                val result = MingFrames.wait(
                    runtime.wantsFrame || layoutDirty || runtime.revision != paintedRevision,
                    runtime.nextDeadlineNanos,
                )
                if (result.code != 0) throw FrameHostFailure(result.code)
                val event = result.event
                if (event == null) {
                    runtime.advance(hostNanos(), false)
                    continue
                }
                if (event.kind == MingFrameKind.CLOSE || event.kind == MingFrameKind.CANCEL) {
                    closed = true
                    if (event.kind == MingFrameKind.CANCEL) status = MingFrames.IO
                    break
                }
                runtime.advance(event.nowNanos, event.kind == MingFrameKind.FRAME)
                if (event.kind == MingFrameKind.INPUT) {
                    val input = checkNotNull(event.input)
                    when (input.type) {
                        1 -> {
                            val w = input.a.coerceIn(1, 4096)
                            val wanted = input.b.coerceIn(1, 4096)
                            val h = if (w.toLong() * wanted > 4_194_304) 4_194_304 / w else wanted
                            if (w != width || h != height) {
                                width = w; height = h; layoutDirty = true
                                onViewportChanged?.invoke()
                            }
                        }
                        4 -> if (onInput == null) renderer.click(input.a, input.b)
                    }
                    val inputStatus = onInput?.invoke(input) ?: 0
                    if (inputStatus != 0) throw FrameHostFailure(inputStatus)
                    runtime.pump()
                }
                if (event.kind == MingFrameKind.FRAME &&
                    (layoutDirty || runtime.revision != paintedRevision)) {
                    status = draw()
                    if (status != 0) break
                }
            }
            if (status == 0 && !closed) status = MingFrames.LIMIT
        }
    } catch (caught: Throwable) { rememberFailure(caught) }
    finally {
        try {
            val cleanupStatus = cleanup?.invoke() ?: 0
            if (cleanupStatus != 0) throw FrameHostFailure(cleanupStatus)
        } catch (caught: Throwable) { rememberFailure(caught) }
        try { owned?.dispose() } catch (caught: Throwable) { rememberFailure(caught) }
        try {
            val cleanup = renderer.dispose()
            if (status == 0 && cleanup < 0) status = cleanup
        } catch (caught: Throwable) { rememberFailure(caught) }
    }
    val problem = failure
    if (problem is FrameHostFailure && problem.suppressedExceptions.isEmpty()) {
        if (status == 0) status = problem.code
    } else if (problem != null) throw problem
    return MingAppResult(status, frames)
}
