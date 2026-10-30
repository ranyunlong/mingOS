@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)

package ming.sdk

import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

class PrivateFilesCallResult internal constructor(val code: Int, val reply: ByteArray?)
class PrivateFilesReadResult internal constructor(val code: Int, val bytes: ByteArray?)
class PrivateFilesWriteResult internal constructor(val code: Int, val directorySynced: Boolean?)
class PrivateFilesEntry internal constructor(val name: String, val isDirectory: Boolean)
class PrivateFilesListResult internal constructor(val code: Int, val entries: List<PrivateFilesEntry>?)

private class WireRequest(op: Int) {
    private val bytes = ByteArray(70_000)
    private var size = 1
    init { bytes[0] = op.toByte() }
    fun number(value: Int): Boolean {
        if (value < 0 || size > bytes.size - 4) return false
        repeat(4) { bytes[size++] = (value ushr (it * 8)).toByte() }
        return true
    }
    fun field(value: ByteArray): Boolean {
        if (value.size > bytes.size - size - 4 || !number(value.size)) return false
        value.copyInto(bytes, size)
        size += value.size
        return true
    }
    fun path(value: String): Boolean {
        val encoded = value.encodeToByteArray()
        return encoded.size <= 1024 && field(encoded)
    }
    fun finish(): ByteArray = bytes.copyOf(size)
}

private class WireReply(val value: Int, val data: ByteArray?)

/** SDK6 private files are scoped and authorized by the parent process. */
object MingPrivateFiles {
    const val DENIED: Int = -1
    const val INVALID_ARGUMENT: Int = -2
    const val NOT_FOUND: Int = -3
    const val IO: Int = -4
    const val MAX_REQUEST_BYTES: Int = 90_000
    const val MAX_REPLY_BYTES: Int = 90_000
    const val MAX_TRANSFER_BYTES: Int = 65_536
    const val MAX_FILE_BYTES: Int = 8 * 1024 * 1024
    const val MAX_LIST_ENTRIES: Int = 32

    private fun request(request: WireRequest, outputSize: (Int) -> Int): WireReply =
        withScopedMemoryAllocator { allocator ->
            val bytes = request.finish()
            val input = allocator.allocate(bytes.size)
            val output = allocator.allocate(MAX_TRANSFER_BYTES)
            for (index in bytes.indices) (input + index).storeByte(bytes[index])
            val value = privateFilesCallV2(input.address.toInt(), bytes.size, output.address.toInt(), MAX_TRANSFER_BYTES)
            if (value < 0) return@withScopedMemoryAllocator WireReply(if (value in IO..DENIED) value else IO, null)
            val length = outputSize(value)
            if (length !in 0..MAX_TRANSFER_BYTES) return@withScopedMemoryAllocator WireReply(IO, null)
            WireReply(value, ByteArray(length) { (output + it).loadByte() })
        }

    /** Reads at most 64 KiB from an offset. */
    fun read(path: String, offset: Int, limit: Int): PrivateFilesReadResult {
        val request = WireRequest(1)
        if (offset < 0 || limit !in 0..MAX_TRANSFER_BYTES || !request.path(path) || !request.number(offset) || !request.number(limit))
            return PrivateFilesReadResult(INVALID_ARGUMENT, null)
        val reply = request(request) { if (it <= limit) it else -1 }
        return if (reply.value < 0) PrivateFilesReadResult(reply.value, null) else PrivateFilesReadResult(0, reply.data)
    }

    /** Atomically replaces a file; false means directory sync was not confirmed. */
    fun write(path: String, bytes: ByteArray): PrivateFilesWriteResult {
        if (bytes.size > MAX_FILE_BYTES) return PrivateFilesWriteResult(INVALID_ARGUMENT, null)
        if (bytes.size <= MAX_TRANSFER_BYTES) {
            val request = WireRequest(2)
            if (!request.path(path) || !request.field(bytes)) return PrivateFilesWriteResult(INVALID_ARGUMENT, null)
            return writeResult(request(request) { if (it == bytes.size) 1 else -1 })
        }
        val begin = WireRequest(3)
        if (!begin.path(path) || !begin.number(bytes.size)) return PrivateFilesWriteResult(INVALID_ARGUMENT, null)
        val started = request(begin) { if (it == 0) 0 else -1 }
        if (started.value < 0) return PrivateFilesWriteResult(started.value, null)
        var offset = 0
        while (offset < bytes.size) {
            val end = minOf(offset + MAX_TRANSFER_BYTES, bytes.size)
            val chunk = WireRequest(4)
            chunk.number(offset)
            chunk.field(bytes.copyOfRange(offset, end))
            val reply = request(chunk) { if (it == end - offset) 0 else -1 }
            if (reply.value < 0) {
                request(WireRequest(6)) { if (it == 0) 0 else -1 }
                return PrivateFilesWriteResult(reply.value, null)
            }
            offset = end
        }
        return writeResult(request(WireRequest(5)) { if (it == 0) 1 else -1 })
    }

    private fun writeResult(reply: WireReply): PrivateFilesWriteResult {
        if (reply.value < 0) return PrivateFilesWriteResult(reply.value, null)
        return when (reply.data?.singleOrNull()?.toInt()) {
            0 -> PrivateFilesWriteResult(0, false)
            1 -> PrivateFilesWriteResult(0, true)
            else -> PrivateFilesWriteResult(IO, null)
        }
    }

    /** Lists one page; entries can change between pages. */
    fun list(path: String, offset: Int, limit: Int): PrivateFilesListResult {
        val request = WireRequest(7)
        if (offset < 0 || limit !in 0..MAX_LIST_ENTRIES || !request.path(path) || !request.number(offset) || !request.number(limit))
            return PrivateFilesListResult(INVALID_ARGUMENT, null)
        val reply = request(request) { if (it <= limit) MAX_TRANSFER_BYTES else -1 }
        if (reply.value < 0) return PrivateFilesListResult(reply.value, null)
        val bytes = reply.data ?: return PrivateFilesListResult(IO, null)
        val entries = mutableListOf<PrivateFilesEntry>()
        var position = 0
        try {
            repeat(reply.value) {
                if (position > bytes.size - 3) return PrivateFilesListResult(IO, null)
                val length = (bytes[position].toInt() and 255) or ((bytes[position + 1].toInt() and 255) shl 8)
                val kind = bytes[position + 2].toInt()
                position += 3
                if (length !in 1..255 || position > bytes.size - length || kind !in 0..1) return PrivateFilesListResult(IO, null)
                val name = bytes.decodeToString(position, position + length, throwOnInvalidSequence = true)
                entries.add(PrivateFilesEntry(name, kind == 1))
                position += length
            }
        } catch (_: IllegalArgumentException) { return PrivateFilesListResult(IO, null) }
        return PrivateFilesListResult(0, entries)
    }

    fun createDirectory(path: String): PrivateFilesWriteResult = sync(8, path)
    fun removeDirectory(path: String): PrivateFilesWriteResult = sync(9, path)
    fun delete(path: String): PrivateFilesWriteResult = sync(10, path)
    fun rename(from: String, to: String): PrivateFilesWriteResult = sync(11, from, to)

    private fun sync(op: Int, path: String, to: String? = null): PrivateFilesWriteResult {
        val request = WireRequest(op)
        if (!request.path(path) || (to != null && !request.path(to))) return PrivateFilesWriteResult(INVALID_ARGUMENT, null)
        val reply = request(request) { if (it in 0..1) 0 else -1 }
        return if (reply.value < 0) PrivateFilesWriteResult(reply.value, null) else PrivateFilesWriteResult(0, reply.value == 1)
    }

    /** Sends a UTF-8 JSON request using the legacy Linux worker interface. */
    fun call(request: ByteArray): PrivateFilesCallResult {
        if (request.isEmpty() || request.size > MAX_REQUEST_BYTES) return PrivateFilesCallResult(INVALID_ARGUMENT, null)
        return withScopedMemoryAllocator { allocator ->
            val input = allocator.allocate(request.size)
            val output = allocator.allocate(MAX_REPLY_BYTES)
            for (index in request.indices) (input + index).storeByte(request[index])
            val length = privateFilesCall(input.address.toInt(), request.size, output.address.toInt(), MAX_REPLY_BYTES)
            when {
                length in IO..DENIED -> PrivateFilesCallResult(length, null)
                length !in 1..MAX_REPLY_BYTES -> PrivateFilesCallResult(INVALID_ARGUMENT, null)
                else -> PrivateFilesCallResult(0, ByteArray(length) { (output + it).loadByte() })
            }
        }
    }
}

@WasmImport("ming_private_files_v1", "call")
private external fun privateFilesCall(input: Int, length: Int, output: Int, capacity: Int): Int

@WasmImport("ming_private_files_v2", "call")
private external fun privateFilesCallV2(input: Int, length: Int, output: Int, capacity: Int): Int
