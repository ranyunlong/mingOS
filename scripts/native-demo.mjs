import assert from 'node:assert/strict';
import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {runApp} from '../src/host/app-host.mjs';
const exec=promisify(execFile);
const local=name=>fileURLToPath(new URL(name,import.meta.url));
const binary=local('../.cache/rust-target/debug/ming-app-host'+(process.platform==='win32'?'.exe':''));
const manifestPath=local('../artifacts/read-file/app.json');
const file=local('../samples/read-file/data/hello.txt');
const invoke=async grants=>JSON.parse((await exec(binary,['run',manifestPath,...grants],{windowsHide:true,timeout:15000,maxBuffer:1048576})).stdout);
try {
 const bytes=await readFile(local('../artifacts/read-file/main.wasm'));
 const native=await invoke(['--grant','1='+file]);
 const reference=await runApp({manifestPath,grants:{1:file}});
 for(const key of ['appId','events','exitCode','requests'])assert.deepEqual(native[key],reference[key],key+' must match the reference host');
 const noGrant=await invoke([]);assert.deepEqual(noGrant.events,[{code:5,value:-1}]);
 const content=Buffer.from(native.events.filter(e=>e.code===2).map(e=>e.value)).toString('utf8');
 assert.equal(content,await readFile(file,'utf8'));
 console.log(JSON.stringify({runtime:native.runtime,kotlinSha256:createHash('sha256').update(bytes).digest('hex'),authorizedContent:content,ungrantedResource:native.events.find(e=>e.code===3).value,withoutAnyGrant:noGrant.events[0].value,requests:native.requests,durationMs:native.durationMs,fuelConsumed:native.fuelConsumed,referenceParity:true},null,2));
} catch(error){console.error(error.message);console.error('Build native host and Kotlin sample first; see docs/native-host.md');process.exitCode=1;}
