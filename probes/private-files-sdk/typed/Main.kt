package qualification

import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

fun main() {
    val write = MingPrivateFiles.write("note.txt", "alpha".encodeToByteArray())
    check(write.code == 0 && write.directorySynced != null)
    val read = MingPrivateFiles.read("note.txt", 0, 16)
    check(read.code == 0 && read.bytes!!.decodeToString() == "alpha")
    check(MingLog.event(7102, 1) == 0)
}
