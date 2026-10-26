import ming.sdk.MingFiles
import ming.sdk.MingLog

fun main() {
    val handle = MingFiles.open(1)
    if (handle < 0) {
        MingLog.event(5, handle)
        return
    }
    val size = MingFiles.length(handle)
    MingLog.event(1, size)
    // Diagnostic byte events keep this first ABI independent of GC/string layout.
    var offset = 0
    while (offset < size) {
        val byte = MingFiles.readByte(handle, offset)
        if (byte < 0) {
            MingLog.event(6, byte)
            MingFiles.close(handle)
            return
        }
        MingLog.event(2, byte)
        offset++
    }
    MingLog.event(3, MingFiles.open(2))
    MingLog.event(4, MingFiles.close(handle))
}
