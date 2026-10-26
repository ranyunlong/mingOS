package androidx.compose.runtime.internal
import ming.compose.port.WasiIdentity
// Experimental object-owned identity for the pinned Kotlin 2.3.20 WASI stdlib.
internal actual fun identityHashCode(instance: Any?): Int = WasiIdentity.hash(instance)
