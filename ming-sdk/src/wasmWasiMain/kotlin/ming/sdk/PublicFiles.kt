@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)

package ming.sdk

import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

class PublicFilesCallResult internal constructor(val code: Int, val reply: ByteArray?)
class PublicFilesReadResult internal constructor(val code: Int, val bytes: ByteArray?)
class PublicFilesWriteResult internal constructor(val code: Int, val directorySynced: Boolean?)
class PublicFilesEntry internal constructor(val name: String, val isDirectory: Boolean)
class PublicFilesListResult internal constructor(val code: Int, val entries: List<PublicFilesEntry>?)

private class PublicWireRequest(op: Int) {
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

private class PublicWireReply(val value: Int, val data: ByteArray?)

/** SDK6 access to the shared public directory after explicit parent authorization. */
object MingPublicFiles {
    const val DENIED: Int = -1
    const val INVALID_ARGUMENT: Int = -2
    const val NOT_FOUND: Int = -3
    const val IO: Int = -4
    const val MAX_REQUEST_BYTES: Int = 90_000
    const val MAX_REPLY_BYTES: Int = 90_000
    const val MAX_TRANSFER_BYTES: Int = 65_536
    const val MAX_FILE_BYTES: Int = 8 * 1024 * 1024
    const val MAX_LIST_ENTRIES: Int = 32

    private fun request(request: PublicWireRequest, outputSize: (Int) -> Int): PublicWireReply =
        withScopedMemoryAllocator { allocator ->
            val bytes = request.finish()
            val input = allocator.allocate(bytes.size)
            val output = allocator.allocate(MAX_TRANSFER_BYTES)
            for (index in bytes.indices) (input + index).storeByte(bytes[index])
            val value = publicFilesCallV2(input.address.toInt(), bytes.size, output.address.toInt(), MAX_TRANSFER_BYTES)
            if (value < 0) return@withScopedMemoryAllocator PublicWireReply(if (value in IO..DENIED) value else IO, null)
            val length = outputSize(value)
            if (length !in 0..MAX_TRANSFER_BYTES) return@withScopedMemoryAllocator PublicWireReply(IO, null)
            PublicWireReply(value, ByteArray(length) { (output + it).loadByte() })
        }

    /** Reads at most 64 KiB from an offset. */
    fun read(path: String, offset: Int, limit: Int): PublicFilesReadResult {
        val request = PublicWireRequest(1)
        if (offset < 0 || limit !in 0..MAX_TRANSFER_BYTES || !request.path(path) || !request.number(offset) || !request.number(limit))
            return PublicFilesReadResult(INVALID_ARGUMENT, null)
        val reply = request(request) { if (it <= limit) it else -1 }
        return if (reply.value < 0) PublicFilesReadResult(reply.value, null) else PublicFilesReadResult(0, reply.data)
    }

    /** Atomically replaces a file; false means directory sync was not confirmed. */
    fun write(path: String, bytes: ByteArray): PublicFilesWriteResult {
        if (bytes.size > MAX_FILE_BYTES) return PublicFilesWriteResult(INVALID_ARGUMENT, null)
        if (bytes.size <= MAX_TRANSFER_BYTES) {
            val request = PublicWireRequest(2)
            if (!request.path(path) || !request.field(bytes)) return PublicFilesWriteResult(INVALID_ARGUMENT, null)
            return writeResult(request(request) { if (it == bytes.size) 1 else -1 })
        }
        val begin = PublicWireRequest(3)
        if (!begin.path(path) || !begin.number(bytes.size)) return PublicFilesWriteResult(INVALID_ARGUMENT, null)
        val started = request(begin) { if (it == 0) 0 else -1 }
        if (started.value < 0) return PublicFilesWriteResult(started.value, null)
        var offset = 0
        while (offset < bytes.size) {
            val end = minOf(offset + MAX_TRANSFER_BYTES, bytes.size)
            val chunk = PublicWireRequest(4)
            chunk.number(offset)
            chunk.field(bytes.copyOfRange(offset, end))
            val reply = request(chunk) { if (it == end - offset) 0 else -1 }
            if (reply.value < 0) {
                request(PublicWireRequest(6)) { if (it == 0) 0 else -1 }
                return PublicFilesWriteResult(reply.value, null)
            }
            offset = end
        }
        return writeResult(request(PublicWireRequest(5)) { if (it == 0) 1 else -1 })
    }

    private fun writeResult(reply: PublicWireReply): PublicFilesWriteResult {
        if (reply.value < 0) return PublicFilesWriteResult(reply.value, null)
        return when (reply.data?.singleOrNull()?.toInt()) {
            0 -> PublicFilesWriteResult(0, false)
            1 -> PublicFilesWriteResult(0, true)
            else -> PublicFilesWriteResult(IO, null)
        }
    }

    /** Lists one page; entries can change between pages. */
    fun list(path: String, offset: Int, limit: Int): PublicFilesListResult {
        val request = PublicWireRequest(7)
        if (offset < 0 || limit !in 0..MAX_LIST_ENTRIES || !request.path(path) || !request.number(offset) || !request.number(limit))
            return PublicFilesListResult(INVALID_ARGUMENT, null)
        val reply = request(request) { if (it <= limit) MAX_TRANSFER_BYTES else -1 }
        if (reply.value < 0) return PublicFilesListResult(reply.value, null)
        val bytes = reply.data ?: return PublicFilesListResult(IO, null)
        val entries = mutableListOf<PublicFilesEntry>()
        var position = 0
        try {
            repeat(reply.value) {
                if (position > bytes.size - 3) return PublicFilesListResult(IO, null)
                val length = (bytes[position].toInt() and 255) or ((bytes[position + 1].toInt() and 255) shl 8)
                val kind = bytes[position + 2].toInt()
                position += 3
                if (length !in 1..255 || position > bytes.size - length || kind !in 0..1) return PublicFilesListResult(IO, null)
                val name = bytes.decodeToString(position, position + length, throwOnInvalidSequence = true)
                entries.add(PublicFilesEntry(name, kind == 1))
                position += length
            }
        } catch (_: IllegalArgumentException) { return PublicFilesListResult(IO, null) }
        return PublicFilesListResult(0, entries)
    }

    fun createDirectory(path: String): PublicFilesWriteResult = sync(8, path)
    fun removeDirectory(path: String): PublicFilesWriteResult = sync(9, path)
    fun delete(path: String): PublicFilesWriteResult = sync(10, path)
    fun rename(from: String, to: String): PublicFilesWriteResult = sync(11, from, to)

    private fun sync(op: Int, path: String, to: String? = null): PublicFilesWriteResult {
        val request = PublicWireRequest(op)
        if (!request.path(path) || (to != null && !request.path(to))) return PublicFilesWriteResult(INVALID_ARGUMENT, null)
        val reply = request(request) { if (it in 0..1) 0 else -1 }
        return if (reply.value < 0) PublicFilesWriteResult(reply.value, null) else PublicFilesWriteResult(0, reply.value == 1)
    }

    /** Sends a bounded UTF-8 JSON request using the legacy Linux worker interface. */
    fun call(request: ByteArray): PublicFilesCallResult {
        if (request.isEmpty() || request.size > MAX_REQUEST_BYTES) return PublicFilesCallResult(INVALID_ARGUMENT, null)
        return withScopedMemoryAllocator { allocator ->
            val input = allocator.allocate(request.size)
            val output = allocator.allocate(MAX_REPLY_BYTES)
            for (index in request.indices) (input + index).storeByte(request[index])
            val length = publicFilesCall(input.address.toInt(), request.size, output.address.toInt(), MAX_REPLY_BYTES)
            when {
                length in IO..DENIED -> PublicFilesCallResult(length, null)
                length !in 1..MAX_REPLY_BYTES -> PublicFilesCallResult(INVALID_ARGUMENT, null)
                else -> PublicFilesCallResult(0, ByteArray(length) { (output + it).loadByte() })
            }
        }
    }
}

@WasmImport("ming_public_files_v1", "call")
private external fun publicFilesCall(input: Int, length: Int, output: Int, capacity: Int): Int

@WasmImport("ming_public_files_v2", "call")
private external fun publicFilesCallV2(input: Int, length: Int, output: Int, capacity: Int): Int
