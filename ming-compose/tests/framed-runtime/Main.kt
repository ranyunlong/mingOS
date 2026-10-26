@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
package ming.compose
import androidx.compose.runtime.*
import androidx.compose.runtime.snapshots.Snapshot
import kotlinx.coroutines.delay
import kotlin.wasm.WasmImport

@WasmImport("ming_v1","log") private external fun log(code:Int,value:Int):Int
private class Observation {
    lateinit var value: MutableState<Int>
    var token: Any? = null
    var starts=0;var finishes=0;var disposals=0
}
private fun text(runtime:FramedComposition):MingNode=runtime.root.children.single().children.single()
fun main() {
    var supplied=0L
    val runtime=FramedComposition(0L,{supplied})
    val observed=Observation()
    try {
        runtime.setContent {
            val value=remember{mutableStateOf(0)}
            val token=remember{Any()}
            observed.value=value;observed.token=token
            DisposableEffect(Unit){ onDispose{observed.disposals++} }
            LaunchedEffect(Unit){
                observed.starts++
                try {
                    withFrameNanos { }
                    value.value=1
                    delay(5)
                    value.value=2
                    withFrameNanos { }
                    value.value=3
                } finally {observed.finishes++}
            }
            MingColumn { MingText(value.value.toString()) }
        }
        check(text(runtime).text=="0" && observed.starts==1 && runtime.wantsFrame)
        val node=text(runtime);val state=observed.value;val token=observed.token
        supplied=16_000_000;runtime.advance(supplied,true)
        check(state.value==1 && runtime.nextDeadlineNanos==21_000_000L)
        supplied=20_000_000;runtime.advance(supplied,true)
        check(text(runtime).text=="1")
        supplied=21_000_000;runtime.advance(supplied,false)
        check(state.value==2 && runtime.wantsFrame)
        supplied=24_000_000;runtime.advance(supplied,true)
        supplied=28_000_000;runtime.advance(supplied,true)
        check(text(runtime).text=="3" && text(runtime)===node)
        check(observed.value===state && observed.token===token && observed.finishes==1)
        val committed=runtime.revision
        Snapshot.withMutableSnapshot{state.value=3};runtime.pump()
        supplied=30_000_000;runtime.advance(supplied,true)
        check(runtime.revision==committed)
        log(810,1)
    } finally {runtime.dispose()}
    check(runtime.isDisposed && runtime.root.children.isEmpty() && observed.disposals==1)
    runtime.dispose();check(observed.disposals==1)
    log(811,1)
    log(899,1)
}
