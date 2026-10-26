import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {adaptKotlinWasi} from '../../scripts/wasm-sdk-adapter.mjs';
import {inspectImports} from '../compose-wasi/port/wasm-imports.mjs';
const probe=dirname(fileURLToPath(import.meta.url));const root=resolve(probe,'../..');const label=process.argv[2];const out=join(root,'artifacts/compose-frame-qualification',label);await mkdir(out,{recursive:true});
async function walk(dir){let all=[];for(const e of await readdir(dir,{withFileTypes:true})){const file=join(dir,e.name);if(e.isDirectory())all.push(...await walk(file));else if(e.name.endsWith('.wasm'))all.push(file);}return all;}
const files=(await walk(join(probe,'build'))).filter(f=>f.includes('productionExecutable')&&f.includes('compileSync'));if(files.length!==1)throw new Error('Expected one actual frame probe Wasm');
const raw=await readFile(files[0]);await writeFile(join(out,'raw.wasm'),raw);let adapted=null,error=null;try{adapted=adaptKotlinWasi(raw)}catch(e){error=String(e)}const bytes=adapted?.bytes??raw;await writeFile(join(out,'main.wasm'),bytes);
const sha=b=>createHash('sha256').update(b).digest('hex');const imports=inspectImports(raw);const module=await WebAssembly.compile(bytes);
const info={label,sourceWasm:files[0],rawBytes:raw.length,rawSha256:sha(raw),packagedBytes:bytes.length,packagedSha256:sha(bytes),adapterError:error,rewrites:adapted?.rewrites??[],rawImports:imports.imports,packagedImports:WebAssembly.Module.imports(module),exports:WebAssembly.Module.exports(module)};
await writeFile(join(out,'module.json'),JSON.stringify(info,null,2)+'\n');await writeFile(join(out,'app.json'),JSON.stringify({schemaVersion:1,id:'dev.ming.frame.'+label,version:'0.1.0',sdkVersion:4,entry:'main.wasm'},null,2)+'\n');
await writeFile(join(out,'Mode.kt'),await readFile(join(probe,'src/wasmWasiMain/kotlin/Mode.kt')));await writeFile(join(out,'Main.kt'),await readFile(join(probe,'src/wasmWasiMain/kotlin/Main.kt')));console.log(JSON.stringify(info,null,2));if(error)process.exitCode=1;
