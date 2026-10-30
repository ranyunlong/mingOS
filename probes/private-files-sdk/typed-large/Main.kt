package qualification

import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

fun main() {
    val bytes = "abc".repeat(43688).encodeToByteArray()
    val write = MingPrivateFiles.write("large.txt", bytes)
    check(write.code == 0 && write.directorySynced != null)
    val read = MingPrivateFiles.read("large.txt", 131058, 6)
    check(read.code == 0 && read.bytes!!.decodeToString() == "abcabc")
    check(MingLog.event(7103, 1) == 0)
}
