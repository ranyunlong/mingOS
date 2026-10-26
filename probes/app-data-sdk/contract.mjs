import assert from 'node:assert/strict';
import {createHash,randomFillSync} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url)),stage=process.argv[2]??'green';assert.match(stage,/^[a-z0-9-]+$/);
const out=join(root,'artifacts/app-data/sdk-'+stage),hash=b=>createHash('sha256').update(b).digest('hex');
const receipt=JSON.parse(await readFile(join(out,'build-receipt.json'),'utf8'));assert.equal(receipt.status,'passed');
const bytes=await readFile(join(out,'main.wasm'));assert.equal(hash(bytes),receipt.package.packagedSha256);
for(const [p,h] of Object.entries(receipt.inputSha256)){
 assert.equal(hash(await readFile(join(out,'input-sources',p))),h,'Saved input changed: '+p);
 if(p.endsWith('/AppData.kt')||p.startsWith('probes/app-data-sdk/'))assert.equal(hash(await readFile(join(root,p))),h,'Owned source changed: '+p);
}
let memory,phase='denied',events=[],trace=[],randomBytes=0;
const store=new Map(),errors=[-1,-2,-3,-4,-99],errorIndex={read:0,write:0,size:0,delete:0},malformed={read:[17,65537],write:[0,4],size:[65537],delete:[1]};
const range=(p,n)=>{
 assert.ok(Number.isInteger(p)&&p>=-2147483648&&p<=2147483647);
 assert.ok(Number.isInteger(n)&&n>=0&&n<=65536);
 const start=p>>>0;assert.ok(start<=memory.buffer.byteLength&&start+n<=memory.buffer.byteLength,'Invalid guest range');return Buffer.from(memory.buffer,start,n);
};
const slot=s=>assert.ok(Number.isInteger(s)&&s>=1&&s<=8,'SDK made an invalid-slot host call');
const answer=(method,s,args,operation,buffer)=>{
 slot(s);const before=buffer?hash(buffer):null;
 let code;
 if(phase==='denied'||s===8)code=-1;
 else if(phase==='errors') {const index=errorIndex[method]++;assert.ok(index<errors.length);code=errors[index];}
 else if(phase==='malformed'){assert.ok(malformed[method].length);code=malformed[method].shift();}
 else {assert.equal(phase,'storage');code=operation();}
 if(code<0&&buffer)assert.equal(hash(buffer),before,'Host error modified output buffer');
 trace.push({method,slot:s,args,returned:code,before,after:buffer?hash(buffer):null,phase});return code;
};
const capabilities={
 read(s,offset,p,capacity){assert.ok(Number.isInteger(offset)&&offset>=0&&offset<=65536);const output=range(p,capacity);return answer('read',s,[offset,p,capacity],()=>{const b=store.get(s);if(!b)return -3;const count=Math.min(capacity,Math.max(0,b.length-offset));b.copy(output,0,offset,offset+count);return count;},output);},
 write(s,p,length){const input=range(p,length),copy=Buffer.from(input);return answer('write',s,[p,length],()=>{store.set(s,copy);return length;});},
 size(s){return answer('size',s,[],()=>store.has(s)?store.get(s).length:-3);},
 delete(s){return answer('delete',s,[],()=>store.delete(s)?0:-3);},
};
const imports={ming_data_v1:capabilities,ming_v1:{
 log(code,value){assert.equal(value,1);events.push({code,value});
  if(receipt.mode==='legacy'){assert.equal(code,1010);return 0;}
  if(receipt.mode==='size-only'){assert.equal(code,1020);return 0;}
  switch(code){
   case 1000:assert.equal(trace.length,4);assert.ok(trace.every(e=>e.returned===-1));phase='storage';break;
   case 1001:assert.equal(trace.length,4,'Invalid SDK arguments invoked host');break;
   case 1002:assert.equal(store.get(1).length,65536);assert.equal(store.get(1)[0],11);assert.ok(!store.has(2));phase='errors';break;
   case 1003:assert.deepEqual(Object.values(errorIndex),[5,5,5,5]);phase='malformed';break;
   case 1004:assert.ok(Object.values(malformed).every(v=>v.length===0));phase='done';break;
   default:assert.fail('Unexpected log '+code);
  }return 0;
 },file_length(handle){assert.equal(receipt.mode,'legacy');assert.equal(handle,1);return 17;}
},ming_v2:{random_fill(p,n){const start=p>>>0;assert.ok(n>=0&&start+n<=memory.buffer.byteLength);randomFillSync(Buffer.from(memory.buffer,start,n));randomBytes+=n;return 0;}}};
const report={schemaVersion:1,status:'running',stage,mode:receipt.mode,wasmSha256:hash(bytes),buildReceiptSha256:hash(await readFile(join(out,'build-receipt.json'))),contractSha256:hash(await readFile(fileURLToPath(import.meta.url))),qualification:'Real Kotlin/Wasm SDK; only the parent capability is doubled. No native engine/RPC/boot claim.'};
try{
 const module=await WebAssembly.compile(bytes);
 if(receipt.mode!=='legacy')await assert.rejects(()=>WebAssembly.instantiate(module,{...imports,ming_data_v1:{}}),WebAssembly.LinkError);
 const instance=await WebAssembly.instantiate(module,imports);memory=instance.exports.memory;assert.ok(memory instanceof WebAssembly.Memory);
 instance.exports._start();
 if(receipt.mode==='full'){
  assert.equal(phase,'done');assert.deepEqual(events.map(e=>e.code),[1000,1001,1002,1003,1004]);
  assert.ok(trace.some(e=>e.method==='write'&&e.returned===65536));assert.ok(trace.some(e=>e.method==='read'&&e.args[0]===65530&&e.returned===6));
 }else assert.equal(events.length,1);
 report.status='passed';report.missingNamespaceRejected=receipt.mode!=='legacy';report.memoryBytes=memory.buffer.byteLength;
}catch(error){report.status='failed';report.error=error.stack;process.exitCode=1;}
finally{Object.assign(report,{trace,events,randomBytes});await writeFile(join(out,'contract-result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({status:report.status,mode:report.mode,calls:trace.length,events,error:report.error}));}
