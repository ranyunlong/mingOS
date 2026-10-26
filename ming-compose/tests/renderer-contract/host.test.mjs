import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {run} from '../../../samples/shadow-demo/tests/host.mjs';

// Execute the compiled Kotlin/Compose/SDK contract. The JS double only owns the
// host capability boundary; pixel acceptance uses the native Wasmtime host.
const module=new WebAssembly.Module(await readFile(new URL('../../../artifacts/compose-bridge/renderer-package/main.wasm',import.meta.url)));
function execute(mode,options={}){
 const state=run(module,{events:[[8,mode,0,0]],...options});
 assert.deepEqual(state.logs,[[398,mode],[399,1]]);
 return state;
}
test('real Compose state changes replace one label and preserve resources on repeated render',()=>{
 const s=execute(0);
 assert.deepEqual(s.texts.map(t=>t.text),['真实 Compose','count=0','count=1']);
 assert.equal(s.creates.length,1);assert.equal(s.styles.length,1);
 assert.equal(s.textCloses.length,3);assert.equal(new Set(s.textCloses).size,3);
 assert.equal(s.closes.length,1);assert.equal(s.frames.length,4);
 assert.deepEqual(s.frames.map(f=>[f.width,f.height]),[[640,480],[640,480],[640,480],[1,1]]);
 for(const f of s.frames){assert.equal(f.commands.filter(c=>c[0]===7).length,1);assert.equal(f.commands.filter(c=>c[0]===5).length,2);}
});
for(const [name,mode,options] of [
 ['style creation',1,{createFailureAt:0}],['text creation',2,{textFailureAt:0}],
 ['text metrics',3,{metricsFailureAt:0}],['frame submission',4,{submitFailureAt:0}],
])test('partial '+name+' failure closes all staged resources and installs no hit targets',()=>{execute(mode,options)});
test('close failures are retained for disposal while cleanup continues',()=>{const s=execute(5,{closeReply:-2});assert.equal(s.closes.length,1);assert.equal(s.textCloses.length,3)});
test('failed text replacement preserves old handles and accepted hit targets',()=>{const s=execute(6,{textFailureAt:2});assert.equal(s.frames.length,1);assert.equal(s.texts.length,2);assert.equal(s.textCloses.length,2)});
test('failed replacement metrics closes its new layout and preserves prior frame',()=>{const s=execute(7,{metricsFailureAt:2});assert.equal(s.frames.length,1);assert.equal(s.texts.length,3);assert.equal(s.textCloses.length,3)});
test('16 committed plus 16 staged replacements roll back after rejected frame',()=>{const s=execute(8,{submitFailureAt:1});assert.equal(s.frames.length,2);assert.equal(s.texts.length,32);assert.equal(s.textCloses.length,32);assert.equal(s.creates.length,0)});
test('17 text nodes reject before allocating host resources',()=>{const s=execute(9);assert.equal(s.frames.length,0);assert.equal(s.texts.length,0);assert.equal(s.creates.length,0)});

test('a rejected replacement frame retains prior accepted input geometry',()=>{
 const s=execute(11,{submitFailureAt:1});assert.equal(s.frames.length,2);
 assert.equal(s.texts.length,3);assert.equal(s.textCloses.length,3);
 const a=s.frames[0].commands.find(c=>c[0]===6),b=s.frames[1].commands.find(c=>c[0]===6);
 assert.equal(a[1],24);assert.equal(b[1],64);
});
