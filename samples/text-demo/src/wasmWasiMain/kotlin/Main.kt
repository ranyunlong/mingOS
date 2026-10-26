import ming.sdk.*
import kotlin.math.max

private const val SPECIMEN = "MingOS 中文，标点。AV fi é A\u0301"
private const val MAX_INPUT_EVENTS = 16_384
private const val INK = 0xffe6edf5.toInt()
private const val MUTED = 0xff9aabba.toInt()
private const val ACCENT = 0xffa8d9bd.toInt()
private data class Label(val layout: TextLayout, val metrics: TextMetrics)
private data class Row(val sample: Label, val size: Label, val measure: Label, val bounds: Label)
private fun floorQ6(value: Int): Int = value shr 6
private fun ceilQ6(value: Int): Int = ((value.toLong() + 63) shr 6).toInt()

fun main() {
    val owned = ArrayList<TextLayout>()
    var frames = 0
    fun label(value: String, em: Int): Label? {
        val created = MingText.create(value, em.toFloat())
        val layout = created.layout
        if (created.code != 0 || layout == null) { MingLog.event(50, created.code); return null }
        // Own the handle before measuring, so a metrics failure also reaches finally.
        owned.add(layout)
        val measured = layout.metrics()
        val metrics = measured.metrics
        if (measured.code != 0 || metrics == null) { MingLog.event(51, measured.code); return null }
        return Label(layout, metrics)
    }
    try {
        val title = label("MingOS 中文 · Typography", 24) ?: return
        val fitButton = label("FIT / F", 16) ?: return
        val help = label("12 / 16 / 24 / 48 px · F fit · 1 left · scroll / drag · Esc close", 12) ?: return
        val clipTitle = label("Clip + painter order · same 24 px layout before / after translucent overlay", 12) ?: return
        val rows = ArrayList<Row>()
        for (em in intArrayOf(12, 16, 24, 48)) {
            val sample = label(SPECIMEN, em) ?: return
            val m = sample.metrics
            val size = label("$em px", 12) ?: return
            val measure = label("advance ${m.advance} px (${m.advanceQ6} Q6)  asc ${m.ascentQ6}  desc ${m.descentQ6}  gap ${m.gapQ6}  line ${m.lineHeightQ6}", 12) ?: return
            val bounds = label("ink [${m.inkLeftQ6}, ${m.inkTopQ6}, ${m.inkRightQ6}, ${m.inkBottomQ6}] Q6  glyphs ${m.glyphCount}  flags ${m.statusFlags}  rev ${m.fontRevision}", 12) ?: return
            rows.add(Row(sample, size, measure, bounds))
        }
        val gutter = 68
        var sheetWidth = max(700, ceilQ6(clipTitle.metrics.advanceQ6))
        var rowsHeight = 0
        for (row in rows) {
            sheetWidth = max(sheetWidth, gutter + max(ceilQ6(row.sample.metrics.advanceQ6),
                max(ceilQ6(row.measure.metrics.advanceQ6), ceilQ6(row.bounds.metrics.advanceQ6))))
            rowsHeight += ceilQ6(row.sample.metrics.lineHeightQ6) + 52
        }
        val sheetHeight = rowsHeight + 120
        var width = 1100
        var height = 760
        var panX = 0
        var panY = 0
        var fitMode = true
        fun constrainPan() {
            panX = panX.coerceIn(-max(0, sheetWidth - (width - 48)), 0)
            panY = panY.coerceIn(-max(0, sheetHeight - (height - 128)), 0)
        }
        fun fit() { fitMode = true; panX = 0; panY = 0 }
        fun draw(): Int {
            val frame = MingFrame3(width, height)
            val viewWidth = width - 48
            val viewHeight = height - 128
            // Fit recenters measured content without changing the four physical font sizes.
            val x = 24 + (if (fitMode) max(0, (viewWidth - sheetWidth) / 2) else 0) + panX
            var y = 80 + panY
            frame.rect(0, 0, width, height, 0xff11171e.toInt())
            frame.rect(0, 0, width, 64, 0xff1b252f.toInt())
            frame.clip(16, 0, (width - 158).coerceAtLeast(0), 64)
            frame.text(title.layout, 24f, 40f, INK)
            frame.clip(0, 0, width, height)
            frame.rect(width - 126, 16, 102, 34, 0xff304c42.toInt(), 6)
            frame.text(fitButton.layout, (width - 112).toFloat(), 38f, ACCENT)
            frame.clip(24, 80, viewWidth, viewHeight)
            for (row in rows) {
                val m = row.sample.metrics
                val textX = x + gutter
                val baselineQ6 = y * 64 + m.ascentQ6
                val inkX = floorQ6(textX * 64 + m.inkLeftQ6)
                val inkY = floorQ6(baselineQ6 + m.inkTopQ6)
                val inkW = ceilQ6(textX * 64 + m.inkRightQ6) - inkX
                val inkH = ceilQ6(baselineQ6 + m.inkBottomQ6) - inkY
                if (inkW > 0 && inkH > 0) frame.rect(inkX, inkY, inkW, inkH, 0xff233341.toInt())
                // Baseline and advance are distinct from the half-open ink rectangle above.
                frame.rect(textX, floorQ6(baselineQ6), max(1, ceilQ6(m.advanceQ6)), 1, 0xff527769.toInt())
                frame.textQ6(row.size.layout, x * 64, y * 64 + row.size.metrics.ascentQ6, ACCENT)
                frame.textQ6(row.sample.layout, textX * 64, baselineQ6, INK)
                val detailY = y + ceilQ6(m.lineHeightQ6) + 4
                frame.textQ6(row.measure.layout, textX * 64, detailY * 64 + row.measure.metrics.ascentQ6, MUTED)
                frame.textQ6(row.bounds.layout, textX * 64, (detailY + 17) * 64 + row.bounds.metrics.ascentQ6, MUTED)
                y += ceilQ6(m.lineHeightQ6) + 52
            }
            frame.textQ6(clipTitle.layout, x * 64, y * 64 + clipTitle.metrics.ascentQ6, MUTED)
            y += 24
            val clipWidth = sheetWidth - gutter
            frame.rect(x + gutter, y, clipWidth, 84, 0xff1b252f.toInt())
            // Intersect explicitly with the viewport: op4 replaces rather than stacks clips.
            val clipX = max(24, x + gutter)
            val clipY = max(80, y)
            val clipRight = minOf(width - 24, x + gutter + clipWidth)
            val clipBottom = minOf(height - 48, y + 84)
            frame.clip(clipX, clipY, max(0, clipRight - clipX), max(0, clipBottom - clipY))
            val shared = rows[2].sample
            frame.textQ6(shared.layout, (x + gutter - 12) * 64, y * 64 + shared.metrics.ascentQ6, INK)
            frame.rect(x + gutter + 80, y, minOf(260, clipWidth), 84, 0x8060a8ed.toInt())
            frame.textQ6(shared.layout, (x + gutter - 12) * 64,
                (y + 42) * 64 + shared.metrics.ascentQ6, INK)
            frame.clip(0, 0, width, height)
            frame.rect(0, height - 40, width, 40, 0xff1b252f.toInt())
            frame.text(help.layout, 24f, (height - 16).toFloat(), MUTED)
            val result = frame.submit()
            if (result == 0) frames++ else MingLog.event(52, result)
            return result
        }
        if (draw() < 0) return
        for (index in 0 until MAX_INPUT_EVENTS) {
            val event = MingInput.next()
            when (event.type) {
                0 -> return
                1 -> {
                    width = event.a.coerceIn(320, 4096)
                    height = event.b.coerceIn(240, 4096)
                    if (width.toLong() * height > 4_194_304) height = 4_194_304 / width
                    constrainPan()
                }
                2 -> {
                    panY = (panY.toLong() - event.a.toLong()).coerceIn(
                        -max(0, sheetHeight - (height - 128)).toLong(), 0L).toInt()
                }
                3 -> {
                    panX = (panX.toLong() + event.a.toLong()).coerceIn(
                        -max(0, sheetWidth - (width - 48)).toLong(), 0L).toInt()
                    panY = (panY.toLong() + event.b.toLong()).coerceIn(
                        -max(0, sheetHeight - (height - 128)).toLong(), 0L).toInt()
                }
                4 -> {
                    if (event.a !in width - 126 until width - 24 || event.b !in 16 until 50) continue
                    fit()
                }
                6 -> { fitMode = false; panX = 0; panY = 0 }
                7 -> fit()
                else -> continue // Includes event 8 (idle); it must not redraw or allocate layouts.
            }
            if (draw() < 0) return
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
