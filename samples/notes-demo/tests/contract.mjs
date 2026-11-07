// Executes the actual compiled NotesDocument and typed SDK wire API. No real storage grants.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const raw=await readFile(process.argv[2]);
const module=await WebAssembly.compile(raw),cases=[];
const scenarios=[...Array.from({length:12},(_,mode)=>({mode,name:String(mode)})),
 {mode:3,name:'stored-newline',stored:Buffer.from('bad\nline')},
 {mode:3,name:'stored-line-separator',stored:Buffer.from('bad\u2028line')}];
for(const {mode,name,stored} of scenarios){
 let instance,reads=0,writes=0;
 let file=stored??(mode===0?null:mode===3?Buffer.alloc(513,97):mode===4?Buffer.from([0xc3,0x28]):Buffer.from(mode===1?'你好😀':'old'));
 const logs=[],calls=[];
 const memory=(p,n)=>new Uint8Array(instance.exports.memory.buffer,p,n);
 const imports={notes_test:{mode:()=>mode},ming_v1:{log:(code,value)=>{logs.push([code,value]);return 0}},ming_v2:{random_fill:(p,n)=>{memory(p,n).fill(37);return 0}},
 ming_private_files_v2:{call(input,length,output,capacity){
  assert.equal(capacity,65536);const request=Buffer.from(memory(input,length));let at=1;
  const number=()=>{assert.ok(at+4<=request.length);const n=request.readUInt32LE(at);at+=4;return n};
  const field=()=>{const size=number();assert.ok(at+size<=request.length);const value=request.subarray(at,at+size);at+=size;return value};
  const operation=request[0],path=field().toString();assert.equal(path,'note.txt');calls.push(operation);
  if(operation===1){
   reads++;const offset=number(),limit=number();assert.equal(at,request.length);assert.equal(offset,0);assert.equal(limit,513);
   if(mode===2||(mode===11&&reads===1))return -1;if(mode===10)return -4;
   if(mode===11&&reads===2)file=Buffer.from('recovered');
   if(file===null)return -3;const value=mode===7&&writes?Buffer.from('wrong readback'):file;
   const chunk=value.subarray(0,limit);memory(output,chunk.length).set(chunk);return chunk.length;
  }
  assert.equal(operation,2);writes++;const value=field();assert.equal(at,request.length);assert.ok(value.length<=512);
  if(mode===5)return -1;file=Buffer.from(value);memory(output,1)[0]=mode===6?0:1;return value.length;
 }}};
 instance=new WebAssembly.Instance(module,imports);
 try{instance.exports._start()}catch(error){console.error(JSON.stringify({failedScenario:name,mode,reads,writes,logs:logs.slice(-12)}));throw error}
 assert.deepEqual(logs.at(-1),[9799,1],'Kotlin assertions passed, mode '+mode);
 if([2,3,4,8,10].includes(mode))assert.equal(writes,0,'blocked or invalid save reached host');else assert.equal(writes,1);
 if(mode===0)assert.deepEqual(file,Buffer.alloc(0));if(mode===1)assert.deepEqual(file,Buffer.from('明天见🌙'));
 if(mode===9)assert.equal(file.length,512);
 if([0,1,9].includes(mode)){
  const expected=[...file.values()];assert.deepEqual(logs.filter(([c])=>c===9714).map(([,v])=>v),expected,'exact successful save readback bytes');
  const loaded=logs.filter(([c])=>c===9704).map(([,v])=>v);assert.deepEqual(loaded.slice(-expected.length||loaded.length),expected,'reopened bytes');
 }
 cases.push({mode,name,reads,writes,calls,status:'passed'});
}
const report={status:'passed-real-kotlin-notes-storage-contract',wasmSha256:createHash('sha256').update(raw).digest('hex'),cases,
 realFilesystem:false,realGrant:false,guestStarted:false,formalQualification:false};
if(process.argv[3])await writeFile(process.argv[3],JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(report,null,2));
