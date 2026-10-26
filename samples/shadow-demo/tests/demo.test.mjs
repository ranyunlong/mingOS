import test from 'node:test';
import assert from 'node:assert/strict';
import {load, run} from './host.mjs';
import {readFile} from 'node:fs/promises';

function validFrames(s) {
  for (const f of s.frames) {
    assert.ok(f.width * f.height * f.commands.length <= 64000000);
    assert.ok(f.commands.filter(c => c[0] === 7).length <= 8);
    assert.ok(f.commands.filter(c => c[0] === 6).length <= 16);
    for (let i = 0; i < f.commands.length; i++) {
      const c = f.commands[i];
      if (c[0] === 7) {
        assert.equal(c[7], 0);
        const panel = f.commands[i + 1]; assert.equal(panel[0], 6);
        assert.equal(panel[1], c[2]); assert.equal(panel[2] + 8, c[3]);
        assert.equal(panel[3], c[4]); assert.equal(panel[4], c[5]);
      }
    }
  }
}

test('real demo draws layered shadows and reuses resources when moving and recoloring', async () => {
  const s = run(await load(), {events: [[4, 40, 40, 0], [4, 144, 40, 0], [4, 40, 40, 0]]});
  assert.equal(s.frames.length, 4); assert.equal(s.styles.length, 2); assert.equal(s.texts.length, 5);
  validFrames(s);
  const main = s.frames.map(f => f.commands.find(c => c[0] === 7));
  assert.equal(main[1][2] - main[0][2], 16); assert.equal(main[1][3] - main[0][3], 8);
  assert.deepEqual(main[1].slice(1, 6), main[2].slice(1, 6));
  assert.notEqual(main[1][6], main[2][6]);
  assert.deepEqual(main[0].slice(1, 6), main[3].slice(1, 6));
  assert.ok(s.frames.every(f => f.styles === 2 && f.texts === 5));
  assert.equal(s.closes.length, 2); assert.equal(s.textCloses.length, 5);
  assert.deepEqual(s.logs, [[40, 4]]);
});

test('all legal viewport sizes survive unchanged, including narrow, tiny and maximum pixels', async () => {
  const sizes = [[1920,1080], [2048,2048], [4096,1024], [1024,4096], [100,4096], [4096,100], [1,1], [320,240]];
  const s = run(await load(), {events: sizes.map(([w,h]) => [1,w,h,0])});
  assert.deepEqual(s.frames.map(f => [f.width,f.height]), [[1100,760], ...sizes]); validFrames(s);
  const main = s.frames.slice(0, 5).map(f => f.commands.find(c => c[0] === 7));
  assert.ok(main.every(c => c[1] === main[0][1] && c[4] > 73 && c[5] > 73), 'stretch sizes retain canonical key');
});

test('duplicate resize, idle, unknown and outside clicks do not redraw; invalid sizes bounded', async () => {
  const s = run(await load(), {events: [[8,0,0,0], [99,0,0,0], [4,0,0,0], [1,1100,760,0],
    [1,2147483647,2147483647,0], [1,-1,-1,0]]});
  assert.deepEqual(s.frames.map(f => [f.width,f.height]), [[1100,760], [4096,1024], [1,1]]); validFrames(s);
});

test('every partial style/text/metrics failure closes already owned resources', async () => {
  const m = await load();
  for (let i = 0; i < 2; i++) {
    const s = run(m, {createFailureAt: i});
    assert.equal(s.styles.length, i); assert.equal(s.closes.length, i); assert.equal(s.frames.length, 0);
    assert.deepEqual(s.logs, [[61,-4], [40,0]]);
  }
  for (let i = 0; i < 5; i++) {
    const s = run(m, {textFailureAt: i});
    assert.equal(s.textCloses.length, i); assert.equal(s.closes.length, 2); assert.equal(s.frames.length, 0);
    assert.deepEqual(s.logs, [[62,-5], [40,0]]);
    const t = run(m, {metricsFailureAt: i});
    assert.equal(t.textCloses.length, i + 1); assert.equal(t.closes.length, 2); assert.equal(t.frames.length, 0);
    assert.deepEqual(t.logs, [[63,-2], [40,0]]);
  }
});

test('typed-handle errors never produce a style; negative host codes propagate', async () => {
  const m = await load();
  for (const reply of [0, 1, 0x30000001, 0x40000000, 0x50000000, 2147483647, -1, -2, -4]) {
    const s = run(m, {createFailureAt: 0, createReply: reply});
    assert.equal(s.styles.length, 0); assert.equal(s.frames.length, 0);
    assert.deepEqual(s.logs, [[61, reply < 0 ? reply : -2], [40,0]]);
  }
});

test('submit failure and bounded endless idle clean up; close errors do not interrupt cleanup', async () => {
  const m = await load();
  for (const i of [0, 1]) {
    const s = run(m, {events: [[4,40,40,0]], submitFailureAt: i});
    assert.equal(s.closes.length, 2); assert.equal(s.textCloses.length, 5);
    assert.deepEqual(s.logs, [[64,-4], [40,i]]);
  }
  const idle = run(m, {endlessIdle: true}); assert.equal(idle.polls, 16384);
  assert.equal(idle.frames.length, 1); assert.deepEqual(idle.logs, [[66,-4], [40,1]]);
  const close = run(m, {closeReply: -2}); assert.equal(close.logs.filter(l => l[0] === 65).length, 7);
  assert.equal(close.closes.length, 2); assert.equal(close.textCloses.length, 5);
});

test('published event contract executes with code40 and exactly two stable canonical keys', async () => {
  const c = JSON.parse(await readFile(new URL('../event-contract.json', import.meta.url), 'utf8'));
  assert.equal(c.completionEvent.code, 40);
  const module = await load();
  for (const [name, scenario] of Object.entries(c.cases)) {
    const s = run(module, {events: scenario.events}); validFrames(s);
    assert.equal(s.frames.length, scenario.frames, name);
    assert.deepEqual(s.logs, [[40,scenario.frames]], name);
    assert.equal(s.styles.length, 2); assert.equal(s.texts.length, 5);
    const keys = new Set();
    for (const f of s.frames) for (const cmd of f.commands.filter(cmd => cmd[0] === 7)) {
      const style = s.styles.find(st => st.handle === cmd[1]);
      const r = Math.min(style.radius, Math.floor(cmd[4] / 2), Math.floor(cmd[5] / 2));
      keys.add(JSON.stringify([Math.min(cmd[4],2*(r+style.blur)+1),
        Math.min(cmd[5],2*(r+style.blur)+1),r,style.blur]));
    }
    assert.deepEqual([...keys].sort(), ['[45,45,16,6]','[73,73,24,12]'], name);
    assert.deepEqual([s.frames[0].width,s.frames[0].height], [1100,760]);
    if (scenario.finalViewport) assert.deepEqual([s.frames.at(-1).width,s.frames.at(-1).height], scenario.finalViewport);
  }
});
