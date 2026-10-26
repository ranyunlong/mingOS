import {readdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../',import.meta.url));
async function check(dir){for(const entry of await readdir(dir,{withFileTypes:true})){const name=join(dir,entry.name);if(entry.isDirectory())await check(name);else if(name.endsWith('.mjs')){const result=spawnSync(process.execPath,['--check',name],{stdio:'inherit'});if(result.status!==0)throw new Error('SYNTAX_CHECK_FAILED: '+name);}}}
for(const dir of ['src','scripts','tests'])await check(join(root,dir));
console.log('All JavaScript syntax checks passed.');
