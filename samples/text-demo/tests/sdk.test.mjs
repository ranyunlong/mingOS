import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {adaptKotlinWasi} from '../../../scripts/wasm-sdk-adapter.mjs';

// The probe has a separate build directory and never replaces artifacts/text-demo.
test('real Kotlin SDK enforces style/UTF8/Q6/frame bounds and preserves exact metric words', async () => {
  const raw = await readFile(new URL('../build/sdk-contract-artifact/sdk-contract.wasm', import.meta.url));
  const {bytes} = adaptKotlinWasi(raw);
  const active = new Set(), created = [], closes = [], frames = [], logs = [];
  let instance;
  const view = () => new DataView(instance.exports.memory.buffer);
  const imports = {
    ming_v1: {log(code, value) { logs.push([code, value]); return 0; }},
    ming_v2: {random_fill(ptr, length) {
      new Uint8Array(instance.exports.memory.buffer, ptr, length).fill(37); return 0;
    }},
    ming_v3: {
      text_layout_create(ptr, length, font, em, flags) {
        const text = new TextDecoder().decode(new Uint8Array(instance.exports.memory.buffer, ptr, length));
        const handle = 0x30000001 + created.length;
        created.push({text, length, font, em, flags, handle}); active.add(handle); return handle;
      },
      text_layout_metrics(handle, ptr, capacity) {
        assert.equal(capacity, 48);
        if (!active.has(handle)) return -2;
        [101, 202, 303, 404, 909, -17, -202, 83, 303, 7, 1, 23]
          .forEach((value, index) => view().setInt32(ptr + index * 4, value, true));
        return 48;
      },
      text_layout_close(handle) { closes.push(handle); return active.delete(handle) ? 0 : -2; },
      frame_submit(ptr, length) {
        const data = Buffer.from(new Uint8Array(instance.exports.memory.buffer, ptr, length));
        const n = data.readInt32LE(16);
        assert.equal(data.readInt32LE(0), 0x4d474632);
        assert.equal(data.readInt32LE(4), 1); assert.equal(data.readInt32LE(20), 0);
        assert.equal(length, 24 + n * 32);
        const commands = Array.from({length: n}, (_, index) =>
          Array.from({length: 8}, (_, word) => data.readInt32LE(24 + index * 32 + word * 4)));
        frames.push(commands);
        return commands.some(c => c[0] === 5 && !active.has(c[1])) ? -2 : 0;
      },
    },
  };
  instance = new WebAssembly.Instance(new WebAssembly.Module(bytes), imports);
  instance.exports._start();
  assert.equal(created.length, 3, 'invalid creates must not call the host');
  assert.deepEqual(created.map(c => [c.length, c.font, c.em, c.flags]), [[0, 1, 512, 0], [512, 1, 6144, 1], [2, 1, 1040, 0]]);
  assert.equal(created[1].text, '中'.repeat(170) + 'ab');
  assert.equal(frames.length, 3, 'only two valid frames and one stale-handle host check');
  assert.deepEqual(frames[0], [
    [1, -1000000, 1000000, 1000000, 1000000, -1, 64, 0],
    [2, 1, 1000000, -1000000, 1, 1, 0, 0],
    [4, 0, 0, 0, 0, 0, 0, 0],
    [5, created[0].handle, -64000000, 64000000, 0x80ffffff | 0, 0, 0, 0],
    [5, created[0].handle, 72, -80, -1, 0, 0, 0],
    [5, created[1].handle, 64000000, -64000000, -1, 0, 0, 0],
  ]);
  assert.equal(frames[1].length, 256);
  assert.deepEqual(closes, [created[0].handle, created[0].handle, created[1].handle, created[2].handle]);
  assert.equal(active.size, 0);
  assert.deepEqual(logs, [[60, 0]], 'all Kotlin-side check assertions must have reached completion');
});
