import ming.sdk.*

private const val MAX_PIXELS = 4_194_304
private const val MAX_CANDIDATES = 64_000_000L
private const val MAX_EVENTS = 16_384
private data class Label(val layout: TextLayout, val metrics: TextMetrics)

fun main() {
    val styles = ArrayList<ShadowStyle>()
    val layouts = ArrayList<TextLayout>()
    var frames = 0
    fun style(radius: Int, blur: Int): ShadowStyle? {
        val result = MingShadows.create(radius, blur)
        val value = result.style
        if (result.code != 0 || value == null) { MingLog.event(61, result.code); return null }
        styles.add(value)
        return value
    }
    fun label(text: String, em: Int): Label? {
        val result = MingText.create(text, em.toFloat())
        val layout = result.layout
        if (result.code != 0 || layout == null) { MingLog.event(62, result.code); return null }
        layouts.add(layout) // Own before measurement so a metrics failure also closes it.
        val measured = layout.metrics()
        val metrics = measured.metrics
        if (measured.code != 0 || metrics == null) { MingLog.event(63, measured.code); return null }
        return Label(layout, metrics)
    }
    try {
        val panelStyle = style(24, 12) ?: return
        val cardStyle = style(16, 6) ?: return
        val title = label("有界阴影", 24) ?: return
        val move = label("移动", 16) ?: return
        val color = label("变色", 16) ?: return
        val help = label("移动与变色复用样式 · 放大窗口观察伸展", 14) ?: return
        val card = label("同一样式", 16) ?: return
        var width = 1100
        var height = 760
        var moved = false
        var colored = false

        fun draw(): Int {
            val frame = MingFrame4(width, height)
            frame.rect(0, 0, width, height, 0xfff1f3f0.toInt())
            if (width >= 320 && height >= 240) {
                fun button(x: Int, text: Label) {
                    frame.rectAA(x, 24, 88, 36, 0xff245e55.toInt(), 12)
                    frame.textQ6(text.layout, x * 64 + (88 * 64 - text.metrics.advanceQ6) / 2,
                        42 * 64 + (text.metrics.ascentQ6 - text.metrics.descentQ6) / 2, -1)
                }
                button(24, move)
                button(128, color)
                if (width >= 480) frame.text(title.layout, 248f, 50f, 0xff203b37.toInt())
                val x = 48 + if (moved) 16 else 0
                val y = 128 + if (moved) 8 else 0
                val w = width - 112
                val h = height - 208
                val shadowColor = if (colored) 0x784c4290 else 0x58304440
                frame.shadow(panelStyle, x, y + 8, w, h, shadowColor)
                frame.rectAA(x, y, w, h, 0xfffcfdfb.toInt(), 24)
                // At most 15 commands even at the full 4M-pixel viewport. Preserve the
                // real viewport; reduce optional scene detail if the candidate cap requires it.
                if (width >= 640 && height >= 480 && width.toLong() * height * 15 <= MAX_CANDIDATES) {
                    val cardWidth = (w - 72) / 2
                    val cardHeight = (h - 96).coerceAtMost(160)
                    for (index in 0..1) {
                        val cx = x + 24 + index * (cardWidth + 24)
                        val cy = y + 48
                        frame.shadow(cardStyle, cx, cy + 8, cardWidth, cardHeight, 0x48213532)
                        frame.rectAA(cx, cy, cardWidth, cardHeight,
                            if (index == 0) 0xffe2efea.toInt() else 0xfff1e5ce.toInt(), 16)
                        frame.text(card.layout, (cx + 16).toFloat(), (cy + 38).toFloat(), 0xff245e55.toInt())
                    }
                }
                frame.text(help.layout, 24f, (height - 20).toFloat(), 0xff536b66.toInt())
            } else {
                // Keep even 1x1 and very narrow legal native viewports unchanged.
                val w = (width - 32).coerceAtLeast(1)
                val h = (height - 32).coerceAtLeast(1)
                val x = if (width > 32) 16 else 0
                val y = if (height > 32) 16 else 0
                frame.shadow(panelStyle, x, y + 8, w, h, 0x58304440)
                frame.rectAA(x, y, w, h, 0xfffcfdfb.toInt(), 24)
            }
            val result = frame.submit()
            if (result == 0) frames++ else MingLog.event(64, result)
            return result
        }

        if (draw() != 0) return
        for (index in 0 until MAX_EVENTS) {
            val event = MingInput.next()
            when (event.type) {
                0 -> return
                1 -> {
                    // Every legal host viewport survives exactly; normalize only invalid input.
                    val w = event.a.coerceIn(1, 4096)
                    var h = event.b.coerceIn(1, 4096)
                    if (w.toLong() * h > MAX_PIXELS) h = MAX_PIXELS / w
                    if (w == width && h == height) continue
                    width = w
                    height = h
                }
                4 -> {
                    if (width < 320 || height < 240 || event.b !in 24 until 60) continue
                    when (event.a) {
                        in 24 until 112 -> moved = !moved
                        in 128 until 216 -> colored = !colored
                        else -> continue
                    }
                }
                else -> continue
            }
            if (draw() != 0) return
        }
        MingLog.event(66, MingText.LIMIT)
    } finally {
        for (layout in layouts.asReversed()) {
            val result = layout.close()
            if (result < 0) MingLog.event(65, result)
        }
        for (style in styles.asReversed()) {
            val result = style.close()
            if (result < 0) MingLog.event(65, result)
        }
        MingLog.event(40, frames)
    }
}
