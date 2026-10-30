package qualification

import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

fun main() {
    check(MingPrivateFiles.read("missing", -1, 1).code == MingPrivateFiles.INVALID_ARGUMENT)
    check(MingPrivateFiles.list("", 0, 33).code == MingPrivateFiles.INVALID_ARGUMENT)
    check(MingPrivateFiles.createDirectory("docs").code == 0)
    check(MingPrivateFiles.write("docs/a\".txt", byteArrayOf(42)).code == 0)
    val listed = MingPrivateFiles.list("docs", 0, 32)
    check(listed.code == 0 && listed.entries!!.size == 1)
    check(listed.entries!![0].name == "a\".txt" && !listed.entries!![0].isDirectory)
    check(MingPrivateFiles.rename("docs/a\".txt", "docs/b.txt").code == 0)
    check(MingPrivateFiles.read("docs/a\".txt", 0, 1).code == MingPrivateFiles.NOT_FOUND)
    check(MingPrivateFiles.delete("docs/b.txt").code == 0)
    check(MingPrivateFiles.removeDirectory("docs").code == 0)
    check(MingLog.event(7104, 1) == 0)
}
