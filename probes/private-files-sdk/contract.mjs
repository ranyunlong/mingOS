import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('./', import.meta.url));
const wasm = await readFile(`${root}build/run/main.wasm`);
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
const timeout = setTimeout(() => worker.kill('SIGKILL'), 30_000);
let stage = 0;
try {
    worker.stdin.write(`${JSON.stringify({ wasm: wasm.toString('base64'), fuel: 10000000, memoryBytes: 67108864, sdkVersion: 6 })}\n`);
    for await (const line of createInterface({ input: worker.stdout })) {
        const message = JSON.parse(line);
        if (stage === 0) {
            assert.equal(message.type, 'call');
            assert.equal(message.method, 'private_file_call');
            assert.deepEqual(message.args, []);
            assert.equal(message.payload, '{"op":"list","path":"","offset":0,"limit":1}');
            worker.stdin.write(`${JSON.stringify({ id: message.id, value: 0 })}\n`);
        } else if (stage === 1) {
            assert.equal(message.type, 'call');
            assert.equal(message.method, 'log');
            assert.deepEqual(message.args, [7101, 1]);
            worker.stdin.end(`${JSON.stringify({ id: message.id, value: 0 })}\n`);
        } else if (stage === 2) {
            assert.equal(message.type, 'result');
            assert.equal(message.exitCode, 0);
        } else {
            throw new Error(`unexpected worker message: ${line}`);
        }
        stage++;
    }
    const exit = await completed;
    assert.deepEqual(exit, { code: 0, signal: null }, stderr);
    assert.equal(stage, 3);
    console.log('Kotlin private files success response passed');
} finally {
    clearTimeout(timeout);
    if (worker.exitCode === null) worker.kill('SIGKILL');
}
