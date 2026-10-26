import assert from 'node:assert/strict';
import {readFile,writeFile} from 'node:fs/promises';
const host=JSON.parse(await readFile(process.argv[2],'utf8'));assert.equal(host.exitCode,0);const lines=[];let i=0;
while(i<host.events.length&&host.events[i].code===1100){
 const length=host.events[i++].value;assert(length>0&&length<=2048);const chunks=[];
 for(let n=0;n<Math.ceil(length/4);n++){const event=host.events[i++];assert.equal(event.code,1101);const chunk=Buffer.alloc(4);chunk.writeUInt32LE(event.value>>>0);chunks.push(chunk)}
 const footer=host.events[i++];assert.equal(footer.code,1102);assert.equal(footer.value,length);
 lines.push(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks).subarray(0,length)));
}
assert.deepEqual(lines,['Ming 诊断 · error\n','x'.repeat(2047)+'\n']);assert.deepEqual(host.events.slice(i),[{code:1199,value:1}]);
const result={status:'passed',utf8RoundTrip:true,lineByteLengths:lines.map(l=>Buffer.byteLength(l)),oversize2049BytesRejected:true,hostEventCount:host.events.length,hostRuntime:host.runtime};
await writeFile(process.argv[3],JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
