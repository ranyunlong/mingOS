import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,readdir,stat} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {adaptKotlinWasi} from '../../scripts/wasm-sdk-adapter.mjs';
import {inspectImports} from '../compose-wasi/port/wasm-imports.mjs';

const probe=fileURLToPath(new URL('./',import.meta.url));
const root=fileURLToPath(new URL('../../',import.meta.url));
const stage=process.argv[2];
assert.ok(/^[a-z0-9-]+$/.test(stage??''),'Provide a new artifact stage name');
const expectRed=process.argv.includes('--expect-red');
const output=join(root,'artifacts/frame-sdk',stage);
await mkdir(output,{recursive:false});
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
async function inventory() {
 const files={};
 async function walk(dir){
  for(const entry of await readdir(dir,{withFileTypes:true})){
   if(['build','.gradle','.kotlin'].includes(entry.name))continue;
   const path=join(dir,entry.name);
   if(entry.isDirectory())await walk(path);
   else if(/\.(kt|kts|properties|mjs)$/.test(entry.name))files[relative(root,path).replaceAll('\\','/')]=hash(await readFile(path));
  }
 }
 await walk(join(root,'ming-sdk/src')); await walk(probe);
 for(const rel of ['ming-sdk/build.gradle.kts','gradle/wrapper/gradle-wrapper.jar','gradle/wrapper/gradle-wrapper.properties','scripts/wasm-sdk-adapter.mjs','probes/compose-wasi/port/wasm-imports.mjs']) files[rel]=hash(await readFile(join(root,rel)));
 return Object.fromEntries(Object.entries(files).sort(([a],[b])=>a.localeCompare(b)));
}
async function oldSdkOutputs(){
 const entries=[];
 async function walk(dir){let list;try{list=await readdir(dir,{withFileTypes:true})}catch(error){if(error.code==='ENOENT')return;throw error;}
  for(const entry of list){const path=join(dir,entry.name);if(entry.isDirectory())await walk(path);else{const info=await stat(path);entries.push([relative(root,path).replaceAll('\\','/'),info.size,info.mtimeMs]);}}}
 await walk(join(root,'ming-sdk/build'));return entries.sort(([a],[b])=>a.localeCompare(b));
}
const inputs=await inventory(),oldOutputs=await oldSdkOutputs();
for(const rel of Object.keys(inputs)){const dest=join(output,'input-sources',rel);await mkdir(join(dest,'..'),{recursive:true});await writeFile(dest,await readFile(join(root,rel)));}
const java=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin/java.exe'):'java';
const javaTmp=join(root,'.cache/java-tmp');await mkdir(javaTmp,{recursive:true});
const env={...process.env,GRADLE_USER_HOME:join(root,'.cache/gradle')};
if(process.platform==='win32')env.JAVA_TOOL_OPTIONS=[env.JAVA_TOOL_OPTIONS,'-Djdk.net.unixdomain.tmpdir="'+javaTmp+'"'].filter(Boolean).join(' ');
const args=['-classpath',join(root,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain','--offline','--no-daemon','--stacktrace','compileProductionExecutableKotlinWasmWasi'];
const receipt={schemaVersion:1,status:'running',stage,expectedRed:expectRed,kotlin:'2.3.20',command:java,args,cwd:probe,startedAt:new Date().toISOString(),inputSha256:inputs,frameClockSourcePresent:'ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/FrameClock.kt' in inputs,sdkOutputDirectory:join(probe,'build/ming-sdk'),oldRootSdkOutputMetadataSha256:hash(JSON.stringify(oldOutputs))};
const stdout=[],stderr=[];
try{
 const code=await new Promise((resolve,reject)=>{
  const child=spawn(java,args,{cwd:probe,env,windowsHide:true});
  child.stdout.on('data',bytes=>{stdout.push(bytes);process.stdout.write(bytes)});
  child.stderr.on('data',bytes=>{stderr.push(bytes);process.stderr.write(bytes)});
  child.on('error',reject);child.on('close',code=>resolve(code??1));
 });
 receipt.compilerExitCode=code;
 assert.deepEqual(await inventory(),inputs,'Frozen compilation sources changed');
 assert.deepEqual(await oldSdkOutputs(),oldOutputs,'Legacy ming-sdk/build outputs changed');
 receipt.legacySdkOutputsUnchanged=true;
 if(expectRed){
  assert.notEqual(code,0,'RED unexpectedly compiled');
  assert.equal(receipt.frameClockSourcePresent,false);
  assert.match(Buffer.concat(stderr).toString()+Buffer.concat(stdout).toString(),/Unresolved reference 'MingFrames'/);
  receipt.status='expected-red';
 }else{
  assert.equal(code,0,'Real Kotlin SDK contract compilation failed');
  const dir=join(probe,'build/compileSync/wasmWasi/main/productionExecutable/kotlin');
  const names=(await readdir(dir)).filter(name=>name.endsWith('.wasm'));assert.equal(names.length,1);
  const raw=await readFile(join(dir,names[0]));const {bytes,rewrites}=adaptKotlinWasi(raw);
  const reflected=await WebAssembly.compile(bytes),typed=inspectImports(bytes).imports;
  const arities={'ming_v1.log':2,'ming_frame_v1.clock_monotonic_ns':2,'ming_frame_v1.wait':4,'ming_v2.random_fill':2};
  for(const entry of typed){const key=entry.module+'.'+entry.name;assert.ok(key in arities,'Unexpected capability: '+key);assert.deepEqual(entry.params,Array(arities[key]).fill('i32'));assert.deepEqual(entry.results,['i32']);}
  for(const required of ['ming_v1.log','ming_frame_v1.clock_monotonic_ns','ming_frame_v1.wait'])assert.ok(typed.some(e=>e.module+'.'+e.name===required),required);
  assert.deepEqual(typed.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(reflected));
  for(const rewrite of rewrites)assert.deepEqual(rewrite,{from:'wasi_snapshot_preview1.random_get',to:'ming_v2.random_fill'});
  await writeFile(join(output,'compiler.wasm'),raw);await writeFile(join(output,'main.wasm'),bytes);
  await writeFile(join(output,'app.json'),JSON.stringify({schemaVersion:1,id:'dev.ming.frame-sdk-contract',version:'0.1.0',sdkVersion:5,entry:'main.wasm'},null,2)+'\n');
  const info={kotlin:'2.3.20',rawPath:join(dir,names[0]),rawSha256:hash(raw),packagedSha256:hash(bytes),bytes:bytes.length,rewrites,imports:typed,sdkProject:'ming-sdk',sdkSource:'../../ming-sdk',sdkBuildDirectory:receipt.sdkOutputDirectory,qualification:'Real Kotlin SDK compilation; Node host double only; native SDK5 execution pending parent integration'};
  await writeFile(join(output,'build-info.json'),JSON.stringify(info,null,2)+'\n');
  receipt.package=info;receipt.status='passed';
 }
}catch(error){receipt.status='failed';receipt.error=error.stack;process.exitCode=1;}
finally{
 const a=Buffer.concat(stdout),b=Buffer.concat(stderr);await writeFile(join(output,'build.stdout.log'),a);await writeFile(join(output,'build.stderr.log'),b);
 receipt.stdoutSha256=hash(a);receipt.stderrSha256=hash(b);receipt.finishedAt=new Date().toISOString();
 await writeFile(join(output,'build-receipt.json'),JSON.stringify(receipt,null,2)+'\n');
 console.log(JSON.stringify({status:receipt.status,compilerExitCode:receipt.compilerExitCode,output,error:receipt.error},null,2));
}
