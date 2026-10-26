import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import fs from 'node:fs';
import {syncBuiltinESMExports} from 'node:module';
import {ResourceBroker} from '../src/host/broker.mjs';
import {readBounded} from '../src/host/manifest.mjs';

// A real non-regular file available on Windows proves the pre-open check.
test('input reader rejects a directory before opening it',t=>{
 const dir=mkdtempSync(join(tmpdir(),'ming-nonfile-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const spy=t.mock.method(fs,'openSync',()=>{throw new Error('BLOCKING_OPEN_REACHED');});syncBuiltinESMExports();
 t.after(()=>{spy.mock.restore();syncBuiltinESMExports();});
 assert.throws(()=>readBounded(dir,1024,'MODULE'),/MODULE_NOT_FILE/);
 assert.throws(()=>ResourceBroker.create({1:dir}),/NOT_REGULAR_FILE/);
 assert.equal(spy.mock.callCount(),0);
});
for(const target of ['broker','reader'])test('POSIX FIFO is rejected without blocking: '+target,{skip:process.platform==='win32'?'FIFO requires POSIX':false},t=>{
 const dir=mkdtempSync(join(tmpdir(),'ming-fifo-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 const fifo=join(dir,'fifo');const mk=spawnSync('mkfifo',[fifo],{timeout:2000});assert.equal(mk.status,0);
 const file=target==='broker'?'../src/host/broker.mjs':'../src/host/manifest.mjs';
 const url=pathToFileURL(fileURLToPath(new URL(file,import.meta.url))).href;
 const source=target==='broker'?'import {ResourceBroker} from '+JSON.stringify(url)+'; ResourceBroker.create({1:'+JSON.stringify(fifo)+'});':'import {readBounded} from '+JSON.stringify(url)+'; readBounded('+JSON.stringify(fifo)+',1024,"MODULE");';
 const result=spawnSync(process.execPath,['--input-type=module','--eval',source],{encoding:'utf8',timeout:2000});
 assert.notEqual(result.error?.code,'ETIMEDOUT','Parent must not block opening FIFO');assert.equal(result.status,1);assert.match(result.stderr,/NOT_REGULAR_FILE|MODULE_NOT_FILE/);
});
