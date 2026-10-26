import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {run} from '../../shadow-demo/tests/host.mjs';
const module=new WebAssembly.Module(await readFile(new URL('../../../artifacts/compose-demo/main.wasm',import.meta.url)));
const close=[0,0,0,0],increase=[4,40,120,0],toggle=[4,40,175,0];
test('real Compose app renders once and disposes every owned resource',()=>{
 const s=run(module,{events:[close]});assert.deepEqual(s.logs,[[40,1]]);
 assert.equal(s.frames.length,1);assert.equal(s.texts.length,5);assert.equal(s.styles.length,1);
 assert.equal(s.textCloses.length,5);assert.equal(s.closes.length,1);
});
test('input automatically updates remembered counter and removes/restores conditional node',()=>{
 const s=run(module,{events:[increase,toggle,toggle,increase,close]});assert.deepEqual(s.logs,[[40,5]]);
 assert.ok(s.texts.some(t=>t.text==='计数：1'));assert.ok(s.texts.some(t=>t.text==='计数：2'));
 assert.ok(s.texts.some(t=>t.text==='展开详情'));
 assert.deepEqual(s.frames.map(f=>f.commands.filter(c=>c[0]===5).length),[5,5,4,5,5]);
 assert.equal(s.creates.length,1);
});
test('idle, duplicate viewport, outside and unknown input do not redraw',()=>{
 const s=run(module,{events:[[8,0,0,0],[1,1100,760,0],[4,-1,-1,0],[99,0,0,0],close]});assert.deepEqual(s.logs,[[40,1]]);
});
test('every legal viewport is preserved and a tiny viewport drops old input targets',()=>{
 const s=run(module,{events:[[1,1,1,0],increase,[1,2048,2048,0],[1,4096,1024,0],close]});
 assert.deepEqual(s.logs,[[40,4]]);assert.deepEqual(s.frames.map(f=>[f.width,f.height]),[[1100,760],[1,1],[2048,2048],[4096,1024]]);
 assert.ok(!s.texts.some(t=>t.text==='计数：1'));
 for(const f of s.frames)assert.ok(f.width*f.height*f.commands.length<=64_000_000);
});
for(const [name,options,code] of [['style',{createFailureAt:0},-4],['text',{textFailureAt:0},-5],['metrics',{metricsFailureAt:0},-2],['submit',{submitFailureAt:0},-4]])
 test(name+' failure exits with a visible error and releases all resources',()=>{const s=run(module,options);assert.deepEqual(s.logs,[[60,code]])});
test('cleanup failure is reported while all remaining handles are still closed',()=>{const s=run(module,{events:[close],closeReply:-2});assert.deepEqual(s.logs,[[60,-2]]);assert.equal(s.textCloses.length,5);assert.equal(s.closes.length,1)});
test('bounded endless idle terminates and closes its composition',()=>{const s=run(module,{endlessIdle:true});assert.deepEqual(s.logs,[[60,-4]]);assert.equal(s.frames.length,1);assert.equal(s.polls,4096)});
