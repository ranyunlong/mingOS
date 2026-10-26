import androidx.compose.runtime.*
import kotlinx.coroutines.delay
import ming.compose.*
import ming.sdk.MingFrames
import ming.sdk.MingInput
import ming.sdk.MingLog

private fun normalCloseCase() {
    var finished = 0
    var disposed = 0
    var state: MutableState<Int>? = null
    val result = MingFramedApplication.run {
        val value = remember { mutableStateOf(0) }
        state = value
        DisposableEffect(Unit) { onDispose { disposed++ } }
        LaunchedEffect(Unit) {
            try {
                delay(1)
                withFrameNanos { }
                value.value = 1
            } finally { finished++ }
        }
        MingColumn { MingText("真实宿主帧：" + value.value) }
    }
    check(result.code == 0 && result.frames >= 2)
    check(state?.value == 1 && finished == 1 && disposed == 1)
    MingLog.event(820, 1)
    MingLog.event(821, result.frames)
}

// The Node capability fixture selects a mode using the existing idle input payload.
// Production application, SDK, Compose Runtime and coroutine code remain real.
private fun failureCase(mode: Int) {
    val disposeFailure = IllegalStateException("expected-dispose-failure")
    val effectFailure = IllegalArgumentException("expected-effect-failure")
    var finished = 0
    var disposed = 0
    var code: Int? = null
    val failure = runCatching {
        val result = MingFramedApplication.run {
            DisposableEffect(Unit) {
                onDispose {
                    disposed++
                    if (mode == 1 || mode == 2 || mode == 6) throw disposeFailure
                }
            }
            LaunchedEffect(Unit) {
                try {
                    when (mode) {
                        2, 4 -> delay(1) // A fresh clock read fails after onDispose is registered.
                        5 -> throw effectFailure
                        else -> withFrameNanos { }
                    }
                } finally { finished++ }
            }
            MingColumn { MingText("framed failure contract") }
        }
        code = result.code
    }.exceptionOrNull()
    check(disposed == 1 && finished == 1) { "CASE_$mode: cleanup counts $disposed/$finished" }
    when (mode) {
        1, 2 -> {
            val hostFailure = checkNotNull(failure) { "CASE_$mode: Host error with cleanup exception was converted to status $code" }
            val expected = if (mode == 1) MingFrames.LIMIT else MingFrames.IO
            check(hostFailure.message == "MING_HOST_FRAME_ERROR: $expected") { "CASE_$mode: original Host failure lost" }
            check(hostFailure.suppressedExceptions.any { it === disposeFailure }) { "CASE_$mode: original cleanup exception missing from chain" }
            check(code == null) { "CASE_$mode: mixed failure returned a status" }
        }
        3, 4 -> {
            check(failure == null) { "CASE_$mode: pure Host error unexpectedly threw" }
            check(code == if (mode == 3) MingFrames.LIMIT else MingFrames.IO) { "CASE_$mode: pure Host status changed: $code" }
        }
        5 -> check(failure === effectFailure) { "CASE_5: original effect exception lost" }
        6 -> check(failure === disposeFailure) { "CASE_6: original dispose exception lost" }
        else -> error("Unknown framed failure mode: $mode")
    }
    MingLog.event(830 + mode, 1)
}

fun main() {
    var mode = 0
    try {
        val selection = MingInput.next()
        check(selection.type == 0 || selection.type == 8) { "Expected EOF or fixture idle selector" }
        mode = if (selection.type == 8) selection.a else 0
        if (mode == 0) normalCloseCase() else failureCase(mode)
        MingLog.event(899, 1)
    } catch (failure: Throwable) {
        // Like the driver contract, preserve a guest assertion diagnostic for the host evaluator.
        MingLog.event(890, mode)
        (failure.message ?: "unknown framed contract failure").forEach { MingLog.event(891, it.code) }
        MingLog.event(899, 0)
    }
}
