import ming.sdk.*

// A separate real Wasm executable: never packaged as the application.
fun main() {
    val layout = checkNotNull(MingText.create("中文 AA", 16f).layout)
    try {
        for ((w, h) in arrayOf(0 to 1, 1 to 0, -1 to 1, 4097 to 1,
            1 to 4097, 4096 to 1025, Int.MAX_VALUE to Int.MAX_VALUE)) {
            check(MingFrame4(w, h).submit() == -2)
        }
        fun invalid(block: (MingFrame4) -> Unit) {
            val frame = MingFrame4(64, 64)
            frame.rectAA(0, 0, 32, 32, -1, 8)
            block(frame)
            frame.rect(0, 0, 1, 1, -1)
            check(frame.submit() == -2)
            check(frame.submit() == -2)
        }
        for (v in intArrayOf(Int.MIN_VALUE, -1_000_001, 1_000_001, Int.MAX_VALUE)) {
            invalid { it.rectAA(v, 0, 1, 1, -1, 0) }
            invalid { it.rectAA(0, v, 1, 1, -1, 0) }
            invalid { it.rect(v, 0, 1, 1, -1) }
            invalid { it.image(1, 0, v, 1, 1) }
            invalid { it.clip(v, 0, 1, 1) }
        }
        for (v in intArrayOf(Int.MIN_VALUE, -1, 0, 1_000_001, Int.MAX_VALUE)) {
            invalid { it.rectAA(0, 0, v, 1, -1, 0) }
            invalid { it.rectAA(0, 0, 1, v, -1, 0) }
            invalid { it.rect(0, 0, v, 1, -1) }
            invalid { it.image(1, 0, 0, 1, v) }
            if (v != 0) invalid { it.clip(0, 0, v, 0) }
        }
        for (v in intArrayOf(-1, 65, Int.MAX_VALUE)) {
            invalid { it.rectAA(0, 0, 1, 1, -1, v) }
            invalid { it.rect(0, 0, 1, 1, -1, v) }
        }
        for (v in intArrayOf(0, -1, Int.MIN_VALUE)) invalid { it.image(v, 0, 0, 1, 1) }
        for (v in floatArrayOf(Float.NaN, Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY,
            -1_000_001f, 1_000_001f)) {
            invalid { it.text(layout, v, 0f, -1) }
            invalid { it.text(layout, 0f, v, -1) }
        }
        for (v in intArrayOf(Int.MIN_VALUE, -64_000_001, 64_000_001, Int.MAX_VALUE)) {
            invalid { it.textQ6(layout, v, 0, -1) }
            invalid { it.textQ6(layout, 0, v, -1) }
        }
        invalid { repeat(256) { _ -> it.rect(0, 0, 1, 1, -1) } }
        val quota = MingFrame4(64, 64)
        quota.clip(0, 0, 0, 0)
        repeat(17) { quota.rectAA(0, 0, 1, 1, 0, 0) }
        check(quota.submit() == -4) // Transparent, clipped and radius-zero still count.
        quota.rectAA(Int.MAX_VALUE, 0, 1, 1, -1, 0)
        check(quota.submit() == -4) // First failure permanently poisons this builder.
        val poisoned = MingFrame4(64, 64)
        poisoned.rectAA(0, 0, 0, 1, -1, 0)
        repeat(17) { poisoned.rectAA(0, 0, 1, 1, -1, 0) }
        check(poisoned.submit() == -2)
        MingLog.event(80, 0) // Mock host asserts zero frame RPCs before this point.

        val boundary = MingFrame4(4096, 1024)
        boundary.rect(-1_000_000, 1_000_000, 1_000_000, 1_000_000, -1, 64)
        boundary.rectAA(1_000_000, -1_000_000, 1, 3, 0x80ffffff.toInt(), 64)
        boundary.image(1, -1_000_000, 1_000_000, 1_000_000, 1)
        boundary.clip(-1_000_000, 1_000_000, 0, 1_000_000)
        boundary.text(layout, -1_000_000f, 1_000_000f, -1)
        boundary.text(layout, 1.125f, -1.25f, 0x80402010.toInt())
        boundary.textQ6(layout, 64_000_000, -64_000_000, -1)
        check(boundary.submit() == 0)
        val maximumAA = MingFrame4(1, 4096)
        repeat(16) { maximumAA.rectAA(0, 0, 1, 1, -1, 64) }
        check(maximumAA.submit() == 0)
        val maximum = MingFrame4(1, 1)
        repeat(256) { maximum.rect(0, 0, 1, 1, -1) }
        check(maximum.submit() == 0)
        check(layout.close() == 0)
        check(layout.metrics().code == -2)
        val stale = MingFrame4(1, 1)
        stale.textQ6(layout, 0, 0, -1)
        check(stale.submit() == -2) // Immutable handle lifetime remains host-authoritative.
        val recovery = MingFrame4(32, 32)
        recovery.rectAA(0, 0, 32, 32, -1, 12)
        check(recovery.submit() == -4) // Injected host quota rejection.
        check(recovery.submit() == 0)
        check(MingFrame4(1024, 4096).submit() == 0)
        MingLog.event(81, 0)
    } finally {
        // The test intentionally closed its immutable layout above; the host rejects a repeat.
        check(layout.close() == -2)
    }
}
