import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath} from 'node:url';
import {moduleBytes} from './fixtures/wasm.mjs';
const cli=fileURLToPath(new URL('../src/cli.mjs',import.meta.url));
const run=args=>spawnSync(process.execPath,[cli,...args],{encoding:'utf8',timeout:5000});
test('CLI runs a packaged app and prints its host-bound identity',t=>{
 const dir=mkdtempSync(join(tmpdir(),'ming-cli-'));t.after(()=>rmSync(dir,{recursive:true,force:true}));
 writeFileSync(join(dir,'main.wasm'),moduleBytes());const manifest=join(dir,'app.json');
 writeFileSync(manifest,JSON.stringify({schemaVersion:1,id:'dev.ming.cli',version:'0.1.0',sdkVersion:1,entry:'main.wasm'}));
 const result=run(['run',manifest]);assert.equal(result.status,0,result.stderr);assert.equal(JSON.parse(result.stdout).appId,'dev.ming.cli');
});
test('CLI rejects malformed, repeated and unknown launch options',()=>{
 for(const args of [[],['run'],['run','x','--unknown'],['run','x','--grant','1'],['run','x','--grant','0=x'],['run','x','--grant','1=x','--grant','1=y'],['run','x','--timeout-ms','abc'],['run','x','--timeout-ms','10junk']]) {
  const result=run(args);assert.notEqual(result.status,0);assert.match(result.stderr,/USAGE|INVALID_|DUPLICATE_/);
 }
});
