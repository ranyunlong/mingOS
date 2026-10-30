package qualification

import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

fun main() {
    val request = """{"op":"list","path":"","offset":0,"limit":1}""".encodeToByteArray()
    val result = MingPrivateFiles.call(request)
    val event = if (result.code == MingPrivateFiles.DENIED) {
        check(result.reply == null)
        7100
    } else {
        check(result.code == 0)
        check(result.reply!!.decodeToString() == """{"id":1,"value":0}""")
        7101
    }
    check(MingPrivateFiles.call(byteArrayOf()).code == MingPrivateFiles.INVALID_ARGUMENT)
    check(MingPrivateFiles.call(ByteArray(90_001)).code == MingPrivateFiles.INVALID_ARGUMENT)
    check(MingLog.event(event, 1) == 0)
}
