import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const artifact = new URL('../../../artifacts/text-demo/', import.meta.url);
const module = async () => new WebAssembly.Module(await readFile(new URL('main.wasm', artifact)));
const specimen = 'MingOS 中文，标点。AV fi é A\u0301';

// This is an ABI test double, not a renderer or a v1 reference-host extension.
async function run({events = [], createFailureAt = -1, createReply = -5,
    metricsFailureAt = -1, metricsReply = -2, submitFailureAt = -1,
    closeReply = 0, endlessIdle = false} = {}) {
  const state = {created: [], metrics: [], closed: [], frames: [], logs: [], polls: 0};
  const active = new Map();
  let instance;
  const view = () => new DataView(instance.exports.memory.buffer);
  const bytes = (ptr, length) => new Uint8Array(instance.exports.memory.buffer, ptr, length);
  const imports = {
    ming_v1: {log(code, value) { state.logs.push({code, value}); return 0; }},
    ming_v2: {
      random_fill(ptr, length) { bytes(ptr, length).fill(73); return 0; },
      event_next(ptr, capacity) {
        assert.equal(capacity, 16);
        const event = events[state.polls++] ?? [endlessIdle ? 8 : 0, 0, 0, 0];
        event.forEach((value, index) => view().setInt32(ptr + index * 4, value, true));
        return 16;
      },
    },
    ming_v3: {
      text_layout_create(ptr, length, font, em, flags) {
        assert.equal(font, 1); assert.equal(flags, 0);
        assert.ok(length >= 0 && length <= 512);
        assert.ok(em >= 8 * 64 && em <= 96 * 64 && em % 16 === 0);
        if (state.created.length === createFailureAt) return createReply;
        const text = new TextDecoder('utf-8', {fatal: true}).decode(bytes(ptr, length));
        const handle = 0x30000001 + state.created.length;
        // Deliberately fractional metrics and a negative bearing catch pixel truncation.
        const advance = Array.from(text).length * em / 2 + 17;
        const asc = em * 3 / 4 + 5, desc = em / 4 + 3, gap = 16;
        const metrics = [advance, asc, desc, gap, asc + desc + gap,
          -17, -asc, advance - 8, desc, Array.from(text).length, 0, 7];
        const layout = {handle, text, length, em, metrics};
        state.created.push(layout); active.set(handle, layout); return handle;
      },
      text_layout_metrics(handle, ptr, capacity) {
        assert.equal(capacity, 48); assert.ok(active.has(handle));
        const index = state.metrics.length; state.metrics.push(handle);
        if (index === metricsFailureAt) return metricsReply;
        active.get(handle).metrics.forEach((value, index) => view().setInt32(ptr + index * 4, value, true));
        return 48;
      },
      text_layout_close(handle) {
        assert.ok(active.delete(handle), 'each owned handle must close exactly once');
        state.closed.push(handle); return closeReply;
      },
      frame_submit(ptr, length) {
        const frame = Buffer.from(bytes(ptr, length));
        assert.equal(frame.readInt32LE(0), 0x4d474632);
        assert.equal(frame.readInt32LE(4), 1);
        const width = frame.readInt32LE(8), height = frame.readInt32LE(12);
        assert.ok(width >= 1 && width <= 4096 && height >= 1 && height <= 4096);
        assert.ok(width * height <= 4194304);
        const count = frame.readInt32LE(16), pool = frame.readInt32LE(20);
        assert.ok(count <= 256); assert.equal(pool, 0);
        assert.equal(length, 24 + count * 32);
        const commands = Array.from({length: count}, (_, index) =>
          Array.from({length: 8}, (_, word) => frame.readInt32LE(24 + index * 32 + word * 4)));
        for (const command of commands) {
          assert.ok([1, 2, 4, 5].includes(command[0]));
          if (command[0] === 5) {
            assert.ok(active.has(command[1]));
            assert.ok(Math.abs(command[2]) <= 64000000 && Math.abs(command[3]) <= 64000000);
            assert.deepEqual(command.slice(5), [0, 0, 0]);
          }
        }
        state.frames.push({width, height, commands, created: state.created.length});
        return state.frames.length - 1 === submitFailureAt ? -4 : 0;
      },
    },
  };
  instance = new WebAssembly.Instance(await module(), imports);
  instance.exports._start();
  assert.equal(active.size, 0, 'cleanup must run on every normal and error exit');
  return state;
}

test('SDK3 manifest needs no grants and executable uses only reviewed Ming imports', async () => {
  const manifest = JSON.parse(await readFile(new URL('app.json', artifact), 'utf8'));
  assert.equal(manifest.sdkVersion, 3); assert.equal(manifest.id, 'dev.ming.text-demo');
  assert.equal(manifest.entry, 'main.wasm'); assert.equal(manifest.grants, undefined);
  const imports = WebAssembly.Module.imports(await module());
  const names = imports.map(item => `${item.module}.${item.name}`);
  for (const name of ['text_layout_create', 'text_layout_metrics', 'text_layout_close', 'frame_submit']) {
    assert.ok(names.includes(`ming_v3.${name}`));
  }
  assert.ok(names.includes('ming_v1.log')); assert.ok(names.includes('ming_v2.event_next'));
  assert.ok(!names.includes('ming_v2.frame_submit'));
  const allowed = new Set(['ming_v1.log', 'ming_v2.random_fill', 'ming_v2.event_next',
    'ming_v3.text_layout_create', 'ming_v3.text_layout_metrics', 'ming_v3.text_layout_close', 'ming_v3.frame_submit']);
  assert.ok(imports.every(item => item.kind === 'function' && allowed.has(`${item.module}.${item.name}`)));
});

test('real Kotlin reuses measured 12/16/24/48px Chinese/Latin layouts across resize and fit', async () => {
  const result = await run({events: [[1, 640, 480, 0], [7, 0, 0, 0], [6, 0, 0, 0], [0, 0, 0, 0]]});
  assert.deepEqual(result.created.filter(layout => layout.text === specimen).map(layout => layout.em), [768, 1024, 1536, 3072]);
  assert.equal(result.frames.length, 4);
  assert.equal(result.metrics.length, result.created.length);
  assert.equal(result.closed.length, result.created.length);
  assert.ok(result.frames.every(frame => frame.created === result.created.length));
  for (const layout of result.created.filter(layout => layout.text === specimen)) {
    assert.ok(result.frames.every(frame => frame.commands.some(c => c[0] === 5 && c[1] === layout.handle)));
  }
  assert.ok(result.frames[0].commands.some(c => c[0] === 5 && c[3] % 64 !== 0), 'preserve fractional baselines');
  assert.ok(result.created.some(layout => layout.text.includes('advance') && layout.text.includes('Q6')));
  assert.deepEqual(result.logs.findLast(event => event.code === 40), {code: 40, value: 4});
});

test('clip and translucent overlay retain painter order around shared text handles', async () => {
  const {frames} = await run();
  const commands = frames[0].commands;
  const overlay = commands.findIndex(c => c[0] === 1 && (c[5] >>> 24) > 0 && (c[5] >>> 24) < 255);
  assert.ok(overlay > 0);
  const before = commands.slice(0, overlay).findLast(c => c[0] === 5);
  const after = commands.slice(overlay + 1).find(c => c[0] === 5);
  assert.equal(before[1], after[1]);
  assert.ok(commands.slice(0, overlay).some(c => c[0] === 4));
  const lastClip = commands.findLast(c => c[0] === 4);
  assert.deepEqual(lastClip.slice(1, 5), [0, 0, frames[0].width, frames[0].height]);
});

test('extreme resize and input remain bounded and idle does not redraw', async () => {
  const result = await run({events: [[8, 0, 0, 0], [99, 0, 0, 0], [1, 2147483647, 2147483647, 0],
    [2, -2147483648, 0, 0], [3, 2147483647, -2147483648, 0], [1, -1, -1, 0], [0, 0, 0, 0]]});
  assert.equal(result.frames.length, 5);
  assert.equal(result.frames[1].width, 4096); assert.equal(result.frames[1].height, 1024);
  assert.equal(result.frames.at(-1).width, 320); assert.equal(result.frames.at(-1).height, 240);
});

test('event polling has a finite budget and still releases every layout', async () => {
  const result = await run({endlessIdle: true});
  assert.ok(result.polls > 0 && result.polls <= 16384);
  assert.equal(result.frames.length, 1);
  assert.ok(result.logs.some(event => event.code === 54 && event.value === -4));
});

test('partial creation failure releases earlier layouts and preserves the host error', async () => {
  const result = await run({createFailureAt: 3, createReply: -5});
  assert.equal(result.created.length, 3); assert.equal(result.closed.length, 3);
  assert.equal(result.frames.length, 0);
  assert.ok(result.logs.some(event => event.code === 50 && event.value === -5));
});

for (const reply of [-4, 0, 47, 49]) {
  test(`metrics reply ${reply} cannot expose partial/uninitialized metrics`, async () => {
    const result = await run({metricsFailureAt: 2, metricsReply: reply});
    assert.equal(result.frames.length, 0); assert.equal(result.closed.length, 3);
    assert.ok(result.logs.some(event => event.code === 51 && event.value === (reply < 0 ? reply : -2)));
  });
}

test('zero is not a valid layout handle', async () => {
  const result = await run({createFailureAt: 0, createReply: 0});
  assert.equal(result.frames.length, 0);
  assert.ok(result.logs.some(event => event.code === 50 && event.value === -2));
});

test('submit failure and close errors still close all layouts', async () => {
  const result = await run({submitFailureAt: 0, closeReply: -2});
  assert.equal(result.frames.length, 1); assert.equal(result.polls, 0);
  assert.equal(result.closed.length, result.created.length);
  assert.ok(result.logs.some(event => event.code === 52 && event.value === -4));
  assert.equal(result.logs.filter(event => event.code === 53).length, result.created.length);
});
