import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { adaptKotlinWasi } from '../../scripts/wasm-sdk-adapter.mjs';
import { inspectImports } from '../compose-wasi/port/wasm-imports.mjs';

const root = fileURLToPath(new URL('./', import.meta.url));
const mode = process.argv[2] ?? 'default';
assert.ok(['default', 'authorized', 'large', 'typed', 'typed-large', 'typed-directory', 'typed-denied', 'typed-malformed', 'private-kernel', 'public', 'public-large', 'public-denied'].includes(mode));
const raw = await readFile(`${root}build/compileSync/wasmWasi/main/productionExecutable/kotlin/ming-private-files-sdk-contract.wasm`);
const { bytes } = adaptKotlinWasi(raw);
const expectedModule = mode.startsWith('public')
    ? 'ming_public_files_v2'
    : (mode.startsWith('typed') || mode === 'private-kernel') ? 'ming_private_files_v2' : 'ming_private_files_v1';
const imports = inspectImports(bytes).imports.filter(item => item.module === expectedModule);
assert.equal(imports.length, 1);
assert.equal(imports[0].name, 'call');
assert.deepEqual(imports[0].params, ['i32', 'i32', 'i32', 'i32']);
assert.deepEqual(imports[0].results, ['i32']);
const destination = `${root}build/${mode === 'default' ? 'run' : `run-${mode}`}`;
await mkdir(destination, { recursive: true });
await writeFile(`${destination}/main.wasm`, bytes);
await writeFile(`${destination}/app.json`, JSON.stringify({
    schemaVersion: 1,
    id: mode === 'private-kernel' ? 'dev.ming.private-kernel-probe' : 'dev.ming.private-files-contract',
    version: '0.1.0', sdkVersion: 6, entry: 'main.wasm',
    ...(mode.startsWith('public') ? { publicStorage: true } : mode === 'default' || mode === 'private-kernel' ? {} : { privateStorage: true }),
}));
