import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const outputs=new URL('../../../artifacts/persistent-update-demo/',import.meta.url);
const alpha=Buffer.from('alpha');

async function execute(variant,files,{synced=true,closeKind=4}={}) {
 const bytes=await readFile(new URL(variant+'/main.wasm',outputs));
 const mod=await WebAssembly.compile(bytes);
 let memory,frames=0,waits=0,nextLayout=1;
 const layouts=new Map(),texts=[],logs=[],calls=[];
 const range=(p,n)=>{assert.ok(n>=0&&(p>>>0)+n<=memory.buffer.byteLength);return Buffer.from(memory.buffer,p>>>0,n);};
 const instance=new WebAssembly.Instance(mod,{
  ming_private_files_v2:{call(p,n,out,capacity){
   const request=Buffer.from(range(p,n));let at=1;
   const number=()=>{const value=request.readUInt32LE(at);at+=4;return value;};
   const field=()=>{const length=number(),value=request.subarray(at,at+length);at+=length;assert.equal(value.length,length);return value;};
   const path=field().toString('utf8');calls.push({op:request[0],path});
   if(request[0]===1){const offset=number(),limit=number();assert.equal(at,n);if(!files.has(path))return -3;const value=files.get(path).subarray(offset,offset+limit);assert.ok(value.length<=capacity);value.copy(range(out,value.length));return value.length;}
   assert.equal(request[0],2,'Only typed read/write are used');const value=Buffer.from(field());assert.equal(at,n);assert.ok(capacity>=1);files.set(path,value);range(out,1)[0]=synced?1:0;return value.length;
  }},
  ming_v1:{log(code,value){logs.push([code,value]);return 0;}},
  ming_v2:{random_fill(p,n){range(p,n).fill(37);return 0;}},
  ming_input_v1:{poll(){return 0;},text_focus(){return 0;}},
  ming_frame_v1:{clock_monotonic_ns(p,n){assert.equal(n,8);range(p,n).writeBigInt64LE(BigInt(waits)*1_000_000n);return n;},wait(p,n,out,capacity){assert.equal(n,16);assert.equal(capacity,48);assert.ok(frames>0,'Close follows a submitted frame');waits++;const reply=range(out,48);reply.fill(0);reply.writeInt32LE(1,0);reply.writeInt32LE(closeKind,4);reply.writeBigInt64LE(BigInt(waits)*1_000_000n,8);return 48;}},
  ming_v3:{text_layout_create(p,n,font,em,flags){assert.equal(font,1);assert.equal(flags,0);const text=range(p,n).toString('utf8'),id=nextLayout++;texts.push(text);layouts.set(id,[text.length*em,em*3/4,em/4,0,em,0,-em*3/4,text.length*em,em/4,text.length,0,7]);return id;},text_layout_metrics(id,p,n){assert.equal(n,48);layouts.get(id).forEach((value,index)=>range(p,n).writeInt32LE(value,index*4));return n;},text_layout_close(id){assert.ok(layouts.delete(id));return 0;}},
  ming_v4:{shadow_style_create(){assert.fail('No shadow needed');},shadow_style_close(){assert.fail('No shadow owned');},frame_submit(p,n){const frame=range(p,n);assert.equal(frame.readUInt32LE(0),0x4d474632);assert.equal(n,24+frame.readUInt32LE(16)*32);frames++;return 0;}},
 });
 memory=instance.exports.memory;
 let error;
 try{instance.exports._start();}catch(caught){error=caught;}
 return {error,frames,waits,texts,logs,calls,liveLayouts:layouts.size};
}

test('old and new real modules read their own layouts and finish only after Close',async()=>{
 for(const [variant,path,version]of [['old','note.txt','0.1.0'],['new','state/note.txt','0.2.0']]){
  const result=await execute(variant,new Map([[path,alpha]]));
  assert.equal(result.error,undefined);assert.ok(result.texts.some(text=>text.includes(version)));
  assert.deepEqual(result.calls,[{op:1,path}]);assert.ok(result.frames>0);assert.equal(result.waits,1);assert.equal(result.liveLayouts,0);
  assert.ok(result.logs.some(([code,value])=>code===9510&&value===0));
  const wrong=await execute(variant,new Map([[path==='note.txt'?'state/note.txt':'note.txt',alpha]]));
  assert.ok(wrong.error,'Wrong layout must fail');assert.equal(wrong.frames,0);
  const corrupt=await execute(variant,new Map([[path,Buffer.from('wrong')]]));
  assert.ok(corrupt.error,'Corrupt content must fail');assert.equal(corrupt.frames,0);
 }
});

test('absent-write creates nonempty data and requires directory sync before rendering',async()=>{
 const files=new Map(),result=await execute('absent-write',files);
 assert.equal(result.error,undefined);assert.deepEqual(files.get('note.txt'),alpha);
 assert.deepEqual(result.calls,[{op:2,path:'note.txt'},{op:1,path:'note.txt'}]);assert.ok(result.frames>0);assert.equal(result.waits,1);assert.equal(result.liveLayouts,0);
 const unsynced=await execute('absent-write',new Map(),{synced:false});
 assert.ok(unsynced.error,'Unsynced write must fail');assert.equal(unsynced.frames,0);assert.equal(unsynced.waits,0);
});

test('Cancel cannot be reported as a successful Close',async()=>{
 for(const [variant,path]of [['old','note.txt'],['new','state/note.txt'],['absent-write','note.txt']]){
  const result=await execute(variant,new Map([[path,alpha]]),{closeKind:5});
  assert.ok(result.error);assert.ok(result.frames>0);assert.equal(result.liveLayouts,0);
  assert.ok(result.logs.some(([code,value])=>code===9510&&value===-3));
 }
});
