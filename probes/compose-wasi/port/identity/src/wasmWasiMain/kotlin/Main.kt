@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
import ming.compose.port.WasiIdentity
import kotlin.wasm.WasmImport
private class MutableHash(var value: Int) {
    var calls = 0
    override fun hashCode(): Int { calls++; return value }
    override fun equals(other: Any?): Boolean = other is MutableHash && other.value == value
}
@WasmImport("ming_v1", "log") private external fun log(code: Int, value: Int): Int
fun main() {
    val a = MutableHash(7)
    val b = MutableHash(7)
    check(a == b && a !== b)
    val aHash = WasiIdentity.hash(a)
    val bHash = WasiIdentity.hash(b)
    check(aHash != bHash)
    repeat(10000) { a.value = it; check(WasiIdentity.hash(a) == aHash) }
    check(a.calls == 0 && b.calls == 0)
    check(WasiIdentity.hash(null) == 0)
    val text = charArrayOf('i','d','e','n','t','i','t','y').concatToString()
    val expected = text.hashCode()
    repeat(1000) { check(WasiIdentity.hash(text) == expected); check(text.hashCode() == expected) }
    val array = intArrayOf(1,2,3)
    val arrayHash = WasiIdentity.hash(array)
    array[0] = 500
    check(WasiIdentity.hash(array) == arrayHash)
    val defaultHashObject = Any()
    val defaultHash = defaultHashObject.hashCode()
    check(WasiIdentity.hash(defaultHashObject) == defaultHash)
    repeat(10000) { val temporary = MutableHash(it); check(WasiIdentity.hash(temporary) == WasiIdentity.hash(temporary)) }
    log(101, 10000)
    log(102, a.calls + b.calls)
    log(103, WasiIdentity.assigned)
}
