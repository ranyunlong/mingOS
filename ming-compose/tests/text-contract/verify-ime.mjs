import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const prefix=process.argv[2]??'ime';assert.match(prefix,/^[a-z0-9-]+$/);
const root=fileURLToPath(new URL('../../../',import.meta.url)),out=join(root,'artifacts/text-field');const commands=[],hash=b=>createHash('sha256').update(b).digest('hex'),json=async p=>JSON.parse(await readFile(join(out,p),'utf8'));
const r={status:'running',commands,startedAt:new Date().toISOString(),changedProduction:['ming-compose/src/wasmWasiMain/kotlin/ming/compose/TextApplication.kt'],priorFullVerification:'frozen-verification.json'};
async function run(script,...args){const cmd=[process.execPath,'ming-compose/tests/text-contract/'+script,...args];commands.push(cmd);const code=await new Promise((resolve,reject)=>{const c=spawn(cmd[0],cmd.slice(1),{cwd:root,env:process.env,stdio:'inherit',windowsHide:true});c.on('error',reject);c.on('close',resolve)});assert.equal(code,0,cmd.join(' '));}
try {
 const prior=await json('frozen-verification.json');assert.equal(prior.status,'passed');
 for(const [p,h] of Object.entries(prior.productionSha256))if(!r.changedProduction.includes(p))assert.equal(hash(await readFile(join(root,p))),h,'Other production changed: '+p);
 for(const [kind,stage] of [['text',prefix+'-text'],['renderer-contract',prefix+'-renderer'],['sample',prefix+'-sample']]) {await run('build.mjs',stage,kind);if(kind==='text')await run('contract.mjs',stage);else if(kind==='renderer-contract')await run('regression.mjs',stage,kind);}
 for(const kind of ['contract','framed-driver','framed-runtime','framed-app'])await run('regression.mjs','frozen-'+kind,kind);
 const renderer=await json(prefix+'-renderer/build-receipt.json'),oldRenderer=await json('frozen-renderer-contract/build-receipt.json');
 assert.deepEqual(renderer.package.imports,oldRenderer.package.imports);assert.equal(renderer.package.rawSha256,oldRenderer.package.rawSha256,'Coordinator change reached legacy renderer bytecode');r.legacyRendererByteIdentical=true;
 r.productionSha256=Object.fromEntries(await Promise.all(Object.keys(prior.productionSha256).map(async p=>[p,hash(await readFile(join(root,p)))])));
 const text=await json(prefix+'-text/build-receipt.json');for(const [p,h]of Object.entries(r.productionSha256))if(p.startsWith('ming-compose/src/'))assert.equal(text.inputSha256[p],h);
 for(const [p,h]of Object.entries(prior.frozenSdkUnchanged))assert.equal(hash(await readFile(join(root,p))),h);r.frozenSdkUnchanged=prior.frozenSdkUnchanged;
 r.text=await json(prefix+'-text/contract-result.json');r.status='passed';
}catch(e){r.status='failed';r.error=e.stack;process.exitCode=1;}
finally{r.finishedAt=new Date().toISOString();await writeFile(join(out,prefix+'-verification.json'),JSON.stringify(r,null,2)+'\n');console.log(JSON.stringify({status:r.status,error:r.error,legacyRendererByteIdentical:r.legacyRendererByteIdentical,productionSha256:r.productionSha256}));}
