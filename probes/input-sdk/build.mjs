import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {adaptKotlinWasi} from '../../scripts/wasm-sdk-adapter.mjs';
import {inspectImports} from '../compose-wasi/port/wasm-imports.mjs';
const probe=fileURLToPath(new URL('./',import.meta.url)),root=fileURLToPath(new URL('../../',import.meta.url));
const stage=process.argv[2];assert.match(stage??'',/^[a-z0-9-]+$/);
const red=process.argv.includes('--expect-red'),legacy=process.argv.includes('--legacy'),pure=process.argv.includes('--pure');
const out=join(root,'artifacts/input-sdk',stage);await mkdir(out,{recursive:false});
const hash=b=>createHash('sha256').update(b).digest('hex');
const inputs={};
async function walk(dir){for(const e of await readdir(dir,{withFileTypes:true})){if(['build','.gradle','.kotlin'].includes(e.name))continue;const p=join(dir,e.name);if(e.isDirectory())await walk(p);else if(/\.(kt|kts|properties|mjs)$/.test(e.name))inputs[relative(root,p).replaceAll('\\','/')]=hash(await readFile(p));}}
await walk(join(root,'ming-sdk/src'));await walk(probe);
for(const p of ['ming-sdk/build.gradle.kts','gradle/wrapper/gradle-wrapper.jar','gradle/wrapper/gradle-wrapper.properties','scripts/wasm-sdk-adapter.mjs','probes/compose-wasi/port/wasm-imports.mjs'])inputs[p]=hash(await readFile(join(root,p)));
for(const p of Object.keys(inputs)){const dest=join(out,'input-sources',p);await mkdir(join(dest,'..'),{recursive:true});await writeFile(dest,await readFile(join(root,p)));}
const java=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin/java.exe'):'java';
const env={...process.env,GRADLE_USER_HOME:join(root,'.cache/gradle')};
const javaTmp=join(probe,'build/java-tmp');await mkdir(javaTmp,{recursive:true});
if(process.platform==='win32')env.JAVA_TOOL_OPTIONS=[env.JAVA_TOOL_OPTIONS,'-Djdk.net.unixdomain.tmpdir="'+javaTmp+'"'].filter(Boolean).join(' ');
const args=['-classpath',join(root,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain','--offline','--no-daemon','--stacktrace',...(legacy?['-Pcontract=legacy']:pure?['-Pcontract=pure']:[]),'compileProductionExecutableKotlinWasmWasi'];
const receipt={stage,status:'running',expectedRed:red,legacy,pure,kotlin:'2.3.20',command:java,args,cwd:probe,startedAt:new Date().toISOString(),inputSha256:inputs};
const stdout=[],stderr=[];
try{
 const code=await new Promise((resolve,reject)=>{const c=spawn(java,args,{cwd:probe,env,windowsHide:true});c.stdout.on('data',b=>{stdout.push(b);process.stdout.write(b)});c.stderr.on('data',b=>{stderr.push(b);process.stderr.write(b)});c.on('error',reject);c.on('close',resolve)});
 receipt.compilerExitCode=code;
 // Parallel Host work may alter unrelated files: freeze only this task's probe and two SDK files.
 for(const [p,h] of Object.entries(inputs))if(p.startsWith('probes/input-sdk/')||/ming\/sdk\/Text(Input|EditState)\.kt$/.test(p))assert.equal(hash(await readFile(join(root,p))),h,'Owned input changed during compilation: '+p);
 if(red){assert.notEqual(code,0);assert.match(Buffer.concat([...stdout,...stderr]).toString(),/Unresolved reference '(MingTextInput|TextEditState|TextInput)/);receipt.status='expected-red';}
 else{
  assert.equal(code,0,'Kotlin compilation failed');
  const dir=join(probe,'build/compileSync/wasmWasi/main/productionExecutable/kotlin');const names=(await readdir(dir)).filter(n=>n.endsWith('.wasm'));assert.equal(names.length,1);
  const raw=await readFile(join(dir,names[0])),{bytes,rewrites}=adaptKotlinWasi(raw);const imports=inspectImports(bytes).imports;
  assert.deepEqual(imports.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(await WebAssembly.compile(bytes)));
  if(legacy||pure)assert.ok(!imports.some(i=>i.module==='ming_input_v1'));
  else {for(const name of ['poll','text_focus']){const i=imports.find(i=>i.module==='ming_input_v1'&&i.name===name);assert.ok(i);assert.deepEqual(i.params,['i32','i32']);assert.deepEqual(i.results,['i32']);}assert.ok(imports.every(i=>['ming_input_v1','ming_v1','ming_v2'].includes(i.module)));}
  await writeFile(join(out,'compiler.wasm'),raw);await writeFile(join(out,'main.wasm'),bytes);
  await writeFile(join(out,'app.json'),JSON.stringify({schemaVersion:1,id:'dev.ming.input-sdk-contract',version:'0.1.0',sdkVersion:legacy?5:6,entry:'main.wasm'},null,2)+'\n');
  receipt.package={rawSha256:hash(raw),packagedSha256:hash(bytes),bytes:bytes.length,rewrites,imports};receipt.status='passed';
 }
}catch(e){receipt.status='failed';receipt.error=e.stack;process.exitCode=1;}
finally{for(const [name,chunks] of [['stdout',stdout],['stderr',stderr]]){const b=Buffer.concat(chunks);await writeFile(join(out,'build.'+name+'.log'),b);receipt[name+'Sha256']=hash(b);}receipt.finishedAt=new Date().toISOString();await writeFile(join(out,'build-receipt.json'),JSON.stringify(receipt,null,2)+'\n');console.log(JSON.stringify({status:receipt.status,out,error:receipt.error}));}
