// Frozen S3 observer/replayer. No prepare/build/compiler dispatch exists here.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {constants} from 'node:fs';
import {open,readFile,readdir,lstat,realpath,statfs} from 'node:fs/promises';
import {join,dirname,resolve} from 'node:path';

const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
const mode=process.argv[2], requestPath=process.argv[3];
assert.ok(['observe','inspect-output','replay-copy'].includes(mode));
assert.equal(requestPath,'/request/request.json');
const requestRaw=await readFile(requestPath);assert.ok(requestRaw.length<=32*1024*1024);
const request=JSON.parse(requestRaw);
const stamp=s=>[s.dev,s.ino,s.mode,s.nlink,s.uid,s.gid,s.size,s.mtimeNs,s.ctimeNs].map(String);
function relative(name){
 assert.ok(typeof name==='string'&&name&&!name.startsWith('/')&&!/[\\:\x00-\x1f\x7f]/.test(name));
 for(const part of name.split('/'))assert.ok(part&&part!=='.'&&part!=='..'&&!/[ .]$/.test(part)&&!/^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(part));
}
async function noLinks(path){
 assert.equal(path,resolve(path));assert.ok(path.startsWith('/'));
 let current='/';for(const part of path.split('/').filter(Boolean)){relative(part);current=join(current,part);assert.ok(!(await lstat(current)).isSymbolicLink());}
}
// Node does not expose statvfs f_flags. This is explicitly fdinfo/mountinfo
// evidence. A separate Python peer supplies the actual statvfs verdict.
const mountinfo=await readFile('/proc/self/mountinfo','utf8');
const mountRows=new Map(mountinfo.trim().split('\n').map(line=>{const fields=line.split(' ');return[fields[0],{line,options:fields[5].split(',')}]}));
async function roFd(fd){
 const info=await readFile('/proc/self/fdinfo/'+fd,'utf8');const id=/^mnt_id:\s*(\d+)$/m.exec(info)?.[1];
 assert.ok(id&&mountRows.get(id)?.options.includes('ro'),'actual opened descriptor is not mounted RO');
}
async function pin(path,{readonly=true}={}){
 await noLinks(path);const before=await lstat(path,{bigint:true});assert.ok(before.isFile()&&before.nlink===1n);
 const fd=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);try{
  assert.deepEqual(stamp(await fd.stat({bigint:true})),stamp(before));if(readonly)await roFd(fd.fd);
  const digest=createHash('sha256'),buffer=Buffer.alloc(1024*1024);let bytes=0;
  for(;;){const {bytesRead}=await fd.read(buffer,0,buffer.length,null);if(!bytesRead)break;bytes+=bytesRead;assert.ok(BigInt(bytes)<=before.size);digest.update(buffer.subarray(0,bytesRead));}
  assert.equal(BigInt(bytes),before.size);assert.deepEqual(stamp(await fd.stat({bigint:true})),stamp(before));assert.deepEqual(stamp(await lstat(path,{bigint:true})),stamp(before));
  return{bytes,sha256:digest.digest('hex'),mode:Number(before.mode&0o7777n)};
 }finally{await fd.close()}
}
async function json(path){await noLinks(path);const st=await lstat(path);assert.ok(st.isFile()&&st.nlink===1&&st.size<=32*1024*1024);return JSON.parse(await readFile(path,'utf8'));}
async function inventory(root){
 await noLinks(root);const files={},directories={};const rootInfo=await lstat(root,{bigint:true});assert.ok(rootInfo.isDirectory());
 async function walk(path,prefix){
  const before=await lstat(path,{bigint:true});const fd=await open(path,constants.O_RDONLY|constants.O_DIRECTORY|constants.O_NOFOLLOW);try{await roFd(fd.fd)}finally{await fd.close()}
  const names=(await readdir(path)).sort(),seen=new Set();
  for(const name of names){relative(name);assert.ok(!seen.has(name.toLowerCase()));seen.add(name.toLowerCase());const child=join(path,name),entry=prefix?prefix+'/'+name:name,info=await lstat(child);
   assert.ok(!info.isSymbolicLink());if(info.isDirectory()){directories[entry]={mode:info.mode&0o7777};await walk(child,entry)}else{assert.ok(info.isFile()&&info.nlink===1);files[entry]=await pin(child)}
  }
  assert.deepEqual((await readdir(path)).sort(),names);assert.deepEqual(stamp(await lstat(path,{bigint:true})),stamp(before));
 }
 await walk(root,'');return{rootMode:Number(rootInfo.mode&0o7777n),directories,files};
}
const snapshot=tree=>({files:Object.fromEntries(Object.entries(tree.files).map(([name,p])=>[name,p.sha256])),directories:['',...Object.keys(tree.directories)]});
function sameSnapshot(a,b){assert.deepEqual(a.files,b.files);assert.deepEqual([...a.directories].sort(),[...b.directories].sort())}
async function effective(){
 const roots=['/',...request.readonly],readOnly={};for(const path of roots){const fd=await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);try{await roFd(fd.fd);readOnly[path]=true}finally{await fd.close()}}
 const cgroup=async n=>(await readFile('/sys/fs/cgroup/'+n,'utf8')).trim();
 const value={uid:process.getuid(),gid:process.getgid(),interfaces:(await readdir('/sys/class/net')).sort(),status:await readFile('/proc/self/status','utf8'),mountinfo,
  memoryMax:await cgroup('memory.max'),pidsMax:await cgroup('pids.max'),cpuMax:await cgroup('cpu.max'),readOnly,readOnlyEvidenceKind:'opened-fd-mountinfo-not-statvfs'};
 assert.equal(value.uid,65534);assert.equal(value.gid,65534);assert.deepEqual(value.interfaces,['lo']);
 for(const cap of ['CapEff','CapPrm'])assert.match(value.status,new RegExp('^'+cap+':\\s*0+$','m'));
 assert.match(value.status,/^NoNewPrivs:\s*1$/m);assert.match(value.status,/^Seccomp:\s*2$/m);
 assert.equal(value.memoryMax,'4294967296');assert.equal(value.pidsMax,'256');const [q,p]=value.cpuMax.split(' ').map(Number);assert.equal(q,2*p);
 const tmp=await statfs('/tmp',{bigint:true});assert.equal(tmp.type,0x01021994n);assert.ok(tmp.blocks*tmp.bsize<=67108864n);
 const tmpRows=mountinfo.trim().split('\n').filter(line=>line.split(' ')[4]==='/tmp');assert.equal(tmpRows.length,1);
 const tmpOptions=tmpRows[0].split(' ')[5].split(',');for(const option of ['rw','nosuid','nodev'])assert.ok(tmpOptions.includes(option));
 return value;
}
async function executables(){
 const records={};for(const [name,config] of Object.entries(request.executables??{node:{},java:{}})){
  assert.ok(['node','java'].includes(name));const path=name==='node'?process.execPath:join(process.env.JAVA_HOME??assert.fail('JAVA_HOME required'),'bin/java');
  const resolvedPath=await realpath(path);const version=name==='node'?process.version:(()=>{const r=spawnSync(resolvedPath,['-version'],{encoding:'utf8',timeout:15000,maxBuffer:1024*1024});assert.equal(r.status,0);assert.equal(r.signal,null);return(r.stdout+r.stderr).trim()})();
  records[name]={path,resolvedPath,...await pin(resolvedPath),version};
  if(config.path)assert.equal(path,config.path);if(config.versionPattern)assert.match(version,new RegExp(config.versionPattern));
 }return records;
}
const eff=await effective();
let result;
if(mode==='observe'){
 const actual=await executables(),locks={};for(const [path,expected] of Object.entries(request.lockedFiles)){const observed=await pin(path);assert.equal(observed.sha256,expected.sha256);locks[path]=observed;}
 const optional={};for(const name of request.optional??[]){assert.match(name,/^[a-z0-9-]+$/);const probe=spawnSync('/bin/sh',['-c','command -v "$1"','observation',name],{encoding:'utf8',timeout:10000,maxBuffer:65536});optional[name]={available:probe.status===0,path:probe.status===0?probe.stdout.trim():null};}
 result={schemaVersion:1,image:request.image,executables:actual,lockedFiles:locks,optional,effective:eff,formalQualification:false,finalSystemAccepted:false};
}else{
 const meta=await json('/profile/generation.json');
 for(const key of ['schemaVersion','generation','nonce','profile','image','originalCacheVolume','runnerSha256','initSha256','baselineManifestSha256','replayContractsSha256','payloadSha256','formalQualification','finalSystemAccepted'])assert.equal(meta[key],request.meta[key]);
 assert.deepEqual(meta.volumes,request.meta.volumes);assert.equal(meta.formalQualification,false);assert.equal(meta.finalSystemAccepted,false);
 assert.deepEqual(meta.labels,request.meta.labels);
 for(const [name,key] of [['kotlin_delivery_pipeline.mjs','payloadSha256'],['kotlin_delivery_replay.mjs','replayContractsSha256'],['dependency-baseline.json','baselineManifestSha256']])assert.equal(hash(await readFile('/profile/'+name)),meta[key]);
 assert.equal(hash(await readFile('/harness/scripts/boot/kotlin_delivery_replay.mjs')),meta.replayContractsSha256);
 assert.equal(hash(await readFile('/repo/scripts/boot/build-linux-host-kotlin.mjs')),meta.runnerSha256);
 assert.equal(hash(await readFile('/repo/scripts/boot/linux-host-kotlin.init.gradle')),meta.initSha256);
 const privateSnapshot=await json('/snapshot/source-inventory.json'),copiedSnapshot=await json('/out/source-snapshot.json'),repoInventory=await inventory('/repo');
 sameSnapshot(privateSnapshot,request.mapping.snapshot);sameSnapshot(copiedSnapshot,request.mapping.snapshot);sameSnapshot(snapshot(repoInventory),request.mapping.snapshot);
 const cacheCopy=await json('/out/cache-copy.json'),baseline=await json('/profile/dependency-baseline.json');
 assert.equal(cacheCopy.status,'readonly-original-cache-copied-and-verified');assert.equal(cacheCopy.originalCacheVolume,meta.originalCacheVolume);assert.equal(cacheCopy.baselineManifestSha256,meta.baselineManifestSha256);
 assert.ok(Number.isSafeInteger(cacheCopy.capacityBytes)&&cacheCopy.capacityBytes>=8*1024**3);assert.deepEqual(cacheCopy.original.files,baseline.files);
 const originalBefore=await inventory('/original-cache');sameSnapshot(snapshot(originalBefore),cacheCopy.original);
 const outputInventory=await inventory('/out');
 if(mode==='inspect-output'){
  result={schemaVersion:1,status:'private-output-readonly-observed',inventory:outputInventory,sourcePosixInventory:repoInventory,
   sourceSnapshot:copiedSnapshot,privateSnapshot,privateRepoSnapshot:snapshot(repoInventory),cacheCopy,
   originalCacheVolume:meta.originalCacheVolume,baselineManifestSha256:meta.baselineManifestSha256,effective:eff};
 }else{
  assert.deepEqual(outputInventory,request.originalInventory,'complete copied directory/file/mode inventory differs');
  const observedTools=await executables();for(const name of ['node','java'])assert.deepEqual(observedTools[name],request.tools.executables[name],'actual same-image executable drift');
  for(const [path,expected] of Object.entries(request.tools.lockedFiles))assert.deepEqual(await pin(path),expected,'actual locked dependency drift');
  const {verifyReplayContracts}=await import('/harness/scripts/boot/kotlin_delivery_replay.mjs');
  const {verifyPackage,compareDependencies}=await import('/repo/scripts/boot/build-linux-host-kotlin.mjs');
  const results=await verifyReplayContracts({meta,readFile,verifyPackage,node:process.execPath});
  compareDependencies(cacheCopy.original.files,snapshot(await inventory('/deps/gradle')).files);
  const packageToolchains=[];for(const label of meta.labels){const toolchain=await json('/out/packages/'+label+'/evidence/toolchain.json');for(const name of ['node','java']){assert.equal(toolchain[name+'Path'],observedTools[name].path);assert.equal(toolchain[name+'Sha256'],observedTools[name].sha256)}
   assert.equal(toolchain.gradleDistributionSha256,request.tools.lockedFiles['/deps/gradle/mingos-verified-gradle-8.14.5-bin.zip'].sha256);packageToolchains.push({label,toolchain});}
  assert.deepEqual(await inventory('/out'),outputInventory);assert.deepEqual(await inventory('/original-cache'),originalBefore);
  result={schemaVersion:1,status:'profile-copy-readonly-replay-verified',inventory:request.originalInventory,copiedInventory:outputInventory,
   results,packageToolchains,profiles:[meta.profile],compileExecuted:false,sourcePosixInventory:repoInventory,effective:eff,formalQualification:false,finalSystemAccepted:false};
 }
}
console.log(JSON.stringify(result));
