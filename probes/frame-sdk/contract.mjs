import assert from 'node:assert/strict';
import {createHash,randomFillSync} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import {join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
import {clockCases,waitCases,checks} from './fixtures.mjs';
import {inspectImports} from '../compose-wasi/port/wasm-imports.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const stage=process.argv[2]??'green';assert.ok(/^[a-z0-9-]+$/.test(stage));
const output=join(root,'artifacts/frame-sdk',stage);
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const json=async path=>JSON.parse(await readFile(path,'utf8'));
const receipt=await json(join(output,'build-receipt.json'));
assert.equal(receipt.status,'passed');
const wasm=await readFile(join(output,'main.wasm'));
assert.equal(digest(wasm),receipt.package.packagedSha256);
for(const [path,hash] of Object.entries(receipt.inputSha256))assert.equal(digest(await readFile(join(root,path))),hash,'Compiled source changed: '+path);
const ownPath=fileURLToPath(import.meta.url),fixturePath=fileURLToPath(new URL('./fixtures.mjs',import.meta.url));
const initialHashes={};
for(const path of [ownPath,fixturePath,join(output,'main.wasm'),join(output,'build-receipt.json')]) initialHashes[relative(root,path).replaceAll('\\','/')]=digest(await readFile(path));
let memory,clockIndex=0,waitIndex=0,randomBytes=0;
const trace=[],events=[];
const range=(pointer,length)=>{
 assert.ok(Number.isInteger(pointer)&&pointer>=-2147483648&&pointer<=2147483647);
 assert.ok(Number.isInteger(length)&&length>=0);
 const start=pointer>>>0;assert.ok(start+length<=memory.buffer.byteLength,'Guest pointer out of bounds');
 // Wasm byte ranges are valid regardless of native alignment and may overlap.
 return Buffer.from(memory.buffer,start,length);
};
const imports={
 ming_frame_v1:{
  clock_monotonic_ns(pointer,capacity){
   const fixture=clockCases[clockIndex++];assert.ok(fixture,'Unexpected clock call');assert.equal(capacity,8);
   const outputBytes=range(pointer,capacity),before=Buffer.from(outputBytes);
   if(fixture.packet){assert.equal(fixture.code,8);const data=Buffer.from(fixture.packet,'hex');assert.equal(data.length,8);data.copy(outputBytes);}
   if(fixture.code<=0)assert.deepEqual(outputBytes,before,'Clock error/pending fixture wrote output');
   trace.push({method:'clock_monotonic_ns',fixture:fixture.label,args:[pointer,capacity],outBefore:before.toString('hex'),outAfter:outputBytes.toString('hex'),returned:fixture.code});return fixture.code;
  },
  wait(requestPointer,length,eventPointer,capacity){
   const fixture=waitCases[waitIndex++];assert.ok(fixture,'Unexpected wait call');assert.equal(length,16);assert.equal(capacity,48);
   // Copy the request before writing the output, so this double does not impose a no-overlap rule.
   const request=Buffer.from(range(requestPointer,length));
   assert.equal(request.readUInt32LE(0),1);assert.equal(request.readUInt32LE(4),fixture.flags);
   assert.equal(request.readBigInt64LE(8),BigInt(fixture.deadline));
   const outputBytes=range(eventPointer,capacity),before=Buffer.from(outputBytes);
   if(fixture.packet){assert.equal(fixture.code,48);const data=Buffer.from(fixture.packet,'hex');assert.equal(data.length,48);data.copy(outputBytes);}
   if(fixture.code<=0)assert.deepEqual(outputBytes,before,'Wait pending/error fixture wrote output');
   trace.push({method:'wait',fixture:fixture.label,args:[requestPointer,length,eventPointer,capacity],request:request.toString('hex'),outBefore:before.toString('hex'),outAfter:outputBytes.toString('hex'),returned:fixture.code});return fixture.code;
  },
 },
 ming_v1:{log(code,value){
  const expected=checks[events.length];assert.ok(expected,'Extra log event');assert.deepEqual([code,value],[expected[0],1]);
  if(code===709){assert.equal(waitIndex,17,'Negative local deadline called host');assert.equal(clockIndex,clockCases.length);}
  events.push({code,value});return 0;
 }},
 ming_v2:{random_fill(pointer,length){assert.ok(length<=1048576);randomFillSync(range(pointer,length));randomBytes+=length;return 0;}},
};
const started=performance.now();
const report={schemaVersion:1,status:'running',qualification:'Real Kotlin/Wasm SDK boundary contract with Node host doubles; not native/Wasmtime execution',stage,nodeVersion:process.version,startedAt:new Date().toISOString(),inputSha256:initialHashes};
try{
 const compiled=await WebAssembly.compile(wasm);
 assert.deepEqual(inspectImports(wasm).imports,receipt.package.imports);
 const instance=await WebAssembly.instantiate(compiled,imports);memory=instance.exports.memory;
 assert.ok(memory instanceof WebAssembly.Memory);assert.equal(typeof instance.exports._start,'function');
 instance.exports._start();
 assert.equal(clockIndex,clockCases.length);assert.equal(waitIndex,waitCases.length);
 assert.deepEqual(events,checks.map(([code])=>({code,value:1})));
 for(const [path,hash] of Object.entries(initialHashes))assert.equal(digest(await readFile(join(root,path))),hash);
 report.status='passed';report.exactEvents=events;report.checks=checks.map(([code,check])=>({code,check}));
 report.callCounts={clock:clockIndex,wait:waitIndex,log:events.length,entropyBytes:randomBytes};
 report.pointerPolicy='Exact 8/16/48 byte ranges checked; no native alignment/no-overlap restriction introduced. Raw guest pointer overlap behavior of native host is not qualified here.';
 report.resourceOwnership='No owned host handles; only scoped linear-memory buffers and detached Kotlin value objects';
}catch(error){report.status='failed';report.error=error.stack??String(error);process.exitCode=1;}
finally{
 report.elapsedMs=performance.now()-started;report.finishedAt=new Date().toISOString();
 await writeFile(join(output,'boundary-trace.json'),JSON.stringify(trace,null,2)+'\n');
 report.traceSha256=digest(Buffer.from(JSON.stringify(trace,null,2)+'\n'));
 await writeFile(join(output,'boundary-result.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify(report,null,2));
}
