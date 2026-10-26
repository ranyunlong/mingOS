/*
 * Copyright 2024 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/*
 * Derived from AndroidX Compose Runtime 1.10.0 (runtime-wasm-js source archive):
 * nonJvmMain/androidx/compose/runtime/internal/Utils.nonJvm.kt.
 * Modified for MingOS on 2026-10-24: replace the two logError println calls
 * with bounded WasiDiagnostics.writeLine calls over the existing Ming log ABI.
 * invokeComposable is unchanged; the original copyright and license are retained above.
 */

package androidx.compose.runtime.internal

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Composer
import ming.compose.port.WasiDiagnostics

internal actual fun invokeComposable(composer: Composer, composable: @Composable () -> Unit) {
    @Suppress("UNCHECKED_CAST") val realFn = composable as Function2<Composer, Int, Unit>
    realFn(composer, 1)
}

internal actual fun logError(message: String, e: Throwable) {
    WasiDiagnostics.writeLine(message)
    WasiDiagnostics.writeLine(e.stackTraceToString())
}
