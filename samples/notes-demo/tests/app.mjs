// Real Compose/Wasm and input routing; storage replies are explicit capability doubles.
import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {run} from '../../../ming-compose/tests/text-contract/host.mjs';
const bytes=await readFile(process.argv[2]),module=await WebAssembly.compile(bytes),cases=[];
assert.ok(WebAssembly.Module.imports(module).some(i=>i.module==='ming_private_files_v2'&&i.name==='call'));
const click=(x,y)=>({kind:4,a:x,b:y,c:1});
for(const scenario of ['denied','new-no-autosave','edit-save-denied','denied-reload']){
 const calls=[];
 const events=scenario==='edit-save-denied'?[click(40,75),{kind:1,text:'记住A'},click(40,120)]:scenario==='denied-reload'?[click(40,120),click(40,170)]:[];
 const state=run(module,{textMode:true,bulkEvents:events,extra:{ming_private_files_v2:{call(_p,n){calls.push(n);return calls.length===1&&scenario!=='denied'&&scenario!=='denied-reload'?-3:-1}}}});
 assert.ok(state.frames.length>0);assert.ok(state.logs.some(([c,v])=>c===9730&&v===0));
 assert.ok(state.texts.some(t=>t.text==='便签 · Notes'));
 if(scenario.startsWith('denied'))assert.ok(state.texts.some(t=>t.text==='保存不可用'));
 if(scenario==='edit-save-denied'){
  assert.deepEqual(calls,[21,24]);assert.ok(state.logs.some(([c,v])=>c===9710&&v===-1));
  assert.ok(state.texts.some(t=>t.text==='记住A'),'typed text survives failed save');
 }else if(scenario==='denied-reload'){assert.deepEqual(calls,[21,21]);assert.equal(state.logs.filter(([c])=>c===9710).length,0)}
 else assert.deepEqual(calls,[21],'startup never writes');
 cases.push({scenario,calls,frames:state.frames.length,status:'passed'});
}
const report={status:'passed-real-kotlin-compose-notes-input-and-save-guards',wasmSha256:createHash('sha256').update(bytes).digest('hex'),cases,
 syntheticInput:true,realGrant:false,realFilesystem:false,guestStarted:false,formalQualification:false};
if(process.argv[3])await writeFile(process.argv[3],JSON.stringify(report,null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify(report,null,2));
