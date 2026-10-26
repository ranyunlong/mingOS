import {readFile,writeFile,readdir,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {join,resolve,dirname,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
const probe=dirname(fileURLToPath(import.meta.url));
const root=resolve(probe,'../..');const out=join(root,'artifacts/compose-qualification');
const sha=b=>createHash('sha256').update(b).digest('hex');
async function walk(dir){const result=[];for(const e of await readdir(dir,{withFileTypes:true})){const full=join(dir,e.name);if(e.isDirectory())result.push(...await walk(full));else result.push(full);}return result;}
const downloads=await walk(join(out,'downloads'));
const artifacts=[];const modules=[];
for(const file of downloads){
 const bytes=await readFile(file);artifacts.push({file:relative(out,file).replaceAll('\\','/'),bytes:bytes.length,sha256:sha(bytes)});
 if(file.endsWith('.module')){
  const value=JSON.parse(bytes.toString());modules.push({file:relative(out,file),component:value.component,variants:value.variants.map(v=>({name:v.name,attributes:v.attributes,availableAt:v['available-at'],dependencies:v.dependencies,files:v.files})),wasiVariants:value.variants.filter(v=>v.attributes?.['org.jetbrains.kotlin.wasm.target']==='wasi').map(v=>v.name)});
 }
}
await writeFile(join(out,'download-hashes.json'),JSON.stringify(artifacts,null,2)+'\n');
await writeFile(join(out,'module-analysis.json'),JSON.stringify(modules,null,2)+'\n');
const src=join(out,'official-sources/androidx-runtime-1.10.0-wasm-js');
const sourceFiles=await walk(src);const expects=[];const actuals=[];const platformFiles=[];const sourceHashes=[];
for(const file of sourceFiles){
 const bytes=await readFile(file);const rel=relative(src,file).replaceAll('\\','/');sourceHashes.push({file:rel,sha256:sha(bytes),bytes:bytes.length});
 if(!file.endsWith('.kt'))continue;
 const lines=bytes.toString().split('\n');
 const matches=pattern=>lines.flatMap((line,index)=>pattern.test(line)?[{line:index+1,text:line.trim()}]:[]);
 const expected=matches(/^\s*(?:(?:public|private|internal|protected)\s+)?expect\s+(?:(?:inline|operator|infix|suspend|annotation|value)\s+)*(?:class|interface|object|fun|val|var)\b/);if(expected.length)expects.push({file:rel,declarations:expected});
 const implemented=matches(/\bactual\b/);if(implemented.length)actuals.push({file:rel,declarations:implemented});
 if(!rel.startsWith('commonMain/'))platformFiles.push({file:rel,imports:matches(/^import /),platformBindings:matches(/\bjs\(|\bexternal\b|requestAnimationFrame|WeakMap|kotlinx\.atomicfu|actual (fun|val|class|interface|typealias)/)});
}
await writeFile(join(out,'source-inventory.json'),JSON.stringify({sourceArchive:'downloads/dl.google.com/dl/android/maven2/androidx/compose/runtime/runtime-wasm-js/1.10.0/runtime-wasm-js-1.10.0-sources.jar',counts:{kotlinFiles:sourceFiles.filter(f=>f.endsWith('.kt')).length,expectFiles:expects.length,actualFiles:actuals.length,platformFiles:platformFiles.length},expects,actuals,platformFiles},null,2)+'\n');
await writeFile(join(out,'official-source-hashes.json'),JSON.stringify(sourceHashes,null,2)+'\n');
console.log(JSON.stringify({downloadedArtifacts:artifacts.length,metadataModules:modules.length,wasiVariants:modules.map(m=>({component:m.component,names:m.wasiVariants})),sourceFiles:sourceFiles.length,expectFiles:expects.length,actualFiles:actuals.length,platformFiles:platformFiles.length},null,2));
