@file:Suppress("INVISIBLE_REFERENCE", "INVISIBLE_MEMBER")

// Negative control: deliberately wrong, must fail when value.hashCode changes.
private fun identity(value: Any?): Int = value?.hashCode() ?: 0
private class MutableHash(var value: Int) {
    var calls = 0
    override fun hashCode(): Int { calls++; return value }
    override fun equals(other: Any?): Boolean = other is MutableHash && other.value == value
}
fun main() {
    val objectA = MutableHash(7)
    val before = identity(objectA)
    objectA.value = 99
    check(before == identity(objectA)) { "identity changed with mutable hashCode" }
    check(objectA.calls == 0) { "identity invoked overridden hashCode" }
}
