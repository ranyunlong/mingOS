package qualification

import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

private fun request(json: String): String {
    val result = MingPrivateFiles.call(json.encodeToByteArray())
    check(result.code == 0 && result.reply != null)
    return result.reply!!.decodeToString()
}

fun main() {
    val write = request("""{"op":"write","path":"note.txt","data":"YWxwaGE="}""")
    check(write.contains("\"value\":5"))
    val read = request("""{"op":"read","path":"note.txt","offset":0,"limit":16}""")
    check(read.contains("\"value\":5") && read.contains("\"payload\":\"YWxwaGE=\""))
    check(MingLog.event(7102, 1) == 0)
}
