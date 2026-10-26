import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {resolve, join} from 'node:path';

const root = fileURLToPath(new URL('../../', import.meta.url));
const hostArg = process.argv.find(a => a.startsWith('--host='));
const host = hostArg ? resolve(hostArg.slice(7)) : join(root, '.cache/rust-target/debug/ming-app-host' + (process.platform === 'win32' ? '.exe' : ''));
const renderers = process.argv.includes('--gpu') ? ['cpu', 'gpu'] : ['cpu'];
const out = join(root, 'artifacts/shadow-kotlin/host'); await mkdir(out, {recursive: true});
const app = join(root, 'artifacts/shadow-demo/app.json');
const wasm = join(root, 'artifacts/shadow-demo/main.wasm');
const sha = b => createHash('sha256').update(b).digest('hex');
const inputs = {hostSha256: sha(await readFile(host)), wasmSha256: sha(await readFile(wasm))};
const cases = [
  {name: 'initial', events: [], frames: 1, size: [1100,760]},
  {name: 'move', events: [[4,40,40,0]], frames: 2, size: [1100,760]},
  {name: 'color', events: [[4,144,40,0]], frames: 2, size: [1100,760]},
  {name: 'restore', events: [[4,40,40,0],[4,144,40,0],[4,144,40,0],[4,40,40,0]], frames: 5, size: [1100,760]},
  {name: 'large-resize', events: [[1,1920,1080,0],[1,2048,2048,0],[1,4096,1024,0],[1,1024,4096,0]], frames: 5, size: [1024,4096]},
  {name: 'narrow', events: [[1,100,4096,0]], frames: 2, size: [100,4096]},
  {name: 'tiny', events: [[1,1,1,0]], frames: 2, size: [1,1]},
  {name: 'minimum', events: [[1,320,240,0]], frames: 2, size: [320,240]},
];
const report = {status: 'running', ...inputs, runtime: 'rust-wasmtime', renderers, runs: []};
const save = () => writeFile(join(out, 'acceptance.json'), JSON.stringify(report, null, 2) + '\n');
await save();
try {
  for (const c of cases) {
    const events = join(out, c.name + '-events.json');
    await writeFile(events, JSON.stringify([...c.events, [0,0,0,0]]) + '\n');
    const hashes = [];
    for (const renderer of renderers) {
      assert.equal(sha(await readFile(host)), inputs.hostSha256, 'host changed during acceptance');
      assert.equal(sha(await readFile(wasm)), inputs.wasmSha256, 'Wasm changed during acceptance');
      const prefix = join(out, c.name + '-' + renderer);
      const args = ['run', app, '--renderer', renderer, '--timeout-ms', '60000', '--fuel', '1000000000',
        '--events', events, '--snapshot', prefix + '.png'];
      let stdout = '', stderr = '';
      const code = await new Promise((done, reject) => {
        const child = spawn(host, args, {cwd: root, windowsHide: true});
        child.stdout.on('data', b => stdout += b); child.stderr.on('data', b => stderr += b);
        child.on('error', reject); child.on('exit', n => done(n ?? 1));
      });
      await writeFile(prefix + '.stdout.json', stdout); await writeFile(prefix + '.stderr.txt', stderr);
      assert.equal(code, 0, c.name + ' ' + renderer + ': ' + stderr);
      const run = JSON.parse(stdout);
      assert.equal(run.runtime, 'rust-wasmtime'); assert.equal(run.appId, 'dev.ming.shadow-demo');
      assert.equal(run.exitCode, 0); assert.equal(run.framesSubmitted, c.frames);
      assert.deepEqual(run.events, [{code: 40, value: c.frames}], 'no create, submit or cleanup error');
      assert.deepEqual([run.frame.width,run.frame.height], c.size);
      assert.ok(run.frame.width * run.frame.height * run.frame.commands <= 64000000);
      assert.equal(run.frame.backend, renderer === 'cpu' ? 'cpu-reference' : 'wgpu-compute-readback');
      const png = await readFile(prefix + '.png');
      assert.equal(png.subarray(1,4).toString(), 'PNG');
      assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], c.size);
      hashes.push(sha(png));
      report.runs.push({case: c.name, renderer, frames: run.framesSubmitted, frame: run.frame,
        pngSha256: hashes.at(-1), gpuStats: run.gpuStats});
      await save(); console.log(c.name + ' ' + renderer + ': ' + c.frames + ' frames, ' + c.size.join('x'));
    }
    if (hashes.length === 2) assert.equal(hashes[0], hashes[1], c.name + ' CPU/GPU pixels');
  }
  for (const renderer of renderers) {
    const find = name => report.runs.find(r => r.case === name && r.renderer === renderer);
    assert.notEqual(find('initial').pngSha256, find('move').pngSha256);
    assert.notEqual(find('initial').pngSha256, find('color').pngSha256);
    assert.equal(find('initial').pngSha256, find('restore').pngSha256);
  }
  assert.equal(sha(await readFile(host)), inputs.hostSha256);
  assert.equal(sha(await readFile(wasm)), inputs.wasmSha256);
  report.status = 'passed'; await save();
} catch (error) {
  report.status = 'failed'; report.error = String(error); await save(); throw error;
}
