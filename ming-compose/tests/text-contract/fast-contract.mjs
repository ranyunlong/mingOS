import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {run} from './host.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url)),stage=process.argv[2],red=process.argv.includes('--expect-red'),out=join(root,'artifacts/text-field',stage);
const bytes=await readFile(join(out,'main.wasm')),mod=await WebAssembly.compile(bytes),cases=[];
const bulk=[{kind:4,a:40,b:80,c:1},{kind:1,text:'甲A'},{kind:4,a:40,b:128,c:1},{kind:1,text:'乙B'}];
for(const [mode,name,options={}]of [[50,'same-epoch-source-fast-fifo'],[51,'press-epoch-fast-fifo'],[52,'old-committed-and-stale-ime'],[53,'same-size-wake-drag'],[54,'same-size-wake-preedit'],[55,'true-resize-clears'],[56,'cross-render-rich-wake-drag'],[57,'unpainted-value-caret'],[58,'review-announced-A-to-B-Latin'],[59,'review-kernel-preedit-rich-wake',{wakeSizes:[[1100,760],[1100,760],[720,448],[720,448]]}],[60,'review-kernel-drag-rich-wake',{wakeSizes:[[1100,760],[1100,760],[720,448],[720,448]]}],[61,'bulk-real-poll-same-context',{bulkEvents:bulk}],[61,'bulk-real-poll-press-fenced',{bulkEvents:bulk,bulkPressFence:true}]]){
 try {const state=run(mod,{events:[[8,mode,0,0]],textMode:true,...options});const message=state.logs.filter(([c])=>c===981).map(([,v])=>String.fromCharCode(v)).join('');const passed=state.logs.at(-1)?.[0]===999&&state.logs.at(-1)?.[1]===1;if(passed&&mode===52){const active=state.focuses.map((h,i)=>h.values[1]===1?i:-1).filter(i=>i>=0);assert.ok(state.focuses.slice(active[0],active.at(-1)+1).every(h=>h.values[1]===1),'Host intent boundary must not be repeated as a fresh SDK Cancel');}if(passed&&mode===60)assert.ok(state.frames.some(f=>f.commands.some(c=>c[0]===1&&c[1]===4&&c[3]===42&&c[5]===(0xffa9cfc0|0))),'review actual selection x4,width42');if(passed&&mode===59){const labels=new Map(state.texts.map(t=>[t.handle,t.text]));assert.ok(state.frames.some(f=>f.commands.some(c=>c[0]===5&&labels.get(c[1])==='中文😀候选')),'review preedit must be in actual frame');}cases.push({mode,name,status:passed?'passed':'failed',message,...state});}catch(e){cases.push({mode,name,status:'failed',message:e.stack});}
}
const failed=cases.filter(c=>c.status==='failed');const r={status:red?(failed.length?'expected-red':'unexpected-green'):failed.length?'failed':'passed',stage,node:process.version,wasmSha256:createHash('sha256').update(bytes).digest('hex'),cases};
await writeFile(join(out,'fast-contract-result.json'),JSON.stringify(r,null,2)+'\n');console.log(JSON.stringify({status:r.status,cases:cases.map(({name,status,message})=>({name,status,message}))}));
if(red)assert.ok(cases.find(c=>c.mode===50)?.status==='failed'&&cases.find(c=>c.mode===53)?.status==='failed'&&cases.find(c=>c.mode===54)?.status==='failed');else assert.equal(failed.length,0);
