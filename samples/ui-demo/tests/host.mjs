import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {adaptKotlinWasi} from '../../../scripts/wasm-sdk-adapter.mjs';

export async function loadContract() {
  const raw = await readFile(new URL('../build/sdk-contract-artifact/sdk-contract.wasm', import.meta.url));
  return new WebAssembly.Module(adaptKotlinWasi(raw).bytes);
}
export async function loadDemo() {
  return new WebAssembly.Module(await readFile(new URL('../../../artifacts/ui-demo/main.wasm', import.meta.url)));
}

// Only an ABI/lifetime host double. Pixel correctness belongs to the real Rust host.
export function run(module, {events = [], endlessIdle = false, createFailureAt = -1,
    metricsFailureAt = -1, submitFailureAt = -1, closeReply = 0,
    contract = false} = {}) {
  let instance;
  const state = {created: [], metrics: [], closed: [], frames: [], logs: [], polls: 0};
  const active = new Map();
  const memory = (p, n) => new Uint8Array(instance.exports.memory.buffer, p, n);
  const view = () => new DataView(instance.exports.memory.buffer);
  const imports = {
    ming_v1: {log(code, value) {
      if (code === 80) assert.equal(state.frames.length, 0, 'local rejection must never submit');
      state.logs.push([code, value]); return 0;
    }},
    ming_v2: {
      random_fill(p, n) { memory(p, n).fill(37); return 0; },
      event_next(p, capacity) {
        assert.equal(capacity, 16);
        const event = events[state.polls++] ?? [endlessIdle ? 8 : 0, 0, 0, 0];
        event.forEach((v, i) => view().setInt32(p + i * 4, v, true));
        return 16;
      },
    },
    ming_v3: {
      text_layout_create(p, n, font, em, flags) {
        assert.equal(font, 1); assert.equal(flags, 0); assert.ok(n <= 512);
        assert.ok(em >= 512 && em <= 6144 && em % 16 === 0);
        if (state.created.length === createFailureAt) return -5;
        const text = new TextDecoder('utf8', {fatal: true}).decode(memory(p, n));
        const handle = 0x30000001 + state.created.length;
        const advance = Array.from(text).reduce((sum, ch) => sum + (ch.charCodeAt(0) > 255 ? em : em / 2), 0) + 17;
        const asc = em * 3 / 4 + 5, desc = em / 4 + 3;
        const metrics = [advance, asc, desc, 16, asc + desc + 16, -17, -asc,
          advance - 8, desc, Array.from(text).length, 0, 7];
        state.created.push({handle, text, em}); active.set(handle, metrics); return handle;
      },
      text_layout_metrics(handle, p, n) {
        assert.equal(n, 48);
        const index = state.metrics.length; state.metrics.push(handle);
        if (!active.has(handle) || index === metricsFailureAt) return -2;
        active.get(handle).forEach((v, i) => view().setInt32(p + i * 4, v, true));
        return 48;
      },
      text_layout_close(handle) {
        state.closed.push(handle);
        if (!active.delete(handle)) { assert.ok(contract, 'layout closed more than once'); return -2; }
        return closeReply;
      },
    },
    ming_v4: {frame_submit(p, n) {
      const data = Buffer.from(memory(p, n));
      assert.equal(data.readInt32LE(0), 0x4d474632);
      assert.equal(data.readInt32LE(4), 1); assert.equal(data.readInt32LE(20), 0);
      const width = data.readInt32LE(8), height = data.readInt32LE(12), count = data.readInt32LE(16);
      assert.ok(width > 0 && width <= 4096 && height > 0 && height <= 4096);
      assert.ok(width * height <= 4194304); assert.ok(count <= 256);
      assert.equal(n, 24 + count * 32);
      const commands = Array.from({length: count}, (_, i) =>
        Array.from({length: 8}, (_, j) => data.readInt32LE(24 + i * 32 + j * 4)));
      assert.ok(commands.filter(c => c[0] === 6).length <= 16);
      for (const c of commands) {
        assert.ok([1, 2, 4, 5, 6].includes(c[0]));
        if (c[0] === 1 || c[0] === 6) {
          assert.ok(Math.abs(c[1]) <= 1000000 && Math.abs(c[2]) <= 1000000);
          assert.ok(c[3] > 0 && c[3] <= 1000000 && c[4] > 0 && c[4] <= 1000000);
          assert.ok(c[6] >= 0 && c[6] <= 64); assert.equal(c[7], 0);
        }
        if (c[0] === 5) {
          assert.ok(Math.abs(c[2]) <= 64000000 && Math.abs(c[3]) <= 64000000);
          assert.deepEqual(c.slice(5), [0, 0, 0]);
        }
      }
      const index = state.frames.length;
      state.frames.push({width, height, commands, created: state.created.length});
      if (commands.some(c => c[0] === 5 && !active.has(c[1]))) return -2;
      return index === submitFailureAt ? -4 : 0;
    }},
  };
  instance = new WebAssembly.Instance(module, imports);
  instance.exports._start();
  assert.equal(active.size, 0, 'owned layouts must close on every exit');
  return state;
}
