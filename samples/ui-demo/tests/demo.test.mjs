import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {loadDemo, run} from './host.mjs';

test('SDK4 actual package keeps inherited font imports and no WASI/legacy submit', async () => {
  const m = JSON.parse(await readFile(new URL('../app.json', import.meta.url)));
  assert.equal(m.sdkVersion, 4); assert.equal(m.id, 'dev.ming.ui-demo');
  assert.equal(m.entry, 'main.wasm'); assert.equal(m.grants, undefined);
  const packaged = JSON.parse(await readFile(new URL('../../../artifacts/ui-demo/app.json', import.meta.url)));
  assert.deepEqual(packaged, m);
  const imports = WebAssembly.Module.imports(await loadDemo());
  assert.ok(imports.every(i => ['ming_v1', 'ming_v2', 'ming_v3', 'ming_v4'].includes(i.module)));
  const names = imports.map(i => i.module + '.' + i.name);
  for (const name of ['ming_v1.log', 'ming_v2.event_next', 'ming_v3.text_layout_create',
      'ming_v3.text_layout_metrics', 'ming_v3.text_layout_close', 'ming_v4.frame_submit']) assert.ok(names.includes(name));
  assert.equal(names.filter(n => n.endsWith('.frame_submit')).length, 1);
});

test('Chinese panels/button compare identical hard/AA shapes, translucency, text and replacement clips', async () => {
  const s = run(await loadDemo());
  assert.equal(s.frames.length, 1); assert.equal(s.created.length, 11);
  assert.ok(s.created.some(c => c.text === '圆角与层叠'));
  const {width, height, commands: c} = s.frames[0];
  assert.equal(width, 1100); assert.equal(height, 760);
  assert.ok(c.filter(x => x[0] === 6).length >= 5);
  assert.ok(c.filter(x => x[0] === 6).length <= 16);
  const hard = c.filter(x => x[0] === 1 && [0xff147d72 | 0, 0x99e7a54a | 0].includes(x[5]));
  const aa = c.filter(x => x[0] === 6 && [0xff147d72 | 0, 0x99e7a54a | 0].includes(x[5]));
  // The green button uses a different color, leaving exactly the two comparison shapes.
  assert.equal(hard.length, 2); assert.equal(aa.length, 2);
  for (let i = 0; i < 2; i++) {
    assert.equal(aa[i][1] - hard[i][1], 520);
    assert.deepEqual(aa[i].slice(2), hard[i].slice(2));
  }
  const clips = c.filter(x => x[0] === 4);
  assert.equal(clips.length, 4);
  assert.deepEqual(clips.at(-1), [4, 0, 0, width, height, 0, 0, 0]);
  let clip = [0, 0, width, height]; let clippedText = 0; let clippedOverlap = 0;
  for (const x of c) {
    if (x[0] === 4) clip = x.slice(1, 5);
    if (clip[0] !== 0) {
      if (x[0] === 5) clippedText++;
      if ([1, 6].includes(x[0]) && x[5] === (0x99e7a54a | 0)) {
        assert.ok(x[1] + x[3] > clip[0] + clip[2]); clippedOverlap++;
      }
    }
  }
  assert.equal(clippedText, 2); assert.equal(clippedOverlap, 2);
  assert.deepEqual(s.logs, [[40, 1]]);
  assert.equal(s.closed.length, s.created.length);
});

test('only existing click event inside button toggles painter order, no idle/unknown/scroll redraw', async () => {
  const s = run(await loadDemo(), {events: [
    [8, 0, 0, 0], [2, 10, 0, 0], [3, 10, 10, 0], [5, 0, 0, 0],
    [4, 10, 10, 0], [4, 916, 30, 0], [4, 1076, 70, 0], [4, 1075, 69, 0],
  ]});
  assert.equal(s.frames.length, 3);
  assert.deepEqual(s.logs, [[40, 3]]);
  const colors = f => f.commands.filter(c => [1, 6].includes(c[0]) &&
    [0xff147d72 | 0, 0x99e7a54a | 0].includes(c[5])).map(c => c[5]);
  assert.deepEqual(colors(s.frames[0]), [0xff147d72 | 0, 0x99e7a54a | 0, 0xff147d72 | 0, 0x99e7a54a | 0]);
  assert.deepEqual(colors(s.frames[1]), [0x99e7a54a | 0, 0xff147d72 | 0, 0x99e7a54a | 0, 0xff147d72 | 0]);
  assert.deepEqual(s.frames[0].commands, s.frames[2].commands);
  assert.ok(s.frames.every(f => f.created === 11), 'redraw reuses layouts');
});

test('invalid headless resize is normalized to SDK limits, duplicate resize does not redraw', async () => {
  const s = run(await loadDemo(), {events: [[1, 480, 520, 0], [1, 480, 520, 0],
    [4, 297, 31, 0], [1, 2147483647, 2147483647, 0], [1, -1, -1, 0]]});
  assert.deepEqual(s.frames.map(f => [f.width, f.height]), [[1100, 760], [480, 520], [480, 520], [4096, 1024], [320, 240]]);
  assert.equal(s.logs.at(-1)[1], 5);
  for (const f of s.frames) for (const c of f.commands) {
    if ([1, 6].includes(c[0])) {
      assert.ok(c[1] >= 0 && c[2] >= 0 && c[1] + c[3] <= f.width && c[2] + c[4] <= f.height);
    }
  }
});

test('endless idle is bounded, submits once, allocates no extra layouts and closes all', async () => {
  const s = run(await loadDemo(), {endlessIdle: true});
  assert.equal(s.polls, 16384); assert.equal(s.frames.length, 1); assert.equal(s.created.length, 11);
  assert.deepEqual(s.logs, [[54, -4], [40, 1]]);
});

test('every partial create/metrics failure closes all layouts already owned', async () => {
  const module = await loadDemo();
  for (let i = 0; i < 11; i++) {
    const create = run(module, {createFailureAt: i});
    assert.equal(create.created.length, i); assert.equal(create.frames.length, 0);
    assert.deepEqual(create.logs, [[50, -5], [40, 0]]);
    const metrics = run(module, {metricsFailureAt: i});
    assert.equal(metrics.created.length, i + 1); assert.equal(metrics.frames.length, 0);
    assert.deepEqual(metrics.logs, [[51, -2], [40, 0]]);
  }
});

test('submit rejection on initial frame or later redraw terminates and cleans up', async () => {
  const module = await loadDemo();
  const first = run(module, {submitFailureAt: 0});
  assert.equal(first.polls, 0); assert.deepEqual(first.logs, [[52, -4], [40, 0]]);
  const later = run(module, {events: [[4, 960, 40, 0]], submitFailureAt: 1});
  assert.equal(later.polls, 1); assert.deepEqual(later.logs, [[52, -4], [40, 1]]);
});

test('close errors are logged while cleanup continues for all owned layouts', async () => {
  const s = run(await loadDemo(), {closeReply: -2});
  assert.equal(s.closed.length, 11); assert.equal(s.logs.filter(l => l[0] === 53).length, 11);
  assert.deepEqual(s.logs.at(-1), [40, 1]);
});
