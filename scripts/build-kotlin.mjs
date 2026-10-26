import {createHash} from 'node:crypto';
import {adaptKotlinWasi} from './wasm-sdk-adapter.mjs';
import {spawn} from 'node:child_process';
import {mkdir,copyFile,readdir,access,readFile,writeFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const requested=process.argv[2]??'read-file';
if(!['read-file','image-viewer','text-demo','ui-demo','shadow-demo','compose-demo','animated-demo','text-entry-demo','desktop','all'].includes(requested))throw new Error('Expected read-file, image-viewer, text-demo, ui-demo, shadow-demo, compose-demo, animated-demo, desktop or all');
const samples=requested==='desktop'?['compose-demo','animated-demo']:requested==='all'?['read-file','image-viewer','text-demo','ui-demo','shadow-demo','compose-demo','animated-demo','text-entry-demo']:[requested];
const root=fileURLToPath(new URL('../',import.meta.url));
const cache=join(root,'.cache');const javaTmp=join(cache,'java-tmp');
await mkdir(javaTmp,{recursive:true});
const wrapper=join(root,'gradle','wrapper','gradle-wrapper.jar');
try {await access(wrapper);}catch{throw new Error('Gradle wrapper is missing; use the checked-in wrapper files.');}
const java=process.env.JAVA_HOME?join(process.env.JAVA_HOME,'bin',process.platform==='win32'?'java.exe':'java'):'java';
const args=['-classpath',wrapper,'org.gradle.wrapper.GradleWrapperMain','--no-daemon','--stacktrace',...samples.map(name=>':'+name+':compileProductionExecutableKotlinWasmWasi')];
const env={...process.env,GRADLE_USER_HOME:join(cache,'gradle')};
// Apply before both JVMs initialize networking; Gradle may filter daemon -D options.
if(process.platform==='win32') {
 const socketOption='-Djdk.net.unixdomain.tmpdir="'+javaTmp+'"';
 env.JAVA_TOOL_OPTIONS=[env.JAVA_TOOL_OPTIONS,socketOption].filter(Boolean).join(' ');
}
const status=await new Promise((resolveStatus,reject)=>{
 const child=spawn(java,args,{cwd:root,env,stdio:'inherit',windowsHide:true});child.on('error',reject);child.on('exit',(code)=>resolveStatus(code??1));
});
if(status!==0)process.exit(status);
async function find(dir){const found=[];for(const entry of await readdir(dir,{withFileTypes:true})){const name=join(dir,entry.name);if(entry.isDirectory())found.push(...await find(name));else if(name.endsWith('.wasm')&&name.includes('productionExecutable'))found.push(name);}return found;}
for(const sample of samples){
const files=await find(join(root,'samples',sample,'build','compileSync'));
if(files.length!==1)throw new Error('Expected exactly one production Kotlin Wasm module for '+sample);
const output=resolve(root,'artifacts',sample);await mkdir(output,{recursive:true});
if(sample==='image-viewer'||sample==='text-demo'||sample==='ui-demo'||sample==='shadow-demo'||sample==='compose-demo'||sample==='animated-demo'||sample==='text-entry-demo'){const raw=await readFile(files[0]);const result=adaptKotlinWasi(raw);await writeFile(join(output,'main.wasm'),result.bytes);const sha=b=>createHash('sha256').update(b).digest('hex');await writeFile(join(output,'build-info.json'),JSON.stringify({kotlin:'2.3.20',adapterVersion:1,rawPath:files[0],rawSha256:sha(raw),packagedSha256:sha(result.bytes),rewrites:result.rewrites},null,2)+'\n');}else{await copyFile(files[0],join(output,'main.wasm'));}
await copyFile(join(root,'samples',sample,'app.json'),join(output,'app.json'));
console.log('Packaged real Kotlin/Wasm application: '+output);
}
