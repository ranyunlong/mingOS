import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {adaptKotlinWasi} from '../../../scripts/wasm-sdk-adapter.mjs';
import {inspectImports} from '../../../probes/compose-wasi/port/wasm-imports.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url)),probe=fileURLToPath(new URL('./',import.meta.url));
const stage=process.argv[2],kind=process.argv[3]??'text',red=process.argv.includes('--expect-red');assert.match(stage??'',/^[a-z0-9-]+$/);
const out=join(root,'artifacts/text-field',stage);await mkdir(out,{recursive:false});
const hash=b=>createHash('sha256').update(b).digest('hex'),inputs={};
async function walk(dir){for(const e of await readdir(dir,{withFileTypes:true})){if(['build','.gradle','.kotlin'].includes(e.name))continue;const p=join(dir,e.name);if(e.isDirectory())await walk(p);else if(/\.(kt|kts|properties|mjs|json)$/.test(e.name))inputs[relative(root,p).replaceAll('\\','/')]=hash(await readFile(p));}}
await walk(join(root,'ming-compose/src'));await walk(join(root,'ming-compose/tests'));await walk(join(root,'ming-sdk/src'));await walk(join(root,'samples/text-entry-demo'));
for(const p of ['ming-compose/build.gradle.kts','ming-sdk/build.gradle.kts','scripts/wasm-sdk-adapter.mjs','probes/compose-wasi/port/wasm-imports.mjs'])inputs[p]=hash(await readFile(join(root,p)));
for(const p of Object.keys(inputs)){const dest=join(out,'input-sources',p);await mkdir(join(dest,'..'),{recursive:true});await writeFile(dest,await readFile(join(root,p)));}
const java=join(process.env.JAVA_HOME??'C:/Program Files/Android/Android Studio/jbr','bin/java.exe');
const javaTmp=join(probe,'build/java-tmp');await mkdir(javaTmp,{recursive:true});
const env={...process.env,GRADLE_USER_HOME:join(root,'.cache/gradle'),JAVA_TOOL_OPTIONS:'-Djdk.net.unixdomain.tmpdir="'+javaTmp+'"'};
const target=kind==='sample'?'text-entry-demo':'ming-compose';
const args=['-classpath',join(root,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain','--offline','--no-daemon','--stacktrace','-PtextContract='+kind,':'+target+':compileProductionExecutableKotlinWasmWasi'];
const r={status:'running',stage,kind,kotlin:'2.3.20',command:java,args,cwd:probe,inputSha256:inputs,startedAt:new Date().toISOString()},stdout=[],stderr=[];
try {
 const code=await new Promise((resolve,reject)=>{const c=spawn(java,args,{cwd:probe,env,windowsHide:true});for(const [stream,buf] of [['stdout',stdout],['stderr',stderr]])c[stream].on('data',b=>{buf.push(b);process[stream].write(b)});c.on('error',reject);c.on('close',resolve)});r.compilerExitCode=code;
 if(red){assert.notEqual(code,0);assert.match(Buffer.concat([...stdout,...stderr]).toString(),/Unresolved reference '(TextFieldState|MingTextField|MingTextApplication|TextInputReceiver)'/);r.status='expected-red';}
 else {
 assert.equal(code,0);for(const [p,h] of Object.entries(inputs))if(p.endsWith('.kt')||p.endsWith('.kts'))assert.equal(hash(await readFile(join(root,p))),h,'Compiled source changed: '+p);
 const dir=join(probe,'build',target,'compileSync/wasmWasi/main/productionExecutable/kotlin'),names=(await readdir(dir)).filter(n=>n.endsWith('.wasm'));assert.equal(names.length,1);
 const raw=await readFile(join(dir,names[0])),{bytes,rewrites}=adaptKotlinWasi(raw),imports=inspectImports(bytes).imports;
 await WebAssembly.compile(bytes);await writeFile(join(out,'compiler.wasm'),raw);await writeFile(join(out,'main.wasm'),bytes);
 r.package={rawSha256:hash(raw),packagedSha256:hash(bytes),rewrites,imports};r.status='passed';
 }
}catch(e){r.status='failed';r.error=e.stack;process.exitCode=1;}
finally{for(const [name,chunks] of [['stdout',stdout],['stderr',stderr]]){const b=Buffer.concat(chunks);await writeFile(join(out,'build.'+name+'.log'),b);r[name+'Sha256']=hash(b);}r.finishedAt=new Date().toISOString();await writeFile(join(out,'build-receipt.json'),JSON.stringify(r,null,2)+'\n');console.log(JSON.stringify({status:r.status,stage,error:r.error}));}
