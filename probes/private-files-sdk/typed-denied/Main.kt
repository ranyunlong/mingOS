package qualification

import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

fun main() {
    check(MingPrivateFiles.read("note.txt", 0, 1).code == MingPrivateFiles.DENIED)
    check(MingPrivateFiles.write("note.txt", byteArrayOf(42)).code == MingPrivateFiles.DENIED)
    check(MingPrivateFiles.list("", 0, 1).code == MingPrivateFiles.DENIED)
    check(MingPrivateFiles.createDirectory("docs").code == MingPrivateFiles.DENIED)
    check(MingLog.event(7105, 1) == 0)
}
