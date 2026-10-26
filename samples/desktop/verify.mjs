// Developer verification only: trusted host actions, no physical input injection.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
import {join,resolve,relative,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=fileURLToPath(new URL('../../',import.meta.url));
const [hostArg,renderer,backend,label,exportMode='snapshot']=process.argv.slice(2);
assert.ok(hostArg&&['cpu','gpu'].includes(renderer)&&['offscreen','native'].includes(backend)&&/^[a-z0-9-]{1,64}$/.test(label??'')&&['snapshot','direct'].includes(exportMode),'Usage: node samples/desktop/verify.mjs <host> cpu|gpu native|offscreen <new-label> [snapshot|direct]');
const output=join(root,'artifacts/desktop-runtime/runs',label);await mkdir(dirname(output),{recursive:true});await mkdir(output,{recursive:false});
const sha=b=>createHash('sha256').update(b).digest('hex');const digest=async p=>sha(await readFile(p));
const report={schemaVersion:1,status:'running',renderer,backend,physicalInput:false,computerUse:false,startedAt:new Date().toISOString(),inputSha256:{},packages:[]};
try{
 const frozen=join(output,'frozen');await mkdir(frozen);
 const originalCatalog=join(root,'samples/desktop/catalog.json');const catalog=JSON.parse(await readFile(originalCatalog,'utf8'));const inputs=[originalCatalog,join(root,'samples/desktop/verification-events.json'),fileURLToPath(import.meta.url),resolve(hostArg)];
 for(const app of catalog.apps){
  const sourceManifest=resolve(dirname(originalCatalog),app.manifest),packageDir=dirname(sourceManifest);
  const manifest=JSON.parse(await readFile(sourceManifest,'utf8'));const info=JSON.parse(await readFile(join(packageDir,'build-info.json'),'utf8'));const receipt=JSON.parse(await readFile(join(packageDir,'build-receipt.json'),'utf8'));
  assert.equal(receipt.status,'passed');assert.equal(receipt.manifestSha256,await digest(sourceManifest));assert.equal(receipt.packagedSha256,await digest(join(packageDir,'main.wasm')));assert.equal(info.rawSha256,await digest(info.rawPath));assert.equal(receipt.rawSha256,info.rawSha256);
  for(const [path,hash]of Object.entries(receipt.inputSha256)){assert.equal(await digest(join(root,path)),hash,'Compiled input changed: '+path);inputs.push(join(root,path));}
  const pkg=join(frozen,'packages',String(app.id));await mkdir(pkg,{recursive:true});for(const name of ['app.json','main.wasm','build-info.json','build-receipt.json']){await copyFile(join(packageDir,name),join(pkg,name));inputs.push(join(pkg,name));}
  await copyFile(info.rawPath,join(pkg,'compiler.wasm'));inputs.push(join(pkg,'compiler.wasm'));
  report.packages.push({app:app.id,packageId:manifest.id,sdkVersion:manifest.sdkVersion,kotlin:info.kotlin,rawSha256:info.rawSha256,packagedSha256:info.packagedSha256});
  app.manifest='packages/'+app.id+'/app.json';
 }
 const host=join(frozen,process.platform==='win32'?'ming-app-host.exe':'ming-app-host');await copyFile(resolve(hostArg),host);assert.equal(await digest(host),await digest(resolve(hostArg)));inputs.push(host);
 const catalogPath=join(frozen,'catalog.json'),events=join(frozen,'events.json');await writeFile(catalogPath,JSON.stringify(catalog,null,2)+'\n');await copyFile(join(root,'samples/desktop/verification-events.json'),events);inputs.push(catalogPath,events);
 report.inputSha256=Object.fromEntries(await Promise.all([...new Set(inputs)].map(async p=>[relative(root,p).replaceAll('\\','/'),await digest(p)])));
 const argv=['desktop',catalogPath,'--renderer',renderer,'--backend',backend,'--width','1280','--height','800','--duration-ms','6500','--events',events];const snapshot=exportMode==='snapshot'?join(output,'desktop.png'):undefined;if(snapshot)argv.push('--snapshot',snapshot);report.command=[host,...argv];
 const started=performance.now();const execution=await new Promise((resolveRun,reject)=>{const stdout=[],stderr=[];const child=spawn(host,argv,{cwd:root,windowsHide:true});const timeout=setTimeout(()=>{child.kill();reject(new Error('Desktop exceeded 360000ms verification deadline'));},360000);child.stdout.on('data',b=>stdout.push(b));child.stderr.on('data',b=>stderr.push(b));child.on('error',e=>{clearTimeout(timeout);reject(e)});child.on('close',(exitCode,signal)=>{clearTimeout(timeout);resolveRun({exitCode,signal,stdout:Buffer.concat(stdout),stderr:Buffer.concat(stderr)})});});
 await writeFile(join(output,'host.stdout.json'),execution.stdout);await writeFile(join(output,'host.stderr.log'),execution.stderr);report.elapsedSeconds=(performance.now()-started)/1000;
 assert.equal(execution.exitCode,0,execution.stderr.toString());const actual=JSON.parse(execution.stdout);report.outcome=actual;
 assert.equal(actual.runtime,'ming-desktop');assert.equal(actual.activeWorkersAfterShutdown,0);assert.equal(actual.actionsExecuted,15);assert.equal(actual.droppedInputs,0);assert.ok(actual.maxConcurrentWorkers>=2);assert.ok(actual.acceptedRoot.sequence>0);assert.deepEqual([actual.acceptedRoot.width,actual.acceptedRoot.height],[actual.width,actual.height]);
 assert.equal(actual.instances.length,4);assert.ok(actual.instances.every(i=>i.completed&&i.workerPid>0&&i.firstReadyMs!==null&&i.lastSourceSequence>0));assert.equal(new Set(actual.instances.map(i=>i.workerPid)).size,4);
 assert.ok(actual.instances.some(i=>i.app===2&&i.generation===1&&i.status==='failed'&&i.error.includes('APP_CANCELLED')));
 assert.ok(actual.instances.some(i=>i.app===1&&i.generation===1&&i.status==='closed'&&i.events.some(e=>e.code===40)));
 for(const pkg of report.packages){const latest=actual.instances.filter(i=>i.app===pkg.app).at(-1);assert.equal(latest.generation,2);assert.equal(latest.packageId,pkg.packageId);assert.equal(latest.status,'closed');assert.ok(latest.framesSubmitted>0);assert.ok(latest.presentedSourceSequence>0);assert.ok(latest.presentedViewport.every(n=>n>0));}
 if(backend==='native'){assert.ok(['win32','wayland','x11'].includes(actual.windowSystem));assert.ok(actual.framesPresented>0);assert.deepEqual(actual.presentedFrame,actual.acceptedRoot);assert.equal(actual.offscreenFrames,0);}else{assert.equal(actual.windowSystem,'offscreen');assert.equal(actual.framesPresented,0);assert.equal(actual.presentedFrame,null);assert.ok(actual.offscreenFrames>0);}
 if(renderer==='gpu'){assert.ok(['DiscreteGpu','IntegratedGpu'].includes(actual.gpu.deviceType),'Actual hardware GPU required');assert.equal(actual.sceneGpuStats.readbackBytes,0);assert.equal(actual.shellGpuStats.readbackBytes,snapshot?actual.width*actual.height*4:0);assert.equal(actual.shellGpuStats.imageUploadBytes,0);for(const app of actual.instances){if(app.gpuStats)assert.equal(app.gpuStats.readbackBytes,0);}if(backend==='native'){assert.equal(actual.surfaceGpuStats.readbackBytes,0);assert.equal(actual.surfaceGpuStats.surfacePresentations,actual.framesPresented);assert.equal(actual.surfaceGpuStats.presentationUploadBytes,32*actual.framesPresented);}}
 if(snapshot){report.pngSha256=await digest(snapshot);report.snapshot='desktop.png';}
 for(const [path,hash]of Object.entries(report.inputSha256))assert.equal(await digest(join(root,path)),hash,'Frozen input changed: '+path);
 report.status='passed-real-workers-and-presentation-contracts';report.finishedAt=new Date().toISOString();
}catch(error){report.status='failed';report.error=error.stack;throw error;}finally{await writeFile(join(output,'result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));}
