import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=fileURLToPath(new URL('../../../',import.meta.url)),out=join(root,'artifacts/text-field'),commands=[];
const hash=b=>createHash('sha256').update(b).digest('hex'),json=async p=>JSON.parse(await readFile(join(out,p),'utf8'));
const r={status:'running',commands,startedAt:new Date().toISOString(),node:process.version,stages:[]};
async function run(script,...args){const cmd=[process.execPath,'ming-compose/tests/text-contract/'+script,...args];commands.push(cmd);const code=await new Promise((resolve,reject)=>{const c=spawn(cmd[0],cmd.slice(1),{cwd:root,env:process.env,stdio:'inherit',windowsHide:true});c.on('error',reject);c.on('close',resolve)});assert.equal(code,0,cmd.join(' '));}
try {
 for(const [kind,stage]of [['text','fast-input-final'],['renderer-contract','fast-input-renderer'],['framed-app','fast-input-framed-app'],['contract','fast-input-composition'],['framed-driver','fast-input-driver'],['framed-runtime','fast-input-runtime']]) {
  r.stages.push({kind,stage});await run('build.mjs',stage,kind);
  if(kind==='text'){await run('fast-contract.mjs',stage);await run('contract.mjs',stage);}
  else await run('regression.mjs',stage,kind);
 }
 const baseline=await json('legacy-before/build-receipt.json'),renderer=await json('fast-input-renderer/build-receipt.json');
 const signature=r=>r.package.imports.map(({module,name,params,results})=>({module,name,params,results})).sort((a,b)=>(a.module+'.'+a.name).localeCompare(b.module+'.'+b.name));
 assert.deepEqual(signature(renderer),signature(baseline));r.legacyImports=signature(renderer);
 const before=await json('fast-input/before.json');for(const [p,h]of Object.entries(before.protected))assert.equal(hash(await readFile(join(root,p))),h,'Protected source/evidence changed '+p);
 r.productionSha256={};for(const name of ['TextApplication.kt','Renderer.kt','FramedApplication.kt']){const p='ming-compose/src/wasmWasiMain/kotlin/ming/compose/'+name;r.productionSha256[p]=hash(await readFile(join(root,p)));}
 const text=await json('fast-input-final/build-receipt.json'),sample=await json('fast-input-sample/build-receipt.json');for(const [p,h]of Object.entries(r.productionSha256)){assert.equal(text.inputSha256[p],h);assert.equal(sample.inputSha256[p],h);}
 r.protectedSha256=before.protected;r.fast=await json('fast-input-final/fast-contract-result.json');r.resources=await json('fast-input-final/contract-result.json');r.sample=await json('fast-input-sample/fast-sample-result.json');r.status='passed';
}catch(e){r.status='failed';r.error=e.stack;process.exitCode=1;}
finally{r.finishedAt=new Date().toISOString();await writeFile(join(out,'fast-input/verification.json'),JSON.stringify(r,null,2)+'\n');console.log(JSON.stringify({status:r.status,error:r.error,stages:r.stages,productionSha256:r.productionSha256}));}
