package appdataprobe

import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import kotlinx.coroutines.awaitCancellation
import ming.compose.MingColumn
import ming.compose.MingFramedApplication
import ming.compose.MingText
import ming.sdk.MingAppData
import ming.sdk.MingLog

private const val SKIPPED = Int.MIN_VALUE
private fun counter(bytes: ByteArray?): Int? {
    if (bytes == null || bytes.size != 4) return null
    var value = 0
    repeat(4) { value = value or ((bytes[it].toInt() and 255) shl (it * 8)) }
    return value.takeIf { it >= 0 }
}
private fun encoded(value: Int) = ByteArray(4) { (value ushr (it * 8)).toByte() }

fun main() {
    MingLog.event(9300, 1)
    val size = MingAppData.size(1)
    val read = MingAppData.read(1, capacity = 4)
    var previous = SKIPPED
    var next = SKIPPED
    var written = SKIPPED
    var verifyCode = SKIPPED
    var verifyLength = SKIPPED
    var verifyValue = SKIPPED
    var matches = SKIPPED
    var status = 3
    val missing = size == MingAppData.NOT_FOUND && read.code == MingAppData.NOT_FOUND
    val parsed = if (size == 4 && read.code == 0) counter(read.bytes) else null
    if (size == MingAppData.DENIED || read.code == MingAppData.DENIED) status = 1
    else if (missing || parsed != null) {
        previous = if (missing) 0 else parsed!!
        if (previous == Int.MAX_VALUE) status = 4
        else {
            next = previous + 1
            written = MingAppData.write(1, encoded(next))
            if (written == 4) {
                val verified = MingAppData.read(1, capacity = 4)
                verifyCode = verified.code
                verifyLength = verified.bytes?.size ?: -1
                verifyValue = counter(verified.bytes) ?: SKIPPED
                matches = if (verifyCode == 0 && verifyLength == 4 && verifyValue == next) 1 else 0
                status = if (matches == 1) 0 else 3
            }
        }
    } else if (read.code == 0 || size >= 0) status = 2
    val slot2Size = MingAppData.size(2)
    val slot2Read = MingAppData.read(2, capacity = 4)
    MingLog.event(9301, size)
    MingLog.event(9302, read.code)
    MingLog.event(9303, read.bytes?.size ?: -1)
    MingLog.event(9304, previous)
    MingLog.event(9305, next)
    MingLog.event(9306, written)
    MingLog.event(9307, verifyCode)
    MingLog.event(9308, verifyLength)
    MingLog.event(9309, verifyValue)
    MingLog.event(9310, matches)
    MingLog.event(9311, slot2Size)
    MingLog.event(9312, slot2Read.code)
    MingLog.event(9313, status)
    var starts = 0
    var finalized = 0
    var disposed = 0
    val shown = if (status == 0) next else previous
    val label = if (status == 1) "AppData DENIED; no write" else "counter=" + shown + " status=" + status
    val result = MingFramedApplication.runPersistent {
        DisposableEffect(Unit) { onDispose { disposed++; MingLog.event(9326, disposed) } }
        LaunchedEffect(Unit) {
            starts++
            try { awaitCancellation() }
            finally { finalized++; MingLog.event(9325, finalized) }
        }
        MingColumn { MingText(label) }
    }
    MingLog.event(9320, result.frames)
    MingLog.event(9321, finalized)
    MingLog.event(9322, disposed)
    MingLog.event(9323, result.code)
    MingLog.event(9324, starts)
}
