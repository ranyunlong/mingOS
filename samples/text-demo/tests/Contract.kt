import ming.sdk.*

// Compiled as a separate executable only with -PtextDemoContract, never into the demo.
fun main() {
    for (em in floatArrayOf(Float.NaN, Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY,
        7.75f, 96.25f, 12.125f)) {
        val result = MingText.create("rejected", em)
        check(result.code == -2 && result.layout == null)
    }
    for (em in intArrayOf(Int.MIN_VALUE, 511, 769, 6145, Int.MAX_VALUE)) {
        val result = MingText.createQ6("rejected", em)
        check(result.code == -2 && result.layout == null)
    }
    for (value in arrayOf("a".repeat(513), "中".repeat(171), "\uD83D\uDE00".repeat(129))) {
        val result = MingText.create(value, 16f)
        check(result.code == -2 && result.layout == null)
    }
    val empty = MingText.create("", 8f)
    val full = MingText.create("中".repeat(170) + "ab", 96f, allowMissingGlyphs = true)
    val quarter = MingText.createQ6("AV", 1040)
    check(empty.code == 0 && full.code == 0 && quarter.code == 0)
    val a = checkNotNull(empty.layout)
    val b = checkNotNull(full.layout)
    val c = checkNotNull(quarter.layout)
    val measured = a.metrics()
    check(measured.code == 0)
    val m = checkNotNull(measured.metrics)
    check(m.advanceQ6 == 101 && m.ascentQ6 == 202 && m.descentQ6 == 303)
    check(m.gapQ6 == 404 && m.lineHeightQ6 == 909)
    check(m.inkLeftQ6 == -17 && m.inkTopQ6 == -202 && m.inkRightQ6 == 83 && m.inkBottomQ6 == 303)
    check(m.glyphCount == 7 && m.statusFlags == 1 && m.fontRevision == 23)
    check(m.advance == 101 / 64f && m.ascent == 202 / 64f && m.descent == 303 / 64f)
    check(m.gap == 404 / 64f && m.lineHeight == 909 / 64f)
    check(m.inkLeft == -17 / 64f && m.inkTop == -202 / 64f)
    check(m.inkRight == 83 / 64f && m.inkBottom == 303 / 64f && m.hasMissingGlyphs)

    for ((w, h) in arrayOf(0 to 1, 1 to 0, -1 to 100, 4097 to 1, 4096 to 4096)) {
        check(MingFrame3(w, h).submit() == -2)
    }
    fun invalid(block: (MingFrame3) -> Unit) {
        val frame = MingFrame3(64, 64)
        frame.rect(0, 0, 64, 64, -1)
        block(frame)
        check(frame.submit() == -2)
    }
    for (value in floatArrayOf(Float.NaN, Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY,
        1_000_001f, -1_000_001f)) {
        invalid { it.text(a, value, 0f, -1) }
        invalid { it.text(a, 0f, value, -1) }
    }
    for (value in intArrayOf(Int.MIN_VALUE, -64_000_001, 64_000_001, Int.MAX_VALUE)) {
        invalid { it.textQ6(a, value, 0, -1) }
        invalid { it.textQ6(a, 0, value, -1) }
    }
    invalid { it.rect(1_000_001, 0, 1, 1, -1) }
    invalid { it.rect(0, -1_000_001, 1, 1, -1) }
    invalid { it.rect(0, 0, 0, 1, -1) }
    invalid { it.rect(0, 0, 1, 1, -1, 65) }
    invalid { it.image(0, 0, 0, 1, 1) }
    invalid { it.image(1, 0, 0, 1_000_001, 1) }
    invalid { it.clip(0, 0, -1, 0) }
    invalid { repeat(256) { _ -> it.rect(0, 0, 1, 1, -1) } }

    val boundary = MingFrame3(4096, 1024)
    boundary.rect(-1_000_000, 1_000_000, 1_000_000, 1_000_000, -1, 64)
    boundary.image(1, 1_000_000, -1_000_000, 1, 1)
    boundary.clip(0, 0, 0, 0)
    boundary.text(a, -1_000_000f, 1_000_000f, 0x80ffffff.toInt())
    boundary.text(a, 1.125f, -1.25f, -1)
    boundary.textQ6(b, 64_000_000, -64_000_000, -1)
    check(boundary.submit() == 0)
    val maximum = MingFrame3(1, 1)
    repeat(256) { maximum.rect(0, 0, 1, 1, -1) }
    check(maximum.submit() == 0)
    check(a.close() == 0)
    check(a.close() == -2)
    val closedMetrics = a.metrics()
    check(closedMetrics.code == -2 && closedMetrics.metrics == null)
    val stale = MingFrame3(1, 1)
    stale.textQ6(a, 0, 0, -1)
    check(stale.submit() == -2)
    check(b.close() == 0 && c.close() == 0)
    MingLog.event(60, 0)
}
