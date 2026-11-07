// Contract tests use synthetic Wasm only; they are not Kotlin compiler evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {mkdtemp, mkdir, writeFile, rm, symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {parseOptions, gradleArguments, requireExecutedTask, requireCompilerSuccess, validatePackage, verifyPackage, assertExternalPaths, snapshotTree, assertUnchanged, assertIsolation, compareDependencies} from './build-linux-host-kotlin.mjs';

const paths={repo:'/repo',workRoot:'/work/one',outputRoot:'/output/one',gradleHome:'/deps/gradle',dependencyManifest:'/deps/manifest.json'};
test('only supported sample/fixture combinations are accepted',()=>{
  for(const sample of ['read-file','image-viewer','text-entry-demo'])assert.equal(parseOptions([sample]).sample,sample);
  for(const fixture of ['old','new','absent-write'])assert.equal(parseOptions(['persistent-update-demo',fixture]).fixture,fixture);
  for(const args of [['all'],['persistent-update-demo'],['persistent-update-demo','other'],['read-file','old'],['read-file','--unknown','x']])assert.throws(()=>parseOptions(args));
});
test('actual wrapper command forces offline execution and external caches',()=>{
  const args=gradleArguments({...paths,sample:'persistent-update-demo',fixture:'new'});
  for(const flag of ['--offline','--rerun-tasks','--no-build-cache','--no-configuration-cache','--project-cache-dir','--init-script',':persistent-update-demo:compileProductionExecutableKotlinWasmWasi'])assert.ok(args.includes(flag));
  assert.ok(args.includes('-PpersistentUpdateFixture=new'));
  assert.ok(args.includes('-Pkotlin.incremental=false'));
  assert.ok(args.includes('-Pkotlin.project.persistent.dir='+join('/work/one','kotlin-persistent')));
});
test('uninstall selects its own offline source task without update fixture arguments',()=>{
  const options=parseOptions(['persistent-uninstall-demo']);
  assert.equal(options.sample,'persistent-uninstall-demo');assert.equal(options.fixture,null);
  const args=gradleArguments({...paths,...options});
  assert.ok(args.includes(':persistent-uninstall-demo:compileProductionExecutableKotlinWasmWasi'));
  for(const flag of ['--offline','--rerun-tasks','--no-build-cache','--no-configuration-cache'])assert.ok(args.includes(flag));
  assert.ok(!args.some(arg=>arg.startsWith('-PpersistentUpdateFixture=')));
  assert.ok(!args.includes(':persistent-update-demo:compileProductionExecutableKotlinWasmWasi'));
  for(const fixture of ['old','new','absent-write'])assert.throws(()=>parseOptions(['persistent-uninstall-demo',fixture]));
});
test('developer Motion selects its own forced offline SDK5 source task',()=>{
  const options=parseOptions(['animated-demo']);
  assert.equal(options.sample,'animated-demo');assert.equal(options.fixture,null);
  const args=gradleArguments({...paths,...options});
  assert.ok(args.includes(':animated-demo:compileProductionExecutableKotlinWasmWasi'));
  for(const flag of ['--offline','--rerun-tasks','--no-build-cache','--no-configuration-cache'])assert.ok(args.includes(flag));
  assert.ok(!args.some(arg=>arg.startsWith('-PpersistentUpdateFixture=')));
  for(const fixture of ['old','new','absent-write'])assert.throws(()=>parseOptions(['animated-demo',fixture]));
});
test('compiler failure and signals cannot reach packaging',()=>{
  requireCompilerSuccess({exitCode:0,signal:null});
  for(const result of [{exitCode:1,signal:null},{exitCode:null,signal:'SIGKILL'},{exitCode:0,signal:'SIGTERM'}])assert.throws(()=>requireCompilerSuccess(result));
});
test('dependency artifact additions, removals and changes fail while derived metadata is recorded',()=>{
  const key='caches/modules-2/files-2.1/group/artifact/one.jar';const before={[key]:'a','daemon/state.bin':'b'};
  const allowed=compareDependencies(before,{...before,'daemon/state.bin':'c','caches/8.14.5/transforms/derived.jar':'d'});
  assert.deepEqual(allowed.derivedChanges.sort(),['caches/8.14.5/transforms/derived.jar','daemon/state.bin']);
  for(const after of [{...before,[key]:'z'},{'daemon/state.bin':'b'},{...before,'caches/modules-2/files-2.1/new/new.klib':'e'}])assert.throws(()=>compareDependencies(before,after));
  assert.throws(()=>compareDependencies({'mingos-verified-gradle-8.14.5-bin.zip':'a'},{'mingos-verified-gradle-8.14.5-bin.zip':'b'}));
});
test('success must contain exactly one actually executed target task',()=>{
  const task=':read-file:compileProductionExecutableKotlinWasmWasi';
  requireExecutedTask('> Task '+task+'\nBUILD SUCCESSFUL',task);
  for(const log of ['', '> Task '+task+' UP-TO-DATE','> Task '+task+' FROM-CACHE','> Task '+task+' SKIPPED','> Task '+task+' NO-SOURCE','> Task '+task+'\n> Task '+task])assert.throws(()=>requireExecutedTask(log,task));
});
test('external roots cannot write into source or overlap each other',()=>{
  assertExternalPaths(paths);
  for(const key of ['workRoot','outputRoot','gradleHome'])assert.throws(()=>assertExternalPaths({...paths,[key]:'/repo/.cache'}));
  assert.throws(()=>assertExternalPaths({...paths,outputRoot:'/work/one/output'}));
});
const leb=n=>{const b=[];do{let x=n&127;n>>>=7;b.push(x|(n?128:0));}while(n);return Buffer.from(b)};
const text=s=>Buffer.concat([leb(Buffer.byteLength(s)),Buffer.from(s)]);
function wasm(imports,{entry=true,entryArity=0}={}){
  const types=imports.map(([, ,arity])=>Buffer.from([0x60,arity,...Array(arity).fill(0x7f),1,0x7f]));
  types.push(Buffer.from([0x60,entryArity,...Array(entryArity).fill(0x7f),0]));
  const typeSection=Buffer.concat([leb(types.length),...types]);
  const impSection=Buffer.concat([leb(imports.length),...imports.map(([m,n],i)=>Buffer.concat([text(m),text(n),Buffer.from([0]),leb(i)]))]);
  const section=(id,payload)=>Buffer.concat([Buffer.from([id]),leb(payload.length),payload]);
  return Buffer.concat([Buffer.from([0,97,115,109,1,0,0,0]),section(1,typeSection),section(2,impSection),
    ...(entry?[section(3,Buffer.concat([leb(1),leb(imports.length)])),section(5,Buffer.from([1,0,1])),section(7,Buffer.concat([leb(2),text('_start'),Buffer.from([0]),leb(imports.length),text('memory'),Buffer.from([2,0])])),section(10,Buffer.from([1,2,0,11]))]:[])]);
}
const files=[['ming_v1','file_open',1],['ming_v1','file_length',1],['ming_v1','file_read_byte',2],['ming_v1','file_close',1],['ming_v1','log',2]];
const readManifest={schemaVersion:1,id:'dev.ming.read-file',version:'0.1.0',sdkVersion:1,entry:'main.wasm'};
test('native Wasm reflection and typed SDK imports accept a synthetic SDK1 module',()=>{
  const result=validatePackage(wasm(files),'read-file',null,readManifest);
  assert.equal(result.imports.length,5);assert.deepEqual(result.rewrites,[]);
});
test('malformed Wasm, unexpected capabilities, missing capabilities and bad ABI fail',()=>{
  for(const bytes of [Buffer.from('not wasm'),wasm([...files,['evil','execute',1]]),wasm(files.slice(1)),wasm(files.map((x,i)=>i?[...x]:['ming_v1','file_open',2]))])assert.throws(()=>validatePackage(bytes,'read-file',null,readManifest));
  assert.throws(()=>validatePackage(wasm(files),'read-file',null,{...readManifest,sdkVersion:2}));
  assert.throws(()=>validatePackage(wasm(files),'read-file',null,{...readManifest,id:'dev.ming.image-viewer'}));
  assert.throws(()=>validatePackage(wasm(files,{entry:false}),'read-file',null,readManifest));
  assert.throws(()=>validatePackage(wasm(files,{entryArity:1}),'read-file',null,readManifest));
});
test('read-file cannot silently receive a runtime adapter rewrite',()=>{
  assert.throws(()=>validatePackage(wasm([...files,['wasi_snapshot_preview1','random_get',2]]),'read-file',null,readManifest));
});
test('viewer, bootstrap and update use real adapter rewrite plus distinct capability requirements',()=>{
  const random=['wasi_snapshot_preview1','random_get',2];
  const graphics=[['ming_v2','file_read',4],['ming_v2','image_open',1],['ming_v2','image_width',1],['ming_v2','image_height',1],['ming_v2','image_close',1],['ming_v2','frame_submit',2],['ming_v2','event_next',2]];
  assert.equal(validatePackage(wasm([...files,...graphics,random]),'image-viewer',null,{...readManifest,id:'dev.ming.image-viewer',sdkVersion:2}).rewrites.length,1);
  const textImports=[['ming_input_v1','poll',2],['ming_input_v1','text_focus',2],['ming_frame_v1','wait',4],['ming_v4','frame_submit',2],random];
  const manifest={...readManifest,id:'dev.ming.text-entry-demo',sdkVersion:6};
  validatePackage(wasm(textImports),'text-entry-demo',null,manifest);
  for(const fixture of ['old','new','absent-write']){
    const updateManifest={...manifest,id:'dev.ming.persistent-update-demo',version:fixture==='old'?'0.1.0':'0.2.0'};
    assert.throws(()=>validatePackage(wasm(textImports),'persistent-update-demo',fixture,updateManifest));
    validatePackage(wasm([...textImports,['ming_private_files_v2','call',4]]),'persistent-update-demo',fixture,updateManifest);
  }
});
const uninstallImports=[['ming_input_v1','poll',2],['ming_input_v1','text_focus',2],['ming_frame_v1','wait',4],['ming_v4','frame_submit',2],['wasi_snapshot_preview1','random_get',2],['ming_private_files_v2','call',4]];
test('developer Motion admits SDK5 frame clock and rejects SDK6 authority',()=>{
  const manifest={schemaVersion:1,id:'dev.ming.animated-demo',version:'0.1.0',sdkVersion:5,entry:'main.wasm'};
  const imports=[['ming_frame_v1','wait',4],['ming_frame_v1','clock_monotonic_ns',2],['ming_v4','frame_submit',2],['wasi_snapshot_preview1','random_get',2]];
  assert.equal(validatePackage(wasm(imports),'animated-demo',null,manifest).rewrites.length,1);
  for(const capability of [['ming_input_v1','poll',2],['ming_input_v1','text_focus',2],['ming_private_files_v2','call',4]])
    assert.throws(()=>validatePackage(wasm([...imports,capability]),'animated-demo',null,manifest));
  for(const sdkVersion of [2,6])assert.throws(()=>validatePackage(wasm(imports),'animated-demo',null,{...manifest,sdkVersion}));
  for(const missing of ['wait','clock_monotonic_ns','frame_submit'])assert.throws(()=>validatePackage(wasm(imports.filter(row=>row[1]!==missing)),'animated-demo',null,manifest));
  assert.throws(()=>validatePackage(wasm(imports),'animated-demo','new',manifest));
});
const uninstallManifest={schemaVersion:1,id:'dev.ming.install-demo',version:'1.0.0',sdkVersion:6,entry:'main.wasm'};
test('uninstall validates its actual install-demo identity and SDK6 adapter contract',()=>{
  const result=validatePackage(wasm(uninstallImports),'persistent-uninstall-demo',null,uninstallManifest);
  assert.deepEqual(result.rewrites,[{from:'wasi_snapshot_preview1.random_get',to:'ming_v2.random_fill'}]);
  assert.ok(result.imports.some(row=>row.module==='ming_private_files_v2'&&row.name==='call'));
  for(const change of [{id:'dev.ming.persistent-update-demo'},{id:'dev.ming.persistent-uninstall-demo'},{version:'0.1.0'},{sdkVersion:2}])assert.throws(()=>validatePackage(wasm(uninstallImports),'persistent-uninstall-demo',null,{...uninstallManifest,...change}));
  assert.throws(()=>validatePackage(wasm(uninstallImports),'persistent-uninstall-demo','old',uninstallManifest));
});
test('uninstall cannot package a text-only module without private file capability',()=>{
  assert.throws(()=>validatePackage(wasm(uninstallImports.slice(0,-1)),'persistent-uninstall-demo',null,uninstallManifest),/Missing SDK capability ming_private_files_v2.call/);
});
test('uninstall replay binds its own manifest, compiler bytes and frozen source hashes',async()=>{
  // Synthetic replay receipt exercises integrity gates, not actual Kotlin execution.
  const root=await mkdtemp(join(tmpdir(),'mingos-uninstall-replay-')),repo=join(root,'repo'),directory=join(root,'package');
  const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
  const save=async(path,value)=>writeFile(path,JSON.stringify(value));
  try{
    await mkdir(directory);await mkdir(join(directory,'evidence'));
    const directories=['samples','samples/persistent-uninstall-demo','samples/persistent-uninstall-demo/src','samples/persistent-uninstall-demo/src/wasmWasiMain','samples/persistent-uninstall-demo/src/wasmWasiMain/kotlin','ming-sdk','ming-compose','probes/compose-wasi/port','probes/compose-frame/coroutines-port','gradle'].sort();
    for(const name of directories)await mkdir(join(repo,name),{recursive:true});
    await mkdir(join(repo,'scripts/boot'),{recursive:true});
    const manifestRaw=Buffer.from(JSON.stringify(uninstallManifest));
    const sourcePath='samples/persistent-uninstall-demo/src/wasmWasiMain/kotlin/Main.kt';
    const sources=Object.fromEntries(['settings.gradle.kts','build.gradle.kts','gradle.properties','gradlew','scripts/build-kotlin.mjs','scripts/wasm-sdk-adapter.mjs','scripts/boot/build-linux-host-kotlin.mjs','scripts/boot/linux-host-kotlin.init.gradle'].map(name=>[name,Buffer.from('synthetic input '+name)]));
    sources['samples/persistent-uninstall-demo/app.json']=manifestRaw;sources[sourcePath]=Buffer.from('synthetic uninstall source');
    for(const [name,bytes]of Object.entries(sources))await writeFile(join(repo,name),bytes);
    const before={files:Object.fromEntries(Object.entries(sources).map(([name,bytes])=>[name,digest(bytes)])),directories};
    const raw=wasm(uninstallImports),result=validatePackage(raw,'persistent-uninstall-demo',null,uninstallManifest);
    const info={kotlin:'2.3.20',adapterVersion:1,rawPath:join(directory,'compiler.wasm'),compilerOutputPath:'/work/builds/uninstall/compileSync/productionExecutable/uninstall.wasm',rawSha256:digest(raw),packagedSha256:digest(result.bytes),rewrites:result.rewrites,fixture:null,sdkVersion:6,composeRuntimeSource:'1.10.0',coroutinesSource:'1.9.0',sdkSourcePath:'ming-sdk/src/wasmWasiMain/kotlin',inputSha256:before.files,imports:result.imports,exports:result.exports};
    const command=['/jdk/bin/java',...gradleArguments({...paths,repo,sample:'persistent-uninstall-demo',fixture:null})];
    const stdout=Buffer.from('> Task :persistent-uninstall-demo:compileProductionExecutableKotlinWasmWasi\nBUILD SUCCESSFUL\n'),stderr=Buffer.alloc(0);
    const deps={manifestSha256:'a'.repeat(64),files:{'caches/modules-2/files-2.1/synthetic.jar':'b'.repeat(64)},directories:[]};
    const evidence={
      'command.json':{argv:command,cwd:repo,environment:{}},'inputs-before.json':before,'inputs-after.json':before,
      'toolchain.json':{node:'v24.22.1',nodePath:'/node',nodeSha256:'c'.repeat(64),javaPath:'/jdk/bin/java',javaSha256:'d'.repeat(64),gradle:'8.14.5',gradleDistributionSha256:'6f74b601422d6d6fc4e1f9a1ab6522f642c2fdcbc15ae33ebd30ba3d7198e854',dependencyManifestSha256:deps.manifestSha256},
      'java.stdout.log':'','java.stderr.log':'openjdk version "21.0.13"\n','gradle-version.stdout.log':'Gradle 8.14.5\n','gradle-version.stderr.log':'','build.stdout.log':stdout,'build.stderr.log':stderr,
      'dependencies-before.json':deps,'dependencies-after.json':deps,'dependencies-comparison.json':{immutableFiles:deps.files,derivedChanges:[]}};
    const evidenceSha256={};for(const [name,value]of Object.entries(evidence)){const bytes=Buffer.isBuffer(value)?value:Buffer.from(typeof value==='string'?value:JSON.stringify(value));await writeFile(join(directory,'evidence',name),bytes);evidenceSha256['evidence/'+name]=digest(bytes)}
    const receipt={schemaVersion:1,status:'passed',sample:'persistent-uninstall-demo',fixture:null,kotlin:'2.3.20',composeRuntimeSource:'1.10.0',coroutinesSource:'1.9.0',offline:true,formalQualification:false,finalSystemAccepted:false,cwd:repo,compilerExitCode:0,compilerSignal:null,command,inputSha256:before.files,inputDirectories:before.directories,rawSha256:info.rawSha256,packagedSha256:info.packagedSha256,manifestSha256:digest(manifestRaw),package:info,stdoutSha256:digest(stdout),stderrSha256:digest(stderr),dependencyManifestSha256:deps.manifestSha256,evidenceSha256};
    await writeFile(join(directory,'compiler.wasm'),raw);await writeFile(join(directory,'main.wasm'),result.bytes);await writeFile(join(directory,'app.json'),manifestRaw);await save(join(directory,'build-info.json'),info);await save(join(directory,'build-receipt.json'),receipt);
    const verified=await verifyPackage({repo,directory});assert.equal(verified.sample,'persistent-uninstall-demo');assert.equal(verified.fixture,null);assert.equal(verified.packagedSha256,digest(result.bytes));
    await writeFile(join(directory,'main.wasm'),Buffer.from('substituted package'));await assert.rejects(verifyPackage({repo,directory}),/adapter replay differs/);await writeFile(join(directory,'main.wasm'),result.bytes);
    await writeFile(join(repo,sourcePath),'changed uninstall source');await assert.rejects(verifyPackage({repo,directory}),/Kotlin source inputs changed/);await writeFile(join(repo,sourcePath),sources[sourcePath]);
    await writeFile(join(directory,'app.json'),JSON.stringify({...uninstallManifest,id:'dev.ming.persistent-update-demo'}));await assert.rejects(verifyPackage({repo,directory}));
  }finally{await rm(root,{recursive:true,force:true})}
});
test('package replay rejects failed receipts and evidence traversal before trusting bytes',async()=>{
  const directory=await mkdtemp(join(tmpdir(),'mingos-kotlin-replay-'));
  try{
    const path=join(directory,'build-receipt.json');await writeFile(path,JSON.stringify({schemaVersion:1,status:'failed'}));await assert.rejects(verifyPackage({repo:directory,directory}));
    await writeFile(path,JSON.stringify({schemaVersion:1,status:'passed',offline:true,compilerExitCode:0,formalQualification:false,sample:'read-file',fixture:null,evidenceSha256:{'../outside.json':'0'.repeat(64)}}));await assert.rejects(verifyPackage({repo:directory,directory}),/unsafe evidence path/);
  }finally{await rm(directory,{recursive:true,force:true})}
});
test('input mutation and new file are detected before publication',async()=>{
  const root=await mkdtemp(join(tmpdir(),'mingos-kotlin-contract-'));
  try{await writeFile(join(root,'source.kt'),'before');const before=await snapshotTree(root);await writeFile(join(root,'source.kt'),'after');
    const after=await snapshotTree(root);assert.throws(()=>assertUnchanged(before,after));await writeFile(join(root,'new.kt'),'new');
    const added=await snapshotTree(root);assert.throws(()=>assertUnchanged(after,added));
  }finally{await rm(root,{recursive:true,force:true})}
});
test('snapshot rejects source symlinks',async t=>{
  const root=await mkdtemp(join(tmpdir(),'mingos-kotlin-link-'));
  try{await writeFile(join(root,'source.kt'),'x');try{await symlink(join(root,'source.kt'),join(root,'link.kt'))}catch(e){if(e.code==='EPERM'){t.skip('Windows symlink privilege unavailable');return}throw e}await assert.rejects(snapshotTree(root),/symlink|regular/);
  }finally{await rm(root,{recursive:true,force:true})}
});
const isolation={platform:'linux',uid:65534,gid:65534,interfaces:['lo'],mounts:[{path:'/',readOnly:true},{path:'/repo',readOnly:true}]};
test('formal runner rejects writable source/root, wrong UID and external network',()=>{
  assertIsolation(isolation,'/repo');
  for(const change of [{platform:'win32'},{uid:0},{gid:0},{interfaces:['lo','eth0']},{mounts:[{path:'/',readOnly:true},{path:'/repo',readOnly:false}]},{mounts:[{path:'/',readOnly:false},{path:'/repo',readOnly:true}]}])assert.throws(()=>assertIsolation({...isolation,...change},'/repo'));
});
