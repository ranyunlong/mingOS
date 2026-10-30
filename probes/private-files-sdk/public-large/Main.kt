package qualification

import ming.sdk.MingLog
import ming.sdk.MingPublicFiles

fun main() {
    val bytes = "abc".repeat(43688).encodeToByteArray()
    val write = MingPublicFiles.write("public-large.txt", bytes)
    check(write.code == 0 && write.directorySynced != null)
    val read = MingPublicFiles.read("public-large.txt", 131058, 6)
    check(read.code == 0 && read.bytes!!.decodeToString() == "abcabc")
    check(MingLog.event(7202, 1) == 0)
}
