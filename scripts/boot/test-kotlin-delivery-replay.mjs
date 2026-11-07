import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {verifyReplayContracts} from './kotlin_delivery_replay.mjs';
const sha=x=>createHash('sha256').update(x).digest('hex');
function fixture(profile='uninstall'){
 const labels=profile==='developer'?['text-entry-demo','animated-demo']:profile==='uninstall'?['read-file','image-viewer','text-entry-demo','uninstall']:['read-file','image-viewer','text-entry-demo','old','new','absent-write'];
 const meta={generation:'fresh-generation',profile,labels,runnerSha256:'r'.repeat(64),initSha256:'i'.repeat(64),baselineManifestSha256:'b'.repeat(64)};
 const tree={files:{'cache.jar':'a'.repeat(64)},directories:['']};const files=new Map();
 const put=(p,v)=>files.set(p,Buffer.from(JSON.stringify(v)));
 put('/out/cache-copy.json',{original:tree});let previous=null;
 for(const label of labels){
  const fixture=['old','new','absent-write'].includes(label)?label:null,sample=fixture?'persistent-update-demo':label==='uninstall'?'persistent-uninstall-demo':label;
  const base='/out/packages/'+label;
  const receipt={sample,fixture,status:'passed'};put(base+'/build-receipt.json',receipt);
  put(base+'/evidence/dependencies-before.json',tree);put(base+'/evidence/dependencies-after.json',tree);
  const dependencyPath='/out/chain/'+label+'.dependency-manifest.json';put(dependencyPath,{schemaVersion:1,files:tree.files});
  put('/out/chain/'+label+'.pre-inventory.json',tree);put('/out/chain/'+label+'.post-inventory.json',tree);
  const command=['/node','/repo/scripts/boot/build-linux-host-kotlin.mjs',sample,...(fixture?[fixture]:[]),'--repo','/repo','--work-root','/work/'+label,'--output-root',base,'--gradle-home','/deps/gradle','--dependency-manifest',dependencyPath];
  const chain={schemaVersion:1,generation:meta.generation,label,sample,fixture,command,status:'passed',exitCode:0,signal:null,previous,buildReceiptSha256:sha(files.get(base+'/build-receipt.json')),preManifestSha256:sha(files.get(dependencyPath)),runnerSha256:meta.runnerSha256,initSha256:meta.initSha256,baselineManifestSha256:meta.baselineManifestSha256};
  const path='/out/chain/'+label+'.receipt.json';put(path,chain);previous={label,sha256:sha(files.get(path))};
 }
 const readFile=async p=>{assert.ok(files.has(p),'missing fixture '+p);return files.get(p)};
 const verifyPackage=async({directory})=>JSON.parse(await readFile(directory+'/build-receipt.json'));
 const run=()=>verifyReplayContracts({meta,readFile,verifyPackage,node:'/node'});
 const mutate=(path,fn)=>{const v=JSON.parse(files.get(path));fn(v);put(path,v)};
 return{files,meta,run,mutate,put};
}
test('valid independently replayed package identities and chains',async()=>{const f=fixture();assert.equal((await f.run()).length,4)});
test('read-file package copied into every label rejected',async()=>{const f=fixture();for(const label of f.meta.labels){f.put('/out/packages/'+label+'/build-receipt.json',{sample:'read-file',fixture:null,status:'passed'})}await assert.rejects(f.run())});
for(const field of ['generation','label','sample','fixture','previous','buildReceiptSha256','preManifestSha256','runnerSha256','initSha256','baselineManifestSha256','command']){
 test('chain '+field+' tampering rejected',async()=>{const f=fixture();f.mutate('/out/chain/read-file.receipt.json',v=>{v[field]=field==='previous'?{label:'foreign',sha256:'0'.repeat(64)}:'foreign'});await assert.rejects(f.run())});
}
test('pre/post dependency chain mutation rejected',async()=>{const f=fixture();f.mutate('/out/chain/image-viewer.pre-inventory.json',v=>{v.files.extra='0'.repeat(64)});await assert.rejects(f.run())});
test('wrong profile labels rejected',async()=>{const f=fixture();f.meta.profile='update';await assert.rejects(f.run())});

test('valid update six-package identity chain',async()=>{const f=fixture('update');assert.equal((await f.run()).length,6)});
test('old update package cannot satisfy new update fixture',async()=>{const f=fixture('update');f.put('/out/packages/new/build-receipt.json',{sample:'persistent-update-demo',fixture:'old',status:'passed'});await assert.rejects(f.run())});

test('valid developer two-package identity chain',async()=>{const f=fixture('developer');assert.equal((await f.run()).length,2)});
