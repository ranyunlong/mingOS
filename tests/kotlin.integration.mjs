import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync,mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {runApp} from '../src/host/app-host.mjs';
const manifestPath=fileURLToPath(new URL('../artifacts/read-file/app.json',import.meta.url));

test('real compiled Kotlin module is present and only imports Ming SDK',()=>{
 const file=fileURLToPath(new URL('../artifacts/read-file/main.wasm',import.meta.url));
 assert.ok(existsSync(file),'Build the real Kotlin sample first: node scripts/build-kotlin.mjs');
 const imports=WebAssembly.Module.imports(new WebAssembly.Module(readFileSync(file)));
 assert.ok(imports.some(e=>e.module==='ming_v1'&&e.name==='file_open'));
 assert.ok(imports.every(e=>e.module==='ming_v1'&&e.kind==='function'));
});
test('Kotlin SDK reads authorized UTF-8 content and receives DENIED for resource 2',async t=>{
 const dir=mkdtempSync(join(tmpdir(),'ming-kotlin-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const file=join(dir,'authorized.txt');const content='Hello, MingOS! 你好\n';writeFileSync(file,content);
 const result=await runApp({manifestPath,grants:{1:file}});
 assert.equal(result.appId,'dev.ming.read-file');
 const bytes=result.events.filter(e=>e.code===2).map(e=>e.value);
 assert.equal(Buffer.from(bytes).toString('utf8'),content);
 assert.ok(result.events.some(e=>e.code===3&&e.value===-1));
 assert.ok(result.events.some(e=>e.code===4&&e.value===0));
});
test('same Kotlin app cannot read the resource without a launcher grant',async()=>{
 const result=await runApp({manifestPath});
 assert.deepEqual(result.events,[{code:5,value:-1}]);
});
