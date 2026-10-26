import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {dirname} from 'node:path';
import {ResourceBroker} from './broker.mjs';
import {loadApplication} from './manifest.mjs';

const runner=fileURLToPath(new URL('../runtime/runner.mjs',import.meta.url));

export async function runApp({manifestPath,grants={},timeoutMs=5000,maxModuleBytes=4194304,maxRequests=32768}={}) {
  for(const [name,n,max] of [['timeoutMs',timeoutMs,60000],['maxModuleBytes',maxModuleBytes,16777216],['maxRequests',maxRequests,1000000]])
    if(!Number.isSafeInteger(n)||n<1||n>max)throw new Error('INVALID_LIMIT: '+name);
  const {manifest,bytes}=loadApplication(manifestPath,maxModuleBytes);
  const broker=ResourceBroker.create(grants);
  try {
    return await new Promise((resolve,reject)=>{
      let child,failure=null,finished=false,buffer='',stderr='',requests=0,lastId=0,closed=false;
      const fail=error=>{if(!failure)failure=error instanceof Error?error:new Error(error);if(child&&!closed)child.kill('SIGKILL');};
      try {
        child=spawn(process.execPath,['--permission','--allow-fs-read='+dirname(runner),'--no-addons','--disable-proto=throw','--max-old-space-size=96',runner],{
          stdio:['pipe','pipe','pipe'],windowsHide:true,
          env:process.platform==='win32'?{SystemRoot:process.env.SystemRoot??''}:{}
        });
      } catch(e){reject(e);return;}
      const timer=setTimeout(()=>fail(new Error('TIMEOUT: application exceeded '+timeoutMs+'ms')),timeoutMs);
      child.on('error',fail);child.stdin.on('error',e=>{if(!finished&&!failure)fail(e);});
      child.stderr.setEncoding('utf8');child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-4096);});
      child.stdout.setEncoding('utf8');
      child.stdout.on('data',chunk=>{
        if(failure)return;buffer+=chunk;
        if(buffer.length>8192){fail('PROTOCOL_LIMIT');return;}
        let end;
        while((end=buffer.indexOf('\n'))>=0&&!failure){
          const line=buffer.slice(0,end);buffer=buffer.slice(end+1);
          try {
            const message=JSON.parse(line);
            if(message.type==='result') {
              if(finished||message.exitCode!==0)throw new Error('PROTOCOL_RESULT');
              finished=true;continue;
            }
            if(message.type==='error')throw new Error(message.code+': '+String(message.message).slice(0,256));
            if(finished||message.type!=='call'||!Number.isSafeInteger(message.id)||message.id!==lastId+1)throw new Error('PROTOCOL_INVALID');
            lastId=message.id;if(++requests>maxRequests)throw new Error('REQUEST_LIMIT');
            const value=broker.dispatch(message.method,message.args);
            child.stdin.write(JSON.stringify({id:message.id,value})+'\n');
          } catch(e){fail(e);}
        }
      });
      child.on('close',(code,signal)=>{
        closed=true;clearTimeout(timer);child.stdin.destroy();
        if(failure){reject(failure);return;}
        if(code!==0||signal||!finished||buffer.length){reject(new Error('RUNNER_FAILED: '+(stderr||'exit '+code+' signal '+signal)));return;}
        resolve({appId:manifest.id,events:broker.events,exitCode:0,requests});
      });
      child.stdin.write(JSON.stringify({wasm:bytes.toString('base64')})+'\n');
    });
  } finally {broker.close();}
}
