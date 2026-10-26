import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {adaptKotlinWasi} from '../../../scripts/wasm-sdk-adapter.mjs';
const probe=dirname(fileURLToPath(import.meta.url));const root=resolve(probe,'../../..');const out=join(root,'artifacts/compose-qualification/port');
const [label,moduleName]=process.argv.slice(2);
async function walk(dir){let result=[];for(const e of await readdir(dir,{withFileTypes:true})){const full=join(dir,e.name);if(e.isDirectory())result.push(...await walk(full));else if(e.name.endsWith('.wasm'))result.push(full);}return result;}
const files=(await walk(join(probe,moduleName,'build'))).filter(f=>f.includes('productionExecutable')&&f.includes('compileSync'));if(files.length!==1)throw new Error('Expected one production module: '+JSON.stringify(files));
const bytes=await readFile(files[0]);const target=join(out,label);await mkdir(target,{recursive:true});await writeFile(join(target,'raw.wasm'),bytes);
const sha=b=>createHash('sha256').update(b).digest('hex');const module=await WebAssembly.compile(bytes);const imports=WebAssembly.Module.imports(module);const exports=WebAssembly.Module.exports(module);let adapted;let adapterError=null;
try{adapted=adaptKotlinWasi(bytes)}catch(error){adapterError=String(error)}
// Always package the actual bytes: on rejection, let the unchanged Ming host deny them.
const actual=adapted?.bytes??bytes;await writeFile(join(target,'main.wasm'),actual);
await writeFile(join(target,'app.json'),JSON.stringify({schemaVersion:1,id:'dev.ming.compose.'+label.replaceAll('_','-'),version:'0.1.0',sdkVersion:4,entry:'main.wasm'},null,2)+'\n');
const info={label,moduleName,sourceWasm:files[0],rawBytes:bytes.length,rawSha256:sha(bytes),packagedSha256:sha(actual),imports,exports,rewrites:adapted?.rewrites??[],adapterError,packagedImports:WebAssembly.Module.imports(await WebAssembly.compile(actual))};await writeFile(join(target,'module.json'),JSON.stringify(info,null,2)+'\n');console.log(JSON.stringify(info,null,2));
