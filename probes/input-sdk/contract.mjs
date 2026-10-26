import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash,randomFillSync} from 'node:crypto';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url));const stage=process.argv[2]??'green';assert.match(stage,/^[a-z0-9-]+$/);
const out=join(root,'artifacts/input-sdk',stage),hash=b=>createHash('sha256').update(b).digest('hex');
const receipt=JSON.parse(await readFile(join(out,'build-receipt.json'),'utf8'));assert.equal(receipt.status,'passed');
for(const [p,h] of Object.entries(receipt.inputSha256)) if(p.startsWith('probes/input-sdk/')||/ming\/sdk\/Text(Input|EditState)\.kt$/.test(p)) assert.equal(hash(await readFile(join(root,p))),h,'Compiled source changed: '+p);
const wasm=await readFile(join(out,'main.wasm'));assert.equal(hash(wasm),receipt.package.packagedSha256);
// Node supplies ABI bytes and checks calls; every decoding/editing assertion runs in Kotlin/Wasm.
function packet(kind,text='',a=0,b=0,c=0){const t=Buffer.from(text),p=Buffer.alloc(56+t.length);p.writeUInt32LE(1,0);p.writeUInt32LE(kind,4);p.writeBigUInt64LE(1n,8);p.writeBigUInt64LE(2n,16);p.writeBigUInt64LE(kind===5?0n:3n,24);p.writeInt32LE(a,32);p.writeInt32LE(b,36);p.writeInt32LE(c,40);p.writeUInt32LE(t.length,48);t.copy(p,56);return p;}
const commit=packet(1,'中文😀'),preedit=packet(2,'A中😀Z',1,8),cancel=packet(5,'',1);
const fixtures=[{label:'pending-poison',code:0},{label:'error-poison',code:-99},{label:'commit',code:commit.length,packet:commit},{label:'preedit',code:preedit.length,packet:preedit},{label:'short-poison',code:55},{label:'long-poison',code:569},{label:'wrong-version',code:56,packet:Buffer.alloc(56)},{label:'recovered-cancel',code:56,packet:cancel}];
let memory,polls=0,focuses=0,entropy=0;const trace=[],logs=[];
const range=(p,n)=>{const start=p>>>0;assert.ok(Number.isInteger(n)&&n>=0&&start+n<=memory.buffer.byteLength);return Buffer.from(memory.buffer,start,n)};
const result={status:'running',stage,node:process.version,qualification:'Synthetic input capability contract running real Kotlin/Wasm; no GUI, physical IME, clipboard or native Host qualification',startedAt:new Date().toISOString(),wasmSha256:hash(wasm),contractSha256:hash(await readFile(fileURLToPath(import.meta.url)))};
try {
 const instance=await WebAssembly.instantiate(await WebAssembly.compile(wasm),{
  ming_input_v1:{
   poll(p,capacity){assert.equal(capacity,568);const f=fixtures[polls++];assert.ok(f,'extra poll');const b=range(p,capacity);b.fill(0xff);if(f.packet)f.packet.copy(b);trace.push({method:'poll',label:f.label,args:[p,capacity],returned:f.code,bytes:b.toString('hex')});return f.code;},
   text_focus(p,n){assert.equal(n,24);const bytes=Buffer.from(range(p,n));const words=Array.from({length:6},(_,i)=>bytes.readInt32LE(i*4));const expected=[[1,1,0,1,4096,4095],[1,0,0,0,0,0],[1,1,4095,4095,1,1]][focuses++];assert.deepEqual(words,expected);const code=focuses===3?-3:0;trace.push({method:'text_focus',args:[p,n],request:bytes.toString('hex'),words,returned:code});return code;},
  },
  ming_v1:{log(code,value){assert.ok(value>0);assert.equal(code,[901,902,903,904,999][logs.length]);if(code===903){assert.equal(focuses,0,'Invalid active rect reached Host');assert.equal(polls,8);}logs.push({code,assertions:value});return 0;}},
  ming_v2:{random_fill(p,n){randomFillSync(range(p,n));entropy+=n;return 0;}},
 });memory=instance.exports.memory;instance.exports._start();assert.equal(polls,8);assert.equal(focuses,3);assert.equal(logs.length,5);
 result.status='passed';
}catch(e){result.status='failed';result.error=e.stack;process.exitCode=1;}
finally {result.logs=logs;result.callCounts={polls,focuses,entropy};result.finishedAt=new Date().toISOString();await writeFile(join(out,'boundary-trace.json'),JSON.stringify(trace,null,2)+'\n');result.traceSha256=hash(await readFile(join(out,'boundary-trace.json')));await writeFile(join(out,'boundary-result.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));}
