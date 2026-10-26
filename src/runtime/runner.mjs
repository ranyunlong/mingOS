// Trusted bootstrap. Wasm receives functions, never JavaScript/Node objects.
import {readSync,writeSync} from 'node:fs';
import {validateModule,SDK_ARITIES} from './policy.mjs';
let input=Buffer.alloc(0),sequence=0;
function send(message){writeSync(1,JSON.stringify(message)+'\n');}
function readLine(maxBytes) {
  while(true) {
    const end=input.indexOf(10);
    if(end>=0){if(end>maxBytes)throw new Error('PROTOCOL_LIMIT');const line=input.subarray(0,end).toString('utf8');input=input.subarray(end+1);return JSON.parse(line);}
    if(input.length>maxBytes)throw new Error('PROTOCOL_LIMIT');
    const chunk=Buffer.allocUnsafe(8192);const n=readSync(0,chunk,0,chunk.length,null);
    if(!n)throw new Error('HOST_DISCONNECTED');input=Buffer.concat([input,chunk.subarray(0,n)]);
  }
}
function call(method,args) {
  const id=++sequence;send({type:'call',id,method,args});const reply=readLine(1024);
  if(reply.id!==id||!Number.isInteger(reply.value)||reply.value<-2147483648||reply.value>2147483647)throw new Error('PROTOCOL_REPLY');
  return reply.value;
}
let stage='MODULE_INVALID';
try {
  const start=readLine(24000000);const module=new WebAssembly.Module(Buffer.from(start.wasm,'base64'));
  stage='IMPORT_DENIED';validateModule(module);
  const api=Object.fromEntries(Object.keys(SDK_ARITIES).map(name=>[name,(...args)=>call(name,args)]));
  stage='APP_TRAP';const instance=new WebAssembly.Instance(module,{ming_v1:api});
  instance.exports._start();send({type:'result',exitCode:0});
} catch(e) {
  send({type:'error',code:stage,message:String(e.message).slice(0,256)});process.exitCode=1;
}
