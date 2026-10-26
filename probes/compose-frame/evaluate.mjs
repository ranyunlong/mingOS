import {readFile,writeFile} from 'node:fs/promises';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const probe=dirname(fileURLToPath(import.meta.url));const root=resolve(probe,'../..');const label=process.argv[2];const out=join(root,'artifacts/compose-frame-qualification',label);
const receipt=JSON.parse(await readFile(join(out,'host.command.json'),'utf8'));const host=JSON.parse(await readFile(join(out,'host.stdout.log'),'utf8'));const events=host.events;const diagnostics=[];const cases=[];
for(let i=0;i<events.length;i++){
 const entry=events[i];if(entry.code===1100){const length=entry.value,chunks=[];for(let n=0;n<Math.ceil(length/4);n++){const e=events[++i];if(e.code!==1101)throw new Error('Diagnostic packet shape');const b=Buffer.alloc(4);b.writeUInt32LE(e.value>>>0);chunks.push(b);}const footer=events[++i];if(footer.code!==1102||footer.value!==length)throw new Error('Diagnostic footer');diagnostics.push(new TextDecoder('utf-8',{fatal:true}).decode(Buffer.concat(chunks).subarray(0,length)));}
 else if(entry.code>=100&&entry.code<400)cases.push(entry);
}
const expected=[101,102,103,104,105,109,106,107,108,201,202,203,204,205,206,207,301,302,303,304,305,306,307,308,309,310,312,311];
const missing=expected.filter(id=>!cases.some(c=>c.code===id));const failed=cases.filter(c=>c.value!==1);const unsupported=events.filter(e=>e.code===1980||e.code===1981);const summary=events.find(e=>e.code===999);
const passed=receipt.exitCode===0&&host.exitCode===0&&missing.length===0&&failed.length===0&&unsupported.length===0&&summary?.value===1;
const result={status:passed?'passed':'failed',hostExitCode:receipt.exitCode,testProgramCompleted:host.exitCode===0,failedCases:failed,missingCases:missing,unsupportedFallbackMarkers:unsupported,diagnostics,observedCases:cases,manualTimeOnly:true,realGuiClockQualified:false,realNativePresentationQualified:false,summary};
await writeFile(join(out,'evaluation.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));if(!passed)process.exitCode=1;
