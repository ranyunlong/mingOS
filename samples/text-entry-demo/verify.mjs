// Real Host + real Kotlin verification. Synthetic host events, no physical IME claim.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readFile,writeFile,mkdir,copyFile} from 'node:fs/promises';
import {join,dirname,resolve,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
const root=fileURLToPath(new URL('../../',import.meta.url));
const [hostArg,renderer,backend,label,scenario='fast',exportMode='snapshot']=process.argv.slice(2);
assert.ok(hostArg&&['cpu','gpu'].includes(renderer)&&['offscreen','native'].includes(backend)&&/^[a-z0-9-]{1,64}$/.test(label??'')&&['fast','spaced'].includes(scenario)&&['snapshot','direct'].includes(exportMode));
const output=join(root,'artifacts/input-e2e/runs',label);await mkdir(dirname(output),{recursive:true});await mkdir(output,{recursive:false});
const sha=b=>createHash('sha256').update(b).digest('hex');const digest=async p=>sha(await readFile(p));
const hashText=s=>{let v=0;for(let i=0;i<s.length;i++)v=(Math.imul(v,31)+s.charCodeAt(i))|0;return v;};
const expected=scenario==='fast'?['甲A','你乙B好 MingOS']:['中文A你文完成','独立字段'];
const report={schemaVersion:1,status:'running',renderer,backend,scenario,physicalInputByAgent:false,startedAt:new Date().toISOString(),inputSha256:{}};
try{
 const pkg=join(root,'artifacts/text-entry-demo'),receipt=JSON.parse(await readFile(join(pkg,'build-receipt.json'),'utf8')),info=JSON.parse(await readFile(join(pkg,'build-info.json'),'utf8'));
 assert.equal(receipt.status,'passed');assert.equal(await digest(join(pkg,'main.wasm')),receipt.packagedSha256);assert.equal(await digest(info.rawPath),receipt.rawSha256);
 const frozen=join(output,'frozen');await mkdir(frozen);const app=join(frozen,'app');await mkdir(app);
 const inputs=[fileURLToPath(import.meta.url),resolve(hostArg),join(root,'samples/text-entry-demo/'+(scenario==='fast'?'fast-events.json':'verification-events.json'))];
 for(const [path,hash]of Object.entries(receipt.inputSha256)){assert.equal(await digest(join(root,path)),hash,'Compiled input changed: '+path);inputs.push(join(root,path));}
 for(const name of ['app.json','main.wasm','build-receipt.json','build-info.json']){await copyFile(join(pkg,name),join(app,name));inputs.push(join(app,name));}
 await copyFile(info.rawPath,join(app,'compiler.wasm'));inputs.push(join(app,'compiler.wasm'));
 const host=join(frozen,process.platform==='win32'?'ming-app-host.exe':'ming-app-host');await copyFile(resolve(hostArg),host);inputs.push(host);
 const catalog=join(frozen,'catalog.json'),events=join(frozen,'events.json');await writeFile(catalog,JSON.stringify({schemaVersion:1,apps:[{id:1,title:'Text Lab',manifest:'app/app.json'}],autostart:[1]})+'\n');await copyFile(inputs[2],events);inputs.push(catalog,events);
 report.inputSha256=Object.fromEntries(await Promise.all([...new Set(inputs)].map(async p=>[relative(root,p).replaceAll('\\','/'),await digest(p)])));
 const args=['desktop',catalog,'--renderer',renderer,'--backend',backend,'--duration-ms',scenario==='fast'?'800':'4000','--events',events];const image=exportMode==='snapshot'?join(output,'desktop.png'):undefined;if(image)args.push('--snapshot',image);report.command=[host,...args];
 const result=await new Promise((done,reject)=>{const stdout=[],stderr=[];const child=spawn(host,args,{cwd:root,windowsHide:true});const timer=setTimeout(()=>{child.kill();reject(Error('text e2e timeout'));},300000);child.stdout.on('data',b=>stdout.push(b));child.stderr.on('data',b=>stderr.push(b));child.on('error',e=>{clearTimeout(timer);reject(e)});child.on('close',(code,signal)=>{clearTimeout(timer);done({code,signal,stdout:Buffer.concat(stdout),stderr:Buffer.concat(stderr)})});});
 await writeFile(join(output,'host.stdout.json'),result.stdout);await writeFile(join(output,'host.stderr.log'),result.stderr);assert.equal(result.code,0,result.stderr.toString());const actual=JSON.parse(result.stdout);report.outcome=actual;
 assert.equal(actual.runtime,'ming-desktop');assert.equal(actual.activeWorkersAfterShutdown,0);assert.equal(actual.droppedInputs,0);assert.equal(actual.actionsExecuted,scenario==='fast'?4:12);assert.equal(actual.instances.length,1);
 const instance=actual.instances[0];assert.equal(instance.packageId,'dev.ming.text-entry-demo');assert.equal(instance.completed,true);assert.equal(instance.status,'closed');assert.equal(instance.error,null);assert.ok(instance.workerPid>0);assert.ok(instance.firstReadyMs>=0);assert.ok(instance.presentedSourceSequence>0);
 for(const [i,text]of expected.entries())assert.deepEqual(instance.events.filter(e=>e.code===640+i),[{code:640+i,value:hashText(text)}],'Wrong final field '+i);
 assert.ok(instance.events.some(e=>e.code===642));report.expectedFinalText=expected;report.finalTextHashEquality=true;
 if(backend==='native'){assert.ok(actual.framesPresented>0);assert.deepEqual(actual.presentedFrame,actual.acceptedRoot);}else{assert.equal(actual.framesPresented,0);assert.ok(actual.offscreenFrames>0);}
 if(renderer==='gpu'){assert.ok(['DiscreteGpu','IntegratedGpu'].includes(actual.gpu.deviceType));assert.equal(actual.sceneGpuStats.readbackBytes,0);assert.equal(actual.shellGpuStats.readbackBytes,image?actual.width*actual.height*4:0);assert.equal(instance.gpuStats.readbackBytes,0);if(backend==='native'){assert.equal(actual.surfaceGpuStats.readbackBytes,0);assert.equal(actual.surfaceGpuStats.surfacePresentations,actual.framesPresented);}}
 if(image){report.pngSha256=await digest(image);report.snapshot='desktop.png';}
 for(const [path,hash]of Object.entries(report.inputSha256))assert.equal(await digest(join(root,path)),hash,'Frozen input changed: '+path);
 report.status='passed-real-kotlin-text-values-and-presentation';report.finishedAt=new Date().toISOString();
}catch(error){report.status='failed';report.error=error.stack;throw error;}finally{await writeFile(join(output,'result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));}
