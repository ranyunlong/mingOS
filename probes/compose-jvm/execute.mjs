import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,dirname,join,delimiter} from 'node:path';
import {fileURLToPath} from 'node:url';
const probe=dirname(fileURLToPath(import.meta.url));const root=resolve(probe,'../..');
const out=join(root,'artifacts/compose-jvm-qualification');
const artifact=JSON.parse(await readFile(join(out,'artifacts.json'),'utf8'));
const javaHome=process.env.COMPOSE_PROBE_JAVA_HOME||'C:/Program Files/Android/Android Studio/jbr';
const sha=b=>createHash('sha256').update(b).digest('hex');
const compileLabel=process.argv[2]||'jvm-compile-02';
const suffix=process.argv[3]||'02';
const compilation=JSON.parse(await readFile(join(out,compileLabel+'.command.json'),'utf8'));
if(compilation.exitCode!==0)throw new Error('Compilation did not succeed');
for(const input of [artifact.applicationJar,...artifact.runtimeClasspath]){
 if(sha(await readFile(input.file))!==input.sha256)throw new Error('Artifact hash changed: '+input.file);
}
const classpath=[artifact.applicationJar.file,...artifact.runtimeClasspath.map(a=>a.file)].join(delimiter);
async function capture(label,command,args){
 const startedAt=new Date().toISOString();const stdout=[];const stderr=[];
 let spawnError=null;
 const exitCode=await new Promise(resolveExit=>{
  const child=spawn(command,args,{cwd:root,windowsHide:true});
  child.stdout.on('data',data=>stdout.push(data));child.stderr.on('data',data=>stderr.push(data));
  child.on('error',error=>{spawnError=String(error);resolveExit(1)});child.on('exit',code=>resolveExit(code??1));
 });
 const text=Buffer.concat(stdout);const errors=Buffer.concat(stderr);
 await writeFile(join(out,label+'.stdout.log'),text);await writeFile(join(out,label+'.stderr.log'),errors);
 const receipt={label,command,args,cwd:root,startedAt,finishedAt:new Date().toISOString(),exitCode,spawnError,applicationJarSha256:artifact.applicationJar.sha256,stdoutSha256:sha(text),stderrSha256:sha(errors)};
 await writeFile(join(out,label+'.command.json'),JSON.stringify(receipt,null,2)+'\n');
 console.log(label+': exit '+exitCode);if(label.startsWith('jvm-run')){process.stdout.write(text);process.stderr.write(errors)}
 return {receipt,stdout:text.toString(),stderr:errors.toString()};
}
const outcomes=await Promise.allSettled([
 capture('jvm-run-'+suffix,join(javaHome,'bin/java.exe'),['-Djava.awt.headless=true','-cp',classpath,artifact.mainClass]),
 capture('javap-main-'+suffix,join(javaHome,'bin/javap.exe'),['-p','-c','-s','-verbose','-classpath',artifact.applicationJar.file,artifact.mainClass]),
 capture('jar-list-'+suffix,join(javaHome,'bin/jar.exe'),['--list','--file',artifact.applicationJar.file]),
]);
for(const outcome of outcomes)if(outcome.status==='rejected')throw outcome.reason;
const [run,disassembly,listing]=outcomes.map(o=>o.value);
const line=run.stdout.split(/\r?\n/).find(l=>l.startsWith('COMPOSE_JVM_RESULT '));
const program=line?JSON.parse(line.slice('COMPOSE_JVM_RESULT '.length)):null;
const phases=run.stdout.split(/\r?\n/).filter(l=>l.startsWith('COMPOSE_PHASE ')).map(l=>JSON.parse(l.slice('COMPOSE_PHASE '.length)));
const lowered=disassembly.receipt.exitCode===0&&disassembly.stdout.includes('Counter(qualification.Observations, androidx.compose.runtime.Composer, int)')&&disassembly.stdout.includes('startRestartGroup');
const successful=run.receipt.exitCode===0&&program?.run==='passed'&&lowered&&listing.receipt.exitCode===0;
const result={schemaVersion:1,checkedAt:new Date().toISOString(),compile:{status:'passed',receipt:compileLabel+'.command.json',applicationJar:artifact.applicationJar},run:{status:successful?'passed':'failed',receipt:'jvm-run-'+suffix+'.command.json',program,phases},lowering:{status:lowered?'confirmed':'not_confirmed',receipt:'javap-main-'+suffix+'.command.json',evidence:'Counter receives androidx.compose.runtime.Composer and changed-mask int; bytecode invokes startRestartGroup'},limits:{gui:false,sdkOnlyIsolationQualified:false,sandboxQualified:false,performanceQualified:false,productionRuntimeChanged:false}};
await writeFile(join(out,'result.json'),JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));if(!successful)process.exitCode=1;
