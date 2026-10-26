import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {readFileSync} from 'node:fs';
import {runApp} from '../src/host/app-host.mjs';
const local=name=>fileURLToPath(new URL(name,import.meta.url));
try {
 const manifestPath=local('../artifacts/read-file/app.json');const file=local('../samples/read-file/data/hello.txt');
 const granted=await runApp({manifestPath,grants:{1:file}});
 const content=Buffer.from(granted.events.filter(e=>e.code===2).map(e=>e.value)).toString('utf8');
 assert.equal(content,readFileSync(file,'utf8'));
 const denied=granted.events.find(e=>e.code===3)?.value;assert.equal(denied,-1);
 const noGrant=await runApp({manifestPath});assert.deepEqual(noGrant.events,[{code:5,value:-1}]);
 console.log(JSON.stringify({appId:granted.appId,authorizedContent:content,ungrantedResource:denied,withoutAnyGrant:noGrant.events[0].value,exitCode:granted.exitCode},null,2));
} catch(e){console.error(e.message);console.error('Build first: node scripts/build-kotlin.mjs');process.exitCode=1;}
