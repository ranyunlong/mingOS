import {spawn} from 'node:child_process';
import {mkdir, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const label = process.argv[2] ?? 'green-tests';
if (!/^[a-z0-9-]+$/.test(label)) throw new Error('Invalid label');
const root = fileURLToPath(new URL('../../', import.meta.url));
let output = '';
const code = await new Promise((done, reject) => {
  const child = spawn(process.execPath, ['--test', 'samples/shadow-demo/tests/sdk.test.mjs',
    'samples/shadow-demo/tests/abi.test.mjs', 'samples/shadow-demo/tests/demo.test.mjs'], {cwd: root, windowsHide: true});
  for (const stream of [child.stdout, child.stderr]) stream.on('data', b => {output += b; process.stdout.write(b);});
  child.on('error', reject); child.on('exit', n => done(n ?? 1));
});
const dir = new URL('../../artifacts/shadow-kotlin/', import.meta.url); await mkdir(dir, {recursive: true});
await writeFile(new URL(label + '.log', dir), output);
await writeFile(new URL(label + '.json', dir), JSON.stringify({code, timestamp: new Date().toISOString()}, null, 2) + '\n');
process.exitCode = code;
