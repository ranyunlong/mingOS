import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {inspectImports} from './wasm-imports.mjs';
const str=s=>[s.length,...Buffer.from(s)];
const section=(id,data)=>[id,data.length,...data];
const module=types=>Buffer.from([0,97,115,109,1,0,0,0,...section(1,types),...section(2,[1,...str('m'),...str('f'),0,types[0]===1?0:2])]);
test('numeric function parameter and result types',async()=>{
 const bytes=module([1,96,3,127,126,127,1,127]);assert(WebAssembly.validate(bytes));
 assert.deepEqual(inspectImports(bytes).imports[0].params,['i32','i64','i32']);
 assert.deepEqual(inspectImports(bytes).imports[0].results,['i32']);
});
test('recursive GC types preserve following function indices',async()=>{
 const bytes=module([3,78,1,79,0,95,1,99,109,0,94,120,1,96,2,127,127,0]);assert(WebAssembly.validate(bytes));
 const reflected=inspectImports(bytes);assert.equal(reflected.typeCount,3);assert.equal(reflected.imports[0].typeIndex,2);assert.deepEqual(reflected.imports[0].params,['i32','i32']);assert.deepEqual(reflected.imports[0].results,[]);
});
test('real Compose counter imports match native reflection and exact ABI',async()=>{
 const bytes=await readFile(new URL('../../../artifacts/compose-qualification/port/counter-01/raw.wasm',import.meta.url));
 const reflected=inspectImports(bytes);assert.deepEqual(reflected.imports.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(await WebAssembly.compile(bytes)));
 const fd=reflected.imports.find(i=>i.name==='fd_write');assert.deepEqual(fd.params,['i32','i32','i32','i32']);assert.deepEqual(fd.results,['i32']);
});
