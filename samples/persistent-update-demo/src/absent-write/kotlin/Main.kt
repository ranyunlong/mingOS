import ming.compose.*
import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

fun main() {
    val bytes = "alpha".encodeToByteArray()
    val written = MingPrivateFiles.write("note.txt", bytes)
    MingLog.event(9501, written.code)
    MingLog.event(9502, if (written.directorySynced == true) 1 else 0)
    check(written.code == 0 && written.directorySynced == true)
    val read = MingPrivateFiles.read("note.txt", 0, 6)
    MingLog.event(9500, read.code)
    check(read.code == 0 && read.bytes!!.contentEquals(bytes))
    val result = MingTextApplication.run {
        MingColumn {
            MingText("Persistent update 0.2.0", emPx = 24)
            MingText("New note.txt = alpha", emPx = 18)
        }
    }
    MingLog.event(9510, result.code)
    MingLog.event(9511, result.frames)
    check(result.code == 0 && result.frames > 0)
}
