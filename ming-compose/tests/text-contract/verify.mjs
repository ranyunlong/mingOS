import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../../',import.meta.url)),out=join(root,'artifacts/text-field'),prefix=process.argv[2]??'final';
assert.match(prefix,/^[a-z0-9-]+$/);const commands=[],r={status:'running',startedAt:new Date().toISOString(),commands,kotlin:'2.3.20',node:process.version,stages:[]};
const hash=b=>createHash('sha256').update(b).digest('hex'),json=async p=>JSON.parse(await readFile(join(out,p),'utf8'));
async function run(script,...args){const command=[process.execPath,'ming-compose/tests/text-contract/'+script,...args];commands.push(command);const code=await new Promise((resolve,reject)=>{const c=spawn(command[0],command.slice(1),{cwd:root,env:process.env,windowsHide:true,stdio:'inherit'});c.on('error',reject);c.on('close',resolve)});assert.equal(code,0,command.join(' '));}
try {
 for(const kind of ['text','renderer-contract','contract','framed-driver','framed-runtime','framed-app','sample']) {
  const stage=prefix+'-'+kind;r.stages.push({kind,stage});let reuse=false;
  if(process.argv.includes('--resume')) {try {const prior=await json(stage+'/build-receipt.json');assert.equal(prior.status,'passed');for(const [p,h] of Object.entries(prior.inputSha256))if(p.startsWith('ming-compose/src/')&&p.endsWith('.kt'))assert.equal(hash(await readFile(join(root,p))),h);reuse=true;}catch(e){if(e.code!=='ENOENT')throw e;}}
  if(!reuse)await run('build.mjs',stage,kind);else commands.push(['reuse-verified-build',stage]);
  if(kind==='text') {await run('contract.mjs',stage);r.text=await json(stage+'/contract-result.json');}
  else if(kind!=='sample')await run('regression.mjs',stage,kind);
 }
 const before=await json('legacy-before/build-receipt.json'),after=await json(prefix+'-renderer-contract/build-receipt.json');
 const signature=r=>r.package.imports.map(({module,name,params,results})=>({module,name,params,results})).sort((a,b)=>(a.module+'.'+a.name).localeCompare(b.module+'.'+b.name));
 assert.deepEqual(signature(before),signature(after));r.legacyImports=signature(after);
 const sample=await json(prefix+'-sample/build-receipt.json');assert.ok(sample.package.imports.some(i=>i.module==='ming_input_v1'));
 const sdk=JSON.parse(await readFile(join(root,'artifacts/input-sdk/frozen-sdk.json'),'utf8'));for(const [p,h]of Object.entries(sdk.files))assert.equal(hash(await readFile(join(root,p))),h,'Frozen SDK changed');r.frozenSdkUnchanged=sdk.files;
 const owned=['Node.kt','Layout.kt','Renderer.kt','FramedApplication.kt','Application.kt','TextField.kt','TextApplication.kt'].map(n=>'ming-compose/src/wasmWasiMain/kotlin/ming/compose/'+n).concat(['settings.gradle.kts','scripts/build-kotlin.mjs','samples/text-entry-demo/build.gradle.kts','samples/text-entry-demo/app.json','samples/text-entry-demo/src/wasmWasiMain/kotlin/Main.kt']);
 r.productionSha256=Object.fromEntries(await Promise.all(owned.map(async p=>[p,hash(await readFile(join(root,p)))])));
 const compiled=await json(prefix+'-text/build-receipt.json');for(const [p,h] of Object.entries(r.productionSha256))if(p.startsWith('ming-compose/src/'))assert.equal(compiled.inputSha256[p],h);
 r.status='passed';
}catch(e){r.status='failed';r.error=e.stack;process.exitCode=1;}
finally{r.finishedAt=new Date().toISOString();await writeFile(join(out,prefix+'-verification.json'),JSON.stringify(r,null,2)+'\n');console.log(JSON.stringify({status:r.status,stages:r.stages,error:r.error,productionSha256:r.productionSha256}));}
