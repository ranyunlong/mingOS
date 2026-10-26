import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {run} from './host.mjs';
const root=fileURLToPath(new URL('../../../',import.meta.url)),stage=process.argv[2],kind=process.argv[3],out=join(root,'artifacts/text-field',stage);
const mod=await WebAssembly.compile(await readFile(join(out,'main.wasm'))),report={status:'running',kind,cases:[]};
assert.ok(!WebAssembly.Module.imports(mod).some(i=>i.module==='ming_input_v1'),'Legacy import namespace growth');
try {
 if(kind==='renderer-contract') {
  for(const [mode,options] of [[0,{}],[1,{createFailureAt:0}],[2,{textFailureAt:0}],[3,{metricsFailureAt:0}],[4,{submitFailureAt:0}],[5,{closeReply:-2}],[6,{textFailureAt:2}],[7,{metricsFailureAt:2}],[8,{submitFailureAt:1}],[9,{}],[11,{submitFailureAt:1}]]) {
   const s=run(mod,{events:[[8,mode,0,0]],...options});assert.deepEqual(s.logs,[[398,mode],[399,1]]);report.cases.push({mode,status:'passed',peakLayouts:s.peakLayouts,frames:s.frames.length});
  }
 } else if(kind==='framed-app') {
  for(let mode=0;mode<=6;mode++) {
   const s=run(mod,{events:[[8,mode,0,0]],frameErrorMode:mode});
   const diag=s.logs.filter(([code])=>code===891).map(([,v])=>String.fromCharCode(v)).join('');
   assert.deepEqual(s.logs.at(-1),[899,1],diag);assert.ok(s.logs.some(([code,v])=>code===(mode===0?820:830+mode)&&v===1));
   report.cases.push({mode,status:'passed',logs:s.logs});
  }
 } else {
  const s=run(mod);report.observed=s.logs;
  if(kind==='contract')assert.deepEqual(s.logs.filter(([code])=>code>=200&&code<=299),[200,...Array.from({length:27},(_,i)=>201+i),299].map(code=>[code,1]));
  else if(kind==='framed-driver'){assert.deepEqual(s.logs.at(-1),[4999,1]);assert.ok(s.logs.filter(([code])=>code>=4000&&code<4900).every(([,v])=>v===1));}
  else assert.deepEqual(s.logs.filter(([code])=>code===810||code===811||code===899),[[810,1],[811,1],[899,1]]);
  report.cases.push({status:'passed',logs:s.logs});
 }
 report.status='passed';
}catch(e){report.status='failed';report.error=e.stack;process.exitCode=1;}
finally{await writeFile(join(out,'regression-result.json'),JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report));}
