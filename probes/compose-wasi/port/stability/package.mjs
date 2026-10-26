import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {resolve,dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {adaptKotlinWasi} from '../../../../scripts/wasm-sdk-adapter.mjs';
import {inspectImports} from '../wasm-imports.mjs';
const moduleRoot=dirname(fileURLToPath(import.meta.url));const root=resolve(moduleRoot,'../../../..');
const target=join(root,'artifacts/compose-qualification/port/stability/run-01');await mkdir(target,{recursive:true});
async function walk(dir){let files=[];for(const e of await readdir(dir,{withFileTypes:true})){const file=join(dir,e.name);if(e.isDirectory())files.push(...await walk(file));else if(e.name.endsWith('.wasm'))files.push(file);}return files;}
const files=(await walk(join(moduleRoot,'builds/stability'))).filter(f=>f.includes('compileSync')&&f.includes('productionExecutable'));if(files.length!==1)throw new Error('Expected one stability module');
const raw=await readFile(files[0]);await writeFile(join(target,'raw.wasm'),raw);const adapted=adaptKotlinWasi(raw);await writeFile(join(target,'main.wasm'),adapted.bytes);
const imports=inspectImports(adapted.bytes).imports;if(imports.some(i=>!((i.module==='ming_v1'&&i.name==='log')||(i.module==='ming_v2'&&i.name==='random_fill'))))throw new Error('Non-Ming import in stability app');
const sha=b=>createHash('sha256').update(b).digest('hex');const info={rawBytes:raw.length,rawSha256:sha(raw),packagedBytes:adapted.bytes.length,packagedSha256:sha(adapted.bytes),rewrites:adapted.rewrites,imports,exports:WebAssembly.Module.exports(await WebAssembly.compile(adapted.bytes))};
await writeFile(join(target,'module.json'),JSON.stringify(info,null,2)+'\n');await writeFile(join(target,'app.json'),JSON.stringify({schemaVersion:1,id:'dev.ming.compose.stability',version:'0.1.0',sdkVersion:4,entry:'main.wasm'},null,2)+'\n');console.log(JSON.stringify(info,null,2));
