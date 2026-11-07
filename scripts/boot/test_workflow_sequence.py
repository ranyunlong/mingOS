"""Full host coordinator sequencing with an explicit Docker/capability double.
No guest, filesystem image, Linux lock, or actual Docker isolation is proved here.
"""
import copy,importlib.util,json,pathlib,tempfile,unittest
HERE=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('outer',HERE/'workflow_outer.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class FakeDocker:
 def __init__(self):
  self.calls=[];self.containers={};self.volumes={};self.sequence=0;self.normal=0;self.generation='00000000-0000-4000-8000-000000000001';self.prior=None;self.fail=None;self.gates=[];self.ready=True;self.desktop_ready=True
  self.volume('pack',{});self.inner=self.report()
 def volume(self,name,labels):
  self.volumes[name]={'Name':name,'Driver':'local','Scope':'local','CreatedAt':'2026-11-07T00:00:00Z','Mountpoint':'/docker/'+name,'Options':None,'Labels':labels}
 def report(self):return {'status':'workflow-instance-ready','generation':self.generation,'normalBoots':self.normal,'instanceSha256':'1'*64,'diskSha256':str((self.normal+2)%10)*64,'historyTipSha256':str((self.normal+3)%10)*64 if self.normal else None,'priorContainerId':self.prior}
 def __call__(self,args,label,input=None,timeout=180):
  self.calls.append((list(args),label,input));args=list(args)
  if self.fail and self.fail==label:raise RuntimeError('injected Docker failure')
  op=args[0]
  if op=='image':return m.encoded([{'Id':m.IMAGE,'RootFS':{'Layers':['layer']}}])
  if op=='volume':
   if args[1]=='ls':return ('\n'.join(self.volumes)+'\n').encode()
   if args[1]=='inspect':return m.encoded([self.volumes[args[2]]])
   labels=dict(args[i+1].split('=',1) for i,v in enumerate(args) if v=='--label');self.volume(args[-1],labels);return (args[-1]+'\n').encode()
  if op=='create':
   one=lambda flag:args[args.index(flag)+1]
   self.sequence+=1;cid=f'{self.sequence:064x}';labels=dict(args[i+1].split('=',1) for i,v in enumerate(args) if v=='--label');mounts=[];effective=[]
   for i,v in enumerate(args):
    if v!='--mount':continue
    fields=dict(x.split('=',1) for x in args[i+1].split(',') if '=' in x);ro='readonly' in args[i+1].split(',')
    mounts.append({'Type':fields['type'],'Source':fields['source'],'Target':fields['target'],'ReadOnly':ro,**({'VolumeOptions':{'NoCopy':True}} if fields['type']=='volume' else {})})
    effective.append({'Type':fields['type'],'Destination':fields['target'],'RW':not ro,**({'Name':fields['source']} if fields['type']=='volume' else {'Source':fields['source']})})
   tmpfs={args[i+1].split(':',1)[0]:args[i+1].split(':',1)[1] for i,v in enumerate(args) if v=='--tmpfs'}
   item={'Id':cid,'Name':'/'+one('--name'),'Image':m.IMAGE,'Config':{'Image':m.IMAGE,'User':one('--user'),'Entrypoint':None,'Cmd':args[args.index(m.IMAGE)+1:],'Labels':labels},'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'NanoCpus':2000000000,'Memory':4294967296,'PidsLimit':256,'CapDrop':['CAP_ALL'],'CapAdd':['CAP_CHOWN'] if '--cap-add' in args else [],'SecurityOpt':['no-new-privileges'],'Init':True,'AutoRemove':False,'RestartPolicy':{'Name':'no','MaximumRetryCount':0},'Tmpfs':tmpfs,'Mounts':mounts,'Binds':None,'VolumesFrom':None,'Devices':[],'DeviceRequests':None,'Links':None,'PortBindings':{},'ExtraHosts':None},'Mounts':effective,'State':{'Status':'created','Running':False,'Restarting':False,'Paused':False,'Dead':False,'OOMKilled':False,'ExitCode':0}}
   self.containers[cid]={'inspect':item,'op':labels['org.mingos.operation'],'logs':None,'gate':False};return (cid+'\n').encode()
  if op=='inspect':return m.encoded([self.containers[cid]['inspect'] for cid in args[1:]])
  if op=='ps':return ('\n'.join(self.containers)+'\n').encode()
  if op=='start':self.containers[args[1]]['inspect']['State'].update(Status='running',Running=True);return (args[1]+'\n').encode()
  if op=='exec':
   cid=args[args.index('--user')+2];c=self.containers[cid]
   if input is not None:
    if 'current-container-id' in args[-1]:
     assert input==(cid+'\n').encode();c['handshake']=True
    else:
     r=json.loads(input);assert r['currentContainerId']==cid and r['requestSha256']==m.digest(m.encoded(self.request(cid)));c['gate']=True;c['gateReceipt']=r;self.gates.append(r)
    return b''
   if 'transition-request.json' in args[-1]:return m.encoded(self.request(cid))
   if 'control.json' in args[-1] and 'ready' in args[-1]:return m.encoded({'ready':self.ready})
   pos=args.index('--control');command=args[pos+2]
   if command=='power':
    self.normal+=1;self.prior=cid;self.inner=self.report();c['logs']=m.encoded(self.inner);c['inspect']['State'].update(Status='exited',Running=False)
   return m.encoded({'status':('ready' if self.desktop_ready else 'starting') if command=='status' else 'accepted','active':False,'state':{'phase':'stopped' if command=='power' else 'ready'} if self.desktop_ready else None})
  if op=='wait':
   c=self.containers[args[1]];operation=c['op']
   if operation in ('reopen','backup'):assert c['gate'],'missing lock-bound transition receipt'
   if operation=='init':self.inner=self.report();c['logs']=m.encoded(self.inner)
   elif operation=='restore':
    self.generation='00000000-0000-4000-8000-000000000002';self.inner=self.report();self.inner['instanceSha256']='9'*64;c['logs']=m.encoded(self.inner)
   elif operation=='backup':c['logs']=m.encoded({'schemaVersion':1,'status':'workflow-backup-complete','backupSha256':m.digest(m.encoded(self.manifest(args[1]))),'normalBoots':self.normal,'generation':self.inner['generation'],'guestBooted':False,'diagnosticOnly':True,'instrumentedQualification':False,'formalQualification':False,'finalSystemAccepted':False})
   elif operation=='inspect':c['logs']=m.encoded(self.inner)
   c['inspect']['State'].update(Status='exited',Running=False);return b'0\n'
  if op=='logs':return self.containers[args[1]]['logs'] or b''
  if op=='cp':pathlib.Path(args[-1]).write_bytes(m.encoded(self.manifest(args[1].split(':')[0])));return b''
  raise AssertionError(args)
 def request(self,cid):
  c=self.containers[cid]
  return {'schemaVersion':1,'profile':m.PROFILE,'operation':c['op'],'nonce':'a'*32,'generation':self.inner['generation'],'instanceSha256':self.inner['instanceSha256'],'historyTipSha256':self.inner['historyTipSha256'],'diskSha256':self.inner['diskSha256'],'priorContainerId':self.prior,'currentContainerId':cid,'runtimeNonce':'b'*32}
 def manifest(self,cid):
  return {'schemaVersion':1,'profile':m.PROFILE,'status':'workflow-backup-complete',**{k:self.inner[k] for k in ('generation','normalBoots','instanceSha256','diskSha256','historyTipSha256')},'files':{'workflow-instance.json':{'bytes':1,'sha256':'a'*64,'mode':0o600}},'directories':{},'transition':{'request':self.request(cid),'receipt':self.containers[cid]['gateReceipt']},'diagnosticOnly':True,'instrumentedQualification':False,'formalQualification':False,'finalSystemAccepted':False}

class Sequence(unittest.TestCase):
 def setup_source(self,p):
  source=p/'source';source.mkdir()
  for name in ('scripts/boot/workflow_vm.py','scripts/boot/workflow_pack.py','scripts/boot/workflow_cli.py','scripts/boot/workflow_control.py','scripts/boot/vm.py','probes/linux-isolation-source-v2/profile.json'):
   target=source/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'# explicit source double\n')
  return source
 def test_new_start_power_reopen_power_backup_restore_reopen(self):
  with tempfile.TemporaryDirectory() as temp:
   p=pathlib.Path(temp);source=self.setup_source(p);fake=FakeDocker();root=p/'instance'
   m.new_instance(root,source,'pack','a'*64,call=fake)
   def run(where,op,command=None):return m.Coordinator(where,fake).run(op,command)
   first=run(root,'start');self.assertEqual(first['index'],1)
   for op in ('status','capture','click','key','type'):
    cmd={'op':op,**({'x':10,'y':20} if op=='click' else {'qcode':'a'} if op=='key' else {'text':'RememberA'} if op=='type' else {})};run(root,op,cmd)
   run(root,'power',{'op':'power','x':10,'y':20})
   stopped=run(root,'status');self.assertEqual(stopped['status'],'workflow-not-running');self.assertFalse(list(root.glob('*/failed.json')))
   second=run(root,'reopen');self.assertEqual(second['index'],2);self.assertNotEqual(first['containerId'],second['containerId'])
   run(root,'power',{'op':'power','x':10,'y':20});backup=run(root,'offlinebackup');self.assertEqual(backup['status'],'offline-backup-complete')
   receipt=next(root.glob('offlinebackup-*/backup.json'));pin=m.digest(receipt.read_bytes());restored=p/'restored'
   m.new_instance(restored,source,'pack','a'*64,restore_receipt=receipt,restore_receipt_pin=pin,call=fake)
   third=run(restored,'reopen');self.assertEqual(third['index'],3)
   self.assertEqual(len(fake.gates),3)
   for gate in fake.gates:self.assertEqual(gate['activeWritableContainerIds'],[gate['currentContainerId']])
   self.assertIn(backup['volume'],m.Coordinator(root,fake).state['volumeIdentities'])
 def test_failed_create_retains_generation_and_next_call_is_not_retried(self):
  with tempfile.TemporaryDirectory() as temp:
   p=pathlib.Path(temp);source=self.setup_source(p);fake=FakeDocker();root=p/'instance';fake.fail='container-create'
   with self.assertRaises(RuntimeError):m.new_instance(root,source,'pack','a'*64,call=fake)
   self.assertTrue((root/'new/failed.json').is_file());calls=len(fake.calls)
   with self.assertRaises(ValueError):m.new_instance(root,source,'pack','a'*64,call=fake)
   self.assertEqual(len(fake.calls),calls)
 def test_readonly_not_ready_is_observation_and_never_submits_or_latches(self):
  with tempfile.TemporaryDirectory() as temp:
   p=pathlib.Path(temp);source=self.setup_source(p);fake=FakeDocker();root=p/'instance';m.new_instance(root,source,'pack','a'*64,call=fake)
   m.Coordinator(root,fake).run('start');fake.ready=False;before=len(fake.calls)
   result=m.Coordinator(root,fake).run('status');self.assertEqual(result['status'],'workflow-not-ready');self.assertFalse(result['inputSent'])
   self.assertFalse(list(root.glob('*/failed.json')))
   self.assertFalse(any('--control' in args for args,_,_ in fake.calls[before:]))
 def test_endpoint_ready_but_desktop_starting_does_not_latch_or_type(self):
  with tempfile.TemporaryDirectory() as temp:
   p=pathlib.Path(temp);source=self.setup_source(p);fake=FakeDocker();root=p/'instance';m.new_instance(root,source,'pack','a'*64,call=fake);m.Coordinator(root,fake).run('start')
   fake.desktop_ready=False;before=len(fake.calls)
   result=m.Coordinator(root,fake).run('type',{'op':'type','text':'hello'});self.assertEqual(result['status'],'workflow-not-ready');self.assertFalse(result['inputSent'])
   cli=[args[args.index('--control')+2] for args,_,_ in fake.calls[before:] if '--control' in args]
   self.assertEqual(cli,['status']);self.assertFalse(list(root.glob('*/failed.json')))
 def test_invalid_input_is_rejected_before_claiming_or_latching_operation(self):
  with tempfile.TemporaryDirectory() as temp:
   p=pathlib.Path(temp);source=self.setup_source(p);fake=FakeDocker();root=p/'instance';m.new_instance(root,source,'pack','a'*64,call=fake);before=set(root.iterdir());count=len(fake.calls)
   for command in ({'op':'type','text':'hello.'},{'op':'key','qcode':'minus'}):
    with self.assertRaises(ValueError):m.Coordinator(root,fake).run(command['op'],command)
   self.assertEqual(set(root.iterdir()),before);self.assertEqual(len(fake.calls),count)
 def test_status_after_init_before_first_start_is_nonfatal_observation(self):
  with tempfile.TemporaryDirectory() as temp:
   p=pathlib.Path(temp);source=self.setup_source(p);fake=FakeDocker();root=p/'instance';m.new_instance(root,source,'pack','a'*64,call=fake)
   result=m.Coordinator(root,fake).run('status');self.assertEqual(result['status'],'workflow-not-started');self.assertFalse(result['inputSent']);self.assertFalse(list(root.glob('*/failed.json')))
   self.assertEqual(m.Coordinator(root,fake).run('start')['index'],1)

if __name__=='__main__':unittest.main()
