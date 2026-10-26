import ming.sdk.*

fun main() {
    for (r in intArrayOf(Int.MIN_VALUE, -1, 65, Int.MAX_VALUE)) {
        check(MingShadows.create(r, 0).code == -2)
    }
    for (b in intArrayOf(Int.MIN_VALUE, -1, 17, Int.MAX_VALUE)) {
        check(MingShadows.create(0, b).code == -2)
    }
    MingLog.event(70, 0) // No invalid style reaches the host.
    val styles = ArrayList<ShadowStyle>()
    try {
        repeat(8) { styles.add(checkNotNull(MingShadows.create(it * 8, it * 2).style)) }
        check(MingShadows.create(64, 16).code == -4)
        val style = styles[0]
        fun invalid(block: (MingFrame4) -> Unit) {
            val f = MingFrame4(64, 64)
            block(f)
            f.shadow(style, 0, 0, 1, 1, -1)
            check(f.submit() == -2)
            check(f.submit() == -2)
        }
        for (v in intArrayOf(Int.MIN_VALUE, -1_000_001, 1_000_001, Int.MAX_VALUE)) {
            invalid { it.shadow(style, v, 0, 1, 1, -1) }
            invalid { it.shadow(style, 0, v, 1, 1, -1) }
        }
        for (v in intArrayOf(Int.MIN_VALUE, -1, 0, 1_000_001, Int.MAX_VALUE)) {
            invalid { it.shadow(style, 0, 0, v, 1, -1) }
            invalid { it.shadow(style, 0, 0, 1, v, -1) }
        }
        for ((w, h) in arrayOf(0 to 1, 1 to 0, 4097 to 1, 4096 to 1025)) {
            val f = MingFrame4(w, h)
            f.shadow(style, 0, 0, 1, 1, -1)
            check(f.submit() == -2)
        }
        val quota = MingFrame4(64, 64)
        quota.clip(0, 0, 0, 0)
        repeat(9) { quota.shadow(style, 0, 0, 1, 1, 0) }
        check(quota.submit() == -4)
        quota.shadow(style, Int.MAX_VALUE, 0, 1, 1, -1)
        check(quota.submit() == -4)
        val aa = MingFrame4(64, 64)
        repeat(17) { aa.rectAA(0, 0, 1, 1, 0) }
        aa.shadow(style, 0, 0, 1, 1, 0)
        check(aa.submit() == -4)
        invalid { f -> repeat(257) { f.rect(0, 0, 1, 1, -1) } }
        MingLog.event(71, 0) // No poisoned builder reaches frame_submit.

        val boundary = MingFrame4(4096, 1024)
        boundary.shadow(style, -1_000_000, 1_000_000, 1_000_000, 1, 0x80402010.toInt())
        boundary.shadow(style, 1_000_000, -1_000_000, 1, 1_000_000, 0)
        check(boundary.submit() == -4) // Injected host rejection; builder remains reusable.
        check(boundary.submit() == 0)
        val maximum = MingFrame4(64, 64)
        maximum.clip(0, 0, 0, 0)
        repeat(8) { maximum.shadow(styles[it], 0, 0, 1, 1, 0) }
        repeat(16) { maximum.rectAA(0, 0, 1, 1, 0, 64) }
        check(maximum.submit() == 0)
        val commands = MingFrame4(1, 1)
        repeat(248) { commands.rect(0, 0, 1, 1, -1) }
        repeat(8) { commands.shadow(style, 0, 0, 1, 1, 0) }
        check(commands.submit() == 0)
        check(style.close() == 0)
        check(style.close() == -2)
        val stale = MingFrame4(1, 1)
        stale.shadow(style, 0, 0, 1, 1, -1)
        check(stale.submit() == -2)
        styles.removeAt(0)
        styles.add(checkNotNull(MingShadows.create(64, 16).style))
        check(MingFrame4(1024, 4096).submit() == 0)
        MingLog.event(72, 0)
    } finally {
        for (style in styles.asReversed()) check(style.close() == 0)
    }
}
