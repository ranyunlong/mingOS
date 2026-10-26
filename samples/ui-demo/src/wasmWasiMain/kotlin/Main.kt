import ming.sdk.*

private const val MAX_INPUT_EVENTS = 16_384
private const val MAX_FRAME_PIXELS = 4_194_304
private const val MAX_CANDIDATES = 64_000_000L
private const val INK = 0xff203b37.toInt()
private const val MUTED = 0xff536b66.toInt()
private const val GREEN = 0xff147d72.toInt()
private const val AMBER = 0x99e7a54a.toInt()
private data class Label(val layout: TextLayout, val metrics: TextMetrics)

fun main() {
    val owned = ArrayList<TextLayout>()
    var frames = 0
    fun label(value: String, em: Int): Label? {
        val result = MingText.create(value, em.toFloat())
        val layout = result.layout
        if (result.code != 0 || layout == null) { MingLog.event(50, result.code); return null }
        owned.add(layout) // Own before metrics: failed measurement still closes this handle.
        val measured = layout.metrics()
        val metrics = measured.metrics
        if (measured.code != 0 || metrics == null) { MingLog.event(51, measured.code); return null }
        return Label(layout, metrics)
    }
    try {
        val title = label("圆角与层叠", 24) ?: return
        val subtitle = label("相同形状，两种边缘", 14) ?: return
        val button = label("切换叠放", 16) ?: return
        val hard = label("硬边圆角", 18) ?: return
        val smooth = label("平滑圆角", 18) ?: return
        val specimen = label("圆角示例", 16) ?: return
        val front = label("琥珀色在上", 14) ?: return
        val back = label("青绿色在上", 14) ?: return
        val note = label("半透明叠放 · 矩形裁剪", 14) ?: return
        val help = label("点击右上角切换 · Esc 退出", 14) ?: return
        val clipLabel = label("裁剪窗口", 12) ?: return
        var width = 1100
        var height = 760
        var reversed = false

        fun draw(): Int {
            val frame = MingFrame4(width, height)
            frame.rect(0, 0, width, height, 0xfff5f4ef.toInt())
            // Preserve the host viewport; reduce commands rather than changing frame dimensions.
            val detailed = width >= 480 && height >= 520 &&
                width.toLong() * height * 26 <= MAX_CANDIDATES
            if (width >= 480) frame.text(title.layout, 32f, 55f, INK)
            if (detailed) frame.text(subtitle.layout, 32f, 84f, MUTED)
            frame.rectAA(width - 184, 30, 160, 40, 0xff245e55.toInt(), 14)
            // Center using the immutable layout's fractional advance and baseline metrics.
            val buttonXQ6 = (width - 184) * 64 + (160 * 64 - button.metrics.advanceQ6) / 2
            val buttonBaselineQ6 = 50 * 64 + (button.metrics.ascentQ6 - button.metrics.descentQ6) / 2
            frame.textQ6(button.layout, buttonXQ6, buttonBaselineQ6, 0xffffffff.toInt())
            if (detailed) {
                frame.rectAA(24, 112, width - 48, height - 176, 0xffffffff.toInt(), 24)
                val columnWidth = (width - 100) / 2
                fun column(x: Int, aa: Boolean, heading: Label) {
                    fun shape(sx: Int, sy: Int, w: Int, h: Int, color: Int, radius: Int) {
                        if (aa) frame.rectAA(sx, sy, w, h, color, radius)
                        else frame.rect(sx, sy, w, h, color, radius)
                    }
                    shape(x, 136, columnWidth, height - 240, 0xffeef2f1.toInt(), 20)
                    frame.text(heading.layout, (x + 16).toFloat(), 170f, INK)
                    val clipX = x + 16
                    frame.clip(clipX, 190, columnWidth - 32, height - 366)
                    fun green() {
                        shape(clipX + 4, 212, columnWidth - 48, 104, GREEN, 28)
                        frame.text(specimen.layout, (clipX + 20).toFloat(), 265f, 0xffffffff.toInt())
                    }
                    fun amber() {
                        // Extends beyond the half-open clip on the right (also below it at minimum height).
                        shape(clipX + 48, 258, columnWidth - 48, 132, AMBER, 36)
                    }
                    if (reversed) { amber(); green() } else { green(); amber() }
                    frame.clip(0, 0, width, height) // op4 replaces the clip, never pushes or pops.
                    frame.text(clipLabel.layout, (x + 16).toFloat(), (height - 150).toFloat(), MUTED)
                    frame.text(if (reversed) back.layout else front.layout,
                        (x + 16).toFloat(), (height - 128).toFloat(), INK)
                }
                column(40, false, hard)
                column(60 + columnWidth, true, smooth)
                frame.text(note.layout, 40f, (height - 84).toFloat(), MUTED)
                frame.text(help.layout, 32f, (height - 26).toFloat(), MUTED)
            } else {
                // At most 14 commands even at 4096x1024 / 2048x2048 (the full pixel limit).
                // A short viewport keeps the button, two comparison rows and order label.
                frame.rectAA(24, 88, width - 48, height - 128, 0xffffffff.toInt(), 24)
                val clipX = 40
                val clipY = 104
                val clipWidth = width - 80
                val clipHeight = height - 168
                val rowHeight = minOf(96, (clipHeight - 8) / 2)
                frame.clip(clipX, clipY, clipWidth, clipHeight)
                fun rows() {
                    frame.rect(clipX + 4, clipY, clipWidth - 8, rowHeight, GREEN, 20)
                    frame.rectAA(clipX + 4, clipY + rowHeight + 8,
                        clipWidth - 8, rowHeight, GREEN, 20)
                    fun rowLabel(label: Label, y: Int) {
                        val baselineQ6 = y * 64 + rowHeight * 32 +
                            (label.metrics.ascentQ6 - label.metrics.descentQ6) / 2
                        frame.textQ6(label.layout, (clipX + 16) * 64, baselineQ6, 0xffffffff.toInt())
                    }
                    rowLabel(hard, clipY)
                    rowLabel(smooth, clipY + rowHeight + 8)
                }
                fun amber() {
                    frame.rectAA(clipX + 32, clipY + rowHeight / 2,
                        clipWidth, rowHeight * 2 + 8, AMBER, 28)
                }
                if (reversed) { amber(); rows() } else { rows(); amber() }
                frame.clip(0, 0, width, height)
                frame.text(if (reversed) back.layout else front.layout,
                    40f, (height - 52).toFloat(), INK)
                if (height >= 360) frame.text(help.layout, 32f, (height - 26).toFloat(), MUTED)
            }
            val result = frame.submit()
            if (result == 0) frames++ else MingLog.event(52, result)
            return result
        }

        if (draw() != 0) return
        for (index in 0 until MAX_INPUT_EVENTS) {
            val event = MingInput.next()
            when (event.type) {
                0 -> return
                1 -> {
                    // Native events already contain the host viewport: preserve every legal size.
                    // Malformed headless events still normalize to SDK edge/pixel bounds.
                    val nextWidth = event.a.coerceIn(320, 4096)
                    var nextHeight = event.b.coerceIn(240, 4096)
                    if (nextWidth.toLong() * nextHeight > MAX_FRAME_PIXELS) {
                        nextHeight = MAX_FRAME_PIXELS / nextWidth
                    }
                    if (nextWidth == width && nextHeight == height) continue
                    width = nextWidth
                    height = nextHeight
                }
                4 -> {
                    if (event.a !in width - 184 until width - 24 || event.b !in 30 until 70) continue
                    reversed = !reversed
                }
                else -> continue // Includes idle (8); no redraw or new layout allocation.
            }
            if (draw() != 0) return
        }
        MingLog.event(54, MingText.LIMIT)
    } finally {
        for (index in owned.indices.reversed()) {
            val result = owned[index].close()
            if (result < 0) MingLog.event(53, result)
        }
        MingLog.event(40, frames)
    }
}
