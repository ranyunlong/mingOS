import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import {adaptKotlinWasi} from '../../../scripts/wasm-sdk-adapter.mjs';

// Read the compiled function types, rather than trusting JS import function arity.
function functionImports(bytes) {
  let p = 8; const types = [], imports = [];
  function uint() { let n = 0, shift = 0, b; do { b = bytes[p++]; n += (b & 127) * 2 ** shift; shift += 7; } while (b & 128); return n; }
  const text = () => { const n = uint(); const s = bytes.subarray(p, p + n).toString('utf8'); p += n; return s; };
  // Kotlin/Wasm uses GC arrays/structs and recursive groups before its function types.
  const value = () => { const tag = bytes[p++]; if (tag === 0x63 || tag === 0x64) uint(); return tag; };
  const field = () => { value(); p++; }; // storage type, mutability
  function subtype() {
    let tag = bytes[p++];
    if (tag === 0x4f || tag === 0x50) {
      const supers = uint(); for (let i = 0; i < supers; i++) uint(); tag = bytes[p++];
    }
    if (tag === 0x60) {
      const params = Array.from({length: uint()}, value);
      const results = Array.from({length: uint()}, value); types.push({params, results});
    } else if (tag === 0x5e) { field(); types.push(null); }
    else if (tag === 0x5f) { const n = uint(); for (let i = 0; i < n; i++) field(); types.push(null); }
    else throw new Error('Unsupported Kotlin type tag: ' + tag.toString(16));
  }
  while (p < bytes.length) {
    const id = bytes[p++], size = uint(), end = p + size;
    if (id === 1) {
      const count = uint();
      for (let i = 0; i < count; i++) {
        if (bytes[p] === 0x4e) { p++; const n = uint(); for (let j = 0; j < n; j++) subtype(); }
        else subtype();
      }
    } else if (id === 2) {
      const count = uint();
      for (let i = 0; i < count; i++) {
        const name = text() + '.' + text(); assert.equal(bytes[p++], 0);
        imports.push({name, ...types[uint()]});
      }
    }
    p = end;
  }
  return imports;
}
const arities = new Map([['ming_v1.log', 2], ['ming_v2.random_fill', 2], ['ming_v2.event_next', 2],
  ['ming_v3.text_layout_create', 5], ['ming_v3.text_layout_metrics', 3], ['ming_v3.text_layout_close', 1],
  ['ming_v4.frame_submit', 2], ['ming_v4.shadow_style_create', 2], ['ming_v4.shadow_style_close', 1]]);

test('official Wasm imports have exact i32 signatures; adapter only rewrites random_get', async () => {
  const signatures = [];
  for (const infoUrl of ['../../../artifacts/shadow-kotlin/contract/build-info.json', '../../../artifacts/shadow-demo/build-info.json']) {
    const info = JSON.parse(await readFile(new URL(infoUrl, import.meta.url), 'utf8'));
    const raw = await readFile(info.rawPath); const adapted = adaptKotlinWasi(raw);
    assert.deepEqual(adapted.rewrites, [{from: 'wasi_snapshot_preview1.random_get', to: 'ming_v2.random_fill'}]);
    const imports = functionImports(adapted.bytes);
    for (const {name, params, results} of imports) {
      assert.ok(arities.has(name), 'SDK-only whitelist: ' + name);
      assert.deepEqual(params, Array(arities.get(name)).fill(0x7f), name);
      assert.deepEqual(results, [0x7f], name);
    }
    for (const required of ['ming_v4.shadow_style_create', 'ming_v4.shadow_style_close', 'ming_v4.frame_submit']) {
      assert.ok(imports.some(i => i.name === required));
    }
    assert.equal(imports.filter(i => i.name.endsWith('.frame_submit')).length, 1);
    signatures.push({artifact: infoUrl, rewrites: adapted.rewrites, imports});
  }
  await writeFile(new URL('../../../artifacts/shadow-kotlin/abi-signatures.json', import.meta.url), JSON.stringify(signatures, null, 2) + '\n');
});

test('adapter continues rejecting other WASI imports', () => {
  // (module (import "wasi_snapshot_preview1" "fd_write" (func (param i32 i32) (result i32))))
  const text = s => Buffer.concat([Buffer.from([s.length]), Buffer.from(s)]);
  const payload = Buffer.concat([Buffer.from([1]), text('wasi_snapshot_preview1'), text('fd_write'), Buffer.from([0, 0])]);
  const raw = Buffer.concat([Buffer.from([0,97,115,109,1,0,0,0, 1,7,1,96,2,127,127,1,127, 2,payload.length]), payload]);
  assert.ok(WebAssembly.validate(raw));
  assert.throws(() => adaptKotlinWasi(raw), /UNSUPPORTED_RUNTIME_IMPORT/);
});
