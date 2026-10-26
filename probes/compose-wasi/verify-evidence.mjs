import assert from 'node:assert/strict';
import {readFile,writeFile,readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const probe=dirname(fileURLToPath(import.meta.url));const out=resolve(probe,'../../artifacts/compose-qualification');
const sha=b=>createHash('sha256').update(b).digest('hex');
const json=async file=>JSON.parse(await readFile(join(out,file),'utf8'));
const checks=[];
for(const file of (await readdir(out)).filter(f=>f.endsWith('.command.json'))){
 const receipt=await json(file);const label=file.slice(0,-'.command.json'.length);
 assert.equal(sha(await readFile(join(out,label+'.stdout.log'))),receipt.stdoutSha256,file+' stdout');
 assert.equal(sha(await readFile(join(out,label+'.stderr.log'))),receipt.stderrSha256,file+' stderr');
 checks.push('receipt log hashes: '+file);
}
const final=await json('compile-counter-final.command.json');assert.equal(final.exitCode,1);
for(const [file,hash] of Object.entries(final.inputHashes))assert.equal(sha(await readFile(join(probe,file))),hash,file+' final compile input');
const failure=await readFile(join(out,'compile-counter-final.stderr.log'),'utf8');
assert(failure.includes('No matching variant of org.jetbrains.compose.runtime:runtime:1.10.0'));
assert(failure.includes("'org.jetbrains.kotlin.wasm.target' with value 'wasi'"));
checks.push('final compile failed at authentic Runtime WASI variant resolution and inputs are unchanged');
for(const artifact of await json('download-hashes.json'))assert.equal(sha(await readFile(join(out,artifact.file))),artifact.sha256,artifact.file);
checks.push('all downloaded official artifact hashes');
for(const artifact of await json('official-source-hashes.json'))assert.equal(sha(await readFile(join(out,'official-sources/androidx-runtime-1.10.0-wasm-js',artifact.file))),artifact.sha256,artifact.file);
checks.push('unmodified official Runtime Wasm source tree hashes');
const modules=await json('module-analysis.json');
const runtime=modules.find(m=>m.component.group==='org.jetbrains.compose.runtime'&&!m.component.url);
assert.deepEqual(runtime.wasiVariants,[]);
assert(runtime.variants.some(v=>v.attributes?.['org.jetbrains.kotlin.wasm.target']==='js'));
const manifest=await readFile(join(out,'runtime-wasm-js-1.10.0.klib-manifest.txt'),'utf8');
assert(manifest.includes('wasm_targets=wasm-js'));checks.push('official runtime module and KLIB manifest agree on wasm-js');
const dependencies=await json('platform-dependencies.json');
for(const name of ['org.jetbrains.compose.runtime:runtime:1.10.0','androidx.compose.runtime:runtime:1.10.0','androidx.collection:collection:1.5.0','androidx.compose.runtime:runtime-annotation:1.10.0'])assert.equal(dependencies.find(d=>d.coordinate===name)?.resolved,false,name);
for(const name of ['org.jetbrains.kotlinx:kotlinx-coroutines-core:1.9.0','org.jetbrains.kotlinx:atomicfu:0.27.0'])assert.equal(dependencies.find(d=>d.coordinate===name)?.resolved,true,name);
for(const dep of dependencies.filter(d=>d.resolved))for(const artifact of dep.artifacts)assert.equal(sha(await readFile(artifact.file)),artifact.sha256,artifact.coordinate);
checks.push('independent dependency resolution receipts and resolved KLIB hashes');
const compiler=await json('compiler-artifacts.json');
const composeCompiler=compiler.flatMap(c=>c.artifacts).find(a=>a.coordinate==='org.jetbrains.kotlin:kotlin-compose-compiler-plugin-embeddable:2.3.20');
assert(composeCompiler);assert.equal(sha(await readFile(composeCompiler.file)),composeCompiler.sha256);
assert.equal((await json('compiler-artifacts.command.json')).exitCode,0);checks.push('official Compose compiler 2.3.20 artifact resolves and hash matches');
for(const location of ['androidx-runtime-1.10.0-common/META-INF/androidx/compose/runtime/runtime/LICENSE.txt','androidx-runtime-1.10.0-wasm-js/META-INF/androidx/compose/runtime/runtime/LICENSE.txt','androidx-collection-1.5.0-common/META-INF/androidx/collection/collection/LICENSE.txt','androidx-runtime-annotation-1.10.0-common/META-INF/androidx/compose/runtime/runtime-annotation/LICENSE.txt']){
 assert((await readFile(join(out,'official-sources',location),'utf8')).includes('Apache License'));
}
checks.push('Apache licenses preserved for all four extracted official source archives');
async function wasmFiles(dir){let files=[];for(const e of await readdir(dir,{withFileTypes:true})){const full=join(dir,e.name);if(e.isDirectory())files.push(...await wasmFiles(full));else if(e.name.endsWith('.wasm'))files.push(full);}return files;}
assert.deepEqual(await wasmFiles(join(probe,'build')),[]);checks.push('no probe Wasm executable exists; no runtime execution can be claimed');
const result={status:'PASS (evidence consistency, not Compose compilation)',checkedAt:new Date().toISOString(),checks};
await writeFile(join(out,'evidence-verification.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
