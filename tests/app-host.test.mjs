import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {runApp} from '../src/host/app-host.mjs';
import {moduleBytes,loopBytes,trapBytes} from './fixtures/wasm.mjs';

function app(t,bytes=moduleBytes(),overrides={}) {
 const dir=mkdtempSync(join(tmpdir(),'ming-app-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 writeFileSync(join(dir,'main.wasm'),bytes);
 writeFileSync(join(dir,'app.json'),JSON.stringify({schemaVersion:1,id:'dev.ming.test',version:'0.1.0',sdkVersion:1,entry:'main.wasm',...overrides}));
 return {manifestPath:join(dir,'app.json'),dir};
}
test('module completes in a child process with a host-bound application identity',async t=>{
 const a=app(t); const result=await runApp(a);assert.equal(result.appId,'dev.ming.test');assert.equal(result.exitCode,0);assert.deepEqual(result.events,[]);
});
test('real child calls the broker; ungranted resource is denied',async t=>{
 const bytes=moduleBytes({imports:[['ming_v1','file_open',1],['ming_v1','log',2]],instructions:[0x41,7,0x41,2,0x10,0,0x10,1,0x1a,0x0b]});
 const result=await runApp(app(t,bytes));assert.deepEqual(result.events,[{code:7,value:-1}]);
});
test('grants supplied by the launcher are available; app manifest cannot grant files',async t=>{
 const bytes=moduleBytes({imports:[['ming_v1','file_open',1],['ming_v1','file_length',1],['ming_v1','log',2]],instructions:[0x41,8,0x41,1,0x10,0,0x10,1,0x10,2,0x1a,0x0b]});
 const a=app(t,bytes);const file=join(a.dir,'private.txt');writeFileSync(file,'hello');
 const result=await runApp({...a,grants:{1:file}});assert.deepEqual(result.events,[{code:8,value:5}]);
 const bad=app(t,moduleBytes(),{grants:{1:file}});await assert.rejects(runApp(bad),/MANIFEST_UNKNOWN_FIELD/);
});
test('WASI and unknown imports fail before execution',async t=>{
 for(const mod of ['wasi_snapshot_preview1','env'])await assert.rejects(runApp(app(t,moduleBytes({imports:[[mod,'fd_read']]}))),/IMPORT_DENIED/);
});
test('infinite execution times out and a subsequent application still runs',async t=>{
 await assert.rejects(runApp({...app(t,loopBytes()),timeoutMs:300}),/TIMEOUT/);
 assert.equal((await runApp(app(t))).exitCode,0);
});
test('a Wasm trap fails the app, not the host',async t=>{
 await assert.rejects(runApp(app(t,trapBytes())),/APP_TRAP/);
 assert.equal((await runApp(app(t))).exitCode,0);
});
test('manifest rejects unsupported SDK, identity, path escapes and unknown privileges',async t=>{
 for(const overrides of [{sdkVersion:2},{id:'../forged'},{entry:'../outside.wasm'},{entry:'C:\\outside.wasm'},{permissions:['all']},{schemaVersion:2}])
  await assert.rejects(runApp(app(t,moduleBytes(),overrides)),/MANIFEST_/);
});
test('module and manifest input have size limits; malformed files fail',async t=>{
 await assert.rejects(runApp({...app(t),maxModuleBytes:4}),/MODULE_TOO_LARGE/);
 const a=app(t);writeFileSync(a.manifestPath,'x'.repeat(20000));await assert.rejects(runApp(a),/MANIFEST_TOO_LARGE/);
 const b=app(t,new Uint8Array([1,2,3]));await assert.rejects(runApp(b),/MODULE_INVALID/);
});
test('request floods terminate at the configured limit',async t=>{
 const bytes=moduleBytes({imports:[['ming_v1','file_open',1]],instructions:[0x03,0x40,0x41,1,0x10,0,0x1a,0x0c,0,0x0b,0x0b]});
 await assert.rejects(runApp({...app(t,bytes),maxRequests:3}),/REQUEST_LIMIT/);
});
