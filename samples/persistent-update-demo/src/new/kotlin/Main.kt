import ming.compose.*
import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

fun main() {
    val read = MingPrivateFiles.read("state/note.txt", 0, 6)
    MingLog.event(9500, read.code)
    check(read.code == 0 && read.bytes!!.contentEquals("alpha".encodeToByteArray()))
    val result = MingTextApplication.run {
        MingColumn {
            MingText("Persistent update 0.2.0", emPx = 24)
            MingText("state/note.txt = alpha", emPx = 18)
        }
    }
    MingLog.event(9510, result.code)
    MingLog.event(9511, result.frames)
    check(result.code == 0 && result.frames > 0)
}
