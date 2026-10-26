import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
export async function load(contract = false) {
  return new WebAssembly.Module(await readFile(new URL(contract
    ? '../../../artifacts/shadow-kotlin/contract/main.wasm' : '../../../artifacts/shadow-demo/main.wasm', import.meta.url)));
}

// ABI/lifetime boundary double only; Kotlin is always the compiled production SDK/application.
// Pixel/cache acceptance runs separately against the real Wasmtime executable.
export function run(module, {events = [], contract = false, endlessIdle = false,
    createFailureAt = -1, createReply = -4, textFailureAt = -1, metricsFailureAt = -1,
    submitFailureAt = -1, closeReply = 0} = {}) {
  let instance, nextStyle = 0x40000001;
  const styles = new Map(), layouts = new Map();
  const state = {creates: [], styles: [], closes: [], texts: [], textCloses: [],
    metrics: [], frames: [], logs: [], polls: 0};
  const bytes = (p, n) => new Uint8Array(instance.exports.memory.buffer, p, n);
  const view = () => new DataView(instance.exports.memory.buffer);
  const imports = {
    ming_v1: {log(code, value) {
      if (code === 70) assert.equal(state.creates.length, 0, 'invalid styles stay local');
      if (code === 71) assert.equal(state.frames.length, 0, 'invalid builders stay local');
      state.logs.push([code, value]); return 0;
    }},
    ming_v2: {
      random_fill(p, n) { bytes(p, n).fill(37); return 0; },
      event_next(p, n) {
        assert.equal(n, 16);
        const e = events[state.polls++] ?? [endlessIdle ? 8 : 0, 0, 0, 0];
        e.forEach((v, i) => view().setInt32(p + i * 4, v, true)); return 16;
      },
    },
    ming_v3: {
      text_layout_create(p, n, font, em, flags) {
        const i = state.texts.length;
        if (i === textFailureAt) return -5;
        assert.equal(font, 1); assert.equal(flags, 0);
        const text = new TextDecoder('utf8', {fatal: true}).decode(bytes(p, n));
        const handle = 0x30000001 + i;
        const metrics = [text.length * em, em * 3 / 4, em / 4, 0, em, 0, -em * 3 / 4,
          text.length * em, em / 4, text.length, 0, 7];
        layouts.set(handle, metrics); state.texts.push({handle, text}); return handle;
      },
      text_layout_metrics(h, p, n) {
        assert.equal(n, 48); const i = state.metrics.length; state.metrics.push(h);
        if (i === metricsFailureAt || !layouts.has(h)) return -2;
        layouts.get(h).forEach((v, i) => view().setInt32(p + i * 4, v, true)); return 48;
      },
      text_layout_close(h) {
        state.textCloses.push(h); assert.ok(layouts.delete(h), 'text closed exactly once'); return closeReply;
      },
    },
    ming_v4: {
      shadow_style_create(radius, blur) {
        const i = state.creates.length; state.creates.push([radius, blur]);
        assert.ok(radius >= 0 && radius <= 64 && blur >= 0 && blur <= 16);
        if (i === createFailureAt) return createReply;
        if (styles.size === 8) return -4;
        const handle = nextStyle++; styles.set(handle, {radius, blur});
        state.styles.push({handle, radius, blur}); return handle;
      },
      shadow_style_close(h) {
        state.closes.push(h);
        if (!styles.delete(h)) { assert.ok(contract, 'style closed exactly once'); return -2; }
        return closeReply;
      },
      frame_submit(p, n) {
        const data = Buffer.from(bytes(p, n));
        assert.equal(data.readInt32LE(0), 0x4d474632); assert.equal(data.readInt32LE(4), 1);
        assert.equal(data.readInt32LE(20), 0);
        const width = data.readInt32LE(8), height = data.readInt32LE(12), count = data.readInt32LE(16);
        assert.ok(width >= 1 && width <= 4096 && height >= 1 && height <= 4096);
        assert.ok(width * height <= 4194304 && count <= 256); assert.equal(n, 24 + count * 32);
        const commands = Array.from({length: count}, (_, i) =>
          Array.from({length: 8}, (_, j) => data.readInt32LE(24 + i * 32 + j * 4)));
        const i = state.frames.length;
        state.frames.push({width, height, commands, styles: state.styles.length, texts: state.texts.length});
        if (commands.some(c => c[0] === 7 && !styles.has(c[1]) || c[0] === 5 && !layouts.has(c[1]))) return -2;
        return i === submitFailureAt ? -4 : 0;
      },
    },
  };
  instance = new WebAssembly.Instance(module, imports); instance.exports._start();
  assert.equal(styles.size, 0, 'all owned styles close on exit');
  assert.equal(layouts.size, 0, 'all owned layouts close on exit');
  return state;
}
