import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {loadDemo, run} from './host.mjs';

const GREEN = 0xff147d72 | 0, AMBER = 0x99e7a54a | 0;
const colored = f => f.commands.filter(c => [1, 6].includes(c[0]) &&
  [GREEN, AMBER].includes(c[5])).map(c => c[5]);
function checkFrame(frame, width, height) {
  assert.deepEqual([frame.width, frame.height], [width, height], 'presented viewport must match requested host viewport');
  assert.ok(frame.commands.length <= Math.floor(64000000 / (width * height)), 'GPU candidate budget must hold at the actual viewport');
  assert.ok(frame.commands.filter(c => c[0] === 6).length <= 16);
  const button = frame.commands.find(c => c[0] === 6 && c[5] === (0xff245e55 | 0));
  assert.deepEqual(button, [6, width - 184, 30, 160, 40, 0xff245e55 | 0, 14, 0]);
  for (const c of frame.commands) {
    if ([1, 6].includes(c[0])) {
      assert.ok(c[3] > 0 && c[4] > 0, 'small windows must not emit negative/empty geometry');
      assert.ok(c[1] >= 0 && c[2] >= 0 && c[1] + c[3] <= width && c[2] + c[4] <= height);
    }
    if (c[0] === 4) {
      assert.ok(c[3] > 0 && c[4] > 0);
      assert.ok(c[1] >= 0 && c[2] >= 0 && c[1] + c[3] <= width && c[2] + c[4] <= height);
    }
  }
}

for (const [name, width, height] of [['maximized', 1920, 1080], ['minimum', 320, 240], ['pixel-cap', 2048, 2048]]) {
  test('real Wasm ' + name + ' preserves host viewport and accepts its actual button click', async () => {
    const events = JSON.parse(await readFile(new URL('./cases/' + name + '.json', import.meta.url)));
    const s = run(await loadDemo(), {events});
    assert.equal(s.frames.length, 3, 'initial + matching resize + effective click');
    checkFrame(s.frames[1], width, height); checkFrame(s.frames[2], width, height);
    assert.equal(colored(s.frames[1])[0], GREEN); assert.equal(colored(s.frames[1]).at(-1), AMBER);
    assert.equal(colored(s.frames[2])[0], AMBER); assert.equal(colored(s.frames[2]).at(-1), GREEN);
    assert.equal(s.created.length, 11); assert.ok(s.frames.every(f => f.created === 11));
    assert.deepEqual(s.logs, [[40, 3]]);
  });
}

test('fast consecutive resizes preserve every frame viewport, budget and click target', async () => {
  const events = JSON.parse(await readFile(new URL('./cases/fast-resize.json', import.meta.url)));
  const s = run(await loadDemo(), {events});
  const sizes = [[1100, 760], [1920, 1080], [320, 240], [2048, 2048], [2048, 2048],
    [1100, 760], [320, 240], [320, 240], [1920, 1080], [1920, 1080]];
  assert.equal(s.frames.length, sizes.length);
  s.frames.forEach((f, i) => checkFrame(f, ...sizes[i]));
  for (const [i, color] of [[3, GREEN], [4, AMBER], [6, AMBER], [7, GREEN], [8, GREEN], [9, AMBER]])
    assert.equal(colored(s.frames[i])[0], color, 'click must use current, not stale viewport coordinates');
  assert.deepEqual(s.logs, [[40, 10]]);
  assert.equal(s.created.length, 11); assert.equal(s.closed.length, 11);
});

test('legal viewport extremes and candidate boundary select bounded positive layouts without resizing the frame', async () => {
  const sizes = [[4096, 1024], [1024, 4096], [320, 4096], [4096, 240],
    [2048, 1201], [2048, 1202], [480, 519], [479, 520]];
  const events = sizes.flatMap(([w, h]) => [[1, w, h, 0], [4, w - 104, 50, 0]]);
  const s = run(await loadDemo(), {events});
  assert.equal(s.frames.length, 1 + sizes.length * 2);
  sizes.forEach(([w, h], i) => {
    checkFrame(s.frames[1 + i * 2], w, h); checkFrame(s.frames[2 + i * 2], w, h);
    assert.notDeepEqual(colored(s.frames[1 + i * 2]), colored(s.frames[2 + i * 2]));
  });
  assert.equal(s.frames[9].commands.length, 26, 'just below 64M keeps original detailed comparison');
  assert.ok(s.frames[11].commands.length <= 15, 'just above 64M uses compact comparison');
  assert.deepEqual(s.logs, [[40, 17]]);
});

test('all five original acceptance cases retain their exact viewports, frame counts and 26-command layout', async () => {
  const module = await loadDemo();
  for (const [name, count, size] of [['initial', 1, [1100, 760]], ['toggle', 2, [1100, 760]],
      ['repeat', 3, [1100, 760]], ['small', 3, [480, 520]], ['maximum', 2, [1600, 1200]]]) {
    const events = JSON.parse(await readFile(new URL('./cases/' + name + '.json', import.meta.url)));
    const s = run(module, {events});
    assert.equal(s.frames.length, count); assert.deepEqual(s.logs, [[40, count]]);
    checkFrame(s.frames.at(-1), ...size);
    assert.ok(s.frames.every(f => f.commands.length === 26));
  }
});

test('legal viewport boundary grid keeps exact frame size, safe geometry and working clicks', async () => {
  const sizes = [320, 321, 479, 480, 481, 1920, 2048, 4095, 4096].flatMap(w =>
    [240, 241, 359, 360, 519, 520, 1080, 1201, 1202, 2048, 4096]
      .filter(h => w * h <= 4194304).map(h => [w, h]));
  const events = sizes.flatMap(([w, h]) => [[1, w, h, 0], [4, w - 104, 50, 0]]);
  const s = run(await loadDemo(), {events});
  assert.equal(s.frames.length, 1 + sizes.length * 2);
  sizes.forEach(([w, h], i) => {
    const before = s.frames[1 + i * 2], after = s.frames[2 + i * 2];
    checkFrame(before, w, h); checkFrame(after, w, h);
    assert.notDeepEqual(colored(before), colored(after));
  });
  assert.equal(s.created.length, 11); assert.equal(s.closed.length, 11);
  assert.deepEqual(s.logs, [[40, 1 + sizes.length * 2]]);
});

test('malformed headless extreme dimensions normalize safely before rendering and hit testing', async () => {
  const module = await loadDemo();
  for (const [w, h, expected] of [[-2147483648, -2147483648, [320, 240]],
      [2147483647, 2147483647, [4096, 1024]], [4096, 4096, [4096, 1024]],
      [0, 4096, [320, 4096]], [4096, 0, [4096, 240]]]) {
    const s = run(module, {events: [[1, w, h, 0], [4, expected[0] - 104, 50, 0]]});
    assert.equal(s.frames.length, 3); assert.deepEqual(s.logs, [[40, 3]]);
    checkFrame(s.frames[1], ...expected); checkFrame(s.frames[2], ...expected);
    assert.notDeepEqual(colored(s.frames[1]), colored(s.frames[2]));
  }
});
