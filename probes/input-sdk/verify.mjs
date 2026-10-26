import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash,randomFillSync} from 'node:crypto';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url)),out=join(root,'artifacts/input-sdk');
const hash=b=>createHash('sha256').update(b).digest('hex'),json=async p=>JSON.parse(await readFile(join(out,p),'utf8'));
const runId=process.argv[2]??'final';assert.match(runId,/^[a-z0-9-]+$/);
const greenStage='green-'+runId,legacyStage='legacy-'+runId,pureStage='pure-'+runId;
const commands=[];
async function run(script,...args){const command=[process.execPath,'probes/input-sdk/'+script,...args];commands.push(command);const code=await new Promise((resolve,reject)=>{const c=spawn(command[0],command.slice(1),{cwd:root,env:process.env,windowsHide:true,stdio:'inherit'});c.on('error',reject);c.on('close',resolve)});assert.equal(code,0,command.join(' '));}
const result={status:'running',startedAt:new Date().toISOString(),commands,node:process.version,kotlin:'2.3.20',stages:{green:greenStage,legacy:legacyStage,pure:pureStage}};
try {
 await run('build.mjs',greenStage);await run('contract.mjs',greenStage);
 await run('build.mjs',legacyStage,'--legacy');await run('build.mjs',pureStage,'--pure');
 const before=await json('legacy-baseline/build-receipt.json'),after=await json(legacyStage+'/build-receipt.json'),pure=await json(pureStage+'/build-receipt.json'),green=await json(greenStage+'/build-receipt.json');
 const signature=r=>r.package.imports.map(({module,name,params,results})=>({module,name,params,results})).sort((a,b)=>(a.module+'.'+a.name).localeCompare(b.module+'.'+b.name));
 assert.deepEqual(signature(after),signature(before),'Legacy imports grew or changed signature');
 assert.ok(!after.package.imports.some(i=>i.module==='ming_input_v1'));
 assert.ok(!pure.package.imports.some(i=>i.module==='ming_input_v1'));
 const owned=['ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/TextInput.kt','ming-sdk/src/wasmWasiMain/kotlin/ming/sdk/TextEditState.kt'];
 const unchanged=[];for(const [p,h] of Object.entries(before.inputSha256))if(p.startsWith('ming-sdk/')&&!owned.includes(p)){assert.equal(hash(await readFile(join(root,p))),h,'Pre-existing SDK changed: '+p);unchanged.push(p);}
 const logs=[];let pureMemory,pureEntropy=0;const pureWasm=await readFile(join(out,pureStage,'main.wasm'));
 const pureInstance=await WebAssembly.instantiate(await WebAssembly.compile(pureWasm),{ming_v1:{log:(code,value)=>{logs.push([code,value]);return 0;}},ming_v2:{random_fill:(p,n)=>{const start=p>>>0;assert.ok(n>=0&&start+n<=pureMemory.buffer.byteLength);randomFillSync(Buffer.from(pureMemory.buffer,start,n));pureEntropy+=n;return 0;}}});pureMemory=pureInstance.exports.memory;pureInstance.exports._start();assert.deepEqual(logs,[[950,1]]);
 const frozen={status:'frozen',frozenAt:new Date().toISOString(),files:Object.fromEntries(await Promise.all(owned.map(async p=>[p,hash(await readFile(join(root,p)))])))};
 for(const [p,h] of Object.entries(frozen.files))assert.equal(green.inputSha256[p],h);
 await writeFile(join(out,'frozen-sdk.json'),JSON.stringify(frozen,null,2)+'\n');
 result.status='passed';result.frozenSdk=frozen;result.legacy={beforeImports:signature(before),afterImports:signature(after),importCount:after.package.imports.length,noNewNamespace:true,binaryIdentical:before.package.packagedSha256===after.package.packagedSha256,beforeSha256:before.package.packagedSha256,afterSha256:after.package.packagedSha256};
 result.pure={imports:signature(pure),events:logs,entropyBytes:pureEntropy,noInputNamespace:true};result.unchangedPreExistingSdkFiles=unchanged;result.boundary=await json(greenStage+'/boundary-result.json');result.red=await json('red/build-receipt.json');
 result.artifactSha256={};for(const stage of ['red',greenStage,'legacy-baseline',legacyStage,pureStage])for(const name of ['build-receipt.json','build.stdout.log','build.stderr.log',...(stage==='red'?[]:['compiler.wasm','main.wasm'])])result.artifactSha256[stage+'/'+name]=hash(await readFile(join(out,stage,name)));
 result.artifactSha256[greenStage+'/boundary-result.json']=hash(await readFile(join(out,greenStage,'boundary-result.json')));
}catch(e){result.status='failed';result.error=e.stack;process.exitCode=1;}
finally {result.finishedAt=new Date().toISOString();await writeFile(join(out,'verification.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({status:result.status,error:result.error,assertions:result.boundary?.logs.at(-1)?.assertions,legacyImportCount:result.legacy?.importCount,legacyBinaryIdentical:result.legacy?.binaryIdentical,frozen:result.frozenSdk}));}
