import ming.compose.*
import ming.sdk.MingPrivateFiles

fun main() {
    val bytes = "alpha".encodeToByteArray()
    val written = MingPrivateFiles.write("note.txt", bytes)
    check(written.code == 0 && written.directorySynced == true)
    val read = MingPrivateFiles.read("note.txt", 0, bytes.size)
    check(read.code == 0 && read.bytes!!.contentEquals(bytes))
    val result = MingTextApplication.run {
        MingColumn { MingText("Private data saved", emPx = 24) }
    }
    check(result.code == 0)
}
