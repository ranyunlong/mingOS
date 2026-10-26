@file:OptIn(kotlin.wasm.ExperimentalWasmInterop::class, kotlin.wasm.unsafe.UnsafeWasmMemoryApi::class)
package ming.sdk
import kotlin.wasm.WasmImport
import kotlin.wasm.unsafe.withScopedMemoryAllocator

class BufferRead(val count: Int, val bytes: ByteArray)
object MingBuffers {
 fun read(handle: Int, offset: Int, length: Int): BufferRead {
  if (length < 0 || length > 65536) return BufferRead(-2, ByteArray(0))
  return withScopedMemoryAllocator { allocator ->
   val pointer = allocator.allocate(if (length == 0) 1 else length)
   val count = readBuffer(handle, offset, pointer.address.toInt(), length)
   if (count < 0 || count > length) BufferRead(if(count < 0) count else -2, ByteArray(0))
   else BufferRead(count, ByteArray(count) { (pointer + it).loadByte() })
  }
 }
}
object MingImages {
 fun open(file: Int): Int = imageOpen(file)
 fun width(image: Int): Int = imageWidth(image)
 fun height(image: Int): Int = imageHeight(image)
 fun close(image: Int): Int = imageClose(image)
}
class InputEvent(val type: Int, val a: Int, val b: Int, val c: Int)
object MingInput {
 fun next(): InputEvent = withScopedMemoryAllocator { allocator ->
  val pointer = allocator.allocate(16)
  if (nextEvent(pointer.address.toInt(), 16) != 16) InputEvent(0,0,0,0)
  else InputEvent(pointer.loadInt(), (pointer + 4).loadInt(), (pointer + 8).loadInt(), (pointer + 12).loadInt())
 }
}
class MingFrame(private val width: Int, private val height: Int) {
 private val commands = ArrayList<Int>()
 private val strings = ArrayList<Byte>()
 private fun command(op: Int, a: Int, b: Int, c: Int, d: Int, e: Int, f: Int = 0, g: Int = 0) {
  commands.add(op); commands.add(a); commands.add(b); commands.add(c); commands.add(d); commands.add(e); commands.add(f); commands.add(g)
 }
 fun rect(x: Int, y: Int, w: Int, h: Int, color: Int, radius: Int = 0) = command(1,x,y,w,h,color,radius)
 fun image(handle: Int, x: Int, y: Int, w: Int, h: Int) = command(2,handle,x,y,w,h)
 fun clip(x: Int, y: Int, w: Int, h: Int) = command(4,x,y,w,h,0)
 fun text(x: Int, y: Int, value: String, color: Int, scale: Int = 2) {
  val bytes = value.encodeToByteArray(); val offset = strings.size
  for (byte in bytes) strings.add(byte)
  command(3,x,y,scale,color,offset,bytes.size)
 }
 fun submit(): Int {
  val size = 24 + commands.size * 4 + strings.size
  if (size > 65536 || commands.size / 8 > 256 || strings.size > 4096) return -2
  return withScopedMemoryAllocator { allocator ->
   val pointer = allocator.allocate(size)
   val header = intArrayOf(0x4d474632,1,width,height,commands.size / 8,strings.size)
   for (i in header.indices) (pointer + i * 4).storeInt(header[i])
   for (i in commands.indices) (pointer + 24 + i * 4).storeInt(commands[i])
   for (i in strings.indices) (pointer + 24 + commands.size * 4 + i).storeByte(strings[i])
   submitFrame(pointer.address.toInt(),size)
  }
 }
}
@WasmImport("ming_v2", "file_read") private external fun readBuffer(handle: Int, offset: Int, pointer: Int, length: Int): Int
@WasmImport("ming_v2", "image_open") private external fun imageOpen(handle: Int): Int
@WasmImport("ming_v2", "image_width") private external fun imageWidth(handle: Int): Int
@WasmImport("ming_v2", "image_height") private external fun imageHeight(handle: Int): Int
@WasmImport("ming_v2", "image_close") private external fun imageClose(handle: Int): Int
@WasmImport("ming_v2", "frame_submit") private external fun submitFrame(pointer: Int, length: Int): Int
@WasmImport("ming_v2", "event_next") private external fun nextEvent(pointer: Int, capacity: Int): Int
