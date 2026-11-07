// Linux-only source builder. Container/storage admission is owned by the caller.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {spawn} from 'node:child_process';
import {constants} from 'node:fs';
import {open,readFile,writeFile,readdir,lstat,realpath,mkdir} from 'node:fs/promises';
import {resolve,join,relative,dirname,isAbsolute,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {adaptKotlinWasi} from '../wasm-sdk-adapter.mjs';
import {inspectImports} from '../../probes/compose-wasi/port/wasm-imports.mjs';

const here=dirname(fileURLToPath(import.meta.url));
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const fail=message=>{throw new Error('LINUX_KOTLIN_BUILD_BLOCKED: '+message)};
const GRADLE_SHA='6f74b601422d6d6fc4e1f9a1ab6522f642c2fdcbc15ae33ebd30ba3d7198e854';
const ignored=new Set(['build','builds','.gradle','.kotlin','.git','.cache','node_modules']);
const inside=(base,path)=>path===base||(!relative(base,path).startsWith('..'+sep)&&relative(base,path)!=='..'&&!isAbsolute(relative(base,path)));

export function parseOptions(argv){
  const args=[...argv],sample=args.shift();
  if(!['read-file','image-viewer','text-entry-demo','animated-demo','persistent-update-demo','persistent-uninstall-demo'].includes(sample))fail('expected read-file, image-viewer, text-entry-demo, animated-demo, persistent-update-demo or persistent-uninstall-demo');
  const fixture=sample==='persistent-update-demo'?args.shift():null;
  if(sample==='persistent-update-demo'&&!['old','new','absent-write'].includes(fixture))fail('expected update fixture old, new or absent-write');
  const value={sample,fixture,repo:resolve(here,'../..')};
  const fields={'--repo':'repo','--work-root':'workRoot','--output-root':'outputRoot','--gradle-home':'gradleHome','--dependency-manifest':'dependencyManifest'};
  const seen=new Set();
  while(args.length){const key=args.shift(),name=fields[key],path=args.shift();if(!name||!path||path.startsWith('--')||seen.has(key))fail('unknown, duplicate or missing argument '+key);seen.add(key);value[name]=resolve(path)}
  return value;
}
export function assertExternalPaths(options){
  const {repo,workRoot,outputRoot,gradleHome}=options;
  for(const path of [repo,workRoot,outputRoot,gradleHome,options.dependencyManifest])if(!path||!isAbsolute(path))fail('explicit absolute repo/work/output/Gradle/dependency paths required');
  for(const path of [workRoot,outputRoot,gradleHome])if(inside(repo,path)||inside(path,repo))fail('writable path overlaps source');
  for(const [a,b] of [[workRoot,outputRoot],[workRoot,gradleHome],[outputRoot,gradleHome]])if(inside(a,b)||inside(b,a))fail('writable roots overlap');
  for(const path of [workRoot,outputRoot])if(inside(path,options.dependencyManifest))fail('dependency manifest overlaps new output/work');
  if(inside(gradleHome,options.dependencyManifest))fail('dependency manifest must be outside Gradle home');
}
export function gradleArguments(o){
  return ['-classpath',join(o.repo,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain',
    '--no-daemon','--stacktrace','--console=plain','--offline','--rerun-tasks','--no-build-cache','--no-configuration-cache',
    '--project-cache-dir',join(o.workRoot,'project-cache/main'),'--init-script',join(o.repo,'scripts/boot/linux-host-kotlin.init.gradle'),
    '-Djava.io.tmpdir='+join(o.workRoot,'tmp'),'-Pkotlin.incremental=false','-Pkotlin.project.persistent.dir='+join(o.workRoot,'kotlin-persistent'),
    ...(o.fixture?['-PpersistentUpdateFixture='+o.fixture]:[]),':'+o.sample+':compileProductionExecutableKotlinWasmWasi'];
}
export function requireExecutedTask(stdout,task){
  const lines=stdout.split(/\r?\n/).filter(line=>line.startsWith('> Task '+task)&&(/^\s*$/.test(line.slice(('> Task '+task).length))||line[('> Task '+task).length]===' '));
  if(lines.length!==1||lines[0].trim()!=='> Task '+task)fail('target task was not uniquely executed: '+task);
}
export function requireCompilerSuccess(result){if(result.exitCode!==0||result.signal)fail('compiler failed')}
export function assertIsolation(state,repo){
  if(state.platform!=='linux'||state.uid!==65534||state.gid!==65534)fail('Linux UID65534:GID65534 required');
  if(state.interfaces.some(name=>name!=='lo'))fail('external network interface present');
  const mountFor=path=>state.mounts.filter(m=>inside(m.path,path)).sort((a,b)=>b.path.length-a.path.length)[0];
  if(!mountFor('/')?.readOnly||!mountFor(repo)?.readOnly)fail('root and source must be mounted read-only');
  if(state.mounts.some(m=>inside(repo,m.path)&&!m.readOnly))fail('writable nested source mount');
}
export function assertUnchanged(before,after){assert.deepEqual(after,before,'Kotlin source inputs changed during build')}
async function regularBytes(path){
  const initial=await lstat(path);if(!initial.isFile()||initial.isSymbolicLink()||initial.nlink!==1)fail('expected single-link regular file: '+path);
  const handle=await open(path,constants.O_RDONLY|(constants.O_NOFOLLOW??0));
  try{const actual=await handle.stat();if(actual.ino!==initial.ino||actual.dev!==initial.dev)fail('file replaced: '+path);const bytes=await handle.readFile();const end=await lstat(path);if(end.ino!==actual.ino||end.dev!==actual.dev||end.size!==actual.size||end.mtimeMs!==actual.mtimeMs||bytes.length!==actual.size)fail('file changed while reading: '+path);return bytes}finally{await handle.close()}
}
export async function snapshotTree(root,{excludeGenerated=true}={}){
  const files={},directories=[];
  async function walk(path){const st=await lstat(path);if(st.isSymbolicLink()||!st.isDirectory())fail('source directory symlink/non-directory: '+path);directories.push(relative(root,path).replaceAll('\\','/'));for(const entry of (await readdir(path,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){if(excludeGenerated&&ignored.has(entry.name))continue;const child=join(path,entry.name);if(entry.isDirectory())await walk(child);else files[relative(root,child).replaceAll('\\','/')]=sha(await regularBytes(child))}}
  await walk(root);return{files,directories};
}
async function sourceInputs(repo){
  const files={},directories=[];
  for(const name of ['samples','ming-sdk','ming-compose','probes/compose-wasi/port','probes/compose-frame/coroutines-port','gradle']){await assertNoSymlinkPath(join(repo,name));const tree=await snapshotTree(join(repo,name));for(const [path,digest]of Object.entries(tree.files))files[name+'/'+path]=digest;directories.push(...tree.directories.map(path=>name+(path?'/'+path:'')))}
  for(const name of ['settings.gradle.kts','build.gradle.kts','gradle.properties','gradlew','scripts/build-kotlin.mjs','scripts/wasm-sdk-adapter.mjs','scripts/boot/build-linux-host-kotlin.mjs','scripts/boot/linux-host-kotlin.init.gradle']){await assertNoSymlinkPath(join(repo,name));files[name]=sha(await regularBytes(join(repo,name)))}
  return{files:Object.fromEntries(Object.entries(files).sort(([a],[b])=>a.localeCompare(b))),directories:directories.sort()};
}
const sdk1={'ming_v1.file_open':1,'ming_v1.file_length':1,'ming_v1.file_read_byte':2,'ming_v1.file_close':1,'ming_v1.log':2};
const sdk2={...sdk1,'ming_v2.file_read':4,'ming_v2.image_open':1,'ming_v2.image_width':1,'ming_v2.image_height':1,'ming_v2.image_close':1,'ming_v2.frame_submit':2,'ming_v2.event_next':2,'ming_v2.random_fill':2};
const sdk6={'ming_private_files_v2.call':4,'ming_v1.log':2,'ming_v2.random_fill':2,'ming_v2.event_next':2,'ming_input_v1.poll':2,'ming_input_v1.text_focus':2,'ming_frame_v1.wait':4,'ming_frame_v1.clock_monotonic_ns':2,'ming_v3.text_layout_create':5,'ming_v3.text_layout_metrics':3,'ming_v3.text_layout_close':1,'ming_v4.frame_submit':2,'ming_v4.shadow_style_create':2,'ming_v4.shadow_style_close':1};
const sdk5=Object.fromEntries(Object.entries(sdk6).filter(([key])=>!key.startsWith('ming_input_v1.')&&key!=='ming_private_files_v2.call'));
function entryAbi(bytes,imports,exports){
  assert.ok(exports.some(row=>row.name==='_start'&&row.kind==='function'),'Missing _start function export');
  assert.ok(exports.some(row=>row.name==='memory'&&row.kind==='memory'),'Missing memory export');
  // Reuse the GC-aware type parser by reflecting the entry's type as a synthetic
  // import. No module is instantiated and no application code is executed.
  let at=8,typeSection,functions=[],entry;
  const uint=()=>{let n=0;for(let shift=0;shift<35;shift+=7){const b=bytes[at++];assert.ok(b!==undefined);n+=(b&127)*2**shift;if(!(b&128))return n}fail('invalid ABI index')};
  const text=()=>{const size=uint(),value=bytes.subarray(at,at+size).toString('utf8');at+=size;return value};
  while(at<bytes.length){const start=at,id=bytes[at++],length=uint(),end=at+length;if(id===1)typeSection=bytes.subarray(start,end);if(id===3){const count=uint();functions=Array.from({length:count},uint)}if(id===7){const count=uint();for(let i=0;i<count;i++){const name=text(),kind=bytes[at++],index=uint();if(name==='_start'){assert.equal(kind,0);entry=index}}}at=end}
  assert.ok(entry>=imports.length&&entry-imports.length<functions.length,'_start must be a defined function');
  const leb=n=>{const out=[];do{let b=n%128;n=Math.floor(n/128);out.push(b|(n?128:0))}while(n);return Buffer.from(out)};
  const string=s=>Buffer.concat([leb(Buffer.byteLength(s)),Buffer.from(s)]);
  const payload=Buffer.concat([leb(1),string('entry'),string('_start'),Buffer.from([0]),leb(functions[entry-imports.length])]);
  const probe=Buffer.concat([bytes.subarray(0,8),typeSection,Buffer.from([2]),leb(payload.length),payload]);
  const signature=inspectImports(probe).imports[0];assert.deepEqual(signature.params,[],'_start params');assert.deepEqual(signature.results,[],'_start results');
}
export function validatePackage(raw,sample,fixture,manifest){
  const uninstall=sample==='persistent-uninstall-demo';
  if(uninstall&&fixture!==null)fail('uninstall source does not accept an update fixture');
  if(sample==='animated-demo'&&fixture!==null)fail('Motion source does not accept an update fixture');
  const sdk=sample==='read-file'?1:sample==='image-viewer'?2:sample==='animated-demo'?5:6;
  assert.equal(manifest.schemaVersion,1);assert.equal(manifest.id,uninstall?'dev.ming.install-demo':'dev.ming.'+sample);assert.equal(manifest.sdkVersion,sdk);assert.equal(manifest.entry,'main.wasm');assert.equal(manifest.version,uninstall?'1.0.0':sample==='persistent-update-demo'&&fixture!=='old'?'0.2.0':'0.1.0');
  const adapted=adaptKotlinWasi(raw),replay=adaptKotlinWasi(raw);assert.deepEqual(adapted,replay);
  assert.deepEqual(adapted.rewrites,sdk===1?[]:[{from:'wasi_snapshot_preview1.random_get',to:'ming_v2.random_fill'}]);
  const imports=inspectImports(adapted.bytes).imports,allowed=sdk===1?sdk1:sdk===2?sdk2:sdk===5?sdk5:sdk6;
  for(const entry of imports){const key=entry.module+'.'+entry.name;assert.ok(Object.hasOwn(allowed,key),'Unexpected SDK capability '+key);assert.equal(entry.kind,'function');assert.deepEqual(entry.params,Array(allowed[key]).fill('i32'),key);assert.deepEqual(entry.results,['i32'],key)}
  const required=sdk===1?Object.keys(sdk1):sdk===2?['ming_v1.file_open','ming_v1.file_close','ming_v2.file_read','ming_v2.image_open','ming_v2.image_width','ming_v2.image_height','ming_v2.image_close','ming_v2.event_next','ming_v2.frame_submit','ming_v2.random_fill']:sdk===5?['ming_frame_v1.clock_monotonic_ns','ming_frame_v1.wait','ming_v4.frame_submit']:[...(sample==='persistent-update-demo'||uninstall?['ming_private_files_v2.call']:[]),'ming_input_v1.poll','ming_input_v1.text_focus','ming_frame_v1.wait','ming_v4.frame_submit'];
  for(const key of required)assert.ok(imports.some(entry=>entry.module+'.'+entry.name===key),'Missing SDK capability '+key);
  const module=new WebAssembly.Module(adapted.bytes);assert.deepEqual(imports.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(module));
  const exports=WebAssembly.Module.exports(module);entryAbi(adapted.bytes,imports,exports);
  return{...adapted,imports,exports};
}
async function assertNoSymlinkPath(path){
  const full=resolve(path),parts=[];let cursor=full;while(dirname(cursor)!==cursor){parts.unshift(cursor);cursor=dirname(cursor)}parts.unshift(cursor);
  for(const part of parts){try{const st=await lstat(part);if(st.isSymbolicLink())fail('symlink path component: '+part)}catch(error){if(error.code!=='ENOENT')throw error}}
  return full;
}
async function dependencies(home,manifestPath){
  const raw=await regularBytes(manifestPath),manifest=JSON.parse(raw);
  if(manifest.schemaVersion!==1||!manifest.files||Array.isArray(manifest.files)||!Object.keys(manifest.files).length)fail('dependency manifest requires schemaVersion:1 and nonempty files map');
  for(const [name,expected]of Object.entries(manifest.files)){if(isAbsolute(name)||name.split(/[\\/]/).some(p=>p==='..'||!p)||!/^[0-9a-f]{64}$/.test(expected))fail('invalid dependency manifest entry');const path=join(home,name);await assertNoSymlinkPath(path);if(sha(await regularBytes(path))!==expected)fail('dependency hash mismatch: '+name)}
  const full=await snapshotTree(home,{excludeGenerated:false});assert.deepEqual(full.files,manifest.files,'full Gradle cache differs from sealed dependency manifest');
  return{manifestSha256:sha(raw),files:manifest.files,directories:full.directories};
}
// Downloaded artifacts and the checked wrapper distribution are immutable inputs.
// Other cache entries are derived state, never represented as freshly downloaded input.
export function compareDependencies(before,after){
  const immutable=name=>name.startsWith('caches/modules-2/files-2.1/')||name==='mingos-verified-gradle-8.14.5-bin.zip'||(name.startsWith('wrapper/dists/')&&!/\.(lck|ok|part)$/.test(name));
  const select=value=>Object.fromEntries(Object.entries(value).filter(([name])=>immutable(name)).sort(([a],[b])=>a.localeCompare(b)));
  assert.deepEqual(select(after),select(before),'Immutable Gradle dependency artifacts added, removed or changed');
  return{immutableFiles:select(before),derivedChanges:[...new Set([...Object.keys(before),...Object.keys(after)])].filter(name=>!immutable(name)&&before[name]!==after[name]).sort()};
}
async function findCompilerOutput(root){
  const matches=[];
  async function walk(path){for(const entry of await readdir(path,{withFileTypes:true})){const child=join(path,entry.name);if(entry.isSymbolicLink())fail('build output symlink');if(entry.isDirectory())await walk(child);else if(entry.isFile()&&entry.name.endsWith('.wasm')&&child.includes('compileSync')&&child.includes('productionExecutable'))matches.push(child)}}
  await walk(root);if(matches.length!==1)fail('expected exactly one fresh compiler production Wasm, found '+matches.length);return matches[0];
}
async function command(args,env,cwd,stdoutPath,stderrPath){
  const stdoutFile=await open(stdoutPath,'wx'),stderrFile=await open(stderrPath,'wx');
  let child;
  try{return await new Promise((resolveStatus,reject)=>{
    child=spawn(args[0],args.slice(1),{cwd,env,detached:true,stdio:['ignore',stdoutFile.fd,stderrFile.fd]});
    const killGroup=()=>{if(child.pid)try{process.kill(-child.pid,'SIGKILL')}catch(error){if(error.code!=='ESRCH')throw error}};
    const timer=setTimeout(killGroup,1800000);timer.unref();
    child.once('error',error=>{clearTimeout(timer);reject(error)});
    child.once('close',(code,signal)=>{clearTimeout(timer);killGroup();resolveStatus({exitCode:code,signal})});
  })}finally{await stdoutFile.close();await stderrFile.close()}
}
async function json(path,value){await writeFile(path,JSON.stringify(value,null,2)+'\n',{flag:'wx'})}
export async function build(options){
  assertExternalPaths(options);
  if(process.platform!=='linux')fail('Linux required');
  for(const name of ['repo','workRoot','outputRoot','gradleHome','dependencyManifest'])await assertNoSymlinkPath(options[name]);
  const repo=await realpath(options.repo);assert.equal(repo,options.repo,'repo must be canonical');
  const mounts=(await readFile('/proc/mounts','utf8')).trim().split('\n').map(line=>{const fields=line.split(' ');return{path:fields[1].replace(/\\([0-7]{3})/g,(_,oct)=>String.fromCharCode(parseInt(oct,8))),readOnly:fields[3].split(',').includes('ro')}});
  assertIsolation({platform:process.platform,uid:process.getuid?.(),gid:process.getgid?.(),interfaces:await readdir('/sys/class/net'),mounts},repo);
  assert.equal(process.versions.node,'24.22.1','Node24.22.1 required');
  const wrapper=await regularBytes(join(repo,'gradle/wrapper/gradle-wrapper.properties'));
  const props=wrapper.toString('utf8');if(!props.includes('gradle-8.14.5-bin.zip')||!props.split(/\r?\n/).includes('distributionSha256Sum='+GRADLE_SHA))fail('Gradle wrapper version/SHA mismatch');
  const dependencyBefore=await dependencies(options.gradleHome,options.dependencyManifest);
  // Both roots must be new. Existing failures and successful packages are never overwritten.
  await mkdir(options.workRoot);await mkdir(options.outputRoot);
  const evidence=join(options.outputRoot,'evidence');await mkdir(evidence);
  const startedAt=new Date().toISOString();let receipt={schemaVersion:1,status:'failed',sample:options.sample,fixture:options.fixture,kotlin:'2.3.20',composeRuntimeSource:'1.10.0',coroutinesSource:'1.9.0',startedAt,offline:true,formalQualification:false,finalSystemAccepted:false,cwd:repo,compilerExitCode:null};
  const receiptPath=join(options.outputRoot,'build-receipt.json');
  try{
    const before=await sourceInputs(repo);await json(join(evidence,'inputs-before.json'),before);await json(join(evidence,'dependencies-before.json'),dependencyBefore);
    const manifestName=options.fixture?options.fixture+'.app.json':'app.json',manifestPath=join(repo,'samples',options.sample,manifestName),manifestRaw=await regularBytes(manifestPath),manifest=JSON.parse(manifestRaw);
    const java=join(process.env.JAVA_HOME??fail('JAVA_HOME required'),'bin/java');await assertNoSymlinkPath(java);
    const env={...process.env,GRADLE_USER_HOME:options.gradleHome,MINGOS_KOTLIN_WORK:options.workRoot,MINGOS_KOTLIN_NODE:process.execPath,TMPDIR:join(options.workRoot,'tmp'),HOME:join(options.workRoot,'home')};
    // Ambient JVM injection can change compilation, networking and output paths.
    for(const key of Object.keys(env))if(['JAVA_TOOL_OPTIONS','JDK_JAVA_OPTIONS','_JAVA_OPTIONS','GRADLE_OPTS'].includes(key)||key.startsWith('ORG_GRADLE_PROJECT_'))delete env[key];
    for(const name of ['tmp','home','kotlin-persistent'])await mkdir(join(options.workRoot,name));
    const version=await command([java,'-version'],env,repo,join(evidence,'java.stdout.log'),join(evidence,'java.stderr.log'));
    if(version.exitCode!==0||!/version "21\./.test(await readFile(join(evidence,'java.stderr.log'),'utf8')))fail('JDK21 version probe failed');
    const gradleVersion=await command([java,'-classpath',join(repo,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain','--offline','--version'],env,repo,join(evidence,'gradle-version.stdout.log'),join(evidence,'gradle-version.stderr.log'));
    if(gradleVersion.exitCode!==0||!/^Gradle 8\.14\.5\r?$/m.test(await readFile(join(evidence,'gradle-version.stdout.log'),'utf8')))fail('Gradle8.14.5 wrapper version probe failed');
    await json(join(evidence,'toolchain.json'),{node:process.version,nodePath:process.execPath,nodeSha256:sha(await readFile(process.execPath)),javaPath:java,javaSha256:sha(await readFile(java)),gradle:'8.14.5',gradleDistributionSha256:GRADLE_SHA,gradleHome:options.gradleHome,dependencyManifest:options.dependencyManifest,dependencyManifestSha256:dependencyBefore.manifestSha256});
    const args=[java,...gradleArguments(options)];receipt.command=args;receipt.inputSha256=before.files;receipt.inputDirectories=before.directories;
    await json(join(evidence,'command.json'),{argv:args,cwd:repo,environment:{GRADLE_USER_HOME:env.GRADLE_USER_HOME,MINGOS_KOTLIN_WORK:env.MINGOS_KOTLIN_WORK,MINGOS_KOTLIN_NODE:env.MINGOS_KOTLIN_NODE,TMPDIR:env.TMPDIR,HOME:env.HOME}});
    const result=await command(args,env,repo,join(evidence,'build.stdout.log'),join(evidence,'build.stderr.log'));receipt.compilerExitCode=result.exitCode;receipt.compilerSignal=result.signal;
    const stdout=await readFile(join(evidence,'build.stdout.log')),stderr=await readFile(join(evidence,'build.stderr.log'));receipt.stdoutSha256=sha(stdout);receipt.stderrSha256=sha(stderr);
    const after=await sourceInputs(repo);await json(join(evidence,'inputs-after.json'),after);assertUnchanged(before,after);
    const dependencyAfter=await snapshotTree(options.gradleHome,{excludeGenerated:false});await json(join(evidence,'dependencies-after.json'),dependencyAfter);
    const dependencyComparison=compareDependencies(dependencyBefore.files,dependencyAfter.files);await json(join(evidence,'dependencies-comparison.json'),dependencyComparison);
    requireCompilerSuccess(result);requireExecutedTask(stdout.toString('utf8'),':'+options.sample+':compileProductionExecutableKotlinWasmWasi');
    const compilerOutputPath=await findCompilerOutput(join(options.workRoot,'builds')),raw=await regularBytes(compilerOutputPath),adapted=validatePackage(raw,options.sample,options.fixture,manifest);
    const info={kotlin:'2.3.20',adapterVersion:1,rawPath:join(options.outputRoot,'compiler.wasm'),compilerOutputPath,rawSha256:sha(raw),packagedSha256:sha(adapted.bytes),rewrites:adapted.rewrites,fixture:options.fixture,sdkVersion:manifest.sdkVersion,composeRuntimeSource:'1.10.0',coroutinesSource:'1.9.0',sdkSourcePath:'ming-sdk/src/wasmWasiMain/kotlin',inputSha256:before.files,imports:adapted.imports,exports:adapted.exports};
    // No package files are written until compiler, freeze, dependencies and ABI all pass.
    for(const [name,bytes]of [['compiler.wasm',raw],['main.wasm',adapted.bytes],['app.json',manifestRaw]])await writeFile(join(options.outputRoot,name),bytes,{flag:'wx'});
    await json(join(options.outputRoot,'build-info.json'),info);
    Object.assign(receipt,{status:'passed',manifestSha256:sha(manifestRaw),rawSha256:info.rawSha256,packagedSha256:info.packagedSha256,package:info,rawToPackageReplayVerified:true,abiValidation:'typed SDK function imports and native WebAssembly reflection',sourceInputsStable:true,dependencyInputsStable:true,dependencyManifestSha256:dependencyBefore.manifestSha256});
  }catch(error){receipt.error=String(error);throw error}finally{
    receipt.finishedAt=new Date().toISOString();receipt.evidenceSha256={};for(const name of await readdir(evidence))receipt.evidenceSha256['evidence/'+name]=sha(await readFile(join(evidence,name)));await json(receiptPath,receipt);
  }
  return receipt;
}
// Independent source/package replay. This does not establish container admission,
// dependency closure or authenticity of a supplied compiler log; the driver binds
// these bytes to its inspected container, frozen inputs and execution receipts.
export async function verifyPackage({repo,directory}){
  repo=resolve(repo);directory=resolve(directory);
  await assertNoSymlinkPath(directory);await assertNoSymlinkPath(repo);
  const readJson=async name=>JSON.parse(await regularBytes(join(directory,name)));
  const receipt=await readJson('build-receipt.json');
  if(receipt.schemaVersion!==1||receipt.status!=='passed'||receipt.offline!==true||receipt.compilerExitCode!==0||receipt.compilerSignal||receipt.formalQualification!==false)fail('package lacks successful offline compiler receipt');
  parseOptions([receipt.sample,...(receipt.fixture?[receipt.fixture]:[])]);
  if(receipt.sample==='persistent-update-demo'&&!receipt.fixture)fail('update fixture missing');
  const requiredEvidence=['command.json','inputs-before.json','inputs-after.json','toolchain.json','java.stdout.log','java.stderr.log','gradle-version.stdout.log','gradle-version.stderr.log','build.stdout.log','build.stderr.log','dependencies-before.json','dependencies-after.json','dependencies-comparison.json'];
  const names=Object.keys(receipt.evidenceSha256??{});
  for(const name of names){if(!/^evidence\/[a-z][a-z.-]*\.json$/.test(name)&&!/^evidence\/[a-z][a-z.-]*\.log$/.test(name))fail('unsafe evidence path');if(sha(await regularBytes(join(directory,name)))!==receipt.evidenceSha256[name])fail('package evidence hash mismatch: '+name)}
  assert.deepEqual(names.map(name=>name.slice(9)).sort(),(await readdir(join(directory,'evidence'))).sort(),'evidence inventory mismatch');
  for(const name of requiredEvidence)if(!names.includes('evidence/'+name))fail('missing evidence '+name);
  const info=await readJson('build-info.json'),manifestRaw=await regularBytes(join(directory,'app.json')),manifest=JSON.parse(manifestRaw),raw=await regularBytes(join(directory,'compiler.wasm')),packaged=await regularBytes(join(directory,'main.wasm'));
  const result=validatePackage(raw,receipt.sample,receipt.fixture,manifest);
  assert.deepEqual(result.bytes,packaged,'adapter replay differs from package');
  for(const field of ['imports','exports','rewrites'])assert.deepEqual(info[field],result[field],field+' reflection mismatch');
  assert.equal(info.adapterVersion,1);assert.equal(info.kotlin,'2.3.20');assert.equal(receipt.kotlin,'2.3.20');
  assert.equal(info.rawSha256,sha(raw));assert.equal(info.packagedSha256,sha(packaged));assert.equal(receipt.rawSha256,sha(raw));assert.equal(receipt.packagedSha256,sha(packaged));assert.equal(receipt.manifestSha256,sha(manifestRaw));assert.deepEqual(receipt.package,info);
  const before=await readJson('evidence/inputs-before.json'),after=await readJson('evidence/inputs-after.json');assertUnchanged(before,after);assert.deepEqual(receipt.inputSha256,before.files);assert.deepEqual(info.inputSha256,before.files);assert.deepEqual(receipt.inputDirectories,before.directories);assertUnchanged(before,await sourceInputs(repo));
  const sourceManifest='samples/'+receipt.sample+'/'+(receipt.fixture?receipt.fixture+'.app.json':'app.json');assert.equal(before.files[sourceManifest],sha(manifestRaw));
  const commandEvidence=await readJson('evidence/command.json');assert.deepEqual(receipt.command,commandEvidence.argv);
  assert.ok(Array.isArray(receipt.command)&&receipt.command.every(arg=>typeof arg==='string'),'compiler argv must be a string array');
  for(const flag of ['--offline','--rerun-tasks','--no-build-cache','--no-configuration-cache',':'+receipt.sample+':compileProductionExecutableKotlinWasmWasi'])assert.ok(receipt.command.includes(flag),'missing compiler flag '+flag);
  if(receipt.fixture)assert.ok(receipt.command.includes('-PpersistentUpdateFixture='+receipt.fixture));
  const stdout=await regularBytes(join(directory,'evidence/build.stdout.log')),stderr=await regularBytes(join(directory,'evidence/build.stderr.log'));assert.equal(receipt.stdoutSha256,sha(stdout));assert.equal(receipt.stderrSha256,sha(stderr));requireExecutedTask(stdout.toString('utf8'),':'+receipt.sample+':compileProductionExecutableKotlinWasmWasi');
  const dependenciesBefore=await readJson('evidence/dependencies-before.json'),dependenciesAfter=await readJson('evidence/dependencies-after.json'),comparison=compareDependencies(dependenciesBefore.files,dependenciesAfter.files);assert.deepEqual(await readJson('evidence/dependencies-comparison.json'),comparison);assert.equal(receipt.dependencyManifestSha256,dependenciesBefore.manifestSha256);
  const toolchain=await readJson('evidence/toolchain.json');assert.equal(toolchain.gradle,'8.14.5');assert.equal(toolchain.gradleDistributionSha256,GRADLE_SHA);assert.equal(toolchain.dependencyManifestSha256,dependenciesBefore.manifestSha256);
  assert.equal(toolchain.node,'v24.22.1');assert.match((await regularBytes(join(directory,'evidence/java.stderr.log'))).toString('utf8'),/version "21\./);assert.match((await regularBytes(join(directory,'evidence/gradle-version.stdout.log'))).toString('utf8'),/^Gradle 8\.14\.5\r?$/m);
  return{schemaVersion:1,status:'package-replay-verified',sample:receipt.sample,fixture:receipt.fixture,rawSha256:sha(raw),packagedSha256:sha(packaged),formalQualification:false};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  try{
    if(process.argv[2]==='--verify'){
      const args=process.argv.slice(3),options={};while(args.length){const flag=args.shift(),value=args.shift();if(!['--repo','--directory'].includes(flag)||!value||options[flag])fail('verify requires --repo and --directory');options[flag]=value}
      if(!options['--repo']||!options['--directory'])fail('verify requires --repo and --directory');console.log(JSON.stringify(await verifyPackage({repo:options['--repo'],directory:options['--directory']})));
    }else{const receipt=await build(parseOptions(process.argv.slice(2)));console.log(JSON.stringify({status:receipt.status,output:'see --output-root',formalQualification:false}))}
  }catch(error){console.error(String(error));process.exitCode=1}
}
