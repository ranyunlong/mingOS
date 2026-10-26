import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {join} from 'node:path';
import {run} from './host.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url)),stage=process.argv[2]??'green',out=join(root,'artifacts/text-field',stage);
const bytes=await readFile(join(out,'main.wasm')),module=await WebAssembly.compile(bytes);
const report={status:'running',stage,node:process.version,wasmSha256:createHash('sha256').update(bytes).digest('hex'),qualification:'Real Kotlin/Compose/Wasm with CPU capability doubles; synthetic input only',cases:[]};
try {
 for(const [name,mode,options] of [['fields',0,{}],['text-create-failure',1,{textFailureAt:0}],['metrics-failure',2,{metricsFailureAt:0}],['submit-failure',3,{submitFailureAt:0}],['replacement-rollback',4,{submitFailureAt:1}],['persistent-text-app',10,{app:true}],['boundary-queued-commit',12,{app:true,boundaryCommit:'旧'}],['boundary-cancel-before-commit',12,{app:true,boundaryCommit:'旧',boundaryOverflow:true}],['published-caret-rollback',11,{submitFailureAt:2}],['64-live-budget',20,{}],['fifth-field-limit',21,{}],['sync-bounded',30,{idleCalls:5010}],['sync-persistent',31,{idleCalls:5010}],['framed-bounded',32,{idleWaits:5010}],['framed-persistent',33,{idleWaits:5010}],['text-persistent',34,{idleWaits:5010}]]) {
  const s=run(module,{textMode:true,events:[[8,mode,0,0]],...options});
  const diag=s.logs.filter(([code])=>code===981).map(([,v])=>String.fromCharCode(v)).join('');
  assert.deepEqual(s.logs.at(-1),[999,1],name+': '+diag);assert.ok(s.logs.find(([code])=>code===960));
  for(const hint of s.focuses)if(hint.values[1]===1)assert.equal(hint.values[4],1,'IME hint uses accepted caret rectangle');
  if(name==='persistent-text-app'||name==='boundary-queued-commit'){
   const decoded=s.inputs.map(h=>Buffer.from(h,'hex'));const tab=decoded.findIndex(b=>b.readInt32LE(4)===3&&b.readInt32LE(32)===9);
   assert.ok(tab>0);assert.equal(decoded[tab-1].readInt32LE(4),5);assert.equal(decoded[tab-1].readInt32LE(32),1);
   assert.equal(decoded[tab-1].readBigInt64LE(16),decoded[tab].readBigInt64LE(16),'Parent Cancel -> Tab shares observed epoch');
   const i=s.focuses.findIndex(h=>h.values[1]===1&&h.values[3]>=32),first=s.focuses.findIndex(h=>h.values[1]===1);
   assert.ok(i>first);assert.ok(s.focuses.slice(first,i+1).every(h=>h.values[1]===1),'Do not re-cancel a Host-fenced field intent');
  }
  if(name==='fields')assert.ok(s.frames[2].commands.some(c=>c[0]===1&&c[5]===(0xffa9cfc0|0)),'preedit cursor range must be painted as selection');
  assert.ok(s.peakLayouts<=64);assert.equal(s.styles.length,0,'Fields add no shadows');
  report.cases.push({name,status:'passed',...s});
 }
 report.status='passed';
}catch(e){report.status='failed';report.error=e.stack;process.exitCode=1;}
finally{await writeFile(join(out,'contract-result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify({status:report.status,cases:report.cases.map(c=>({name:c.name,checks:c.logs.find(([code])=>code===960)?.[1],peakLayouts:c.peakLayouts,frames:c.frames.length})),error:report.error}));}
