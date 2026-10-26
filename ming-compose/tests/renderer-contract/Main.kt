package ming.compose

import androidx.compose.runtime.mutableStateOf
import ming.sdk.MingInput
import ming.sdk.MingLog

fun main() {
    val mode = MingInput.next().a
    val count = mutableStateOf(0)
    val composition = MingComposition()
    val renderer = MingRenderer()
    try {
        composition.setContent {
            MingColumn(padding = if (mode == 11 && count.value > 0) 64 else 24, gap = 12) {
                if (mode == 8 || mode == 9) {
                    repeat(if (mode == 9) 17 else 16) { MingText(count.value.toString() + ":" + it) }
                } else {
                    MingText("真实 Compose", emPx = 18)
                    MingButton("count=" + count.value) { count.value++ }
                }
            }
        }
        val initial = renderer.render(composition.root, 640, 480)
        val expected = when (mode) { 1 -> -4; 2 -> -5; 3 -> -2; 4 -> -4; 9 -> -4; else -> 0 }
        check(initial == expected) { "Initial render: $initial != $expected" }
        if (expected != 0) {
            check(!renderer.click(30, 75)) { "Rejected frame installed hit targets" }
        } else if (mode == 8) {
            count.value = 1
            check(composition.recompose())
            check(renderer.render(composition.root, 640, 480) == -4)
        } else {
            check(!renderer.click(-1, 75))
            check(renderer.click(30, 75))
            check(count.value == 1 && composition.recompose())
            val next = renderer.render(composition.root, 640, 480)
            check(next == when (mode) { 6 -> -5; 7 -> -2; 11 -> -4; else -> 0 })
            if (mode == 6 || mode == 7 || mode == 11) {
                check(renderer.click(30, 75)) { "Rejected replacement lost previous hit targets" }
                check(count.value == 2)
                if (mode == 11) check(!renderer.click(75, 120)) { "Rejected frame installed new geometry" }
            } else {
                check(renderer.render(composition.root, 640, 480) == 0)
                check(renderer.render(composition.root, 0, 480) == -2)
                check(renderer.render(composition.root, 1, 1) == 0)
                check(!renderer.click(30, 75)) { "Tiny viewport retained out-of-frame target" }
            }
        }
    } finally {
        try { composition.dispose() } finally {
            val closed = renderer.dispose()
            check(closed == if (mode == 5) -2 else 0)
            check(renderer.dispose() == 0)
        }
    }
    check(composition.isDisposed)
    MingLog.event(398, mode)
    MingLog.event(399, 1)
}
