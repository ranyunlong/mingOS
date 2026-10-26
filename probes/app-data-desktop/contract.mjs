import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../',import.meta.url)),stage=process.argv[2]??'package',out=join(root,'artifacts/app-data-desktop',stage),bytes=await readFile(join(out,'main.wasm')),mod=await WebAssembly.compile(bytes);
assert.ok(!WebAssembly.Module.imports(mod).some(i=>i.module==='ming_data_v1'&&i.name==='delete'));
const encode=n=>{const b=Buffer.alloc(4);b.writeInt32LE(n);return b;},SKIP=-2147483648;
function execute({store=new Map(),grant=true,writeError=0,readbackMismatch=false}={}) {
 let memory,now=0n,next=1,peak=0,frames=0,writes=0,read1=0;const layouts=new Map(),logs=[],trace=[];
 const range=(p,n)=>{const start=p>>>0;assert.ok(n>=0&&start+n<=memory.buffer.byteLength);return Buffer.from(memory.buffer,start,n);};
 const allowed=slot=>slot===1&&grant;
 const instance=new WebAssembly.Instance(mod,{
  ming_data_v1:{size(slot){const code=!allowed(slot)?-1:store.has(slot)?store.get(slot).length:-3;trace.push({method:'size',slot,code});return code;},
   read(slot,offset,p,n){assert.equal(offset,0);assert.equal(n,4);if(slot===1)read1++;let code;if(!allowed(slot))code=-1;else if(!store.has(slot))code=-3;else{let b=store.get(slot);if(readbackMismatch&&slot===1&&writes>0)b=encode(99);code=Math.min(n,b.length);b.copy(range(p,n),0,0,code);}trace.push({method:'read',slot,offset,capacity:n,code});return code;},
   write(slot,p,n){assert.equal(slot,1,'No slot2 writes');assert.equal(n,4);writes++;let code;if(!allowed(slot))code=-1;else if(writeError)code=writeError;else{store.set(slot,Buffer.from(range(p,n)));code=n;}trace.push({method:'write',slot,length:n,code});return code;}},
  ming_v1:{log(c,v){logs.push([c,v]);return 0;}},
  ming_v2:{random_fill(p,n){range(p,n).fill(37);return 0;}},
  ming_frame_v1:{clock_monotonic_ns(p,n){assert.equal(n,8);range(p,n).writeBigInt64LE(now);return 8;},wait(req,n,p,cap){assert.equal(n,16);assert.equal(cap,48);now=1000000n;const b=range(p,48);b.fill(0);b.writeInt32LE(1,0);b.writeInt32LE(4,4);b.writeBigInt64LE(now,8);return 48;}},
  ming_v3:{text_layout_create(p,n,font,em,flags){assert.equal(font,1);assert.equal(flags,0);assert.ok(n<=512&&layouts.size<64);const text=new TextDecoder('utf8',{fatal:true}).decode(range(p,n)),h=next++;layouts.set(h,{text,m:[text.length*em,em*3/4,em/4,0,em,0,-em*3/4,text.length*em,em/4,text.length,0,7]});peak=Math.max(peak,layouts.size);return h;},text_layout_metrics(h,p,n){assert.equal(n,48);layouts.get(h).m.forEach((v,i)=>range(p,n).writeInt32LE(v,i*4));return 48;},text_layout_close(h){assert.ok(layouts.delete(h));return 0;}},
  ming_v4:{shadow_style_create(){throw Error('Fixture added a shadow');},shadow_style_close(){throw Error('No shadow to close');},frame_submit(p,n){const b=range(p,n);assert.equal(b.readInt32LE(0),0x4d474632);assert.equal(n,24+b.readInt32LE(16)*32);for(let o=24;o<n;o+=32)if(b.readInt32LE(o)===5)assert.ok(layouts.has(b.readInt32LE(o+4)));frames++;return 0;}},
 });memory=instance.exports.memory;instance.exports._start();
 const mark=c=>{const a=logs.filter(([code])=>code===c);assert.equal(a.length,1,'Marker '+c);return a[0][1];};
 assert.equal(mark(9311),-1);assert.equal(mark(9312),-1);assert.equal(mark(9320),frames);assert.ok(frames>=1);for(const c of [9321,9322,9324,9325,9326])assert.equal(mark(c),1);assert.equal(mark(9323),0);assert.equal(layouts.size,0);assert.ok(peak<=64);
 return {logs,trace,frames,writes,read1,peakLayouts:peak,liveLayouts:layouts.size,mark,store};
}
const report={status:'running',qualification:'Real Kotlin/Compose/Wasm; synthetic in-memory AppData capability and Close, not real filesystem/generation/reboot validation',node:process.version,wasmSha256:createHash('sha256').update(bytes).digest('hex'),cases:[]};
const save=(name,r)=>report.cases.push({name,status:'passed',logs:r.logs,trace:r.trace,frames:r.frames,writes:r.writes,peakLayouts:r.peakLayouts,liveLayouts:r.liveLayouts});
try {
 const store=new Map();let r=execute({store});assert.equal(r.mark(9301),-3);assert.equal(r.mark(9302),-3);assert.equal(r.mark(9304),0);assert.equal(r.mark(9305),1);assert.equal(r.mark(9306),4);assert.equal(r.mark(9309),1);assert.equal(r.mark(9310),1);assert.equal(r.mark(9313),0);assert.equal(store.get(1).readInt32LE(),1);save('missing-slot1-initializes-0-to-1',r);
 r=execute({store});assert.equal(r.mark(9304),1);assert.equal(r.mark(9305),2);assert.equal(r.mark(9309),2);assert.equal(r.mark(9310),1);assert.equal(store.get(1).readInt32LE(),2);save('second-fresh-Wasm-instance-1-to-2',r);
 const denied=new Map([[1,encode(7)],[2,encode(1234)]]);r=execute({store:denied,grant:false});assert.equal(r.mark(9301),-1);assert.equal(r.mark(9302),-1);assert.equal(r.mark(9306),SKIP);assert.equal(r.mark(9313),1);assert.equal(r.writes,0);assert.equal(denied.get(1).readInt32LE(),7);assert.equal(denied.get(2).readInt32LE(),1234);save('no-grant-DENIED-still-renders-no-modification',r);
 for(const [name,value,status]of [['truncated',Buffer.from([1,2]),2],['extra-bytes',Buffer.alloc(8,1),2],['negative-counter',encode(-1),2],['max-counter',encode(2147483647),4]]){const s=new Map([[1,Buffer.from(value)]]);r=execute({store:s});assert.equal(r.writes,0);assert.equal(r.mark(9313),status);assert.deepEqual(s.get(1),value);save(name+'-preserves-stored-bytes',r);}
 const failed=new Map([[1,encode(12)]]);r=execute({store:failed,writeError:-4});assert.equal(r.mark(9306),-4);assert.equal(r.mark(9313),3);assert.equal(failed.get(1).readInt32LE(),12);save('write-IO-error-recorded-without-retry',r);
 r=execute({store:new Map([[1,encode(12)]]),readbackMismatch:true});assert.equal(r.mark(9307),0);assert.equal(r.mark(9309),99);assert.equal(r.mark(9310),0);assert.equal(r.mark(9313),3);assert.equal(r.writes,1);save('readback-mismatch-visible-no-second-write',r);
 report.status='synthetic-passed';
}catch(e){report.status='failed';report.error=e.stack;process.exitCode=1;}
finally{await writeFile(join(out,'synthetic-result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({status:report.status,cases:report.cases.map(c=>({name:c.name,frames:c.frames,writes:c.writes,peakLayouts:c.peakLayouts})),error:report.error}));}
