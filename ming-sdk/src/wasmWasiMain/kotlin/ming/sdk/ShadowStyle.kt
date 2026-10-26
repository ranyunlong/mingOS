@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)

package ming.sdk

import kotlin.wasm.WasmImport

class ShadowStyleResult(val code: Int, val style: ShadowStyle?)

/**
 * Immutable, session-local radius/blur resource. Keep alive through frame submission.
 * The host owns the eight-active-style quota and monotonic typed handles.
 * Closing again or submitting after close returns a host error, as with TextLayout.
 * Close does not mutate the handle or invalidate an already completed output frame.
 */
class ShadowStyle internal constructor(internal val handle: Int) {
    fun close(): Int = shadowStyleClose(handle)
}

object MingShadows {
    /** Radius 0..64px, blur 0..16px. Geometry and color belong to each draw. */
    fun create(radius: Int, blur: Int): ShadowStyleResult {
        if (radius !in 0..64 || blur !in 0..16) return ShadowStyleResult(MingText.INVALID, null)
        val handle = shadowStyleCreate(radius, blur)
        return if (handle in 0x40000001..0x4fffffff) ShadowStyleResult(0, ShadowStyle(handle))
        else ShadowStyleResult(if (handle < 0) handle else MingText.INVALID, null)
    }
}

@WasmImport("ming_v4", "shadow_style_create")
private external fun shadowStyleCreate(radius: Int, blur: Int): Int
@WasmImport("ming_v4", "shadow_style_close")
private external fun shadowStyleClose(handle: Int): Int
