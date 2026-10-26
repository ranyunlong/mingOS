package qualification
import ming.sdk.*

private fun ok(code: Int) { check(MingLog.event(code, 1) == 0) }
private fun readError(code: Int, slot: Int = 1) { val result=MingAppData.read(slot, 0, 16);check(result.code==code && result.bytes==null) }
fun main() {
    // The Node capability starts with no grant. Absence is DENIED, even for empty writes.
    check(MingAppData.size(1)==-1);readError(-1);check(MingAppData.write(1,byteArrayOf())==-1);check(MingAppData.delete(1)==-1)
    ok(1000)
    for (slot in listOf(Int.MIN_VALUE,-1,0,9,Int.MAX_VALUE)) {
        check(MingAppData.size(slot)==-2);readError(-2,slot);check(MingAppData.write(slot,byteArrayOf(1))==-2);check(MingAppData.delete(slot)==-2)
    }
    for ((offset,cap) in listOf(-1 to 1, 0 to -1, 0 to 65537, 65537 to 0, Int.MAX_VALUE to 1)) {
        val r=MingAppData.read(1,offset,cap);check(r.code==-2 && r.bytes==null)
    }
    check(MingAppData.write(1,ByteArray(65537))==-2)
    ok(1001) // No host call is allowed between 1000 and 1001.
    check(MingAppData.size(1)==-3);readError(-3)
    val original=ByteArray(65536) { ((it*37+11) and 255).toByte() }
    check(MingAppData.write(1,original)==65536);check(MingAppData.size(1)==65536)
    val result=MingAppData.read(1);check(result.code==0 && result.bytes!!.contentEquals(original))
    result.bytes!![0]=99 // A detached ByteArray cannot mutate host storage.
    val partial=MingAppData.read(1,65530,16);check(partial.code==0 && partial.bytes!!.contentEquals(original.copyOfRange(65530,65536)))
    val eof=MingAppData.read(1,65536,16);check(eof.code==0 && eof.bytes!!.isEmpty())
    val zero=MingAppData.read(1,12,0);check(zero.code==0 && zero.bytes!!.isEmpty())
    val again=MingAppData.read(1,0,1);check(again.bytes!!.contentEquals(byteArrayOf(11)))
    check(MingAppData.write(2,byteArrayOf(0,-1,0,127))==4);check(MingAppData.read(2).bytes!!.contentEquals(byteArrayOf(0,-1,0,127)))
    check(MingAppData.write(2,byteArrayOf())==0);check(MingAppData.size(2)==0);check(MingAppData.read(2).bytes!!.isEmpty())
    check(MingAppData.delete(2)==0);check(MingAppData.size(2)==-3);check(MingAppData.delete(2)==-3)
    check(MingAppData.size(8)==-1);readError(-1,8);check(MingAppData.write(8,byteArrayOf(1))==-1);check(MingAppData.delete(8)==-1)
    ok(1002)
    // Host response/error fixture mode, consumed in a fixed order by the double.
    for (error in listOf(-1,-2,-3,-4,-99)) { readError(error);check(MingAppData.write(1,byteArrayOf(1,2,3))==error);check(MingAppData.size(1)==error);check(MingAppData.delete(1)==error) }
    ok(1003)
    // Invalid positive host results must never become valid Kotlin results.
    readError(-2);readError(-2)
    check(MingAppData.write(1,byteArrayOf(1,2,3))==-2);check(MingAppData.write(1,byteArrayOf(1,2,3))==-2)
    check(MingAppData.size(1)==-2);check(MingAppData.delete(1)==-2)
    ok(1004)
}
