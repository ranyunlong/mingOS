import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
const root=fileURLToPath(new URL('../../',import.meta.url)),stage=process.argv[2],out=join(root,'artifacts/persistent-kotlin',stage);
const receipt=JSON.parse(await readFile(join(out,'build-receipt.json'),'utf8'));assert.equal(receipt.status,'passed');const mode=receipt.mode;
const bytes=await readFile(join(out,'main.wasm'));assert.equal(createHash('sha256').update(bytes).digest('hex'),receipt.package.packagedSha256);
let memory,now=0n,sentFrames=0,waits=0,closed=false,nextLayout=1,nextStyle=1,peak=0,creates=0,closes=0,submits=0;
const targetFrames=5005,frameInterval=16666667n,idleJump=310000000000n,layouts=new Map(),styles=new Set(),logs=[],waitTrace=[];
const range=(p,n)=>{const start=p>>>0;assert.ok(n>=0&&start+n<=memory.buffer.byteLength);return Buffer.from(memory.buffer,start,n);};
const words=(p,n)=>{const b=range(p,n);return Array.from({length:n/4},(_,i)=>b.readInt32LE(i*4));};
const r={status:'running',mode,stage,qualification:{clock:'synthetic',realKotlinComposeWasm:true,native5000FrameRun:false,native300SecondRun:false,physicalPresentation:false},startedAt:new Date().toISOString(),node:process.version,wasmSha256:receipt.package.packagedSha256};const wallStart=performance.now();
try {
 const instance=await WebAssembly.instantiate(await WebAssembly.compile(bytes),{
  ming_v1:{log(code,value){logs.push([code,value]);return 0;}},
  ming_v2:{random_fill(p,n){range(p,n).fill(37);return 0;},event_next(p,n){throw Error('Persistent probe unexpectedly called old synchronous input');}},
  ming_frame_v1:{clock_monotonic_ns(p,n){assert.equal(n,8);range(p,n).writeBigInt64LE(now);return 8;},wait(req,n,p,capacity){
   assert.equal(n,16);assert.equal(capacity,48);const request=range(req,n);assert.equal(request.readInt32LE(0),1);const flags=request.readInt32LE(4),deadline=request.readBigInt64LE(8);waits++;
   let kind;if(mode==='idle'){now=idleJump;kind=4;closed=true;}else if(sentFrames<targetFrames){now+=frameInterval;kind=1;sentFrames++;}else{now+=1n;kind=4;closed=true;}
   const b=range(p,48);b.fill(0);b.writeInt32LE(1,0);b.writeInt32LE(kind,4);b.writeBigInt64LE(now,8);b.writeBigInt64LE(kind===1?BigInt(sentFrames):0n,16);
   waitTrace.push({flags,deadlineNs:String(deadline),kind,nowNs:String(now)});return 48;
  }},
  ming_v3:{text_layout_create(p,n,font,em,flags){assert.equal(font,1);assert.equal(flags,0);assert.ok(n<=512);assert.ok(layouts.size<64);const text=new TextDecoder('utf8',{fatal:true}).decode(range(p,n)),handle=nextLayout++;const m=[text.length*em,em*3/4,em/4,0,em,0,-em*3/4,text.length*em,em/4,text.length,0,7];layouts.set(handle,{m,text});creates++;peak=Math.max(peak,layouts.size);return handle;},
   text_layout_metrics(h,p,n){assert.equal(n,48);assert.ok(layouts.has(h));const b=range(p,n);layouts.get(h).m.forEach((v,i)=>b.writeInt32LE(v,i*4));return 48;},
   text_layout_close(h){assert.ok(layouts.delete(h),'Layout closes exactly once');closes++;return 0;}},
  ming_v4:{shadow_style_create(){const h=nextStyle++;styles.add(h);return h;},shadow_style_close(h){assert.ok(styles.delete(h));return 0;},frame_submit(p,n){const w=words(p,n);assert.equal(w[0],0x4d474632);assert.equal(w[1],1);assert.equal(n,24+w[4]*32);assert.equal(w[5],0);for(let i=6;i<w.length;i+=8)if(w[i]===5)assert.ok(layouts.has(w[i+1]),'Frame uses live immutable layout');submits++;return 0;}},
 });memory=instance.exports.memory;instance.exports._start();
 const marker=code=>{const found=logs.filter(([c])=>c===code);assert.equal(found.length,1,'Marker '+code+' exactly once');return found[0][1];};
 const joinWords=code=>BigInt(marker(code)>>>0)|(BigInt(marker(code+1)>>>0)<<32n);
 assert.equal(marker(9200),mode==='ticker'?1:mode==='idle'?2:3);assert.equal(marker(9204),1);assert.equal(marker(9206),1);assert.equal(marker(9209),1);assert.equal(marker(9207),1);assert.equal(marker(9208),1);assert.equal(marker(9216),0);assert.equal(marker(9217),0);
 assert.equal(layouts.size,0);assert.equal(styles.size,0);assert.equal(creates,closes);assert.ok(peak<=64);assert.equal(marker(9203),submits);
 const ticks=marker(9202),code=marker(9205),elapsed=joinWords(9210);
 if(mode==='bounded'){assert.equal(code,-4);assert.equal(waits,4096);assert.ok(ticks<5000);assert.equal(closed,false);assert.ok(!logs.some(([c])=>c===9201));r.status='expected-red-bounded-control';}
 else {assert.equal(code,0);assert.equal(closed,true);if(mode==='ticker'){assert.equal(ticks,targetFrames);assert.equal(marker(9201),5000);assert.ok(waits>4096);assert.equal(joinWords(9214)-joinWords(9212),BigInt(targetFrames-1)*frameInterval);}else{assert.equal(ticks,0);assert.equal(submits,1);assert.ok(elapsed>300000000000n);}r.status='synthetic-passed';}
 r.result={ticks,frames:submits,finallyCount:1,disposalCount:1,effectStarts:1,code,elapsedVirtualNs:String(elapsed),hostCloseDelivered:closed};
}catch(e){r.status='failed';r.error=e.stack;process.exitCode=1;}
finally{r.wallElapsedMs=performance.now()-wallStart;r.finishedAt=new Date().toISOString();r.logs=logs;r.resources={creates,closes,peakLayouts:peak,liveLayouts:layouts.size,liveStyles:styles.size};r.clock={qualification:'SYNTHETIC ONLY: no sleeping, no wall-time/frame-presentation acceptance',frameIntervalNs:String(frameInterval),framesSent:sentFrames,waits};await writeFile(join(out,'synthetic-wait-trace.json'),JSON.stringify(waitTrace,null,2)+'\n');await writeFile(join(out,'synthetic-result.json'),JSON.stringify(r,null,2)+'\n');console.log(JSON.stringify({status:r.status,result:r.result,resources:r.resources,wallElapsedMs:r.wallElapsedMs,error:r.error}));}
