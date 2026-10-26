import {resolve} from 'node:path';
import {runApp} from './host/app-host.mjs';
const usage='USAGE: node src/cli.mjs run <app.json> [--grant <id>=<path>] [--timeout-ms <1..60000>]';
try {
  const [command,manifest,...args]=process.argv.slice(2);
  if(command!=='run'||!manifest)throw new Error(usage);
  const grants={};let timeoutMs=5000,hasTimeout=false;
  for(let i=0;i<args.length;i+=2) {
    const option=args[i],value=args[i+1];
    if(!value)throw new Error('INVALID_OPTION: '+option);
    if(option==='--grant') {
      const separator=value.indexOf('=');const key=value.slice(0,separator),file=value.slice(separator+1);
      if(separator<1||!(/^[1-9][0-9]*$/).test(key)||Number(key)>65535||!file)throw new Error('INVALID_GRANT');
      if(Object.hasOwn(grants,key))throw new Error('DUPLICATE_GRANT');grants[key]=resolve(file);
    } else if(option==='--timeout-ms') {
      if(hasTimeout)throw new Error('DUPLICATE_TIMEOUT');hasTimeout=true;
      if(!(/^[0-9]+$/).test(value))throw new Error('INVALID_TIMEOUT');timeoutMs=Number(value);
    } else throw new Error('INVALID_OPTION: '+option);
  }
  const result=await runApp({manifestPath:resolve(manifest),grants,timeoutMs});
  console.log(JSON.stringify(result,null,2));
} catch(e){console.error(e.message);process.exitCode=1;}
