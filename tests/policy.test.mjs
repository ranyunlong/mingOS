import test from 'node:test';
import assert from 'node:assert/strict';
import {validateModule} from '../src/runtime/policy.mjs';
import {moduleBytes} from './fixtures/wasm.mjs';

test('explicit Ming SDK function imports are accepted',()=>{
  for(const [name,type] of [['file_open',1],['file_length',1],['file_read_byte',2],['file_close',1],['log',2]])
    assert.doesNotThrow(()=>validateModule(new WebAssembly.Module(moduleBytes({imports:[['ming_v1',name,type]]}))));
});
test('WASI, unknown modules, unknown APIs and non-function imports fail closed',()=>{
  for(const imports of [[['wasi_snapshot_preview1','fd_read']],[['env','system']],[['ming_v1','network_open']]])
    assert.throws(()=>validateModule(new WebAssembly.Module(moduleBytes({imports}))),/IMPORT_DENIED/);
  assert.throws(()=>validateModule(new WebAssembly.Module(moduleBytes({memoryImport:true}))),/IMPORT_DENIED/);
});
