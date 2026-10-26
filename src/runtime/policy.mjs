export const SDK_ARITIES=Object.freeze({file_open:1,file_length:1,file_read_byte:2,file_close:1,log:2});
export function validateModule(module) {
  for(const entry of WebAssembly.Module.imports(module)) {
    if(entry.kind!=='function'||entry.module!=='ming_v1'||!Object.hasOwn(SDK_ARITIES,entry.name))
      throw new Error('IMPORT_DENIED: '+entry.module+'.'+entry.name+' ('+entry.kind+')');
  }
  if(!WebAssembly.Module.exports(module).some(e=>e.name==='_start'&&e.kind==='function'))
    throw new Error('ENTRYPOINT_MISSING: _start');
}
