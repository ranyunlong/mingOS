import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {adaptKotlinWasi} from './wasm-sdk-adapter.mjs';
import {inspectImports} from '../probes/compose-wasi/port/wasm-imports.mjs';

const kind=process.argv[2]??'composition';
if(!['composition','renderer','driver','runtime','framed-app'].includes(kind))throw new Error('Expected composition, renderer, driver, runtime or framed-app');
const root=fileURLToPath(new URL('../',import.meta.url));
const output=join(root,'artifacts/compose-bridge',kind+'-package');
const buildDir={composition:'contract',renderer:'renderer-contract',driver:'driver-contract',runtime:'framed-runtime-contract','framed-app':'framed-app-contract'}[kind];
const property={composition:'mingComposeContract',renderer:'mingComposeRendererContract',driver:'mingComposeDriverContract',runtime:'mingComposeRuntimeContract','framed-app':'mingComposeFramedAppContract'}[kind];
await mkdir(output,{recursive:true});
const sha=b=>createHash('sha256').update(b).digest('hex');
async function sources(){
 const inputs={};
 async function walk(file){for(const entry of await readdir(file,{withFileTypes:true})){
  if(['build','.gradle','.kotlin'].includes(entry.name))continue;
  const p=join(file,entry.name);
  if(entry.isDirectory())await walk(p);
  else if(/\.(kt|kts|txt|md|json|mjs)$/.test(entry.name))inputs[relative(root,p).replaceAll('\\','/')]=sha(await readFile(p));
 }}
 for(const rel of ['ming-compose/src','ming-compose/tests','ming-sdk/src',
  'probes/compose-wasi/port/upstream','probes/compose-wasi/port/platform',
  'probes/compose-wasi/port/annotation','probes/compose-wasi/port/collection',
  'probes/compose-wasi/port/runtime-annotation','probes/compose-wasi/port/runtime',
  'probes/compose-frame/coroutines-port'])await walk(join(root,rel));
 for(const rel of ['settings.gradle.kts','build.gradle.kts','gradle.properties','ming-compose/build.gradle.kts','ming-sdk/build.gradle.kts',
  'probes/compose-wasi/port/settings.gradle.kts','probes/compose-wasi/port/build.gradle.kts',
  'scripts/build-compose-contract.mjs','scripts/wasm-sdk-adapter.mjs','probes/compose-wasi/port/wasm-imports.mjs'])inputs[rel]=sha(await readFile(join(root,rel)));
 return Object.fromEntries(Object.entries(inputs).sort(([a],[b])=>a.localeCompare(b)));
}
const inputs=await sources();
const java=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin',process.platform==='win32'?'java.exe':'java'):'java';
const args=['-classpath',join(root,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain',
 '--offline','--no-daemon','--stacktrace','-P'+property,':ming-compose:compileProductionExecutableKotlinWasmWasi'];
const env={...process.env,GRADLE_USER_HOME:join(root,'.cache/gradle')};
const javaTmp=join(root,'.cache/java-tmp');await mkdir(javaTmp,{recursive:true});
if(process.platform==='win32')env.JAVA_TOOL_OPTIONS=[env.JAVA_TOOL_OPTIONS,'-Djdk.net.unixdomain.tmpdir="'+javaTmp+'"'].filter(Boolean).join(' ');
const receipt={status:'running',kind,kotlin:'2.3.20',officialRuntimeSource:'1.10.0',command:java,args,cwd:root,startedAt:new Date().toISOString(),inputSha256:inputs};
const save=()=>writeFile(join(output,'build-receipt.json'),JSON.stringify(receipt,null,2)+'\n');
await save();
const stdout=[],stderr=[];
try{
 const exitCode=await new Promise((resolve,reject)=>{
  const child=spawn(java,args,{cwd:root,env,windowsHide:true});
  child.stdout.on('data',b=>{stdout.push(b);process.stdout.write(b)});
  child.stderr.on('data',b=>{stderr.push(b);process.stderr.write(b)});
  child.on('error',reject);child.on('exit',code=>resolve(code??1));
 });
 receipt.exitCode=exitCode;
 assert.equal(exitCode,0,'Kotlin contract compilation failed');
 assert.deepEqual(await sources(),inputs,'Sources changed during compilation');
 const rawDir=join(root,'ming-compose/build',buildDir,'compileSync/wasmWasi/main/productionExecutable/kotlin');
 const files=(await readdir(rawDir)).filter(f=>f.endsWith('.wasm'));
 assert.equal(files.length,1,'Expected exactly one real compiler Wasm');
 const rawPath=join(rawDir,files[0]),raw=await readFile(rawPath);
 const {bytes,rewrites}=adaptKotlinWasi(raw);
 const compiled=await WebAssembly.compile(bytes),imports=inspectImports(bytes).imports;
 const arities={'ming_v1.log':2,'ming_v2.random_fill':2,'ming_v2.event_next':2,
  'ming_v3.text_layout_create':5,'ming_v3.text_layout_metrics':3,'ming_v3.text_layout_close':1,
  'ming_frame_v1.clock_monotonic_ns':2,'ming_frame_v1.wait':4,'ming_v4.frame_submit':2,'ming_v4.shadow_style_create':2,'ming_v4.shadow_style_close':1};
 for(const i of imports){const name=i.module+'.'+i.name;assert.ok(name in arities,'Unexpected external capability: '+name);assert.deepEqual(i.params,Array(arities[name]).fill('i32'),name);assert.deepEqual(i.results,['i32'],name);}
 assert.deepEqual(imports.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(compiled));
 assert.deepEqual(rewrites,[{from:'wasi_snapshot_preview1.random_get',to:'ming_v2.random_fill'}]);
 await writeFile(join(output,'compiler.wasm'),raw);
 await writeFile(join(output,'main.wasm'),bytes);
 await writeFile(join(output,'app.json'),JSON.stringify({schemaVersion:1,id:'dev.ming.compose-'+kind+'-contract',version:'0.1.0',sdkVersion:kind==='framed-app'?5:4,entry:'main.wasm'},null,2)+'\n');
 const info={kotlin:'2.3.20',rawPath,rawSha256:sha(raw),packagedSha256:sha(bytes),rewrites,imports};
 await writeFile(join(output,'build-info.json'),JSON.stringify(info,null,2)+'\n');
 receipt.package=info;receipt.status='passed';
} catch(error){receipt.status='failed';receipt.error=error.stack;process.exitCode=1;}
finally{
 const a=Buffer.concat(stdout),b=Buffer.concat(stderr);
 await writeFile(join(output,'build.stdout.log'),a);await writeFile(join(output,'build.stderr.log'),b);
 receipt.stdoutSha256=sha(a);receipt.stderrSha256=sha(b);receipt.finishedAt=new Date().toISOString();await save();
}
