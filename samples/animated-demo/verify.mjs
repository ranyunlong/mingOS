// CLI verification tool only. It never implements the application animation or supplies a clock.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
import {join,resolve,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {adaptKotlinWasi} from '../../scripts/wasm-sdk-adapter.mjs';
import {inspectImports} from '../../probes/compose-wasi/port/wasm-imports.mjs';

const root=fileURLToPath(new URL('../../',import.meta.url));
const sample=fileURLToPath(new URL('./',import.meta.url));
const [mode,hostArg,manifestArg,label,caseName='complete']=process.argv.slice(2);
assert.ok(['compare','native','native-snapshot'].includes(mode), 'Usage: node samples/animated-demo/verify.mjs compare|native|native-snapshot <host.exe> <app.json> <new-run-label> [case]');
assert.ok(hostArg&&manifestArg&&/^[a-z0-9-]+$/.test(label??''),'Explicit host, package and a new run label are required');
if(mode!=='compare')assert.equal(caseName,'complete','Native uses Live input, not a queued --events file');
const contract=JSON.parse(await readFile(join(sample,'event-contract.json'),'utf8'));
const scenario=contract.cases[caseName];assert.ok(scenario,'Unknown case');
const parentDir=join(root,'artifacts/animated-demo-prep/runs');await mkdir(parentDir,{recursive:true});
const output=join(parentDir,label);await mkdir(output,{recursive:false});
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const digest=async path=>sha(await readFile(path));
const json=async path=>JSON.parse(await readFile(path,'utf8'));
const report={schemaVersion:1,status:'running',mode,case:caseName,startedAt:new Date().toISOString(),limits:contract.verificationLimits,computerUse:false,mouseTestPerformed:false,observations:[]};
try{
 const sourceManifest=resolve(manifestArg),sourceHost=resolve(hostArg);
 const manifest=await json(sourceManifest);assert.equal(manifest.sdkVersion,5);assert.equal(manifest.entry,'main.wasm');
 assert.equal(manifest.id,'dev.ming.animated-demo');
 const packageDir=join(sourceManifest,'..'),info=await json(join(packageDir,'build-info.json'));
 assert.equal(info.kotlin,'2.3.20');
 const wasm=await readFile(join(packageDir,'main.wasm'));assert.equal(sha(wasm),info.packagedSha256);
 const raw=await readFile(info.rawPath);assert.equal(sha(raw),info.rawSha256);
 const adapted=adaptKotlinWasi(raw);assert.deepEqual(adapted.bytes,wasm);assert.deepEqual(adapted.rewrites,info.rewrites);
 for(const rewrite of adapted.rewrites)assert.deepEqual(rewrite,{from:'wasi_snapshot_preview1.random_get',to:'ming_v2.random_fill'});
 const typed=inspectImports(wasm).imports;
 const arities={'ming_v1.log':2,'ming_v2.random_fill':2,'ming_frame_v1.clock_monotonic_ns':2,'ming_frame_v1.wait':4,
  'ming_v3.text_layout_create':5,'ming_v3.text_layout_metrics':3,'ming_v3.text_layout_close':1,
  'ming_v4.frame_submit':2,'ming_v4.shadow_style_create':2,'ming_v4.shadow_style_close':1};
 for(const entry of typed){const key=entry.module+'.'+entry.name;assert.ok(key in arities,'Unexpected capability: '+key);assert.deepEqual(entry.params,Array(arities[key]).fill('i32'));assert.deepEqual(entry.results,['i32']);}
 for(const required of ['ming_frame_v1.clock_monotonic_ns','ming_frame_v1.wait','ming_v4.frame_submit'])assert.ok(typed.some(e=>e.module+'.'+e.name===required),required);
 const reflected=await WebAssembly.compile(wasm);
 assert.deepEqual(typed.map(({module,name,kind})=>({module,name,kind})),WebAssembly.Module.imports(reflected));
 const buildReceiptPath=join(packageDir,'build-receipt.json');
 const receipt=await json(buildReceiptPath);assert.equal(receipt.status,'passed');
 assert.equal(receipt.packagedSha256,sha(wasm));assert.equal(receipt.rawSha256,sha(raw));
 assert.equal(receipt.manifestSha256,await digest(sourceManifest));
 for(const [path,hash] of Object.entries(receipt.inputSha256))assert.equal(await digest(join(root,path)),hash,'Compiled input changed: '+path);
 const frozen=join(output,'frozen');await mkdir(frozen);
 const host=join(frozen,'ming-app-host.exe'),app=join(frozen,'app.json');
 await copyFile(sourceHost,host);assert.equal(await digest(host),await digest(sourceHost));
 await writeFile(app,JSON.stringify(manifest,null,2)+'\n');await writeFile(join(frozen,'main.wasm'),wasm);
 await writeFile(join(frozen,'compiler.wasm'),raw);await writeFile(join(frozen,'build-info.json'),JSON.stringify(info,null,2)+'\n');
 let events;
 if(mode==='compare'&&scenario.events!==null){events=join(frozen,'events.json');await writeFile(events,JSON.stringify(scenario.events)+'\n');}
 await copyFile(buildReceiptPath,join(frozen,'build-receipt.json'));
 const inputPaths=[...Object.keys(receipt.inputSha256).map(p=>join(root,p)),join(frozen,'build-receipt.json'),host,app,join(frozen,'main.wasm'),join(frozen,'compiler.wasm'),join(frozen,'build-info.json'),
  join(sample,'src/wasmWasiMain/kotlin/Main.kt'),join(sample,'build.gradle.kts'),join(sample,'app.json'),join(sample,'event-contract.json'),fileURLToPath(import.meta.url),
  join(root,'scripts/wasm-sdk-adapter.mjs'),join(root,'probes/compose-wasi/port/wasm-imports.mjs'),...(events?[events]:[])];
 report.inputSha256=Object.fromEntries(await Promise.all(inputPaths.map(async path=>[relative(root,path).replaceAll('\\','/'),await digest(path)])));
 report.imports=typed;
 report.sourceBinding='Exact source inputs matched before/after compilation and remain frozen during execution; raw/package/host/receipt/imports are bound.';
 for(const renderer of mode==='compare'?['cpu','gpu']:['gpu']){
  const name=mode==='compare'?renderer:mode;
  const screenshot=mode==='native'||(mode==='compare'&&!scenario.expectCompleteAnimation)?undefined:join(output,name+'.png');
  const argv=[mode==='compare'?'run':'view',app,'--renderer',renderer,'--timeout-ms','60000','--fuel','300000000'];
  if(events)argv.push('--events',events);
  if(mode!=='compare')argv.push('--auto-close-ms',String(contract.verificationLimits.nativeAutoCloseMs));
  if(screenshot)argv.push('--snapshot',screenshot);
  const start=performance.now();
  const result=await new Promise((resolveRun,reject)=>{
   const stdout=[],stderr=[];const child=spawn(host,argv,{cwd:root,windowsHide:true});
   const deadline=setTimeout(()=>{child.kill();reject(new Error('Verifier process exceeded 65000ms including host startup'));},65000);
   child.stdout.on('data',bytes=>stdout.push(bytes));child.stderr.on('data',bytes=>stderr.push(bytes));
   child.on('error',error=>{clearTimeout(deadline);reject(error)});
   child.on('close',(exitCode,signal)=>{clearTimeout(deadline);resolveRun({exitCode,signal,stdout:Buffer.concat(stdout),stderr:Buffer.concat(stderr)})});
  });
  await writeFile(join(output,name+'.stdout.json'),result.stdout);await writeFile(join(output,name+'.stderr.log'),result.stderr);
  assert.equal(result.exitCode,0,result.stderr.toString());const actual=JSON.parse(result.stdout);
  assert.equal(actual.runtime,'rust-wasmtime');assert.equal(actual.exitCode,0);assert.equal(actual.appId,manifest.id);
  assert.deepEqual(actual.events,[{code:40,value:actual.framesSubmitted}]);assert.ok(actual.framesSubmitted>=(scenario.minimumFrames??1));
  assert.equal(actual.bulkBytesRead,0);assert.ok(actual.fuelConsumed>0&&actual.fuelConsumed<=300000000);
  assert.equal(actual.frameClock?.source,'host-monotonic-opportunity');assert.ok(actual.frameClock.waits>0);
  const frame=actual.frame;
  if(frame){
   assert.equal(frame.sequence,actual.framesSubmitted,'Final frame must match accepted submission count');
   assert.ok(frame.width>=1&&frame.height>=1&&frame.width<=4096&&frame.height<=4096&&frame.width*frame.height<=contract.budget.maximumPixels);
   assert.ok(frame.commands<=contract.budget.maximumCommands&&frame.width*frame.height*frame.commands<=contract.budget.maximumPixelCommandWork);
  }else{assert.ok(mode==='compare'&&!scenario.expectCompleteAnimation);assert.equal(actual.framesSubmitted,0);}
  if(scenario.expectCompleteAnimation){assert.ok(actual.frameClock.frames>=2,'Missing real frame opportunities');// A due timer may be pumped by a coincident FRAME; timers can legitimately be zero.
   assert.ok(actual.framesSubmitted>=2,'No animation redraw');}
  if(renderer==='gpu'){assert.ok(['DiscreteGpu','IntegratedGpu'].includes(actual.gpu?.deviceType),'Hardware GPU required');assert.equal(frame.backend,mode==='compare'?'wgpu-compute-readback':'wgpu-compute-surface');}
  if(mode==='compare'){
   if(frame)assert.deepEqual([frame.width,frame.height],scenario.viewport);assert.equal(actual.framesPresented,0);assert.equal(actual.presentationBackend,'none');
   if(scenario.expectedClickInputs!==undefined)assert.equal(actual.inputCounts[4],scenario.expectedClickInputs);
  }else{
   assert.ok(actual.framesPresented>0);assert.equal(actual.presentationBackend,'wgpu-surface');
   assert.deepEqual(actual.presentedFrame,{sequence:frame.sequence,width:frame.width,height:frame.height});
   assert.ok(actual.gpuStats?.surfacePresentations>0);
   if(mode==='native')assert.equal(actual.gpuStats.readbackBytes,0);
   else assert.equal(actual.gpuStats.readbackBytes,frame.width*frame.height*4);
   assert.equal(actual.gpuStats.surfacePresentations,actual.framesPresented);
   assert.equal(actual.gpuStats.presentationUploadBytes,32*actual.framesPresented);
  }
  report.observations.push({name,argv,exitCode:result.exitCode,elapsedSeconds:(performance.now()-start)/1000,
   frame:frame??null,framesSubmitted:actual.framesSubmitted,framesPresented:actual.framesPresented,presentedFrame:actual.presentedFrame,
   frameClock:actual.frameClock,gpuStats:actual.gpuStats??null,screenshot:screenshot?relative(output,screenshot):null,pngSha256:screenshot?await digest(screenshot):null,
   rawOutcome:name+'.stdout.json'});
 }
 if(mode==='compare'&&scenario.expectCompleteAnimation){
  assert.equal(report.observations[0].pngSha256,report.observations[1].pngSha256,'CPU/GPU terminal PNG bytes differ');
  report.terminalImageByteEquality=true;
 }
 for(const [path,hash] of Object.entries(report.inputSha256))assert.equal(await digest(join(root,path)),hash,'Frozen input changed: '+path);
 report.status='passed-protocol-and-image-checks';
 report.remainingVisualChecks=['Confirm visible 动画完成 and final opaque text in the saved terminal image','For replay, confirm the provisional click point hits 重新播放; logs do not count canceled coroutine instances'];
 report.limitations=['No physical mouse/ComputerUse interaction','Clock opportunities are not vsync or presentation','Wall time includes JIT/startup; no state-to-present latency measurement','Final-state equality does not compare time-dependent intermediate images','Native view has real Live input; user/OS resize may change its viewport'];
}catch(error){report.status='failed';report.error=error.stack;process.exitCode=1;}
finally{report.finishedAt=new Date().toISOString();await writeFile(join(output,'result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));}
