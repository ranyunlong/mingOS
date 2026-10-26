// Explicit compiler-runtime adaptation. This is not a generic WASI linker.
const leb=n=>{const out=[];do{let b=n%128;n=Math.floor(n/128);if(n)b|=128;out.push(b);}while(n);return Buffer.from(out);};
const string=s=>{const b=Buffer.from(s);return Buffer.concat([leb(b.length),b]);};
export function adaptKotlinWasi(input){
 const bytes=Buffer.from(input);if(bytes.length<8||bytes.length>4194304||!bytes.subarray(0,8).equals(Buffer.from([0,97,115,109,1,0,0,0])))throw new Error('ADAPTER_INVALID');
 let at=8;const out=[bytes.subarray(0,8)],rewrites=[];
 const uint=end=>{let n=0;for(let i=0;i<5;i++){if(at>=end)throw new Error('ADAPTER_INVALID');const b=bytes[at++];if(i===4&&b>15)throw new Error('ADAPTER_INVALID');n+=(b&127)*2**(7*i);if(!(b&128))return n;}throw new Error('ADAPTER_INVALID');};
 const text=end=>{const n=uint(end);if(n>65536||at+n>end)throw new Error('ADAPTER_INVALID');const result=new TextDecoder('utf-8',{fatal:true}).decode(bytes.subarray(at,at+n));at+=n;return result;};
 while(at<bytes.length){const start=at,id=bytes[at++],length=uint(bytes.length),end=at+length;if(end>bytes.length)throw new Error('ADAPTER_INVALID');
  if(id!==2){out.push(bytes.subarray(start,end));at=end;continue;}
  const count=uint(end);if(count>4096)throw new Error('ADAPTER_INVALID');const entries=[leb(count)];
  for(let i=0;i<count;i++){let module=text(end),name=text(end);if(at>=end)throw new Error('ADAPTER_INVALID');const kind=bytes[at++];if(kind!==0)throw new Error('ADAPTER_IMPORT_KIND');const type=uint(end);
   if(module==='wasi_snapshot_preview1'&&name==='random_get'){rewrites.push({from:module+'.'+name,to:'ming_v2.random_fill'});module='ming_v2';name='random_fill';}
   else if(module.startsWith('wasi'))throw new Error('UNSUPPORTED_RUNTIME_IMPORT: '+module+'.'+name);
   entries.push(string(module),string(name),Buffer.from([kind]),leb(type));
  }if(at!==end)throw new Error('ADAPTER_INVALID');const payload=Buffer.concat(entries);out.push(Buffer.from([2]),leb(payload.length),payload);
 }const result=Buffer.concat(out);if(!WebAssembly.validate(result))throw new Error('ADAPTER_INVALID');return{bytes:result,rewrites};
}
