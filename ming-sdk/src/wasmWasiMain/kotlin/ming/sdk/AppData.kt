@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)

package ming.sdk

import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

/** code=0 owns detached bytes (possibly empty); negative code has bytes=null. */
class AppDataReadResult internal constructor(val code: Int, val bytes: ByteArray?)

/**
 * Optional SDK6 capability. The parent binds a catalog grant; SDK version alone grants nothing.
 * Slots are positive IDs 1..8, never host paths or manifest identities. Missing grants return DENIED.
 * Each slot is at most 65536 bytes (eight slots total); calls do not grant execution fuel.
 */
object MingAppData {
    const val DENIED: Int = -1
    const val INVALID_ARGUMENT: Int = -2
    const val NOT_FOUND: Int = -3
    const val IO: Int = -4
    const val MAX_SLOT_BYTES: Int = 65536

    /** Returns size, or a negative error. An empty stored value has size 0; missing is -3. */
    fun size(slot: Int): Int {
        if (slot !in 1..8) return INVALID_ARGUMENT
        val result = dataSize(slot)
        return if (result <= MAX_SLOT_BYTES) result else INVALID_ARGUMENT
    }

    /** Reads a detached slice. EOF and capacity=0 succeed with an empty ByteArray. */
    fun read(slot: Int, offset: Int = 0, capacity: Int = MAX_SLOT_BYTES): AppDataReadResult {
        if (slot !in 1..8 || offset !in 0..MAX_SLOT_BYTES || capacity !in 0..MAX_SLOT_BYTES) {
            return AppDataReadResult(INVALID_ARGUMENT, null)
        }
        return withScopedMemoryAllocator { allocator ->
            val output = allocator.allocate(maxOf(1, capacity))
            val length = dataRead(slot, offset, output.address.toInt(), capacity)
            when {
                length < 0 -> AppDataReadResult(length, null)
                length > capacity -> AppDataReadResult(INVALID_ARGUMENT, null)
                else -> AppDataReadResult(0, ByteArray(length) { (output + it).loadByte() })
            }
        }
    }

    /** Atomically replaces the entire slot. Success returns bytes.size, including 0 for empty. */
    fun write(slot: Int, bytes: ByteArray): Int {
        if (slot !in 1..8 || bytes.size > MAX_SLOT_BYTES) return INVALID_ARGUMENT
        return withScopedMemoryAllocator { allocator ->
            val input = allocator.allocate(maxOf(1, bytes.size))
            for (index in bytes.indices) (input + index).storeByte(bytes[index])
            val result = dataWrite(slot, input.address.toInt(), bytes.size)
            if (result < 0 || result == bytes.size) result else INVALID_ARGUMENT
        }
    }

    /** Success is 0; missing is NOT_FOUND. No host resource or guest close operation is owned. */
    fun delete(slot: Int): Int {
        if (slot !in 1..8) return INVALID_ARGUMENT
        val result = dataDelete(slot)
        return if (result <= 0) result else INVALID_ARGUMENT
    }
}

@WasmImport("ming_data_v1", "read")
private external fun dataRead(slot: Int, offset: Int, outputPointer: Int, capacity: Int): Int

@WasmImport("ming_data_v1", "write")
private external fun dataWrite(slot: Int, pointer: Int, length: Int): Int

@WasmImport("ming_data_v1", "size")
private external fun dataSize(slot: Int): Int

@WasmImport("ming_data_v1", "delete")
private external fun dataDelete(slot: Int): Int
