"""Developer-workflow host coordinator. Import and --help never run Docker."""
import argparse, contextlib, hashlib, json, os, re, stat, subprocess, time, uuid
from pathlib import Path
IMAGE='sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091'
PROFILE='developer-workflow-v1'
CID=re.compile(r'[0-9a-f]{64}\Z')
NONCE=re.compile(r'[0-9a-f]{32}\Z')
PYTHON=['/usr/bin/python3','-I','-B']
RUNTIME='/run/mingos-runtime'
WORK='/work/mingos-boot-infra-20261025'
TMPFS={'/tmp':'rw,nosuid,nodev,size=512m,mode=1777',RUNTIME:'rw,nosuid,nodev,size=3g,mode=700,uid=65534,gid=65534'}

def require(value,message):
 if not value:raise ValueError(message)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode()
def write(path,value):
 with Path(path).open('xb') as f:f.write(encoded(value));f.flush();os.fsync(f.fileno())
def strict_json(raw):
 def obj(pairs):
  value={}
  for key,item in pairs:require(key not in value,'duplicate JSON member');value[key]=item
  return value
 def constant(value):raise ValueError('nonfinite JSON constant')
 return json.loads(raw,object_pairs_hook=obj,parse_constant=constant)
def read(path):return strict_json(Path(path).read_bytes())
def safe_path(path):
 path=Path(path);require(path.is_absolute() and '..' not in path.parts and not any(c in str(path) for c in ',\n\r\0'),'canonical absolute path required')
 for p in (*reversed(path.parents),path):
  if p.exists():
   s=p.lstat();require(not p.is_symlink() and not getattr(s,'st_file_attributes',0)&0x400,'reparse/symlink ancestor forbidden')
 return path
def literal(path,directory=False):
 path=Path(path);s=path.lstat()
 require(not path.is_symlink() and not getattr(s,'st_file_attributes',0)&0x400,'literal non-reparse path required')
 require(stat.S_ISDIR(s.st_mode) if directory else stat.S_ISREG(s.st_mode) and s.st_nlink==1,'regular single-link file required')
 return s
def inventory(root):
 result={};root=Path(root);literal(root,True)
 for base,dirs,files in os.walk(root,followlinks=False):
  for name in dirs:literal(Path(base)/name,True)
  for name in files:
   p=Path(base)/name;before=literal(p);raw=p.read_bytes();after=literal(p)
   require((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'source changed while reading')
   result[p.relative_to(root).as_posix()]={'sha256':digest(raw),'bytes':len(raw)}
 return dict(sorted(result.items()))
def verify_inventory(root,pins):require(inventory(root)==pins,'complete frozen source inventory differs')
def freeze_source(source,target):
 source=safe_path(Path(source).absolute());target=safe_path(Path(target).absolute());require(not target.is_relative_to(source) and not target.exists(),'new nonoverlapping source freeze required')
 pins=inventory(source);require('source-manifest.json' not in pins,'input source must not carry outer manifest');target.mkdir()
 for name,record in pins.items():
  raw=(source/name).read_bytes();require({'bytes':len(raw),'sha256':digest(raw)}==record,'source changed during freeze')
  p=target/name;p.parent.mkdir(parents=True,exist_ok=True)
  with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 require(inventory(source)==pins and inventory(target)==pins,'complete source freeze differs')
 write(target/'source-manifest.json',{'schemaVersion':1,'profile':PROFILE,'files':pins});return pins,digest((target/'source-manifest.json').read_bytes())
def claim_operation(root,name):
 require(re.fullmatch(r'[a-z][a-z0-9-]{0,63}',name),'bounded operation name required')
 target=Path(root)/name
 require(not target.exists(),'operation already exists; failed operations cannot be retried')
 target.mkdir();return target
def require_exited(item,cid):
 require(CID.fullmatch(cid) and item.get('Id')==cid,'exact prior container required')
 s=item.get('State',{})
 require(s.get('Status')=='exited' and all(s.get(k) is False for k in ('Running','Restarting','Paused','Dead','OOMKilled')) and type(s.get('ExitCode')) is int and s['ExitCode']==0,'prior exact container must have exited successfully')
def active_writers(items,volumes,current=None):
 writers=[];seen=set()
 for item in items:
  cid=item.get('Id');require(isinstance(cid,str) and CID.fullmatch(cid) and cid not in seen,'closed unique full container scan required');seen.add(cid)
  s=item.get('State',{});require(s.get('Status') in ('created','running','paused','restarting','removing','exited','dead'),'unknown container state')
  # Created containers can start without notice; treat them as reserved writers too.
  active=s.get('Status') not in ('exited','dead') or s.get('Running') is True
  if active and any(m.get('Type')=='volume' and m.get('Name') in volumes and m.get('RW') is True for m in item.get('Mounts',[])):writers.append(cid)
 require(all(cid==current for cid in writers),'another active/reserved container has a writable volume mount')
 return sorted(writers)
def control_argv(command,index=1,source_pin=None):
 require(type(command) is dict,'command object required');op=command.get('op')
 require(op in ('status','capture','click','key','type','power'),'unsupported control operation')
 args=[*PYTHON,'-c',BOOTSTRAP.replace('workflow_vm.py','workflow_cli.py'),source_pin or '0'*64,'--control',RUNTIME+f'/control-{index:04d}',op]
 if op in ('status','capture'):require(set(command)=={'op'},'no extra control fields')
 elif op in ('click','power'):
  require(set(command)=={'op','x','y'} and all(type(command[k]) is int and 0<=command[k]<4096 for k in ('x','y')),'bounded integer pointer required');args.extend([str(command['x']),str(command['y'])])
 elif op=='type':
  text=command.get('text');require(set(command)=={'op','text'} and type(text) is str and 1<=len(text)<=512 and all(c in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 \n' for c in text),'type supports 1..512 Latin letters, digits, spaces and newlines');args.append(text)
 else:
  key=command.get('qcode');mods=command.get('modifiers',[])
  allowed=set('abcdefghijklmnopqrstuvwxyz0123456789')|{'esc','ret','spc','tab','backspace','delete','left','right','up','down','home','end','shift','shift_r','ctrl','ctrl_r','alt','alt_r','meta_l','meta_r'}
  require(set(command)<= {'op','qcode','modifiers'} and {'op','qcode'}<=set(command) and key in allowed and type(mods) is list and len(mods)<=2 and len(set(mods))==len(mods) and all(m in ('ctrl','shift','alt') for m in mods),'bounded supported key required');args.append(key)
  if mods:args.extend(['--modifiers',*mods])
 return args

class Docker:
 def __init__(self,root,executable='docker'):self.root=Path(root);self.executable=executable;self.sequence=0
 def __call__(self,args,label,input=None,timeout=180):
  self.sequence+=1;prefix=self.root/(f'{self.sequence:04d}-'+label)
  write(str(prefix)+'.command.json',{'argv':[self.executable,*args],'timeoutSeconds':timeout,'stdinSha256':digest(input) if input is not None else None})
  try:
   result=subprocess.run([self.executable,*args],input=input,capture_output=True,timeout=timeout)
   Path(str(prefix)+'.stdout.log').write_bytes(result.stdout);Path(str(prefix)+'.stderr.log').write_bytes(result.stderr)
   write(str(prefix)+'.exit.json',{'exitCode':result.returncode});require(result.returncode==0,'Docker operation failed: '+label)
   return result.stdout
  except subprocess.TimeoutExpired as e:
   Path(str(prefix)+'.stdout.log').write_bytes(e.stdout or b'');Path(str(prefix)+'.stderr.log').write_bytes(e.stderr or b'');write(str(prefix)+'.exit.json',{'exitCode':None,'timedOut':True});raise

def container_spec(generation,nonce,operation,transition,mounts,command,initializer=False):
 require(re.fullmatch(r'[a-z0-9][a-z0-9-]{1,100}',generation) and NONCE.fullmatch(nonce) and NONCE.fullmatch(transition),'owned generation/nonce required')
 require(len({m[2] for m in mounts})==len(mounts),'unique mount targets required')
 for kind,source,target,readonly in mounts:
  require(kind in ('volume','bind') and isinstance(readonly,bool) and not any(c in source for c in ',\r\n\0') and target.startswith('/'),'closed safe mount required')
 return {'name':generation+'-'+transition,'labels':{'org.mingos.generation':generation,'org.mingos.nonce':nonce,'org.mingos.transition':transition,'org.mingos.operation':operation},'mounts':[list(m) for m in mounts],'command':list(command),'user':'0:0' if initializer else '65534:65534','initializer':initializer,'tmpfs':{} if initializer else dict(TMPFS)}
def create_argv(spec):
 args=['create','--pull','never','--init','--name',spec['name'],'--network','none','--read-only','--user',spec['user'],'--cpus','2','--memory','4g','--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges','--restart','no']
 if spec['initializer']:args.extend(['--cap-add','CHOWN'])
 for key,value in sorted(spec['labels'].items()):args.extend(['--label',key+'='+value])
 for target,flags in sorted(spec['tmpfs'].items()):args.extend(['--tmpfs',target+':'+flags])
 for kind,source,target,readonly in spec['mounts']:
  text=f'type={kind},source={source},target={target}'+(',readonly' if readonly else '')+(',volume-nocopy' if kind=='volume' else '')
  args.extend(['--mount',text])
 return [*args,IMAGE,*spec['command']]
def check_container(item,spec,cid):
 require(CID.fullmatch(cid) and item.get('Id')==cid and item.get('Image')==IMAGE and item.get('Name')=='/'+spec['name'],'container identity differs')
 c=item.get('Config',{});h=item.get('HostConfig',{})
 require(c.get('Image')==IMAGE and c.get('User')==spec['user'] and not c.get('Entrypoint') and c.get('Cmd')==spec['command'] and all(c.get('Labels',{}).get(k)==v for k,v in spec['labels'].items()),'actual executable/user/labels differ')
 required={'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'NanoCpus':2000000000,'Memory':4294967296,'PidsLimit':256,'Init':True,'AutoRemove':False,'RestartPolicy':{'Name':'no','MaximumRetryCount':0}}
 require(all(h.get(k)==v for k,v in required.items()),'confinement fields differ')
 require((h.get('Tmpfs') or {})==spec['tmpfs'],'runtime tmpfs differs: '+repr((h.get('Tmpfs'),spec['tmpfs'])))
 for key in ('PidMode','UTSMode','UsernsMode','CgroupParent'):require(h.get(key,'')=='','runtime namespace differs: '+key+'='+repr(h.get(key)))
 require(h.get('IpcMode','private')=='private','private IPC namespace required')
 require(h.get('OomKillDisable') is None or h.get('OomKillDisable') is False,'OOM disable policy differs: '+repr(h.get('OomKillDisable')))
 require([x.removeprefix('CAP_') for x in h.get('CapDrop',[])]==['ALL'] and [x.removeprefix('CAP_') for x in h.get('CapAdd') or []]==(['CHOWN'] if spec['initializer'] else []),'capability set differs')
 require(h.get('SecurityOpt') in (['no-new-privileges'],['no-new-privileges=true'],['no-new-privileges:true']),'no-new-privileges required')
 require(all(not h.get(k) for k in ('Binds','VolumesFrom','Devices','DeviceRequests','Links','PortBindings','ExtraHosts')),'unexpected host attachment')
 effective=item.get('Mounts',[]);planned=h.get('Mounts',[])
 require(len(effective)==len(planned)==len(spec['mounts']),'closed mount count differs')
 def source_matches(actual,want):
  possible=[want]
  if re.match(r'^[A-Za-z]:[\\/]',want):possible.append('/run/desktop/mnt/host/'+want[0].lower()+'/'+want[3:].replace('\\','/'))
  return actual in possible
 for kind,source,target,readonly in spec['mounts']:
  matches=[v for v in effective if v.get('Destination')==target and v.get('Type')==kind and v.get('RW') is (not readonly)]
  require(len(matches)==1 and (matches[0].get('Name')==source if kind=='volume' else source_matches(matches[0].get('Source'),source)),'effective mount source/mode differs')
  matches=[v for v in planned if v.get('Target')==target and v.get('Type')==kind and bool(v.get('ReadOnly',False))==readonly and source_matches(v.get('Source'),source)]
  require(len(matches)==1 and (kind!='volume' or matches[0].get('VolumeOptions')=={'NoCopy':True}),'planned mount source/options differ')
def transition_receipt(raw,generation,operation,prior,current,prior_raw,scan_raw,writers):
 req=json.loads(raw)
 keys={'schemaVersion','profile','operation','nonce','generation','instanceSha256','historyTipSha256','diskSha256','priorContainerId','currentContainerId','runtimeNonce'}
 require(type(req) is dict and set(req)==keys,'closed transition request required')
 require(req['schemaVersion']==1 and req['profile']==PROFILE and req['operation']==operation and req['generation']==generation and req['priorContainerId']==prior and req['currentContainerId']==current and CID.fullmatch(prior) and CID.fullmatch(current),'transition identity differs')
 require(all(isinstance(req[k],str) and CID.fullmatch(req[k]) for k in ('instanceSha256','historyTipSha256','diskSha256')) and all(isinstance(req[k],str) and NONCE.fullmatch(req[k]) for k in ('nonce','runtimeNonce')),'transition digest/nonce differs')
 require(writers==[current],'current must be the only active writer')
 return {'schemaVersion':1,'profile':PROFILE,'requestSha256':digest(raw),'priorContainerId':prior,'currentContainerId':current,'priorState':'exited','activeWritableContainerIds':writers,'priorInspectSha256':digest(prior_raw),'writerScanSha256':digest(scan_raw)}

BOOTSTRAP="""import hashlib,json,os,runpy,stat,sys
from pathlib import Path
root=Path('/repo');raw=(root/'source-manifest.json').read_bytes();assert hashlib.sha256(raw).hexdigest()==sys.argv.pop(1)
pins=json.loads(raw)['files'];actual={}
for base,dirs,files in os.walk(root,followlinks=False):
 for name in dirs:
  s=(Path(base)/name).lstat();assert stat.S_ISDIR(s.st_mode) and not stat.S_ISLNK(s.st_mode)
 for name in files:
  p=Path(base)/name;rel=p.relative_to(root).as_posix()
  if rel=='source-manifest.json':continue
  s=p.lstat();assert stat.S_ISREG(s.st_mode) and s.st_nlink==1;b=p.read_bytes();actual[rel]={'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
assert actual==pins
sys.path.insert(0,'/repo/scripts/boot');runpy.run_path('/repo/scripts/boot/workflow_vm.py',run_name='__main__')
"""
def driver_argv(operation,pack_pin,index,backup_pin=None,source_pin=None):
 require(operation in ('init','start','reopen','inspect','backup','restore') and CID.fullmatch(pack_pin),'supported driver operation and package pin required')
 args=[*PYTHON,'-c',BOOTSTRAP,source_pin or pack_pin,operation]
 if operation in ('init','start','reopen','restore'):args.extend(['--pack','/pack','--manifest-sha256',pack_pin])
 if operation in ('init','restore'):args.extend(['--work-root',WORK])
 if operation in ('start','reopen','inspect','backup'):args.extend(['--instance',WORK+'/workflow-instance.json'])
 if operation in ('start','reopen'):args.extend(['--output',f'/results/boot-{index:04d}'])
 if operation in ('backup','restore'):args.extend(['--backup-root','/backup'])
 if operation=='restore':require(isinstance(backup_pin,str) and CID.fullmatch(backup_pin),'exact backup pin required');args.extend(['--backup-sha256',backup_pin])
 if operation!='inspect':args.extend(['--runtime-root',RUNTIME])
 return ['/usr/bin/timeout','--signal=TERM','--kill-after=10s','1950s',*args]
def runtime_write_argv(cid,name):
 require(CID.fullmatch(cid) and name in ('current-container-id','transition-receipt.json'),'fixed runtime write only')
 path=RUNTIME+'/'+name
 script="import os,sys;raw=sys.stdin.buffer.read(16385);assert 0<len(raw)<=16384\nfd=os.open("+repr(path)+",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)\nwith os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())\nfd=os.open("+repr(path+'.ready')+",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.fsync(fd);os.close(fd)\nfd=os.open("+repr(RUNTIME)+",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);os.fsync(fd);os.close(fd)"
 return ['exec','-i','--user','65534:65534',cid,*PYTHON,'-c',script]
def runtime_read_script(name):
 require(name=='transition-request.json','fixed request file required')
 return """import os,stat,sys
from pathlib import Path
p=Path("""+repr(RUNTIME+'/'+name)+""");marker=Path(str(p)+'.ready')
try:s=marker.lstat()
except FileNotFoundError:sys.exit(0)
assert stat.S_ISREG(s.st_mode) and s.st_nlink==1 and s.st_size==0 and s.st_uid==65534 and stat.S_IMODE(s.st_mode)==0o600
fd=os.open(marker,os.O_RDONLY|os.O_NOFOLLOW);a=os.fstat(fd);assert (a.st_dev,a.st_ino,a.st_size,a.st_uid,a.st_mode)==(s.st_dev,s.st_ino,s.st_size,s.st_uid,s.st_mode);os.close(fd)
fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)
try:
 a=os.fstat(fd);assert stat.S_ISREG(a.st_mode) and a.st_nlink==1 and a.st_uid==65534 and stat.S_IMODE(a.st_mode)==0o600 and 0<a.st_size<=16384
 raw=os.read(fd,16385);b=os.fstat(fd);assert len(raw)==a.st_size and (a.st_dev,a.st_ino,a.st_size,a.st_mtime_ns)==(b.st_dev,b.st_ino,b.st_size,b.st_mtime_ns)
 sys.stdout.buffer.write(raw)
finally:os.close(fd)
"""

def check_volume(item,name,owner=None):
 require(item.get('Name')==name and item.get('Driver')=='local' and item.get('Scope')=='local' and item.get('CreatedAt') and item.get('Mountpoint') and not item.get('Options'),'plain existing local volume required')
 if owner:require(all(item.get('Labels',{}).get(k)==v for k,v in zip(('org.mingos.generation','org.mingos.nonce'),owner)),'volume ownership differs')
def validate_inner(report,generation=None,index=None):
 require(type(report) is dict and isinstance(report.get('generation'),str),'inner result object/identity required')
 try:require(str(uuid.UUID(report['generation']))==report['generation'],'canonical inner UUID required')
 except (ValueError,AttributeError):raise ValueError('inner generation UUID differs')
 require(generation is None or report['generation']==generation,'inner generation changed')
 n=report.get('normalBoots');require(type(n) is int and n>=0 and (index is None or index==n),'inner finalized boot count differs')
 require(all(isinstance(report.get(k),str) and CID.fullmatch(report[k]) for k in ('instanceSha256','diskSha256')),'inner disk/instance pins required')
 require((n==0 and report.get('historyTipSha256') is None and report.get('priorContainerId') is None) or (n>0 and all(isinstance(report.get(k),str) and CID.fullmatch(report[k]) for k in ('historyTipSha256','priorContainerId'))),'inner finalized history bindings differ')
def verify_backup_export(report,raw,expected,transition):
 flags={'diagnosticOnly':True,'instrumentedQualification':False,'formalQualification':False,'finalSystemAccepted':False}
 require(type(report) is dict and set(report)=={'schemaVersion','status','backupSha256','normalBoots','generation','guestBooted',*flags} and type(report['schemaVersion']) is int and report['schemaVersion']==1 and report['status']=='workflow-backup-complete' and report['guestBooted'] is False and all(report[k] is v for k,v in flags.items()),'real inner backup result schema differs')
 require(report['backupSha256']==digest(raw),'exported backup manifest differs from inner sealed backup')
 value=strict_json(raw)
 require(type(value) is dict and set(value)=={'schemaVersion','profile','status','instanceSha256','generation','diskSha256','historyTipSha256','normalBoots','files','directories','transition',*flags} and type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['profile']==PROFILE and value['status']=='workflow-backup-complete' and all(value[k] is v for k,v in flags.items()),'closed backup manifest schema differs')
 require(type(value['normalBoots']) is int and value['normalBoots']>0 and type(report['normalBoots']) is int and all(value[k]==expected[k] for k in ('generation','normalBoots','instanceSha256','diskSha256','historyTipSha256')) and all(report[k]==expected[k] for k in ('generation','normalBoots')),'backup does not match prior finalized identity/history/disk')
 require(value['transition']==transition and type(value['files']) is dict and bool(value['files']) and type(value['directories']) is dict,'backup transition/inventory shape differs')
 for name in (*value['files'],*value['directories']):require(isinstance(name,str) and '\\' not in name and ':' not in name and all(p not in ('','.','..') for p in name.split('/')),'unsafe backup inventory name')
 return value
def operation_mounts(state,operation,backup=None):
 mounts=[('bind',state['source'],'/repo',True),('volume',state['pack'],'/pack',True),('volume',state['volumes']['work'],'/work',operation in ('backup','inspect')),('volume',state['volumes']['results'],'/results',operation in ('backup','inspect'))]
 if backup:mounts.append(('volume',backup,'/backup',operation=='restore'))
 return mounts
@contextlib.contextmanager
def host_lock(root):
 path=Path(root)/'host.lock'
 if path.exists():literal(path)
 with path.open('a+b') as f:
  f.seek(0,2)
  if f.tell()==0:f.write(b'\0');f.flush();os.fsync(f.fileno())
  f.seek(0)
  if os.name=='nt':
   import msvcrt
   msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
  else:
   import fcntl
   fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
  try:yield
  finally:
   f.seek(0)
   if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
   else:fcntl.flock(f.fileno(),fcntl.LOCK_UN)

class Coordinator:
 def __init__(self,root,docker=None):
  self.root=safe_path(Path(root).absolute());literal(self.root,True);self.state=read(self.root/'state.json');self.call=docker
  require(self.state['profile']==PROFILE and self.state['image']==IMAGE,'fixed workflow metadata required')
  for path in sorted(self.root.glob('volume-ledger-*.json')):
   entry=read(path);identity=entry['identity'];name=identity['Name']
   require(entry['generation']==self.state['generation'] and entry['nonce']==self.state['nonce'],'volume ledger generation differs')
   check_volume(identity,name,(entry['generation'],entry['nonce']))
   require(name not in self.state['volumeIdentities'] or self.state['volumeIdentities'][name]==identity,'conflicting volume identity ledger')
   self.state['volumeIdentities'][name]=identity
 def one(self,args,label):
  raw=self.call(args,label);values=json.loads(raw);require(type(values) is list and len(values)==1,'exact single inspection required');return values[0],raw
 def scan(self,volumes,current=None):
  def ids(label):
   text=self.call(['ps','-aq','--no-trunc'],label).decode().split()
   require(len(text)==len(set(text)) and all(CID.fullmatch(cid) for cid in text),'full closed container IDs required');return sorted(text)
  first=ids('writer-scan-before');items=[]
  for start in range(0,len(first),64):
   batch=first[start:start+64];values=strict_json(self.call(['inspect',*batch],f'writer-scan-batch-{start//64:04d}'))
   require(type(values) is list and all(type(v) is dict for v in values),'container inspection batch must be an array of objects')
   returned=[v.get('Id') for v in values]
   require(len(returned)==len(batch) and len(set(returned))==len(batch) and set(returned)==set(batch),'container inspection batch missing/extra/duplicate/changed IDs')
   by_id={v['Id']:v for v in values};items.extend(by_id[cid] for cid in batch)
  require(first==ids('writer-scan-after'),'container set changed during observation')
  writers=active_writers(items,set(volumes),current)
  return encoded({'containerIds':first,'containers':items,'managedVolumes':sorted(volumes),'allowedWriter':current}),writers
 def source_check(self):
  s=self.state;require(digest((Path(s['source'])/'source-manifest.json').read_bytes())==s['sourceManifestSha256'],'source manifest changed')
  pins=dict(s['sourcePins']);pins['source-manifest.json']={'bytes':(Path(s['source'])/'source-manifest.json').stat().st_size,'sha256':s['sourceManifestSha256']};verify_inventory(s['source'],pins)
  image,_=self.one(['image','inspect',IMAGE],'fixed-image-inspect');require(image.get('Id')==IMAGE and image.get('RootFS',{}).get('Layers'),'fixed image unavailable')
 def volume(self,name,owner=None):
  item,_=self.one(['volume','inspect',name],'volume-'+name[-70:]);check_volume(item,name,owner);return item
 def check_volumes(self,mounts):
  s=self.state
  for kind,name,target,ro in mounts:
   if kind!='volume':continue
   identity=self.volume(name,(s['generation'],s['nonce']) if name in s['volumes'].values() else None)
   require(identity==s['volumeIdentities'][name],'volume identity changed')
 def new_volume(self,role):
  s=self.state;name=s['generation']+'-'+role
  existing=self.call(['volume','ls','--format','{{.Name}}'],'volume-list-'+role).decode().splitlines();require(name not in existing,'new volume name already exists')
  self.call(['volume','create','--label','org.mingos.generation='+s['generation'],'--label','org.mingos.nonce='+s['nonce'],name],'volume-create-'+role)
  item=self.volume(name,(s['generation'],s['nonce']));s['volumeIdentities'][name]=item
  write(self.root/('volume-ledger-'+role+'.json'),{'generation':s['generation'],'nonce':s['nonce'],'identity':item});return name
 def launch(self,operation,mounts,command,initializer=False):
  self.source_check();self.check_volumes(mounts);s=self.state
  spec=container_spec(s['generation'],s['nonce'],operation,uuid.uuid4().hex,mounts,command,initializer)
  cid=self.call(create_argv(spec),'container-create').decode().strip();require(CID.fullmatch(cid),'full newly created CID required')
  record={'cid':cid,'spec':spec};write(self.stage/('container-'+cid+'.json'),record)
  item,_=self.one(['inspect',cid],'container-before');check_container(item,spec,cid)
  self.call(['start',cid],'container-start')
  if not initializer and operation!='inspect':self.call(runtime_write_argv(cid,'current-container-id'),'current-container-handshake',input=(cid+'\n').encode())
  return record
 def wait(self,record):
  cid=record['cid'];value=self.call(['wait',cid],'container-wait',timeout=1950).decode().strip();require(value=='0','container process failed')
  item,_=self.one(['inspect',cid],'container-after');check_container(item,record['spec'],cid);require_exited(item,cid)
  raw=self.call(['logs',cid],'container-logs');write(self.stage/('driver-output-'+cid+'.json'),{'stdoutSha256':digest(raw)})
  return json.loads(raw) if raw.strip() else None
 def prior(self,record):
  item,raw=self.one(['inspect',record['cid']],'prior-exact-inspect');check_container(item,record['spec'],record['cid']);require_exited(item,record['cid']);return raw
 def runtime_read(self,cid,name,optional=False):
  require(name=='transition-request.json','fixed runtime read required')
  script=runtime_read_script(name)
  return self.call(['exec','--user','65534:65534',cid,*PYTHON,'-c',script],'runtime-read-'+name)
 def gate(self,operation,record,prior):
  cid=record['cid'];deadline=time.monotonic()+60;raw=b''
  while time.monotonic()<deadline:
   raw=self.runtime_read(cid,'transition-request.json',True)
   if raw:break
   time.sleep(.2)
  require(raw,'inner transition request did not arrive; retain failed generation')
  request=json.loads(raw)
  if self.state.get('restoredFrom') and prior==self.state.get('lineagePrior'):
   expected=self.state['inner']
  else:
   # The last driver's final stdout is generated after normal finalize under EX
   # lock, and is bound here to that exact container's actual exited state.
   expected=json.loads(self.call(['logs',prior['cid']],'prior-driver-final-result'))
  for key in ('instanceSha256','historyTipSha256','diskSha256'):
   require(request.get(key)==expected.get(key) and expected.get(key) is not None,'transition does not match the prior finalized history: '+key)
  prior_raw=self.prior(prior);volumes=set(self.state['volumeIdentities']);scan_raw,writers=self.scan(volumes,cid)
  receipt=transition_receipt(raw,self.state['inner']['generation'],operation,prior['cid'],cid,prior_raw,scan_raw,writers)
  write(self.stage/'transition-request.json',json.loads(raw));write(self.stage/'prior-observation.json',json.loads(prior_raw));write(self.stage/'writer-scan.json',json.loads(scan_raw));write(self.stage/'transition-receipt.json',receipt)
  self.call(runtime_write_argv(cid,'transition-receipt.json'),'transition-receipt-handshake',input=encoded(receipt))
  return {'request':request,'receipt':receipt},expected
 def initialize_empty(self,roles):
  mounts=[('volume',self.state['volumes'][role],'/'+role,False) for role in roles]
  paths=['/'+role for role in roles]
  script="import os\nfor path in "+repr(paths)+":\n fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n assert not os.listdir(fd), 'new volume root is not empty'\n os.fchmod(fd,0o700);os.fsync(fd);os.fchown(fd,65534,65534);os.fsync(fd);os.close(fd)\n"
  record=self.launch('initialize-empty',mounts,[*PYTHON,'-c',script],True);self.wait(record)
 def control(self,record,command):
  self.source_check();cid=record['cid'];item,_=self.one(['inspect',cid],'control-container-inspect')
  if command['op']=='status' and not (item.get('State',{}).get('Status')=='running' and item['State'].get('Running') is True):
   check_container(item,record['spec'],cid)
   return {'status':'workflow-not-running','containerId':cid,'state':item.get('State'),'inputSent':False}
  require(item.get('State',{}).get('Status')=='running' and item['State'].get('Running') is True,'control requires the exact live container')
  check_container(item,record['spec'],cid)
  directory=RUNTIME+f"/control-{record['index']:04d}"
  probe="import json;from pathlib import Path;p=Path("+repr(directory)+");print(json.dumps({'ready':(p/'control.json').is_file() and (p/'control.sock').is_socket()}))"
  readiness=json.loads(self.call(['exec','--user','65534:65534',cid,*PYTHON,'-c',probe],'control-readiness'))
  if readiness.get('ready') is not True:return {'status':'workflow-not-ready','containerId':cid,'inputSent':False}
  view=json.loads(self.call(['exec','--user','65534:65534',cid,*control_argv({'op':'status'},record['index'],self.state['sourceManifestSha256'])],'workflow-status-observation',timeout=45))
  if view.get('status')!='ready' or view.get('active') is not False or not view.get('state'):
   return {'status':'workflow-not-ready','containerId':cid,'inputSent':False,'observation':view}
  if command['op']=='status':return view
  args=control_argv(command,record['index'],self.state['sourceManifestSha256'])
  raw=self.call(['exec','--user','65534:65534',cid,*args],'workflow-control',timeout=45)
  result=json.loads(raw);require(result.get('status')!='rejected','input rejected; outer never resubmits')
  if command['op']=='power':
   final=self.wait(record);validate_inner(final,self.state['inner']['generation'],record['index']);require(final['priorContainerId']==cid,'final driver is not bound to current container')
   result={'control':result,'driver':final}
  return result
 def latest_boot(self):
  paths=sorted(self.root.glob('boot-[0-9][0-9][0-9][0-9].json'))
  return read(paths[-1]) if paths else self.state.get('lineagePrior')
 def backup(self,prior):
  self.prior(prior);self.scan(set(self.state['volumeIdentities']))
  name=self.new_volume('backup-'+uuid.uuid4().hex);self.state['volumes']['backup']=name
  self.initialize_empty(['backup'])
  record=self.launch('backup',operation_mounts(self.state,'backup',name),driver_argv('backup',self.state['packManifestSha256'],prior['index'],source_pin=self.state['sourceManifestSha256']))
  transition,expected=self.gate('backup',record,prior);report=self.wait(record)
  require(report.get('status')=='workflow-backup-complete','inner backup did not complete')
  manifest=self.stage/'backup-manifest.json';self.call(['cp',record['cid']+':/backup/backup-manifest.json',str(manifest)],'export-backup-manifest');literal(manifest)
  verify_backup_export(report,manifest.read_bytes(),expected,transition)
  receipt={'schemaVersion':1,'profile':PROFILE,'status':'offline-backup-complete','volume':name,'volumeIdentity':self.state['volumeIdentities'][name],'backupManifestSha256':digest(manifest.read_bytes()),'prior':prior,'inner':report,'sourceVolumeIdentities':self.state['volumeIdentities'],'sourceGeneration':self.state['generation']}
  write(self.stage/'backup.json',receipt);return receipt
 def run(self,operation,command=None):
  require(operation in ('start','reopen','inspect','offlinebackup','status','capture','click','key','type','power'),'supported outer operation required')
  if operation in ('status','capture','click','key','type','power'):control_argv(command or {'op':operation},1,self.state['sourceManifestSha256'])
  with host_lock(self.root):
   prior=self.latest_boot();index=prior['index']+1 if prior else int(self.state['inner']['normalBoots'])+1
   name=(operation+f'-{index:04d}') if operation in ('start','reopen','offlinebackup') else operation+'-'+uuid.uuid4().hex
   self.stage=claim_operation(self.root,name);self.call=self.call or Docker(self.stage,self.state['docker'])
   write(self.stage/'operation.json',{'operation':operation,'nonce':uuid.uuid4().hex,'priorContainerId':prior['cid'] if prior else None})
   try:
    if operation not in ('inspect','status'):
     failures=list(self.root.glob('*/failed.json'));require(not failures,'failed operation is retained; use a new generation')
    self.source_check()
    if operation in ('start','reopen'):
     require((operation=='start' and prior is None and self.state['inner'].get('priorContainerId') is None) or (operation=='reopen' and prior is not None),'initial start and cross-container reopen must be distinct')
     if prior:self.prior(prior)
     self.scan(set(self.state['volumeIdentities']))
     record=self.launch(operation,operation_mounts(self.state,operation),driver_argv(operation,self.state['packManifestSha256'],index,source_pin=self.state['sourceManifestSha256']))
     if prior:self.gate('reopen',record,prior)
     record['index']=index;write(self.root/f'boot-{index:04d}.json',record)
     result={'status':'workflow-container-started','index':index,'containerId':record['cid'],'controlDirectory':RUNTIME+f'/control-{index:04d}'}
    elif operation=='offlinebackup':
     require(prior is not None,'a completed prior boot is required');result=self.backup(prior)
    elif operation=='inspect':
     # Inspect the persistent instance with read-only data mounts. The inner lock
     # rejects a concurrent VM; this command has no lifecycle gate or new boot.
     self.scan(set(self.state['volumeIdentities']))
     record=self.launch('inspect',operation_mounts(self.state,'inspect'),driver_argv('inspect',self.state['packManifestSha256'],index,source_pin=self.state['sourceManifestSha256']))
     result=self.wait(record)
    else:
     result=self.control(prior,command or {'op':operation}) if prior is not None else {'status':'workflow-not-started','inputSent':False}
    write(self.stage/'result.json',result);return result
   except BaseException as error:
    write(self.stage/'failed.json',{'error':str(error)[:4096],'type':type(error).__name__,'resourcesPreserved':True});raise
   finally:self.call=None

def new_instance(root,source,pack,pack_pin,docker='docker',restore_receipt=None,restore_receipt_pin=None,call=None):
 root=safe_path(Path(root).absolute());require(not root.exists() and root.parent.is_dir(),'absent generation directory required')
 require(isinstance(pack,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,180}',pack) and CID.fullmatch(pack_pin),'fixed existing pack volume and SHA256 required')
 origin=None
 if restore_receipt:
  literal(restore_receipt);raw=Path(restore_receipt).read_bytes();require(digest(raw)==restore_receipt_pin,'backup receipt pin differs');origin=json.loads(raw)
  require(origin.get('profile')==PROFILE and origin.get('status')=='offline-backup-complete','complete offline backup receipt required')
 root.mkdir();stage=claim_operation(root,'restore' if origin else 'new');o=object.__new__(Coordinator);o.root=root;o.stage=stage;o.call=call or Docker(stage,docker)
 try:
  pins,pin=freeze_source(source,root/'trusted-source')
  for needed in ('scripts/boot/workflow_vm.py','scripts/boot/workflow_pack.py','scripts/boot/workflow_cli.py','scripts/boot/workflow_control.py','scripts/boot/vm.py','probes/linux-isolation-source-v2/profile.json'):require(needed in pins,'trusted installation incomplete: '+needed)
  o.state={'schemaVersion':1,'profile':PROFILE,'image':IMAGE,'generation':'workflow-'+uuid.uuid4().hex,'nonce':uuid.uuid4().hex,'docker':docker,'source':str(root/'trusted-source'),'sourcePins':pins,'sourceManifestSha256':pin,'pack':pack,'packManifestSha256':pack_pin,'volumes':{},'volumeIdentities':{}}
  s=o.state;pack_identity=o.volume(pack);s['volumeIdentities'][pack]=pack_identity
  if origin:
   s['volumeIdentities'].update(origin['sourceVolumeIdentities']);s['volumeIdentities'][origin['volume']]=origin['volumeIdentity'];s['lineagePrior']=origin['prior']
   o.prior(origin['prior']);o.scan(set(s['volumeIdentities']))
  for role in ('work','results'):s['volumes'][role]=o.new_volume(role)
  o.initialize_empty(['work','results'])
  op='restore' if origin else 'init';mounts=operation_mounts(s,op,origin['volume'] if origin else None)
  record=o.launch(op,mounts,driver_argv(op,pack_pin,0,origin['backupManifestSha256'] if origin else None,pin));inner=o.wait(record)
  validate_inner(inner)
  s['inner']=inner;s['restoredFrom']=origin['backupManifestSha256'] if origin else None
  write(root/'state.json',s);write(stage/'result.json',inner);return {'status':'workflow-restored' if origin else 'workflow-created','evidence':str(root),'generation':s['generation'],'inner':inner}
 except BaseException as error:
  write(stage/'failed.json',{'error':str(error)[:4096],'type':type(error).__name__,'resourcesPreserved':True});raise

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='operation',required=True)
 for op in ('new','restore'):
  p=commands.add_parser(op);p.add_argument('--evidence',type=Path,required=True);p.add_argument('--source',type=Path,required=True);p.add_argument('--pack-volume',required=True);p.add_argument('--pack-manifest-sha256',required=True);p.add_argument('--docker',default='docker')
  if op=='restore':p.add_argument('--backup-receipt',type=Path,required=True);p.add_argument('--backup-receipt-sha256',required=True)
 for op in ('start','reopen','inspect','offlinebackup','status','capture','click','key','type','power'):
  p=commands.add_parser(op);p.add_argument('--evidence',type=Path,required=True)
  if op in ('click','power'):p.add_argument('x',type=int);p.add_argument('y',type=int)
  if op=='key':p.add_argument('qcode');p.add_argument('--modifiers',nargs='*',default=[])
  if op=='type':p.add_argument('text')
 args=parser.parse_args(argv)
 if args.operation in ('new','restore'):
  result=new_instance(args.evidence,args.source,args.pack_volume,args.pack_manifest_sha256,args.docker,getattr(args,'backup_receipt',None),getattr(args,'backup_receipt_sha256',None))
 else:
  command={k:v for k,v in vars(args).items() if k not in ('operation','evidence')};command['op']=args.operation
  result=Coordinator(args.evidence).run(args.operation,command)
 print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
