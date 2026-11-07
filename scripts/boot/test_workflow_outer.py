import copy, importlib.util, json, os, pathlib, subprocess, sys, tempfile, unittest
HERE=pathlib.Path(__file__).parent
spec=importlib.util.spec_from_file_location('workflow_outer',HERE/'workflow_outer.py')
m=importlib.util.module_from_spec(spec) if spec and (HERE/'workflow_outer.py').exists() else None
if m:spec.loader.exec_module(m)

class Guards(unittest.TestCase):
 def setUp(self):
  self.assertIsNotNone(m,'host coordinator implementation is missing')
 def test_prior_requires_actual_exited_exact_identity(self):
  cid='a'*64
  item={'Id':cid,'State':{'Status':'exited','Running':False,'Restarting':False,'Paused':False,'Dead':False,'OOMKilled':False,'ExitCode':0}}
  m.require_exited(item,cid)
  for key,value in [('Status','running'),('Running',True),('Restarting',True),('Paused',True),('Dead',True),('OOMKilled',True),('ExitCode',1)]:
   bad=copy.deepcopy(item);bad['State'][key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):m.require_exited(bad,cid)
  with self.assertRaises(ValueError):m.require_exited(item,'b'*64)
 def test_scan_rejects_any_other_active_writer(self):
  current='b'*64
  stopped={'Id':'a'*64,'State':{'Status':'exited','Running':False},'Mounts':[{'Type':'volume','Name':'work','RW':True}]}
  own={'Id':current,'State':{'Status':'running','Running':True},'Mounts':[{'Type':'volume','Name':'work','RW':True}]}
  self.assertEqual(m.active_writers([stopped,own],{'work'},current),[current])
  other=copy.deepcopy(own);other['Id']='c'*64
  with self.assertRaises(ValueError):m.active_writers([own,other],{'work'},current)
  for status in ['paused','restarting','created']:
   other['State']={'Status':status,'Running':False}
   with self.subTest(status=status),self.assertRaises(ValueError):m.active_writers([other],{'work'},current)
 def test_control_argv_has_no_arbitrary_command_or_path(self):
  for command in [{'op':'status'},{'op':'capture'},{'op':'click','x':10,'y':20},{'op':'power','x':10,'y':20},{'op':'key','qcode':'a','modifiers':['ctrl']},{'op':'type','text':'RememberA\n'}]:
   args=m.control_argv(command);self.assertIn(command['op'],args);self.assertEqual(args[:3],['/usr/bin/python3','-I','-B'])
  for command in [{'op':'exec','text':'sh'},{'op':'capture','path':'/etc/passwd'},{'op':'click','x':True,'y':0},{'op':'type','text':'a.'},{'op':'type','text':'中文'},{'op':'key','qcode':'minus'},{'op':'key','qcode':'../../sh'}]:
   with self.subTest(command=command),self.assertRaises(ValueError):m.control_argv(command)
 def test_operation_directory_cannot_be_retried(self):
  with tempfile.TemporaryDirectory() as root:
   first=m.claim_operation(pathlib.Path(root),'start');self.assertTrue(first.is_dir())
   m.write(first/'failed.json',{'error':'retained'})
   with self.assertRaises(ValueError):m.claim_operation(pathlib.Path(root),'start')
   self.assertTrue(m.claim_operation(pathlib.Path(root),'reopen').is_dir())
 def test_frozen_source_closed_inventory_detects_extra_file(self):
  with tempfile.TemporaryDirectory() as root:
   p=pathlib.Path(root);(p/'driver.py').write_text('pass\n');pins=m.inventory(p)
   m.verify_inventory(p,pins);(p/'extra').write_text('x')
   with self.assertRaises(ValueError):m.verify_inventory(p,pins)
 def test_fixed_create_plan_binds_executable_mounts_labels_and_fresh_runtime(self):
  spec=m.container_spec('wf-'+'a'*32,'b'*32,'start','c'*32,[('volume','source','/repo',True),('volume','pack','/pack',True),('volume','work','/work',False),('volume','results','/results',False)],['/usr/bin/python3','-I','-B','/repo/driver.py'])
  args=m.create_argv(spec)
  self.assertEqual(args[0],'create');self.assertIn('--pull',args);self.assertIn('never',args)
  self.assertEqual(args[-5:],[m.IMAGE,'/usr/bin/python3','-I','-B','/repo/driver.py'])
  self.assertEqual(spec['tmpfs'][m.RUNTIME],'rw,nosuid,nodev,size=3g,mode=700,uid=65534,gid=65534')
  self.assertEqual(spec['labels']['org.mingos.transition'],'c'*32)
  self.assertEqual([v[3] for v in spec['mounts']],[True,True,False,False])
 def test_actual_inspection_rejects_confinement_or_command_drift(self):
  spec=m.container_spec('wf-'+'a'*32,'b'*32,'start','c'*32,[('volume','work','/work',False)],['/usr/bin/python3','-I','-B','/repo/driver.py'])
  item={'Id':'d'*64,'Name':'/'+spec['name'],'Image':m.IMAGE,
   'Config':{'Image':m.IMAGE,'User':'65534:65534','Entrypoint':None,'Cmd':spec['command'],'Labels':spec['labels']},
   'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'NanoCpus':2000000000,'Memory':4294967296,'PidsLimit':256,'CapDrop':['CAP_ALL'],'CapAdd':None,'SecurityOpt':['no-new-privileges'],'Init':True,'AutoRemove':False,'RestartPolicy':{'Name':'no','MaximumRetryCount':0},'Tmpfs':m.TMPFS,'Binds':None,'VolumesFrom':None,'Devices':[],'DeviceRequests':None,'Links':None,'PortBindings':{},'ExtraHosts':None,'Mounts':[{'Type':'volume','Source':'work','Target':'/work','ReadOnly':False,'VolumeOptions':{'NoCopy':True}}]},
   'Mounts':[{'Type':'volume','Name':'work','Destination':'/work','RW':True}]}
  m.check_container(item,spec,'d'*64)
  for key,value in [('NetworkMode','bridge'),('ReadonlyRootfs',False),('Memory',0),('AutoRemove',True),('Devices',[{}]),('Binds',['/dev:/dev']),('PidMode','host'),('IpcMode','host'),('UsernsMode','host')]:
   bad=copy.deepcopy(item);bad['HostConfig'][key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):m.check_container(bad,spec,'d'*64)
  bad=copy.deepcopy(item);bad['Config']['Cmd']=['sh']
  with self.assertRaises(ValueError):m.check_container(bad,spec,'d'*64)
 def test_transition_receipt_binds_request_prior_current_and_scan(self):
  req={'schemaVersion':1,'profile':m.PROFILE,'operation':'reopen','nonce':'1'*32,'generation':'g','instanceSha256':'2'*64,'historyTipSha256':'3'*64,'diskSha256':'4'*64,'priorContainerId':'a'*64,'currentContainerId':'b'*64,'runtimeNonce':'5'*32}
  raw=m.encoded(req);r=m.transition_receipt(raw,'g','reopen','a'*64,'b'*64,b'prior',b'scan',['b'*64])
  self.assertEqual(r['requestSha256'],m.digest(raw));self.assertEqual(r['priorState'],'exited')
  self.assertEqual(r['priorInspectSha256'],m.digest(b'prior'));self.assertEqual(r['writerScanSha256'],m.digest(b'scan'))
  for key,value in [('priorContainerId','c'*64),('currentContainerId','c'*64),('operation','backup'),('generation','other'),('nonce','bad')]:
   bad=dict(req);bad[key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):m.transition_receipt(m.encoded(bad),'g','reopen','a'*64,'b'*64,b'p',b's',['b'*64])
  with self.assertRaises(ValueError):m.transition_receipt(raw,'g','reopen','a'*64,'b'*64,b'p',b's',['c'*64])
 def test_driver_argv_uses_fixed_instance_output_control_and_supervision(self):
  args=m.driver_argv('reopen','a'*64,2,'b'*64)
  self.assertEqual(args[:4],['/usr/bin/timeout','--signal=TERM','--kill-after=10s','1950s'])
  self.assertIn('/work/mingos-boot-infra-20261025/workflow-instance.json',args)
  self.assertIn('/results/boot-0002',args);self.assertIn('/run/mingos-runtime',args)
  self.assertEqual(args[-1],'/run/mingos-runtime')
  b=m.driver_argv('backup','a'*64,2,'b'*64);self.assertIn('/backup',b);self.assertNotIn('--pack',b)
  r=m.driver_argv('restore','a'*64,0,'b'*64);self.assertEqual(r[r.index('--backup-sha256')+1],'b'*64)
 def test_gate_writer_is_exclusive_fixed_path_and_full_cid(self):
  writer=m.runtime_write_argv('b'*64,'current-container-id')
  self.assertEqual(writer[:5],['exec','-i','--user','65534:65534','b'*64])
  self.assertIn('O_EXCL',writer[-1]);self.assertIn('/run/mingos-runtime/current-container-id',writer[-1])
  self.assertIn('current-container-id.ready',writer[-1]);self.assertLess(writer[-1].index("with os.fdopen"),writer[-1].index('current-container-id.ready'))
  with self.assertRaises(ValueError):m.runtime_write_argv('b'*64,'../../other')
 def test_volume_identity_is_plain_local_and_owned_when_required(self):
  item={'Name':'work','Driver':'local','Scope':'local','CreatedAt':'today','Mountpoint':'/docker/work','Options':None,'Labels':{'org.mingos.generation':'g','org.mingos.nonce':'n'}}
  m.check_volume(item,'work',('g','n'))
  for key,value in [('Options',{'device':'/etc'}),('Driver','other'),('Scope','global'),('Name','other'),('Labels',{})]:
   bad=copy.deepcopy(item);bad[key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):m.check_volume(bad,'work',('g','n'))
 def test_mount_roles_make_backup_original_readonly_restore_target_new(self):
  state={'source':'C:/frozen','pack':'pack','volumes':{'work':'work','results':'results'}}
  b=m.operation_mounts(state,'backup','new-backup')
  self.assertIn(('volume','work','/work',True),b);self.assertIn(('volume','new-backup','/backup',False),b)
  r=m.operation_mounts(state,'restore','old-backup')
  self.assertIn(('volume','work','/work',False),r);self.assertIn(('volume','old-backup','/backup',True),r)
 def test_host_lock_is_persistent_and_excludes_competing_controller(self):
  with tempfile.TemporaryDirectory() as root:
   p=pathlib.Path(root)
   with m.host_lock(p):
    with self.assertRaises((ValueError,OSError)):
     with m.host_lock(p):pass
   self.assertTrue((p/'host.lock').is_file())
   with m.host_lock(p):pass
 def test_scan_enumerates_all_ids_twice_and_binds_inspection_bytes(self):
  with tempfile.TemporaryDirectory() as root:
   o=object.__new__(m.Coordinator);o.root=pathlib.Path(root);calls=[]
   item={'Id':'a'*64,'State':{'Status':'exited','Running':False},'Mounts':[]}
   def call(args,label,**kw):
    calls.append(args);return (('a'*64+'\n').encode() if args[0]=='ps' else m.encoded([item]))
   o.call=call;raw,writers=o.scan({'work'})
   self.assertEqual(writers,[]);self.assertEqual(json.loads(raw)['containers'],[item]);self.assertEqual([v[0] for v in calls],['ps','inspect','ps'])
 def test_scan_rejects_container_set_changed_during_observation(self):
  o=object.__new__(m.Coordinator);count=0
  def call(args,label,**kw):
   nonlocal count
   if args[0]=='ps':count+=1;return b'' if count==1 else ('a'*64+'\n').encode()
   raise AssertionError(args)
  o.call=call
  with self.assertRaises(ValueError):o.scan({'work'})
 def test_scan_uses_bounded_complete_batches_and_rejects_incomplete_inspection_sets(self):
  ids=[f'{i:064x}' for i in range(1,131)]
  for drift in ('none','missing','extra','duplicate','changed'):
   o=object.__new__(m.Coordinator);calls=[]
   def call(args,label,**kw):
    calls.append(args)
    if args[0]=='ps':return ('\n'.join(ids)+'\n').encode()
    values=[{'Id':cid,'State':{'Status':'exited','Running':False},'Mounts':[]} for cid in args[1:]]
    if drift=='missing':values=values[:-1]
    if drift=='extra':values.append({'Id':'f'*64,'State':{'Status':'exited','Running':False},'Mounts':[]})
    if drift=='duplicate':values.append(values[0])
    if drift=='changed':values[0]['Id']='e'*64
    return m.encoded(values)
   o.call=call
   if drift=='none':
    raw,writers=o.scan({'work'});self.assertEqual(writers,[]);self.assertEqual(len(json.loads(raw)['containers']),130)
    self.assertEqual([len(args)-1 for args in calls if args[0]=='inspect'],[64,64,2])
   else:
    with self.subTest(drift=drift),self.assertRaises(ValueError):o.scan({'work'})
 def test_control_checks_actual_live_container_and_does_not_resubmit(self):
  with tempfile.TemporaryDirectory() as root:
   o=object.__new__(m.Coordinator);o.root=pathlib.Path(root);o.stage=o.root;o.state={'sourceManifestSha256':'a'*64};calls=[]
   o.source_check=lambda:None
   item={'Id':'b'*64,'State':{'Status':'exited','Running':False}}
   o.one=lambda *args:(item,b'[]')
   o.call=lambda args,label,**kw:calls.append(args) or b'{}'
   with self.assertRaises(ValueError):o.control({'cid':'b'*64,'spec':{},'index':1},{'op':'type','text':'Hello'})
   self.assertEqual(calls,[])
 def test_new_source_freeze_keeps_complete_literal_bytes(self):
  with tempfile.TemporaryDirectory() as root:
   p=pathlib.Path(root);source=p/'input';source.mkdir();(source/'one.py').write_text('x=1\n')
   target=p/'frozen';pins,pin=m.freeze_source(source,target)
   self.assertEqual((target/'one.py').read_bytes(),(source/'one.py').read_bytes());self.assertEqual(m.digest((target/'source-manifest.json').read_bytes()),pin)
   self.assertEqual(json.loads((target/'source-manifest.json').read_bytes())['files'],pins)
   with self.assertRaises((ValueError,FileExistsError)):m.freeze_source(source,target)
 def test_boot_history_uses_new_index_for_normal_reopen(self):
  with tempfile.TemporaryDirectory() as root:
   o=object.__new__(m.Coordinator);o.root=pathlib.Path(root);o.state={'inner':{'normalBoots':0}}
   self.assertIsNone(o.latest_boot())
   m.write(o.root/'boot-0001.json',{'cid':'a'*64,'index':1,'spec':{}})
   self.assertEqual(o.latest_boot()['index'],1)
   m.write(o.root/'boot-0002.json',{'cid':'b'*64,'index':2,'spec':{}})
   self.assertEqual(o.latest_boot()['cid'],'b'*64)
 def test_offline_backup_checks_stopped_before_creating_volume(self):
  with tempfile.TemporaryDirectory() as root:
   o=object.__new__(m.Coordinator);o.root=pathlib.Path(root);o.stage=o.root;o.state={'volumeIdentities':{}};calls=[]
   def prior(record):calls.append('prior');raise ValueError('still running')
   o.prior=prior;o.new_volume=lambda role:calls.append('new-volume')
   with self.assertRaises(ValueError):o.backup({'cid':'a'*64,'index':1})
   self.assertEqual(calls,['prior'])
 def test_noncanonical_paths_and_bad_final_history_are_rejected(self):
  with self.assertRaises(ValueError):m.safe_path(pathlib.Path.cwd()/'..'/'other')
  good={'generation':'00000000-0000-4000-8000-000000000001','normalBoots':1,'instanceSha256':'a'*64,'diskSha256':'b'*64,'historyTipSha256':'c'*64,'priorContainerId':'d'*64}
  m.validate_inner(good,good['generation'],1)
  for key,value in [('normalBoots',True),('generation','other'),('diskSha256','bad'),('priorContainerId',None)]:
   bad=dict(good);bad[key]=value
   with self.subTest(key=key),self.assertRaises(ValueError):m.validate_inner(bad,good['generation'],1)
 @unittest.skipUnless(sys.platform=='linux' and os.geteuid()==65534,'actual Linux nonroot publish boundary')
 def test_actual_runtime_writer_publishes_data_then_zero_byte_ready_exclusively(self):
  with tempfile.TemporaryDirectory() as root:
   script=m.runtime_write_argv('b'*64,'current-container-id')[-1].replace(m.RUNTIME,root)
   p=subprocess.run([sys.executable,'-I','-B','-c',script],input=('b'*64+'\n').encode(),capture_output=True)
   self.assertEqual(p.returncode,0,p.stderr);path=pathlib.Path(root)/'current-container-id';marker=pathlib.Path(str(path)+'.ready')
   self.assertEqual(path.read_bytes(),('b'*64+'\n').encode());self.assertEqual(marker.stat().st_size,0);self.assertEqual(marker.stat().st_mode&0o777,0o600)
   again=subprocess.run([sys.executable,'-I','-B','-c',script],input=b'other',capture_output=True);self.assertNotEqual(again.returncode,0);self.assertEqual(path.read_bytes(),('b'*64+'\n').encode())
 @unittest.skipUnless(sys.platform=='linux' and os.geteuid()==65534,'actual Linux nonroot publish boundary')
 def test_actual_request_reader_ignores_unpublished_empty_data_and_rejects_bad_marker(self):
  with tempfile.TemporaryDirectory() as root:
   p=pathlib.Path(root)/'transition-request.json';p.touch(mode=0o600);script=m.runtime_read_script('transition-request.json').replace(m.RUNTIME,root)
   out=subprocess.run([sys.executable,'-I','-B','-c',script],capture_output=True);self.assertEqual(out.returncode,0);self.assertEqual(out.stdout,b'')
   p.write_bytes(b'{"complete":true}');marker=pathlib.Path(str(p)+'.ready');marker.touch(mode=0o600)
   out=subprocess.run([sys.executable,'-I','-B','-c',script],capture_output=True);self.assertEqual(out.returncode,0,out.stderr);self.assertEqual(out.stdout,p.read_bytes())
   marker.write_bytes(b'not zero');out=subprocess.run([sys.executable,'-I','-B','-c',script],capture_output=True);self.assertNotEqual(out.returncode,0)
 def test_real_inner_backup_dto_matches_manifest_and_finalized_pins(self):
  flags={'diagnosticOnly':True,'instrumentedQualification':False,'formalQualification':False,'finalSystemAccepted':False}
  expected={'generation':'00000000-0000-4000-8000-000000000001','normalBoots':1,'instanceSha256':'a'*64,'diskSha256':'b'*64,'historyTipSha256':'c'*64}
  transition={'request':{'bound':'request'},'receipt':{'bound':'receipt'}}
  manifest={'schemaVersion':1,'profile':m.PROFILE,'status':'workflow-backup-complete',**expected,'files':{'file':{'bytes':1,'sha256':'d'*64,'mode':0o600}},'directories':{},'transition':transition,**flags}
  raw=m.encoded(manifest)
  report={'schemaVersion':1,'status':'workflow-backup-complete','backupSha256':m.digest(raw),'normalBoots':1,'generation':expected['generation'],'guestBooted':False,**flags}
  m.verify_backup_export(report,raw,expected,transition)
  duplicate=raw.replace(b'"schemaVersion":1',b'"schemaVersion":1,"schemaVersion":1',1)
  with self.assertRaises(ValueError):m.verify_backup_export(dict(report,backupSha256=m.digest(duplicate)),duplicate,expected,transition)
  for key,value in [('generation','wrong'),('normalBoots',2),('instanceSha256','e'*64),('diskSha256','e'*64),('historyTipSha256','e'*64)]:
   bad=dict(manifest);bad[key]=value;blob=m.encoded(bad);r=dict(report,backupSha256=m.digest(blob))
   with self.subTest(key=key),self.assertRaises(ValueError):m.verify_backup_export(r,blob,expected,transition)

if __name__=='__main__':unittest.main()
