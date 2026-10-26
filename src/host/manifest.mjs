import {openSync,closeSync,fstatSync,readSync,lstatSync,realpathSync,constants} from 'node:fs';
import {dirname,join,relative,isAbsolute} from 'node:path';

export function readBounded(file,maxBytes,label) {
  const info=lstatSync(file);
  if(info.isSymbolicLink())throw new Error(label+'_SYMLINK_DENIED');
  if(!info.isFile())throw new Error(label+'_NOT_FILE');
  const fd=openSync(file,constants.O_RDONLY|(constants.O_NOFOLLOW??0)|(constants.O_NONBLOCK??0));
  try {
    const stat=fstatSync(fd);if(!stat.isFile())throw new Error(label+'_NOT_FILE');
    if(stat.size>maxBytes)throw new Error(label+'_TOO_LARGE');
    const bytes=Buffer.alloc(stat.size);let read=0;
    while(read<bytes.length){const n=readSync(fd,bytes,read,bytes.length-read,read);if(!n)throw new Error(label+'_CHANGED');read+=n;}
    return bytes;
  } finally {closeSync(fd);}
}
export function loadApplication(manifestPath,maxModuleBytes) {
  let manifest;
  try {manifest=JSON.parse(readBounded(manifestPath,16384,'MANIFEST').toString('utf8'));}
  catch(e){if(e.message.startsWith('MANIFEST_'))throw e;throw new Error('MANIFEST_INVALID: '+e.message);}
  if(!manifest||Array.isArray(manifest)||typeof manifest!=='object')throw new Error('MANIFEST_INVALID');
  const fields=['schemaVersion','id','version','sdkVersion','entry'];
  if(Object.keys(manifest).some(k=>!fields.includes(k)))throw new Error('MANIFEST_UNKNOWN_FIELD');
  if(manifest.schemaVersion!==1||manifest.sdkVersion!==1)throw new Error('MANIFEST_UNSUPPORTED_VERSION');
  if(typeof manifest.id!=='string'||!(/^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$/).test(manifest.id)||manifest.id.length>128)throw new Error('MANIFEST_INVALID_ID');
  if(typeof manifest.version!=='string'||!(/^\d+\.\d+\.\d+$/).test(manifest.version))throw new Error('MANIFEST_INVALID_VERSION');
  if(typeof manifest.entry!=='string'||!(/^[A-Za-z0-9][A-Za-z0-9._-]*\.wasm$/).test(manifest.entry))throw new Error('MANIFEST_INVALID_ENTRY');
  const dir=realpathSync(dirname(manifestPath));const entry=join(dir,manifest.entry);
  if(lstatSync(entry).isSymbolicLink())throw new Error('MANIFEST_ENTRY_SYMLINK_DENIED');
  const rel=relative(dir,realpathSync(entry));
  if(rel.startsWith('..')||isAbsolute(rel))throw new Error('MANIFEST_ENTRY_ESCAPE');
  return {manifest,bytes:readBounded(entry,maxModuleBytes,'MODULE')};
}
