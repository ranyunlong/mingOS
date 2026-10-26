package ming.compose

import androidx.compose.runtime.Composable
import androidx.compose.runtime.snapshots.Snapshot
import ming.sdk.InputEvent
import ming.sdk.MingTextInput
import ming.sdk.TextInputEvent
import ming.sdk.TextInputKind

/** One ordered SDK6 receiver per run. Input context follows FIFO intents, not painted hints. */
internal class TextInputReceiver(private val renderer: MingRenderer) {
    private var sequence = 0L
    private var epoch = 0L
    private var sourceFrame = 0L
    private var pendingFocusBoundary = 0L
    private var logicalFocusBoundary = 0L
    private var announcedField: TextFieldState? = null
    private var announcedEpoch = 0L

    fun drain(input: InputEvent? = null): Int {
        // INPUT(type=1) is also the Host's same-size rich wake. Only the kernel can decide
        // whether its normalized, adopted viewport actually changed.
        repeat(4096) {
            val polled = MingTextInput.poll()
            if (polled.code != 0) return polled.code
            val event = polled.event ?: return 0
            val code = receive(event)
            if (code != 0) return code
        }
        return -4
    }

    fun viewportChanged() { renderer.cancelInput() }

    fun receive(event: TextInputEvent): Int {
        if (event.sequence <= sequence || event.epoch < epoch) return 0
        // Only spatial input requires a current source geometry. A fresh ordered text/key
        // packet may use the same source as its press, before any newer frame is submitted.
        if (event.kind == TextInputKind.POINTER && event.epoch == epoch && event.frameSequence < sourceFrame) return 0
        val newEpoch = event.epoch > epoch
        val focusIntent = (event.kind == TextInputKind.POINTER && event.c == 1) ||
            (event.kind == TextInputKind.KEY && event.a == 9 && event.b == 1 && event.modifiers and 14 == 0)
        val fencedIntent = focusIntent && pendingFocusBoundary == event.epoch
        pendingFocusBoundary = 0L
        if (newEpoch) { renderer.cancelInput(); sourceFrame = 0 }
        sequence = event.sequence
        epoch = event.epoch
        if (event.frameSequence > 0) sourceFrame = maxOf(sourceFrame, event.frameSequence)
        val focusBefore = renderer.focusVersion
        val result = when (event.kind) {
            TextInputKind.CANCEL -> {
                renderer.cancelInput(event.a == 4)
                if (newEpoch && event.a == 1) pendingFocusBoundary = event.epoch
                0
            }
            TextInputKind.POINTER -> renderer.pointer(event.a, event.b, event.c, event.modifiers)
            else -> {
                if (event.kind == TextInputKind.KEY && event.a == 9 && event.b == 1 && event.modifiers and 14 == 0) {
                    renderer.focusNext(event.modifiers and 1 != 0)
                } else {
                    // The Host fences old captured Native IME epochs before focus intents.
                    // Already queued old commits precede that boundary and are applied in FIFO.
                    Snapshot.withMutableSnapshot { renderer.focusedState?.let { it.value = it.value.apply(event) } }
                }
                0
            }
        }
        if (renderer.focusVersion != focusBefore) logicalFocusBoundary = if (fencedIntent) event.epoch else 0L
        return result
    }

    /** Hints describe only the accepted frame. They never decide the owner of queued text. */
    fun afterDraw(): Int {
        val rect = renderer.focusRect
        val next = if (rect == null) null else renderer.publishedFocus
        if (announcedField != null && announcedField !== next) {
            // Reuse an observed Host Cancel -> focus-intent boundary. Releasing again would
            // enqueue another Cancel and erase fresh preedit already accepted for the new field.
            if (next == null || logicalFocusBoundary <= announcedEpoch) {
                val released = MingTextInput.textFocus(false)
                if (released != 0) return released
            }
            announcedField = null
            if (next == null) return 0
        }
        if (next !== renderer.focusedState) return 0
        val result = if (next == null || rect == null) MingTextInput.textFocus(false)
            else MingTextInput.textFocus(true, rect.x, rect.y, rect.width, rect.height)
        if (result == 0) { announcedField = next; announcedEpoch = epoch }
        return result
    }

    fun cleanup(): Int {
        renderer.cancelInput(clearFocus = true)
        announcedField = null
        return MingTextInput.textFocus(false)
    }
}

public object MingTextApplication {
    public fun run(content: @Composable () -> Unit): MingAppResult {
        val renderer = MingRenderer()
        val receiver = TextInputReceiver(renderer)
        return runFramed(null, renderer,
            onInput = receiver::drain, afterDraw = receiver::afterDraw, cleanup = receiver::cleanup,
            onViewportChanged = receiver::viewportChanged, content = content)
    }
}
