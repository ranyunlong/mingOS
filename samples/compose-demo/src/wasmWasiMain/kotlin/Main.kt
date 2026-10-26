import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import ming.compose.MingApplication
import ming.compose.MingButton
import ming.compose.MingColumn
import ming.compose.MingText
import ming.sdk.MingLog

fun main() {
    val result = MingApplication.runPersistent {
        var count by remember { mutableStateOf(0) }
        var details by remember { mutableStateOf(true) }
        MingColumn(padding = 24, gap = 12) {
            MingText("MingOS · Compose", emPx = 24)
            MingText("计数：" + count)
            MingButton("增加计数") { count++ }
            MingButton(if (details) "收起详情" else "展开详情") { details = !details }
            if (details) MingText("Kotlin/Wasm · 真正的 Compose Runtime · Ming SDK")
        }
    }
    MingLog.event(if (result.code == 0) 40 else 60, if (result.code == 0) result.frames else result.code)
}
