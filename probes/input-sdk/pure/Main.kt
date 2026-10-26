package qualification
import ming.sdk.*
fun main() {
    check(MingTextInput.decode(ByteArray(56)).code == -2)
    val state = TextEditState().commit("中😀").key(1)
    check(state.text == "中" && state.caret == 1)
    check(state.preedit("文",0,1).display().text == "中文")
    MingLog.event(950,1)
}
