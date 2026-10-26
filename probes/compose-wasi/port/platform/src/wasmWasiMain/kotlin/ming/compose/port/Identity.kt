@file:Suppress("INVISIBLE_REFERENCE", "INVISIBLE_MEMBER")
package ming.compose.port

/**
 * EXPERIMENTAL: pinned to kotlin-stdlib-wasm-wasi 2.3.20 Any.kt / String.kt.
 * Uses the existing object-owned internal cache, not virtual value.hashCode or a registry.
 * Kotlin explicitly warns that invisible API suppression has no stability guarantee.
 * Single Wasm thread only; no cross-thread atomic initialization is provided.
 * String is immutable and owns this same field for content hashing: leave its cache alone.
 * Equal string hashes may collide, as all Int identity hashes are permitted to do.
 */
object WasiIdentity {
    private var next = 1
    var assigned = 0
        private set
    fun hash(value: Any?): Int {
        if (value == null) return 0
        if (value is String) return value.hashCode()
        val cached = value._hashCode
        if (cached != 0) return cached
        val id = next
        next++
        if (next == 0) next++
        value._hashCode = id
        assigned++
        return id
    }
}
