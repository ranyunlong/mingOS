package qualification

import ming.sdk.MingLog
import ming.sdk.MingPublicFiles

fun main() {
    check(MingPublicFiles.read("shared-note.txt", 0, 1).code == MingPublicFiles.DENIED)
    check(MingPublicFiles.write("shared-note.txt", byteArrayOf(42)).code == MingPublicFiles.DENIED)
    check(MingPublicFiles.list("", 0, 1).code == MingPublicFiles.DENIED)
    check(MingPublicFiles.createDirectory("docs").code == MingPublicFiles.DENIED)
    check(MingLog.event(7203, 1) == 0)
}
