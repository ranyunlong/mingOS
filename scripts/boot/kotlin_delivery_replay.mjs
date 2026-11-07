import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
const sha=x=>createHash('sha256').update(x).digest('hex');
export const profiles={developer:['text-entry-demo','animated-demo'],'developer-workflow':['notes-demo','animated-demo'],uninstall:['read-file','image-viewer','text-entry-demo','uninstall'],update:['read-file','image-viewer','text-entry-demo','old','new','absent-write']};
export function identity(label){return ['old','new','absent-write'].includes(label)?{sample:'persistent-update-demo',fixture:label}:{sample:label==='uninstall'?'persistent-uninstall-demo':label,fixture:null}}
export function commandFor(label,node){const {sample,fixture}=identity(label);return[node,'/repo/scripts/boot/build-linux-host-kotlin.mjs',sample,...(fixture?[fixture]:[]),'--repo','/repo','--work-root','/work/'+label,'--output-root','/out/packages/'+label,'--gradle-home','/deps/gradle','--dependency-manifest','/out/chain/'+label+'.dependency-manifest.json']}
export async function verifyReplayContracts({meta,readFile,verifyPackage,node}){
 assert.ok(Object.hasOwn(profiles,meta.profile),'unknown replay profile');assert.deepEqual(meta.labels,profiles[meta.profile],'profile/label set differs');
 const readJson=async path=>JSON.parse(await readFile(path));
 const original=(await readJson('/out/cache-copy.json')).original;
 let previous=null,previousPost=original;const results=[];
 for(const label of meta.labels){
  const expected=identity(label),base='/out/packages/'+label;
  const result=await verifyPackage({repo:'/repo',directory:base});
  assert.equal(result.sample,expected.sample,'replayed package sample differs for '+label);assert.equal(result.fixture,expected.fixture,'replayed package fixture differs for '+label);
  const receiptRaw=await readFile(base+'/build-receipt.json'),receipt=JSON.parse(receiptRaw);
  assert.equal(receipt.sample,expected.sample);assert.equal(receipt.fixture,expected.fixture);
  const chainPath='/out/chain/'+label+'.receipt.json',chainRaw=await readFile(chainPath),chain=JSON.parse(chainRaw);
  for(const [key,want] of Object.entries({schemaVersion:1,generation:meta.generation,label,...expected,status:'passed',exitCode:0,signal:null,runnerSha256:meta.runnerSha256,initSha256:meta.initSha256,baselineManifestSha256:meta.baselineManifestSha256,buildReceiptSha256:sha(receiptRaw)}))assert.equal(chain[key],want,'chain binding differs: '+key);
  assert.deepEqual(chain.previous,previous,'predecessor chain differs');assert.deepEqual(chain.command,commandFor(label,node),'compiler command/identity differs');
  const dependencyPath='/out/chain/'+label+'.dependency-manifest.json',dependencyRaw=await readFile(dependencyPath),dependency=JSON.parse(dependencyRaw);
  assert.equal(chain.preManifestSha256,sha(dependencyRaw),'pre dependency manifest hash differs');assert.equal(dependency.schemaVersion,1);
  const before=await readJson('/out/chain/'+label+'.pre-inventory.json'),after=await readJson('/out/chain/'+label+'.post-inventory.json');
  assert.deepEqual(before,previousPost,'previous post/current pre cache inventory differs');assert.deepEqual(before.files,dependency.files,'pre inventory/manifest differs');
  assert.deepEqual((await readJson(base+'/evidence/dependencies-before.json')).files,before.files,'package dependency-before differs');
  assert.deepEqual((await readJson(base+'/evidence/dependencies-after.json')).files,after.files,'package dependency-after differs');
  previous={label,sha256:sha(chainRaw)};previousPost=after;results.push(result);
 }
 return results;
}
