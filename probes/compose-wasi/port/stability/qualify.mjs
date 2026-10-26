import {spawn} from 'node:child_process';
import {mkdir,readFile,writeFile,readdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,dirname,join,relative} from 'node:path';
import {fileURLToPath} from 'node:url';
const moduleRoot=dirname(fileURLToPath(import.meta.url));
const probe=resolve(moduleRoot,'..');
const root=resolve(probe,'../../..');
const out=join(root,'artifacts/compose-qualification/port/stability');
await mkdir(out,{recursive:true});
const mode=process.argv[2];
const javaHome=process.env.COMPOSE_PROBE_JAVA_HOME || 'C:/Program Files/Android/Android Studio/jbr';
const java=join(javaHome,'bin/java.exe');
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
async function run(label,command,args,env=process.env){
 const startedAt=new Date().toISOString();
 const inputHashes={};for(const file of ['settings.gradle.kts','build.gradle.kts','gradle.properties','src/wasmWasiMain/kotlin/Main.kt']) inputHashes[file]=sha(await readFile(join(probe,file)));
 async function hashSources(dir){for(const entry of await readdir(dir,{withFileTypes:true})){if(['build','builds','.gradle','.kotlin'].includes(entry.name))continue;const file=join(dir,entry.name);if(entry.isDirectory())await hashSources(file);else inputHashes[relative(probe,file).replaceAll('\\','/')]=sha(await readFile(file));}}
 await hashSources(probe);
 const chunks={stdout:[],stderr:[]};
 console.log(JSON.stringify({label,command,args,startedAt}));
 const exitCode=await new Promise((resolveExit,reject)=>{
  const child=spawn(command,args,{cwd:root,env,windowsHide:true});
  child.stdout.on('data',data=>{chunks.stdout.push(data);process.stdout.write(data)});
  child.stderr.on('data',data=>{chunks.stderr.push(data);process.stderr.write(data)});
  child.on('error',reject);child.on('exit',code=>resolveExit(code??1));
 });
 for(const stream of ['stdout','stderr']) await writeFile(join(out,label+'.'+stream+'.log'),Buffer.concat(chunks[stream]));
 const receipt={label,command,args,cwd:root,startedAt,finishedAt:new Date().toISOString(),exitCode,inputHashes,environment:{JAVA_HOME:javaHome,GRADLE_USER_HOME:join(root,'.cache/gradle'),node:process.version},stdoutSha256:sha(Buffer.concat(chunks.stdout)),stderrSha256:sha(Buffer.concat(chunks.stderr))};
 await writeFile(join(out,label+'.command.json'),JSON.stringify(receipt,null,2)+'\n');
 return exitCode;
}
if(mode==='env'){
 await writeFile(join(out,'jdk-release.txt'),await readFile(join(javaHome,'release')));
 const versionStatus=await run('java-version',java,['-version']);
 if(versionStatus) process.exit(versionStatus);
}
if(mode==='env'||mode==='gradle'){
 const args=mode==='env'?['--version']:process.argv.slice(4);
 const label=mode==='env'?'gradle-version':process.argv[3];
 const env={...process.env,JAVA_HOME:javaHome,GRADLE_USER_HOME:join(root,'.cache/gradle'),JAVA_TOOL_OPTIONS:'-Djdk.net.unixdomain.tmpdir="'+join(root,'.cache/java-tmp')+'"'};
 process.exitCode=await run(label,java,['-classpath',join(root,'gradle/wrapper/gradle-wrapper.jar'),'org.gradle.wrapper.GradleWrapperMain','-p',probe,'--project-cache-dir',join(moduleRoot,'.gradle'),'--init-script',join(moduleRoot,'include.init.gradle'),'--no-daemon','--console=plain',...args],env);
}else if(mode==='exec'){
 process.exitCode=await run(process.argv[3],process.argv[4],process.argv.slice(5));
}else if(mode==='fetch'){
 const urls=process.argv.slice(3);const results=[];const stdout=[];const stderr=[];
 const label='fetch-'+Date.now();const startedAt=new Date().toISOString();
 for(const url of urls){
  const parsed=new URL(url);const filename='downloads/'+parsed.hostname+parsed.pathname;
  try {
   const response=await fetch(url);const data=Buffer.from(await response.arrayBuffer());
   const result={url,status:response.status,bytes:data.length,sha256:sha(data),fetchedAt:new Date().toISOString(),file:filename};results.push(result);
   await mkdir(dirname(join(out,filename)),{recursive:true});await writeFile(join(out,filename),data);
   const line=JSON.stringify(result)+'\n';stdout.push(line);process.stdout.write(line);
  }catch(error){const line=JSON.stringify({url,error:String(error),cause:String(error.cause)})+'\n';stderr.push(line);process.stderr.write(line);results.push({url,error:String(error)});}
 }
 await writeFile(join(out,label+'.json'),JSON.stringify(results,null,2)+'\n');
 await writeFile(join(out,label+'.stdout.log'),stdout.join(''));await writeFile(join(out,label+'.stderr.log'),stderr.join(''));
 const exitCode=results.some(result=>result.status!==200)?1:0;
 await writeFile(join(out,label+'.command.json'),JSON.stringify({command:process.execPath,args:[fileURLToPath(import.meta.url),...process.argv.slice(2)],cwd:root,startedAt,finishedAt:new Date().toISOString(),exitCode,stdoutSha256:sha(stdout.join('')),stderrSha256:sha(stderr.join(''))},null,2)+'\n');
 process.exitCode=exitCode;
}else if(mode!=='env')throw new Error('Expected env, gradle LABEL TASKS..., or fetch URLS...');
