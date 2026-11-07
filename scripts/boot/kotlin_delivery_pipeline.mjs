// Linux delivery payload. Actual compilation is delegated to the tracked runner.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {constants} from 'node:fs';
import {readFile,writeFile,readdir,lstat,mkdir,copyFile,chmod,open,statfs} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
const sha=b=>createHash('sha256').update(b).digest('hex');
const json=async(p,v)=>writeFile(p,JSON.stringify(v,null,2)+'\n',{flag:'wx'});
const meta=JSON.parse(await readFile('/harness/generation.json'));
assert.equal(sha(await readFile(fileURLToPath(import.meta.url))),meta.payloadSha256);
assert.equal(sha(await readFile('/harness/kotlin_delivery_replay.mjs')),meta.replayContractsSha256);
const {verifyReplayContracts}=await import('/harness/kotlin_delivery_replay.mjs');
const mode=process.argv[2],label=process.argv[3],repo=mode==='prepare'?'/checkout':'/repo';
const {snapshotTree,compareDependencies,verifyPackage}=await import(repo+'/scripts/boot/build-linux-host-kotlin.mjs');
assert.equal(sha(await readFile(repo+'/scripts/boot/build-linux-host-kotlin.mjs')),meta.runnerSha256);
assert.equal(sha(await readFile(repo+'/scripts/boot/linux-host-kotlin.init.gradle')),meta.initSha256);
assert.equal(process.platform,'linux');assert.equal(process.getuid(),65534);assert.equal(process.getgid(),65534);assert.equal(process.versions.node,'24.22.1');
assert.deepEqual((await readdir('/sys/class/net')).sort(),['lo']);
const status=await readFile('/proc/self/status','utf8');assert.match(status,/^CapEff:\s*0+$/m);assert.match(status,/^CapPrm:\s*0+$/m);assert.match(status,/^NoNewPrivs:\s*1$/m);assert.match(status,/^Seccomp:\s*2$/m);
const mounts=(await readFile('/proc/mounts','utf8')).trim().split('\n').map(line=>{const p=line.split(' ');return{path:p[1],options:p[3].split(',')}});
const ro=path=>assert.ok(mounts.some(m=>m.path===path&&m.options.includes('ro')),'read-only mount required: '+path);
for(const path of ['/','/harness','/original-cache',repo])ro(path);
const cgroup=async n=>(await readFile('/sys/fs/cgroup/'+n,'utf8')).trim();
assert.equal(await cgroup('memory.max'),'4294967296');assert.equal(await cgroup('pids.max'),'256');
const [quota,period]=(await cgroup('cpu.max')).split(' ').map(Number);assert.equal(quota/period,2);
const rawBaseline=await readFile('/harness/dependency-baseline.json');assert.equal(sha(rawBaseline),meta.baselineManifestSha256);const baseline=JSON.parse(rawBaseline.toString('utf8').replace(/^\uFEFF/,''));
const excluded=new Set(['.git','.cache','.gradle','.kotlin','.codegraph','.superpowers','artifacts','node_modules','target','build','builds','__pycache__','dist']);
async function sourceTree(root){
 const files={},directories=[];
 async function walk(p,name){const info=await lstat(p);assert.ok(info.isDirectory()&&!info.isSymbolicLink());directories.push(name);for(const entry of (await readdir(p,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){if(excluded.has(entry.name))continue;const child=join(p,entry.name),relative=name?name+'/'+entry.name:entry.name;if(entry.isDirectory())await walk(child,relative);else{const st=await lstat(child);assert.ok(st.isFile()&&!st.isSymbolicLink()&&st.nlink===1,'regular source required');files[relative]=sha(await readFile(child))}}}
 await walk(root,'');return{files,directories};
}
async function copyTree(source,target,tree){
 for(const name of tree.directories){if(name)await mkdir(join(target,name),{mode:0o700})}
 for(const name of Object.keys(tree.files)){const from=join(source,name),to=join(target,name),st=await lstat(from);assert.ok(st.isFile()&&!st.isSymbolicLink()&&st.nlink===1);await copyFile(from,to,constants.COPYFILE_EXCL);await chmod(to,st.mode&0o777);const fd=await open(to,'r');try{await fd.sync()}finally{await fd.close()}}
 // All source/cache directories remain traversable for the current UID; original executable file modes survive.
 for(const name of [...tree.directories].reverse()){const fd=await open(join(target,name),'r');try{await fd.sync()}finally{await fd.close()}}
}
const inventory=path=>snapshotTree(path,{excludeGenerated:false});
let expectedSource;
if(mode==='prepare'){
 for(const p of ['/source','/deps/gradle','/work','/out']){assert.deepEqual(await readdir(p),[],'literal empty generation root required');const st=await lstat(p);assert.equal(st.uid,65534);assert.equal(st.gid,65534);assert.equal(st.mode&0o777,0o700)}
 const capacity=await statfs('/out');assert.ok(capacity.bavail*capacity.bsize>=8*1024**3,'8GiB minimum Linux backing storage observation required');
 const source=await sourceTree('/checkout');await mkdir('/source/repo',{mode:0o700});await copyTree('/checkout','/source/repo',source);
 assert.deepEqual(await sourceTree('/checkout'),source,'checkout changed during snapshot');assert.deepEqual(await inventory('/source/repo'),source,'source copy differs');
 await json('/source/source-inventory.json',source);
 const before=await inventory('/original-cache');assert.ok(Object.keys(before.files).length,'original cache empty');
 // The original is sealed exactly; builds receive only the verified private copy.
 assert.deepEqual(before.files,baseline.files,'original cache differs from sealed baseline');
 await copyTree('/original-cache','/deps/gradle',before);const copied=await inventory('/deps/gradle');assert.deepEqual(copied,before);
 assert.deepEqual(await inventory('/original-cache'),before,'original cache changed during copy');
 await json('/out/cache-copy.json',{schemaVersion:1,status:'readonly-original-cache-copied-and-verified',original:before,originalCacheVolume:meta.originalCacheVolume,capacityBytes:capacity.bavail*capacity.bsize,baselineManifestSha256:sha(rawBaseline),formalQualification:false,finalSystemAccepted:false});
 await json('/out/source-snapshot.json',source);console.log('PREPARE_RESULT '+JSON.stringify({status:'passed',sourceFiles:Object.keys(source.files).length,cacheFiles:Object.keys(before.files).length,originalCacheUnchanged:true}));
}else{
 ro('/snapshot');expectedSource=JSON.parse(await readFile('/snapshot/source-inventory.json'));assert.deepEqual(await inventory('/repo'),expectedSource,'frozen source differs');
 const copy=JSON.parse(await readFile('/out/cache-copy.json'));assert.deepEqual(await inventory('/original-cache'),copy.original,'original cache changed');
 assert.deepEqual(copy.original.files,baseline.files,'copy baseline differs');
 if(mode==='input-readonly-control'){
  for(const p of ['/repo','/deps/gradle','/original-cache','/out']){ro(p);await assert.rejects(writeFile(p+'/.synthetic-readonly-write',Buffer.from('must fail'),{flag:'wx'}),e=>['EROFS','EACCES','EPERM'].includes(e.code),'readonly write unexpectedly succeeded: '+p)}
  assert.deepEqual(await inventory('/deps/gradle'),copy.original,'derived cache changed during readonly control');
  console.log('SYNTHETIC_CONTROL '+JSON.stringify({status:'readonly-input-control-passed',compileExecuted:false,originalCacheUnchanged:true,sourceFiles:Object.keys(expectedSource.files).length,cacheFiles:Object.keys(copy.original.files).length}));
 }else if(mode==='verify-all'){
  ro('/out');ro('/deps/gradle');const results=await verifyReplayContracts({meta,readFile,verifyPackage,node:process.execPath});
  const comparison=compareDependencies(copy.original.files,(await inventory('/deps/gradle')).files);
  assert.deepEqual(await inventory('/original-cache'),copy.original,'original cache changed after replay');
  console.log('DELIVERY_RESULT '+JSON.stringify({schemaVersion:1,status:'independent-readonly-replay-verified',generation:meta.generation,profile:meta.profile,results,originalCacheUnchanged:true,dependencyComparison:comparison,sourceInventorySha256:sha(await readFile('/snapshot/source-inventory.json')),formalQualification:false,finalSystemAccepted:false}));
 }else{
  assert.equal(mode,'build');const index=meta.labels.indexOf(label);assert.ok(index>=0);
  await mkdir('/out/chain',{recursive:true});await mkdir('/out/packages',{recursive:true});
  const pre=await inventory('/deps/gradle');compareDependencies(baseline.files,pre.files);let previous=null;
  if(index===0)assert.deepEqual(pre.files,copy.original.files);else{const previousRaw=await readFile('/out/chain/'+meta.labels[index-1]+'.receipt.json');const old=JSON.parse(previousRaw);assert.equal(old.status,'passed');assert.deepEqual(pre,JSON.parse(await readFile('/out/chain/'+meta.labels[index-1]+'.post-inventory.json')));previous={label:meta.labels[index-1],sha256:sha(previousRaw)}}
  const dependencyPath='/out/chain/'+label+'.dependency-manifest.json';await json(dependencyPath,{schemaVersion:1,files:pre.files});await json('/out/chain/'+label+'.pre-inventory.json',pre);
  const fixture=['old','new','absent-write'].includes(label)?label:null,sample=fixture?'persistent-update-demo':label==='uninstall'?'persistent-uninstall-demo':label;
  const args=['/repo/scripts/boot/build-linux-host-kotlin.mjs',sample,...(fixture?[fixture]:[]),'--repo','/repo','--work-root','/work/'+label,'--output-root','/out/packages/'+label,'--gradle-home','/deps/gradle','--dependency-manifest',dependencyPath];
  // Tracked runner enforces --offline/--rerun-tasks/--no-build-cache and target task execution.
  const startedAt=new Date().toISOString();console.log('RUNNER_COMMAND '+JSON.stringify([process.execPath,...args]));
  const result=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,args,{stdio:'inherit',detached:true});const kill=()=>{try{process.kill(-child.pid,'SIGKILL')}catch(e){if(e.code!=='ESRCH')throw e}};const timer=setTimeout(kill,1800000);timer.unref();child.once('error',e=>{clearTimeout(timer);reject(e)});child.once('close',(code,signal)=>{clearTimeout(timer);kill();resolve({exitCode:code,signal})})});
  const after=await inventory('/deps/gradle');await json('/out/chain/'+label+'.post-inventory.json',after);compareDependencies(pre.files,after.files);
  const buildReceipt=await readFile('/out/packages/'+label+'/build-receipt.json').catch(()=>null);
  await json('/out/chain/'+label+'.receipt.json',{schemaVersion:1,generation:meta.generation,label,sample,fixture,startedAt,finishedAt:new Date().toISOString(),command:[process.execPath,...args],...result,status:result.exitCode===0&&!result.signal?'passed':'failed',previous,buildReceiptSha256:buildReceipt?sha(buildReceipt):null,preManifestSha256:sha(await readFile(dependencyPath)),runnerSha256:meta.runnerSha256,initSha256:meta.initSha256,baselineManifestSha256:meta.baselineManifestSha256,formalQualification:false,finalSystemAccepted:false});
  assert.equal(result.exitCode,0,'compiler failed');assert.equal(result.signal,null,'compiler terminated');
 }
 assert.deepEqual(await inventory('/repo'),expectedSource,'frozen source changed');assert.deepEqual(await inventory('/original-cache'),copy.original,'original cache changed after stage');
}
