import assert from 'node:assert/strict';
import {readFile,writeFile,readdir,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {dirname,join,resolve,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
const probe=dirname(fileURLToPath(import.meta.url));const root=resolve(probe,'../../..');const out=join(root,'artifacts/compose-qualification/port');
const sha=b=>createHash('sha256').update(b).digest('hex');
async function walk(dir){const files=[];for(const e of await readdir(dir,{withFileTypes:true})){const file=join(dir,e.name);if(e.isDirectory())files.push(...await walk(file));else files.push(file);}return files;}
const provenance=JSON.parse(await readFile(join(out,'upstream-provenance.json'),'utf8'));const files=[];let commonKotlinFiles=0;
for(const source of provenance){
 for(const file of await walk(source.workingCopy)){
  const rel=relative(source.workingCopy,file);const original=join(source.extractedSource,rel);const data=await readFile(file);const hash=sha(data);
  assert.equal(hash,sha(await readFile(original)),file+' upstream source changed');
  if(rel.replaceAll('\\','/').startsWith('commonMain/')&&file.endsWith('.kt'))commonKotlinFiles++;
  files.push({module:source.name,path:relative(root,file).replaceAll('\\','/'),sha256:hash,bytes:data.length});
 }
 const licenses=(await walk(source.workingCopy)).filter(f=>/LICENSE/i.test(f));assert(licenses.length);assert((await readFile(licenses[0],'utf8')).includes('Apache'));
}
const copiedActuals=['internal/ComposableLambda','snapshots/SnapshotId','OldIdentityHashCode'];
for(const rel of copiedActuals)assert.equal(sha(await readFile(join(probe,'runtime/src/wasmWasiMain/kotlin/androidx/compose/runtime',rel+'.wasmWasi.kt'))),sha(await readFile(join(probe,'upstream/runtime/wasmJsMain/androidx/compose/runtime',rel+'.wasmJs.kt'))));
const pairs=[['collection-build','checkpoint-resume/collection/build.gradle.kts',join(probe,'collection/build.gradle.kts')],['runtime-build','checkpoint-resume/runtime/build.gradle.kts',join(probe,'runtime/build.gradle.kts')],['counter','checkpoint-resume/counter/src/wasmWasiMain/kotlin/Main.kt',join(probe,'counter/src/wasmWasiMain/kotlin/Main.kt')],['platform-logging',null,join(probe,'runtime/src/wasmWasiMain/kotlin/androidx/compose/runtime/internal/Utils.runtime.wasmWasi.kt')]];
await mkdir(join(out,'patches'),{recursive:true});
const patches=await Promise.all(pairs.map(async([label,old,newFile])=>{
 const oldFile=old?join(out,old):join(probe,'upstream/runtime/nonJvmMain/androidx/compose/runtime/internal/Utils.nonJvm.kt');const stdout=[],stderr=[];const args=['diff','--no-index','--no-ext-diff','--unified=3','--',oldFile,newFile];
 const exitCode=await new Promise((done,reject)=>{const child=spawn('git',args,{cwd:root,windowsHide:true});child.stdout.on('data',b=>stdout.push(b));child.stderr.on('data',b=>stderr.push(b));child.on('error',reject);child.on('exit',code=>done(code));});assert([0,1].includes(exitCode));
 const bytes=Buffer.concat(stdout),errors=Buffer.concat(stderr);await writeFile(join(out,'patches',label+'.patch'),bytes);await writeFile(join(out,'patches',label+'.stderr.log'),errors);const receipt={command:'git',args,exitCode,stdoutSha256:sha(bytes),stderrSha256:sha(errors)};await writeFile(join(out,'patches',label+'.command.json'),JSON.stringify(receipt,null,2)+'\n');return {label,sha256:sha(bytes)};
}));
const result={status:'passed',checkedAt:new Date().toISOString(),upstreamFiles:files.length,unchangedCommonKotlinFiles:commonKotlinFiles,commonAlgorithmEdits:0,licensesPreserved:true,copiedLanguageOnlyActualsIdentical:true,patches,files};await writeFile(join(out,'source-audit.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({...result,files:undefined},null,2));
