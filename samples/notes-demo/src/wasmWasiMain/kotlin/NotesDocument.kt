import ming.sdk.MingLog
import ming.sdk.MingPrivateFiles
import ming.sdk.TextEditState

enum class NoteStatus(val message: String) {
    NEW("还没有便签，编辑后点击保存"),
    LOADED("已读取保存的便签"),
    READ_FAILED("读取失败：请授权后重新读取，保存已锁定"),
    INVALID_TEXT("便签须为有效单行文字，最多 512 UTF-8 字节"),
    WRITE_FAILED("保存失败，编辑内容仍保留"),
    READBACK_FAILED("写后核对失败，请重新读取；保存已锁定"),
    SYNC_UNCONFIRMED("已写入并核对，目录同步未确认"),
    SAVED("已保存、核对并确认目录同步"),
}

/** One named private file. A failed load can never authorize an empty overwrite. */
class NotesDocument(private val diagnosticBytes: Boolean = false) {
    var text: String = ""
        private set
    var canSave: Boolean = false
        private set
    var status: NoteStatus = NoteStatus.READ_FAILED
        private set

    fun load() {
        canSave = false
        val result = MingPrivateFiles.read("note.txt", 0, 513)
        MingLog.event(9700, result.code)
        val bytes = result.bytes
        val decoded = if (result.code == 0 && bytes != null) decode(bytes) else null
        when {
            result.code == MingPrivateFiles.NOT_FOUND -> { text = ""; canSave = true; status = NoteStatus.NEW }
            decoded != null -> { text = decoded; canSave = true; status = NoteStatus.LOADED }
            result.code == 0 -> status = NoteStatus.INVALID_TEXT
            else -> status = NoteStatus.READ_FAILED
        }
        val length = when { decoded != null -> bytes!!.size; result.code == MingPrivateFiles.NOT_FOUND -> 0; else -> -1 }
        MingLog.event(9701, length)
        MingLog.event(9703, if (canSave) 1 else 0)
        if (decoded != null && diagnosticBytes) logBytes(9704, bytes!!)
    }

    fun save(value: String) {
        if (!canSave) return
        val bytes = value.encodeToByteArray()
        if (decode(bytes) != value) { status = NoteStatus.INVALID_TEXT; return }
        val written = MingPrivateFiles.write("note.txt", bytes)
        MingLog.event(9710, written.code)
        MingLog.event(9711, when (written.directorySynced) { true -> 1; false -> 0; null -> -1 })
        if (written.code != 0) { status = NoteStatus.WRITE_FAILED; return }
        val readback = MingPrivateFiles.read("note.txt", 0, 513)
        val match = readback.code == 0 && readback.bytes?.contentEquals(bytes) == true
        MingLog.event(9712, readback.code)
        MingLog.event(9713, if (match) 1 else 0)
        MingLog.event(9715, if (match) bytes.size else -1)
        if (!match) { canSave = false; status = NoteStatus.READBACK_FAILED; return }
        if (diagnosticBytes) logBytes(9714, bytes)
        text = value
        status = if (written.directorySynced == true) NoteStatus.SAVED else NoteStatus.SYNC_UNCONFIRMED
    }

    private fun decode(bytes: ByteArray): String? {
        if (bytes.size > 512) return null
        return try {
            val value = bytes.decodeToString(throwOnInvalidSequence = true)
            if (value.any { it.code < 32 || it.code == 127 || it.code == 0x85 || it.code in 0x2028..0x2029 }) null
            else value.also { TextEditState(it) }
        } catch (_: Exception) { null }
    }

    private fun logBytes(code: Int, bytes: ByteArray) {
        check(bytes.size <= 512)
        for (byte in bytes) MingLog.event(code, byte.toInt() and 255)
    }
}
