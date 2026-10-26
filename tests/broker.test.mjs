import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,rmSync,symlinkSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {ResourceBroker} from '../src/host/broker.mjs';

function fixture(t, content='MingOS\n') {
  const dir=mkdtempSync(join(tmpdir(),'ming-broker-'));
  t.after(()=>rmSync(dir,{recursive:true,force:true}));
  const file=join(dir,'granted.txt'); writeFileSync(file,content);
  return {dir,file};
}

test('only explicitly granted resource IDs produce opaque handles',t=>{
  const {file}=fixture(t); const broker=ResourceBroker.create({1:file}); t.after(()=>broker.close());
  const handle=broker.dispatch('file_open',[1]);
  assert.ok(handle>0); assert.equal(broker.dispatch('file_open',[2]),-1);
  assert.equal(broker.dispatch('file_length',[handle]),7);
  assert.equal(broker.dispatch('file_read_byte',[handle,0]),77);
  assert.equal(broker.dispatch('file_read_byte',[handle,6]),10);
});
test('invalid handles, offsets, methods and non-i32 arguments are denied',t=>{
  const {file}=fixture(t); const broker=ResourceBroker.create({1:file}); t.after(()=>broker.close());
  const h=broker.dispatch('file_open',[1]);
  for(const args of [[h,-1],[h,7],[h,1.5],[h,'0'],[h,0,1]]) assert.equal(broker.dispatch('file_read_byte',args),-2);
  assert.equal(broker.dispatch('file_length',[0]),-2);
  assert.equal(broker.dispatch('file_length',[2147483648]),-2);
  assert.equal(broker.dispatch('fs.readFile',['/etc/passwd']),-2);
  assert.equal(broker.dispatch('file_open',[1,2]),-2);
});
test('closing a handle or broker removes access; handles are scoped per broker',t=>{
  const {file}=fixture(t); const a=ResourceBroker.create({1:file}), b=ResourceBroker.create({1:file});
  t.after(()=>{a.close();b.close();}); const h=a.dispatch('file_open',[1]);
  assert.equal(b.dispatch('file_length',[h]),-2);
  assert.equal(a.dispatch('file_close',[h]),0); assert.equal(a.dispatch('file_length',[h]),-2);
  a.close(); assert.equal(a.dispatch('file_open',[1]),-2); a.close();
});
test('resource sizes, counts and logs have limits',t=>{
  const {file}=fixture(t);
  assert.throws(()=>ResourceBroker.create({1:file},{maxFileBytes:2}),/RESOURCE_TOO_LARGE/);
  assert.throws(()=>ResourceBroker.create({0:file}),/INVALID_GRANT/);
  assert.throws(()=>ResourceBroker.create({1:file,2:file},{maxResources:1}),/TOO_MANY_RESOURCES/);
  const broker=ResourceBroker.create({1:file},{maxLogs:1,maxHandles:1}); t.after(()=>broker.close());
  assert.ok(broker.dispatch('file_open',[1])>0); assert.equal(broker.dispatch('file_open',[1]),-4);
  assert.equal(broker.dispatch('log',[1,77]),0); assert.equal(broker.dispatch('log',[2,88]),-4);
  assert.deepEqual(broker.events,[{code:1,value:77}]);
});
test('non-files cannot be granted',t=>{
  const {dir}=fixture(t);
  assert.throws(()=>ResourceBroker.create({1:dir}),/NOT_REGULAR_FILE/);
});
test('final symlinks cannot be granted',t=>{
  const {dir,file}=fixture(t);
  const link=join(dir,'link');
  try{symlinkSync(file,link);}catch(e){if(e.code==='EPERM'){t.skip('Windows does not permit symlink creation in this session');return;}throw e;}
  assert.throws(()=>ResourceBroker.create({1:link}),/SYMLINK_DENIED/);
});
