import test from 'node:test';
import assert from 'node:assert/strict';
import {loadContract, run} from './host.mjs';

test('real SDK4 Wasm: ABI, local geometry/quotas, immutable v3 layout lifetime and recovery', async () => {
  const module = await loadContract();
  const names = WebAssembly.Module.imports(module).map(i => i.module + '.' + i.name);
  assert.ok(names.includes('ming_v4.frame_submit'));
  assert.ok(names.includes('ming_v3.text_layout_create'));
  assert.ok(!names.includes('ming_v3.frame_submit'));
  const s = run(module, {contract: true, submitFailureAt: 4});
  assert.deepEqual(s.logs, [[80, 0], [81, 0]]);
  assert.equal(s.frames.length, 7, 'local rejections must not reach host');
  const handle = s.created[0].handle;
  assert.equal(s.created[0].text, '中文 AA');
  assert.deepEqual(s.frames[0].commands, [
    [1, -1000000, 1000000, 1000000, 1000000, -1, 64, 0],
    [6, 1000000, -1000000, 1, 3, 0x80ffffff | 0, 64, 0],
    [2, 1, -1000000, 1000000, 1000000, 1, 0, 0],
    [4, -1000000, 1000000, 0, 1000000, 0, 0, 0],
    [5, handle, -64000000, 64000000, -1, 0, 0, 0],
    [5, handle, 72, -80, 0x80402010 | 0, 0, 0, 0],
    [5, handle, 64000000, -64000000, -1, 0, 0, 0],
  ]);
  assert.equal(s.frames[1].commands.length, 16);
  assert.ok(s.frames[1].commands.every(c => c[0] === 6 && c[6] === 64));
  assert.equal(s.frames[2].commands.length, 256);
  assert.deepEqual(s.frames[4], s.frames[5], 'host rejection does not poison valid builder');
  assert.equal(s.frames[6].width, 1024); assert.equal(s.frames[6].height, 4096);
  assert.deepEqual(s.frames[6].commands, []);
  assert.deepEqual(s.closed, [handle, handle]);
});
