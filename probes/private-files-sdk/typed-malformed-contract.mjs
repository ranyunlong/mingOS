import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('./', import.meta.url));
const wasm = await readFile(`${root}build/run-typed-malformed/main.wasm`);
const target = process.env.MING_HOST_TARGET ?? '/private/tmp/mingos-private-file-target-20261029';
const worker = spawn('docker', [
    'run', '--rm', '-i', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
    '--user', '501:20', '--cpus', '2', '--memory', '4g', '--pids-limit', '128',
    '--tmpfs', '/tmp:exec,size=256m', '--mount', `type=bind,src=${target},dst=/target,readonly`,
    'rust:latest', '/target/debug/ming-app-host', '__worker',
], { stdio: ['pipe', 'pipe', 'pipe'] });
let stderr = '';
worker.stderr.setEncoding('utf8');
worker.stderr.on('data', chunk => { stderr += chunk; });
const completed = new Promise((resolve, reject) => {
    worker.on('error', reject);
    worker.on('close', (code, signal) => resolve({ code, signal }));
});
const timeout = setTimeout(() => worker.kill('SIGKILL'), 120_000);
const steps = [
    ['read', { value: 1, payload: '!' }],
    ['list', { value: 1, payload: Buffer.from('[{"name":2,"isDirectory":false}]').toString('base64') }],
    ['mkdir', { value: 2 }],
    ['write', { value: 1, payload: '2' }],
    ['write_begin', { value: 0 }],
    ['write_chunk', { value: 1 }],
    ['write_abort', { value: 0 }],
];
let stage = 0;
try {
    worker.stdin.write(`${JSON.stringify({ wasm: wasm.toString('base64'), fuel: 100000000, memoryBytes: 67108864, sdkVersion: 6 })}\n`);
    for await (const line of createInterface({ input: worker.stdout })) {
        const message = JSON.parse(line);
        if (stage < steps.length) {
            assert.equal(message.type, 'call');
            assert.equal(message.method, 'private_file_call');
            assert.equal(JSON.parse(message.payload).op, steps[stage][0]);
            worker.stdin.write(`${JSON.stringify({ id: message.id, ...steps[stage][1] })}\n`);
        } else if (stage === steps.length) {
            assert.equal(message.type, 'call');
            assert.equal(message.method, 'log');
            assert.deepEqual(message.args, [7106, 1]);
            worker.stdin.end(`${JSON.stringify({ id: message.id, value: 0 })}\n`);
        } else if (stage === steps.length + 1) {
            assert.equal(message.type, 'result');
            assert.equal(message.exitCode, 0);
        } else {
            throw new Error(`unexpected worker message: ${line}`);
        }
        stage++;
    }
    assert.deepEqual(await completed, { code: 0, signal: null }, stderr);
    assert.equal(stage, steps.length + 2);
    console.log('Typed private file malformed responses rejected; staged upload aborted');
} finally {
    clearTimeout(timeout);
    if (worker.exitCode === null) worker.kill('SIGKILL');
}
