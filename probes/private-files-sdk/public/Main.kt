package qualification

import ming.sdk.MingLog
import ming.sdk.MingPublicFiles

fun main() {
    val write = MingPublicFiles.write("shared-note.txt", "shared".encodeToByteArray())
    check(write.code == 0 && write.directorySynced != null)
    val read = MingPublicFiles.read("shared-note.txt", 0, 16)
    check(read.code == 0 && read.bytes!!.decodeToString() == "shared")
    check(MingPublicFiles.createDirectory("docs").code == 0)
    check(MingPublicFiles.write("docs/a\".txt", byteArrayOf(42)).code == 0)
    val listed = MingPublicFiles.list("docs", 0, 32)
    check(listed.code == 0 && listed.entries!!.single().name == "a\".txt")
    check(MingPublicFiles.rename("docs/a\".txt", "docs/b.txt").code == 0)
    check(MingPublicFiles.delete("docs/b.txt").code == 0)
    check(MingPublicFiles.removeDirectory("docs").code == 0)
    check(MingLog.event(7201, 1) == 0)
}
