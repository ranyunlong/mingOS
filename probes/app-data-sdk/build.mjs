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
const red=process.argv.includes('--expect-red');const mode=process.argv.includes('--legacy')?'legacy':process.argv.includes('--size-only')?'size-only':'full';
const out=join(root,'artifacts/app-data/sdk-'+stage);await mkdir(out,{recursive:false});
const hash=b=>createHash('sha256').update(b).digest('hex');const inputs={};
async function walk(dir){for(const e of await readdir(dir,{withFileTypes:true})){if(['build','.gradle','.kotlin','rust'].includes(e.name))continue;const p=join(dir,e.name);if(e.isDirectory())await walk(p);else if(/\.(kt|kts|properties|mjs)$/.test(e.name))inputs[relative(root,p).replaceAll('\\','/')]=hash(await readFile(p));}}
await walk(join(root,'ming-sdk/src'));await walk(probe);
for(const p of ['ming-sdk/build.gradle.kts','gradle/wrapper/gradle-wrapper.jar','gradle/wrapper/gradle-wrapper.properties','scripts/wasm-sdk-adapter.mjs','probes/compose-wasi/port/wasm-imports.mjs'])inputs[p]=hash(await readFile(join(root,p)));
for(const p of Object.keys(inputs)){const dest=join(out,'input-sources',p);await mkdir(join(dest,'..'),{recursive:true});await writeFile(dest,await readFile(join(root,p)));}
const java=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin/java.exe'):'java';
const env={...process.env,GRADLE_USER_HOME:join(root,'.cache/gradle')};
const javaTmp=join(probe,'build/java-tmp');await mkdir(javaTmp,{recursive:true});
if(process.platform==='win32')env.JAVA_TOOL_OPTIONS=[env.JAVA_TOOL_OPTIONS,'-Djdk.net.unixdomain.tmpdir="'+javaTmp+'"'].filter(Boolean).join(' ');
const args=['-classpath',join(root,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain','--offline','--no-daemon','--stacktrace',...(mode==='full'?[]:['-Pcontract='+mode]),'compileProductionExecutableKotlinWasmWasi'];
const receipt={stage,status:'running',expectedRed:red,mode,kotlin:'2.3.20',command:java,args,cwd:probe,startedAt:new Date().toISOString(),inputSha256:inputs,limitations:['Node capability double; native engine/RPC/catalog/VM integration is delegated to main.']};
const stdout=[],stderr=[];
try{
 const code=await new Promise((resolve,reject)=>{const c=spawn(java,args,{cwd:probe,env,windowsHide:true});c.stdout.on('data',b=>{stdout.push(b);process.stdout.write(b)});c.stderr.on('data',b=>{stderr.push(b);process.stderr.write(b)});c.on('error',reject);c.on('close',resolve)});
 receipt.compilerExitCode=code;
 for(const [p,h] of Object.entries(inputs))if(p.startsWith('probes/app-data-sdk/')||p.endsWith('/AppData.kt'))assert.equal(hash(await readFile(join(root,p))),h,'Owned input changed during compilation: '+p);
 if(red){assert.notEqual(code,0);assert.match(Buffer.concat([...stdout,...stderr]).toString(),/Unresolved reference 'MingAppData'/);receipt.status='expected-red';}
 else{
  assert.equal(code,0,'Kotlin compilation failed');const dir=join(probe,'build/compileSync/wasmWasi/main/productionExecutable/kotlin');const names=(await readdir(dir)).filter(n=>n.endsWith('.wasm'));assert.equal(names.length,1);
  const raw=await readFile(join(dir,names[0])),{bytes,rewrites}=adaptKotlinWasi(raw);const imports=inspectImports(bytes).imports;
  assert.deepEqual(imports.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(await WebAssembly.compile(bytes)));
  const dataImports=imports.filter(i=>i.module==='ming_data_v1');
  assert.deepEqual(dataImports.map(i=>i.name).sort(),mode==='legacy'?[]:mode==='size-only'?['size']:['delete','read','size','write']);
  const arities={read:4,write:3,size:1,delete:1};for(const i of dataImports){assert.deepEqual(i.params,Array(arities[i.name]).fill('i32'));assert.deepEqual(i.results,['i32']);}
  assert.ok(imports.every(i=>['ming_data_v1','ming_v1','ming_v2'].includes(i.module)));
  await writeFile(join(out,'compiler.wasm'),raw);await writeFile(join(out,'main.wasm'),bytes);
  await writeFile(join(out,'app.json'),JSON.stringify({schemaVersion:1,id:'dev.ming.app-data-sdk-contract',version:'0.1.0',sdkVersion:mode==='legacy'?5:6,entry:'main.wasm'},null,2)+'\n');
  receipt.package={rawSha256:hash(raw),packagedSha256:hash(bytes),bytes:bytes.length,rewrites,imports};receipt.status='passed';
 }
}catch(e){receipt.status='failed';receipt.error=e.stack;process.exitCode=1;}
finally{for(const [name,chunks] of [['stdout',stdout],['stderr',stderr]]){const b=Buffer.concat(chunks);await writeFile(join(out,'build.'+name+'.log'),b);receipt[name+'Sha256']=hash(b);}receipt.finishedAt=new Date().toISOString();await writeFile(join(out,'build-receipt.json'),JSON.stringify(receipt,null,2)+'\n');console.log(JSON.stringify({status:receipt.status,out,error:receipt.error}));}
