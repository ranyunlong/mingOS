import androidx.compose.runtime.*
import ming.compose.*
import ming.sdk.MingLog
import ming.sdk.TextEditState

fun main() {
    val name=TextFieldState()
    val note=TextFieldState(TextEditState("你好 MingOS"))
    val result=MingTextApplication.run {
        MingColumn {
            MingText("SDK6 · Compose 文本输入",emPx=24)
            MingTextField(name,placeholder="输入姓名")
            MingTextField(note,placeholder="输入备注")
            MingText("Tab 切换 · Shift 选择 · Ctrl+A 全选",emPx=14)
        }
    }
    MingLog.event(640,name.value.text.hashCode())
    MingLog.event(641,note.value.text.hashCode())
    MingLog.event(if(result.code==0)642 else 643,if(result.code==0)result.frames else result.code)
}
