import test from 'node:test';
import assert from 'node:assert/strict';
import {load, run} from './host.mjs';

test('real SDK: local geometry/style negatives, sticky quota and independent AA/shadow limits', async () => {
  const s = run(await load(true), {contract: true, submitFailureAt: 0});
  assert.deepEqual(s.logs, [[70, 0], [71, 0], [72, 0]]);
  assert.equal(s.creates.length, 10); assert.equal(s.styles.length, 9);
  assert.deepEqual(s.creates.at(-1), [64, 16]);
  assert.equal(s.styles.at(-1).handle, 0x40000009);
  assert.equal(s.frames.length, 6, 'local failures must never reach the host');
  assert.deepEqual(s.frames[0].commands, [
    [7, 0x40000001, -1000000, 1000000, 1000000, 1, 0x80402010 | 0, 0],
    [7, 0x40000001, 1000000, -1000000, 1, 1000000, 0, 0],
  ]);
  assert.deepEqual(s.frames[0], s.frames[1], 'host rejection preserves immutable builder');
  assert.equal(s.frames[2].commands.filter(c => c[0] === 7).length, 8);
  assert.equal(s.frames[2].commands.filter(c => c[0] === 6).length, 16);
  assert.equal(s.frames[3].commands.length, 256);
  assert.deepEqual(s.frames[4].commands, [[7, 0x40000001, 0, 0, 1, 1, -1, 0]],
    'closed handles still go to authoritative host');
  assert.deepEqual(s.frames[5].commands, []);
  assert.deepEqual(s.closes.slice(0, 2), [0x40000001, 0x40000001]);
  assert.equal(s.closes.length, 10);
});
