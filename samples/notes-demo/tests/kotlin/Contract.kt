@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class)
import kotlin.wasm.WasmImport
import ming.sdk.MingLog

@WasmImport("notes_test", "mode")
external fun mode(): Int

fun main() {
    val document = NotesDocument(diagnosticBytes = true)
    document.load()
    when (mode()) {
        0 -> {
            check(document.canSave && document.text == "" && document.status == NoteStatus.NEW)
            document.save("")
            check(document.status == NoteStatus.SAVED)
            val reopened = NotesDocument(diagnosticBytes = true); reopened.load()
            check(reopened.canSave && reopened.text == "" && reopened.status == NoteStatus.LOADED)
        }
        1 -> {
            check(document.canSave && document.text == "你好😀" && document.status == NoteStatus.LOADED)
            document.save("明天见🌙")
            check(document.status == NoteStatus.SAVED)
            val reopened = NotesDocument(diagnosticBytes = true); reopened.load(); check(reopened.text == "明天见🌙")
        }
        2, 3, 4, 10 -> {
            check(!document.canSave && document.text == "")
            document.save("must not overwrite")
            check(!document.canSave)
        }
        5 -> { check(document.canSave); document.save("keep edit"); check(document.status == NoteStatus.WRITE_FAILED && document.canSave) }
        6 -> { document.save("sync warning"); check(document.status == NoteStatus.SYNC_UNCONFIRMED && document.canSave) }
        7 -> {
            document.save("mismatch"); check(document.status == NoteStatus.READBACK_FAILED && !document.canSave)
            document.save("must not overwrite")
        }
        8 -> {
            document.save("a".repeat(513)); check(document.status == NoteStatus.INVALID_TEXT)
            document.save("\uD800"); check(document.status == NoteStatus.INVALID_TEXT)
        }
        9 -> { val value = "中".repeat(170) + "ab"; document.save(value); check(document.status == NoteStatus.SAVED); val reopened=NotesDocument(diagnosticBytes = true);reopened.load();check(reopened.text==value) }
        11 -> {
            check(!document.canSave);document.load();check(document.canSave && document.text=="recovered")
            document.save("after reload");check(document.status==NoteStatus.SAVED)
        }
        else -> error("unknown test mode")
    }
    MingLog.event(9799, 1)
}
