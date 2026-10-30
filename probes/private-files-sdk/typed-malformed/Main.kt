package qualification

import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

fun main() {
    check(MingPrivateFiles.read("note", 0, 1).code == MingPrivateFiles.IO)
    check(MingPrivateFiles.list("", 0, 1).code == MingPrivateFiles.IO)
    check(MingPrivateFiles.createDirectory("docs").code == MingPrivateFiles.IO)
    check(MingPrivateFiles.write("note", byteArrayOf(42)).code == MingPrivateFiles.IO)
    check(MingPrivateFiles.write("large", ByteArray(65537)).code == MingPrivateFiles.IO)
    check(MingLog.event(7106, 1) == 0)
}
