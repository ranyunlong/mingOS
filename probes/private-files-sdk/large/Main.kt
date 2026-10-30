package qualification

import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles

private fun request(json: String): String {
    val result = MingPrivateFiles.call(json.encodeToByteArray())
    check(result.code == 0 && result.reply != null)
    return result.reply!!.decodeToString()
}

fun main() {
    val encoded = "YWJj".repeat(10922)
    check(request("""{"op":"write_begin","path":"large.txt","total":131064}""").contains("\"value\":0"))
    for (index in 0 until 4) {
        val reply = request("""{"op":"write_chunk","offset":${index * 32766},"data":"$encoded"}""")
        check(reply.contains("\"value\":32766"))
    }
    check(request("""{"op":"write_commit"}""").contains("\"value\":0"))
    val read = request("""{"op":"read","path":"large.txt","offset":131058,"limit":6}""")
    check(read.contains("\"value\":6") && read.contains("\"payload\":\"YWJjYWJj\""))
    check(MingLog.event(7103, 1) == 0)
}
