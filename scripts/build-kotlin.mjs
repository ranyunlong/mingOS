import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
import {adaptKotlinWasi} from './wasm-sdk-adapter.mjs';
import {inspectImports} from '../probes/compose-wasi/port/wasm-imports.mjs';
import {spawn} from 'node:child_process';
import {mkdir,copyFile,readdir,access,readFile,writeFile} from 'node:fs/promises';
import {join,resolve,relative} from 'node:path';
import {fileURLToPath} from 'node:url';

const requested=process.argv[2]??'read-file';
if(!['read-file','image-viewer','text-demo','ui-demo','shadow-demo','compose-demo','animated-demo','text-entry-demo','persistent-uninstall-demo','persistent-update-demo','desktop','all'].includes(requested))throw new Error('Expected read-file, image-viewer, text-demo, ui-demo, shadow-demo, compose-demo, animated-demo, text-entry-demo, persistent-uninstall-demo, persistent-update-demo, desktop or all');
const updateFixture=requested==='persistent-update-demo';
const fixture=updateFixture?(process.argv[3]??'old'):null;
if(updateFixture&&!['old','new','absent-write'].includes(fixture))throw new Error('Expected persistent-update-demo old, new or absent-write');
const samples=requested==='desktop'?['compose-demo','animated-demo']:requested==='all'?['read-file','image-viewer','text-demo','ui-demo','shadow-demo','compose-demo','animated-demo','text-entry-demo']:[requested];
const root=fileURLToPath(new URL('../',import.meta.url));
const sha=b=>createHash('sha256').update(b).digest('hex');
async function updateInputs(){
 const inputs={};
 async function walk(dir){for(const entry of await readdir(dir,{withFileTypes:true})){
  if(['build','builds','.gradle','.kotlin','.git','node_modules'].includes(entry.name))continue;
  const path=join(dir,entry.name);
  if(entry.isDirectory())await walk(path);
  else if(entry.isFile())inputs[relative(root,path).replaceAll('\\','/')]=sha(await readFile(path));
 }}
 for(const path of ['samples/persistent-update-demo','ming-sdk','ming-compose','probes/compose-wasi/port','probes/compose-frame/coroutines-port'])await walk(join(root,path));
 for(const path of ['settings.gradle.kts','build.gradle.kts','gradle.properties','gradle/wrapper/gradle-wrapper.jar','gradle/wrapper/gradle-wrapper.properties','scripts/build-kotlin.mjs','scripts/wasm-sdk-adapter.mjs'])inputs[path]=sha(await readFile(join(root,path)));
 return Object.fromEntries(Object.entries(inputs).sort(([a],[b])=>a.localeCompare(b)));
}
const inputSha256=updateFixture?await updateInputs():null;
const cache=join(root,'.cache');const javaTmp=join(cache,'java-tmp');
await mkdir(javaTmp,{recursive:true});
const wrapper=join(root,'gradle','wrapper','gradle-wrapper.jar');
try {await access(wrapper);}catch{throw new Error('Gradle wrapper is missing; use the checked-in wrapper files.');}
const java=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin',process.platform==='win32'?'java.exe':'java'):'java';
const args=['-classpath',wrapper,'org.gradle.wrapper.GradleWrapperMain','--no-daemon','--stacktrace',...(requested==='persistent-uninstall-demo'||updateFixture?['--offline']:[]),...(updateFixture?['-PpersistentUpdateFixture='+fixture]:[]),...samples.map(name=>':'+name+':compileProductionExecutableKotlinWasmWasi')];
const env={...process.env,GRADLE_USER_HOME:join(cache,'gradle')};
// Apply before both JVMs initialize networking; Gradle may filter daemon -D options.
if(process.platform==='win32') {
 const socketOption='-Djdk.net.unixdomain.tmpdir="'+javaTmp+'"';
 env.JAVA_TOOL_OPTIONS=[env.JAVA_TOOL_OPTIONS,socketOption].filter(Boolean).join(' ');
}
const startedAt=new Date().toISOString(),stdout=[],stderr=[];
const status=await new Promise((resolveStatus,reject)=>{
 const child=spawn(java,args,{cwd:root,env,stdio:updateFixture?['ignore','pipe','pipe']:'inherit',windowsHide:true});
 if(updateFixture)for(const [stream,chunks]of [['stdout',stdout],['stderr',stderr]])child[stream].on('data',b=>{chunks.push(b);process[stream].write(b);});
 child.on('error',reject);child.on('close',(code)=>resolveStatus(code??1));
});
if(updateFixture){
 const output=join(root,'artifacts/persistent-update-demo',fixture);await mkdir(output,{recursive:true});
 for(const [stream,chunks]of [['stdout',stdout],['stderr',stderr]])await writeFile(join(output,'build.'+stream+'.log'),Buffer.concat(chunks));
 await writeFile(join(output,'build-receipt.json'),JSON.stringify({schemaVersion:1,status:status===0?'compiled':'failed',fixture,kotlin:'2.3.20',composeRuntimeSource:'1.10.0',coroutinesSource:'1.9.0',startedAt,finishedAt:new Date().toISOString(),command:[java,...args],offline:true,compilerExitCode:status,inputSha256,stdoutSha256:sha(Buffer.concat(stdout)),stderrSha256:sha(Buffer.concat(stderr))},null,2)+'\n');
}
if(status!==0)process.exit(status);
if(updateFixture)assert.deepEqual(await updateInputs(),inputSha256,'Kotlin build inputs changed during compilation');
async function find(dir){const found=[];for(const entry of await readdir(dir,{withFileTypes:true})){const name=join(dir,entry.name);if(entry.isDirectory())found.push(...await find(name));else if(name.endsWith('.wasm')&&name.includes('productionExecutable'))found.push(name);}return found;}
for(const sample of samples){
const files=await find(join(root,'samples',sample,'build',...(updateFixture?[fixture]:[]),'compileSync'));
if(files.length!==1)throw new Error('Expected exactly one production Kotlin Wasm module for '+sample);
const output=resolve(root,'artifacts',sample,...(updateFixture?[fixture]:[]));await mkdir(output,{recursive:true});
if(sample==='image-viewer'||sample==='text-demo'||sample==='ui-demo'||sample==='shadow-demo'||sample==='compose-demo'||sample==='animated-demo'||sample==='text-entry-demo'||sample==='persistent-uninstall-demo'||updateFixture){
 const raw=await readFile(files[0]),result=adaptKotlinWasi(raw);
 const info={kotlin:'2.3.20',adapterVersion:1,rawPath:files[0],rawSha256:sha(raw),packagedSha256:sha(result.bytes),rewrites:result.rewrites};
 if(updateFixture){
  const imports=inspectImports(result.bytes).imports;
  const arities={'ming_private_files_v2.call':4,'ming_v1.log':2,'ming_v2.random_fill':2,'ming_v2.event_next':2,'ming_input_v1.poll':2,'ming_input_v1.text_focus':2,'ming_frame_v1.wait':4,'ming_frame_v1.clock_monotonic_ns':2,'ming_v3.text_layout_create':5,'ming_v3.text_layout_metrics':3,'ming_v3.text_layout_close':1,'ming_v4.frame_submit':2,'ming_v4.shadow_style_create':2,'ming_v4.shadow_style_close':1};
  for(const entry of imports){const key=entry.module+'.'+entry.name;assert.ok(key in arities,'Unexpected SDK capability '+key);assert.deepEqual(entry.params,Array(arities[key]).fill('i32'),key);assert.deepEqual(entry.results,['i32'],key);}
  for(const key of ['ming_private_files_v2.call','ming_input_v1.poll','ming_input_v1.text_focus','ming_frame_v1.wait','ming_v4.frame_submit'])assert.ok(imports.some(entry=>entry.module+'.'+entry.name===key),'Missing SDK capability '+key);
  const module=await WebAssembly.compile(result.bytes);
  assert.deepEqual(imports.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(module));
  assert.deepEqual(result.rewrites,[{from:'wasi_snapshot_preview1.random_get',to:'ming_v2.random_fill'}]);
  await writeFile(join(output,'compiler.wasm'),raw);
  Object.assign(info,{fixture,sdkVersion:6,rawPath:join(output,'compiler.wasm'),compilerOutputPath:files[0],composeRuntimeSource:'1.10.0',coroutinesSource:'1.9.0',sdkSourcePath:'ming-sdk/src/wasmWasiMain/kotlin',inputSha256,imports,exports:WebAssembly.Module.exports(module)});
 }
 await writeFile(join(output,'main.wasm'),result.bytes);await writeFile(join(output,'build-info.json'),JSON.stringify(info,null,2)+'\n');
}else{await copyFile(files[0],join(output,'main.wasm'));}
await copyFile(join(root,'samples',sample,updateFixture?fixture+'.app.json':'app.json'),join(output,'app.json'));
if(updateFixture){
 const receiptPath=join(output,'build-receipt.json'),receipt=JSON.parse(await readFile(receiptPath,'utf8'));
 Object.assign(receipt,{status:'passed',cwd:root,finishedAt:new Date().toISOString(),manifestSha256:sha(await readFile(join(output,'app.json'))),package:JSON.parse(await readFile(join(output,'build-info.json'),'utf8'))});
 await writeFile(receiptPath,JSON.stringify(receipt,null,2)+'\n');
}
console.log('Packaged real Kotlin/Wasm application: '+output);
}
