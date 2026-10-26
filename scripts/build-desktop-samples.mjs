import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,readdir,writeFile,mkdir} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
const requested=process.argv[2]??'desktop';assert.ok(['desktop','text-entry-demo'].includes(requested));
const sampleNames=requested==='desktop'?['compose-demo','animated-demo']:['text-entry-demo'];
const evidence=join(root,requested==='desktop'?'artifacts/desktop-runtime/samples-build':'artifacts/input-host/text-build');await mkdir(evidence,{recursive:true});
const sha=b=>createHash('sha256').update(b).digest('hex');
async function inputs(){const result={};async function walk(dir){for(const e of await readdir(dir,{withFileTypes:true})){if(['build','.gradle','.kotlin'].includes(e.name))continue;const p=join(dir,e.name);if(e.isDirectory())await walk(p);else result[relative(root,p).replaceAll('\\','/')]=sha(await readFile(p));}}
 for(const d of ['ming-sdk/src','ming-compose/src','samples/animated-demo/src','samples/compose-demo/src','samples/text-entry-demo/src','probes/compose-wasi/port/upstream','probes/compose-wasi/port/runtime/src','probes/compose-wasi/port/platform/src','probes/compose-frame/coroutines-port','crates/ming-app-host/src'])await walk(join(root,d));
 for(const p of ['settings.gradle.kts','build.gradle.kts','gradle.properties','ming-sdk/build.gradle.kts','ming-compose/build.gradle.kts','samples/text-entry-demo/build.gradle.kts','samples/text-entry-demo/app.json','samples/compose-demo/build.gradle.kts','samples/compose-demo/app.json','samples/compose-demo/event-contract.json','samples/compose-demo/tests/app.test.mjs','scripts/build-desktop-samples.mjs','samples/desktop/catalog.json','samples/desktop/verification-events.json','samples/desktop/verify.mjs','samples/animated-demo/build.gradle.kts','samples/animated-demo/app.json','samples/animated-demo/event-contract.json','samples/animated-demo/verify.mjs','scripts/build-kotlin.mjs','scripts/build-animated.mjs','scripts/wasm-sdk-adapter.mjs','gradle/wrapper/gradle-wrapper.jar','gradle/wrapper/gradle-wrapper.properties','Cargo.toml','Cargo.lock','rust-toolchain.toml','crates/ming-app-host/Cargo.toml',...['annotation','collection','runtime-annotation','runtime','platform'].map(m=>'probes/compose-wasi/port/'+m+'/build.gradle.kts'),'probes/compose-wasi/port/settings.gradle.kts','probes/compose-wasi/port/build.gradle.kts'])result[p]=sha(await readFile(join(root,p)));
 return Object.fromEntries(Object.entries(result).sort(([a],[b])=>a.localeCompare(b)));}
const before=await inputs();await writeFile(join(evidence,'source-before-build.json'),JSON.stringify(before,null,2)+'\n');
const startedAt=new Date().toISOString();const stdout=[],stderr=[];
const exitCode=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,['scripts/build-kotlin.mjs',requested],{cwd:root,env:process.env,windowsHide:true});child.stdout.on('data',b=>{stdout.push(b);process.stdout.write(b)});child.stderr.on('data',b=>{stderr.push(b);process.stderr.write(b)});child.on('error',reject);child.on('exit',c=>resolve(c??1));});
await writeFile(join(evidence,'build.stdout.log'),Buffer.concat(stdout));await writeFile(join(evidence,'build.stderr.log'),Buffer.concat(stderr));
assert.equal(exitCode,0,'Desktop sample Kotlin build failed');assert.deepEqual(await inputs(),before,'Inputs changed during compilation');
for(const name of sampleNames){
const pkg=join(root,'artifacts',name);const info=JSON.parse(await readFile(join(pkg,'build-info.json'),'utf8'));
assert.equal(sha(await readFile(join(pkg,'main.wasm'))),info.packagedSha256);assert.equal(sha(await readFile(info.rawPath)),info.rawSha256);
const receipt={schemaVersion:1,status:'passed',kotlin:'2.3.20',runtimeSource:'1.10.0',coroutineSource:'1.9.0',startedAt,finishedAt:new Date().toISOString(),command:[process.execPath,'scripts/build-kotlin.mjs',requested],exitCode,inputSha256:before,rawSha256:info.rawSha256,packagedSha256:info.packagedSha256,manifestSha256:sha(await readFile(join(pkg,'app.json'))),stdoutSha256:sha(Buffer.concat(stdout)),stderrSha256:sha(Buffer.concat(stderr))};
await writeFile(join(pkg,'build-receipt.json'),JSON.stringify(receipt,null,2)+'\n');console.log(JSON.stringify({status:'source-bound',sample:name,sources:Object.keys(before).length,wasm:info.packagedSha256}));
}
