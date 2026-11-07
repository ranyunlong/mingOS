import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import ming.compose.*
import ming.sdk.MingLog
import ming.sdk.TextEditState

fun main() {
    // Bounded development replay intentionally logs valid note bytes. See README.
    val document = NotesDocument(diagnosticBytes = true)
    document.load()
    val note = TextFieldState(TextEditState(document.text))
    var status by mutableStateOf(document.status)
    var allowed by mutableStateOf(document.canSave)
    MingLog.event(9720, 1)
    val result = MingTextApplication.run {
        MingColumn {
            MingText("便签 · Notes", emPx = 24)
            MingTextField(note, placeholder = "写一条便签，点击保存")
            MingButton(if (allowed) "保存" else "保存不可用") {
                if (document.canSave) {
                    document.save(note.value.text)
                    status = document.status
                    allowed = document.canSave
                }
            }
            MingButton("重新读取（替换编辑内容）") {
                document.load()
                if (document.canSave) note.value = TextEditState(document.text)
                status = document.status
                allowed = document.canSave
            }
            MingText(status.message, emPx = 14)
            MingText("单行，最多 512 UTF-8 字节；关闭不会自动保存", emPx = 14)
        }
    }
    MingLog.event(9730, result.code)
    MingLog.event(9731, result.frames)
    check(result.code == 0)
}
