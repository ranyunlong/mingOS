import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

// Read-only core Wasm GC type/import reflection. It never rewrites or executes code.
export function inspectImports(bytes) {
 if(!Buffer.from(bytes).subarray(0,8).equals(Buffer.from([0,97,115,109,1,0,0,0])))throw new Error('Invalid core Wasm header');
 let at=8;const types=[];let imports=[];
 const byte=()=>{if(at>=bytes.length)throw new Error('Unexpected EOF');return bytes[at++]};
 const uint=()=>{let n=0;for(let i=0;i<5;i++){const b=byte();n+=(b&127)*2**(7*i);if(!(b&128))return n;}throw new Error('Invalid u32 LEB')};
 const sint=()=>{let n=0,shift=0,b;do{b=byte();n+=(b&127)*2**shift;shift+=7;if(shift>35)throw new Error('Invalid signed LEB')}while(b&128);return b&64?n-2**shift:n};
 const text=()=>{const n=uint();const end=at+n;if(end>bytes.length)throw new Error('Invalid name length');const s=new TextDecoder('utf-8',{fatal:true}).decode(bytes.subarray(at,end));at=end;return s};
 const value=()=>{const t=byte();const names={127:'i32',126:'i64',125:'f32',124:'f64',123:'v128',120:'i8',119:'i16',112:'funcref',111:'externref',110:'anyref',109:'eqref',108:'i31ref',107:'structref',106:'arrayref',105:'exnref',113:'nullref',114:'nullexternref',115:'nullfuncref'};if(t===99||t===100)return (t===99?'ref null ':'ref ')+sint();if(names[t])return names[t];throw new Error('Unknown value type 0x'+t.toString(16))};
 const vector=read=>{const n=uint();return Array.from({length:n},read)};
 const field=()=>{const type=value();const mut=byte();if(mut>1)throw new Error('Invalid field mutability');return {type,mutable:!!mut}};
 const subtype=()=>{let tag=byte();if(tag===79||tag===80){vector(uint);tag=byte()};let type;
  if(tag===96)type={kind:'function',params:vector(value),results:vector(value)};
  else if(tag===95)type={kind:'struct',fields:vector(field)};
  else if(tag===94)type={kind:'array',field:field()};
  else throw new Error('Unknown composite type 0x'+tag.toString(16));
  types.push(type);
 };
 while(at<bytes.length){const id=byte();const length=uint();const end=at+length;if(end>bytes.length)throw new Error('Invalid section length');
  if(id===1){const n=uint();for(let i=0;i<n;i++){if(bytes[at]===78){at++;const count=uint();for(let j=0;j<count;j++)subtype()}else subtype()}}
  else if(id===2){const n=uint();for(let i=0;i<n;i++){const module=text(),name=text(),kind=byte();if(kind!==0)throw new Error('Qualification expects only function imports');const typeIndex=uint();const type=types[typeIndex];if(type?.kind!=='function')throw new Error('Import does not reference a function type');imports.push({module,name,kind:'function',typeIndex,params:type.params,results:type.results})}}
  else {at=end;continue}
  if(at!==end)throw new Error('Section parser did not consume its exact length: '+id);
 }
 return {typeCount:types.length,imports};
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const bytes=await readFile(process.argv[2]);const compiled=await WebAssembly.compile(bytes);const result=inspectImports(bytes);
 const reflected=WebAssembly.Module.imports(compiled);if(JSON.stringify(result.imports.map(({module,name,kind})=>({module,name,kind})))!==JSON.stringify(reflected))throw new Error('Native import reflection disagrees');
 const report={file:resolve(process.argv[2]),sha256:createHash('sha256').update(bytes).digest('hex'),bytes:bytes.length,...result,exports:WebAssembly.Module.exports(compiled)};
 if(process.argv[3])await writeFile(process.argv[3],JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));
}
