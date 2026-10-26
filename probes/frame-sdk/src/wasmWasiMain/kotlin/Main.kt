package qualification

import ming.sdk.MingFrames
import ming.sdk.MingFrameKind
import ming.sdk.FrameWaitResult
import ming.sdk.MingLog

private const val ENDIAN = 0x01234567fedcba98L

private fun success(code: Int) { MingLog.event(code, 1) }
private fun invalid(result: FrameWaitResult) {
    check(result.code == -2 && result.event == null)
}

fun main() {
    // Fixture calls are consumed by the Node boundary double in the exact order below.
    for (expected in listOf(0L, ENDIAN, Long.MAX_VALUE)) {
        val read = MingFrames.monotonicNanos()
        check(read.code == 0 && read.nanos == expected)
    }
    success(701)
    for (expected in listOf(-2, -3, -4, -99)) {
        val read = MingFrames.monotonicNanos()
        check(read.code == expected && read.nanos == null)
    }
    success(702)
    repeat(7) {
        val read = MingFrames.monotonicNanos()
        check(read.code == -2 && read.nanos == null)
    }
    success(703)
    repeat(2) {
        val read = MingFrames.monotonicNanos()
        check(read.code == -2 && read.nanos == null)
    }
    success(704)

    val close = MingFrames.wait(wantFrame = false)
    check(close.code == 0 && close.event?.kind == MingFrameKind.CLOSE)
    check(close.event?.nowNanos == 0L && close.event?.frameSequence == 0L && close.event?.input == null)
    val frame = MingFrames.wait(wantFrame = true)
    check(frame.code == 0 && frame.event?.kind == MingFrameKind.FRAME)
    check(frame.event?.nowNanos == ENDIAN && frame.event?.frameSequence == 1L && frame.event?.input == null)
    val timer = MingFrames.wait(wantFrame = false, deadlineNs = 0L)
    check(timer.code == 0 && timer.event?.kind == MingFrameKind.TIMER)
    check(timer.event?.nowNanos == Long.MAX_VALUE && timer.event?.frameSequence == 0L)
    val max = MingFrames.wait(wantFrame = true, deadlineNs = Long.MAX_VALUE, nonblocking = true)
    check(max.code == 0 && max.event?.kind == MingFrameKind.FRAME)
    check(max.event?.nowNanos == Long.MAX_VALUE && max.event?.frameSequence == Long.MAX_VALUE)
    val cancel = MingFrames.wait(wantFrame = false, deadlineNs = ENDIAN, nonblocking = true)
    check(cancel.code == 0 && cancel.event?.kind == MingFrameKind.CANCEL && cancel.event?.input == null)
    check(cancel.event?.frameSequence == ENDIAN)
    success(705)

    for (type in 1..7) {
        val result = MingFrames.wait(wantFrame = false, deadlineNs = type.toLong())
        val event = result.event
        check(result.code == 0 && event?.kind == MingFrameKind.INPUT)
        check(event?.nowNanos == ENDIAN + type && event?.frameSequence == Long.MAX_VALUE)
        check(event?.input?.type == type && event.input?.a == Int.MIN_VALUE)
        check(event.input?.b == Int.MAX_VALUE && event.input?.c == -123456789)
    }
    success(706)

    val pending = MingFrames.wait(wantFrame = false, nonblocking = true)
    check(pending.code == 0 && pending.event == null)
    success(707)
    for (expected in listOf(-2, -3, -4, -99)) {
        val result = MingFrames.wait(wantFrame = false)
        check(result.code == expected && result.event == null)
    }
    success(708)
    for (deadline in listOf(-1L, -2L, Long.MIN_VALUE)) {
        invalid(MingFrames.wait(wantFrame = true, deadlineNs = deadline, nonblocking = true))
    }
    success(709)

    repeat(6) { invalid(MingFrames.wait(wantFrame = false)) }
    success(710)
    repeat(3) { invalid(MingFrames.wait(wantFrame = false)) }
    success(711)
    repeat(3) { invalid(MingFrames.wait(wantFrame = false)) }
    success(712)
    repeat(2) { invalid(MingFrames.wait(wantFrame = false)) }
    success(713)
    repeat(2) { invalid(MingFrames.wait(wantFrame = false)) }
    success(714)
    invalid(MingFrames.wait(wantFrame = false))
    success(715)
    repeat(3) { invalid(MingFrames.wait(wantFrame = false)) }
    success(716)
    repeat(16) { invalid(MingFrames.wait(wantFrame = false)) }
    success(717)
    repeat(4) { invalid(MingFrames.wait(wantFrame = false)) }
    success(718)
    // A valid call after every malformed/error packet proves the wrapper keeps no poisoned state.
    val recovered = MingFrames.wait(wantFrame = true, nonblocking = true)
    check(recovered.code == 0 && recovered.event?.kind == MingFrameKind.FRAME)
    check(recovered.event?.nowNanos == ENDIAN && recovered.event?.frameSequence == 7L)
    success(719)
    success(799)
}
