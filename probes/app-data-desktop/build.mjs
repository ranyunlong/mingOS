import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {adaptKotlinWasi} from '../../scripts/wasm-sdk-adapter.mjs';
import {inspectImports} from '../compose-wasi/port/wasm-imports.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url)),probe=fileURLToPath(new URL('./',import.meta.url)),mode='appdata',stage=process.argv[2]??'package';
assert.match(stage,/^[a-z0-9-]+$/);const out=join(root,'artifacts/app-data-desktop',stage);await mkdir(out,{recursive:false});
const hash=b=>createHash('sha256').update(b).digest('hex'),inputs={};
async function inventory(dir){for(const e of await readdir(dir,{withFileTypes:true})){if(['build','.gradle','.kotlin'].includes(e.name))continue;const p=join(dir,e.name);if(e.isDirectory())await inventory(p);else if(/\.(kt|kts|properties|mjs)$/.test(e.name))inputs[relative(root,p).replaceAll('\\','/')]=hash(await readFile(p));}}
for(const p of ['probes/app-data-desktop','ming-sdk/src','ming-compose/src'])await inventory(join(root,p));
for(const p of ['ming-sdk/build.gradle.kts','ming-compose/build.gradle.kts','gradle/wrapper/gradle-wrapper.jar','gradle/wrapper/gradle-wrapper.properties','scripts/wasm-sdk-adapter.mjs','probes/compose-wasi/port/wasm-imports.mjs'])inputs[p]=hash(await readFile(join(root,p)));
// Snapshot only actual local Kotlin/Gradle/probe inputs. Rust, AppData worker and unrelated
// mutable artifacts are deliberately not part of this compilation inventory.
for(const p of Object.keys(inputs)){const dest=join(out,'input-sources',p);await mkdir(join(dest,'..'),{recursive:true});await writeFile(dest,await readFile(join(root,p)));}
const java=join(process.env.JAVA_HOME??'C:/Program Files/Android/Android Studio/jbr','bin/java.exe'),tmp=join(probe,'build/java-tmp');await mkdir(tmp,{recursive:true});
const env={...process.env,GRADLE_USER_HOME:join(root,'.cache/gradle'),JAVA_TOOL_OPTIONS:'-Djdk.net.unixdomain.tmpdir="'+tmp+'"'};
const args=['-classpath',join(root,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain','--offline','--no-daemon','--stacktrace','-PprobeMode='+mode,':compileProductionExecutableKotlinWasmWasi'];
const r={status:'running',mode,stage,kotlin:'2.3.20',command:java,args,cwd:probe,inputSha256:inputs,startedAt:new Date().toISOString()},stdout=[],stderr=[];
try {
 const code=await new Promise((resolve,reject)=>{const c=spawn(java,args,{cwd:probe,env,windowsHide:true});for(const [stream,chunks]of [['stdout',stdout],['stderr',stderr]])c[stream].on('data',b=>{chunks.push(b);process[stream].write(b)});c.on('error',reject);c.on('close',resolve)});r.compilerExitCode=code;assert.equal(code,0);
 for(const [p,h]of Object.entries(inputs))if(p.endsWith('.kt')||p.endsWith('.kts'))assert.equal(hash(await readFile(join(root,p))),h,'Kotlin input changed during compile '+p);
 const dir=join(probe,'build',mode,'compileSync/wasmWasi/main/productionExecutable/kotlin'),names=(await readdir(dir)).filter(n=>n.endsWith('.wasm'));assert.equal(names.length,1);
 const raw=await readFile(join(dir,names[0])),{bytes,rewrites}=adaptKotlinWasi(raw),imports=inspectImports(bytes).imports,mod=await WebAssembly.compile(bytes);
 assert.deepEqual(imports.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(mod));
 const arities={'ming_data_v1.size':1,'ming_data_v1.read':4,'ming_data_v1.write':3,'ming_v1.log':2,'ming_v2.random_fill':2,'ming_v2.event_next':2,'ming_frame_v1.wait':4,'ming_frame_v1.clock_monotonic_ns':2,'ming_v3.text_layout_create':5,'ming_v3.text_layout_metrics':3,'ming_v3.text_layout_close':1,'ming_v4.frame_submit':2,'ming_v4.shadow_style_create':2,'ming_v4.shadow_style_close':1};
 for(const i of imports){assert.ok(i.module+'.'+i.name in arities,'Unexpected capability '+i.module+'.'+i.name);assert.deepEqual(i.params,Array(arities[i.module+'.'+i.name]).fill('i32'));assert.deepEqual(i.results,['i32']);}
 assert.ok(!imports.some(i=>i.module==='ming_input_v1'));assert.ok(!imports.some(i=>i.module==='ming_data_v1'&&i.name==='delete'));for(const name of ['size','read','write'])assert.ok(imports.some(i=>i.module==='ming_data_v1'&&i.name===name));for(const key of ['ming_frame_v1.wait','ming_frame_v1.clock_monotonic_ns','ming_v4.frame_submit'])assert.ok(imports.some(i=>i.module+'.'+i.name===key));
 await writeFile(join(out,'compiler.wasm'),raw);await writeFile(join(out,'main.wasm'),bytes);
 const app={schemaVersion:1,id:'dev.ming.app-data-desktop',version:'0.1.0',sdkVersion:6,entry:'main.wasm'};await writeFile(join(out,'app.json'),JSON.stringify(app,null,2)+'\n');
 r.package={rawSha256:hash(raw),packagedSha256:hash(bytes),bytes:bytes.length,rewrites,imports,exports:WebAssembly.Module.exports(mod)};r.status='passed';
 const protectedSource=JSON.parse(await readFile(join(root,'artifacts/app-data-desktop/protected-source.json'),'utf8'));for(const [p,h]of Object.entries(protectedSource))assert.equal(hash(await readFile(join(root,p))),h,'Existing SDK/Compose source modified '+p);r.existingImplementationUnchanged=true;
}catch(e){r.status='failed';r.error=e.stack;process.exitCode=1;}
finally {for(const [name,chunks]of [['stdout',stdout],['stderr',stderr]]){const bytes=Buffer.concat(chunks);await writeFile(join(out,'build.'+name+'.log'),bytes);r[name+'Sha256']=hash(bytes);}r.finishedAt=new Date().toISOString();await writeFile(join(out,'build-receipt.json'),JSON.stringify(r,null,2)+'\n');console.log(JSON.stringify({status:r.status,mode,stage,error:r.error,package:r.package?.packagedSha256}));}
