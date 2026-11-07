"""Prototype developer cache coordinator. Export never restores; restore is explicit.

No downloads, image loads, resource removal or guest/build commands exist.
Only fixed local helper image, existing RO inputs and fresh owned outputs.
"""
import argparse,copy,hashlib,json,os,re,secrets,shutil,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
for parent in HERE.parents:
    candidate=parent/'scripts/boot'
    if (candidate/'qualification_volume_restore.py').is_file():sys.path.insert(0,str(candidate));break
import qualification_volume_archive as archive
import qualification_volume_restore as original
import qualification_volume_plan as plan
import qualification_volume_worker as worker
import developer_cache_worker as content
IMAGE=plan.HELPER_IMAGE
NAME=re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,200}\Z')
INIT="import os; paths=('/cargo-consumer','/gradle-consumer'); assert all(not os.listdir(p) for p in paths); fds=[os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW) for p in paths]; [(os.fchmod(fd,0o700),os.fsync(fd),os.fchown(fd,65534,65534),os.fsync(fd),os.close(fd)) for fd in fds]"
def mount(kind,source,target,readonly=False,subpath=None):
    source=str(source)
    if any(c in source for c in ',\r\n\0'):raise ValueError('mount source delimiters forbidden')
    return {'type':kind,'source':source,'target':target,'readonly':readonly,**({'subpath':subpath} if subpath else {})}
def mount_text(m):return f"type={m['type']},source={m['source']},target={m['target']}"+(',readonly' if m['readonly'] else '')+(',volume-nocopy' if m['type']=='volume' else '')+(',volume-subpath='+m['subpath'] if m.get('subpath') else '')
def confinement(item,cid,nonce,label,mounts,command,root=False,env=None,finished=False):
    content.require(item.get('Id')==cid and item.get('Image')==IMAGE,'container image/id differs')
    config,host=item.get('Config',{}),item.get('HostConfig',{})
    content.require(config.get('User')==('0:0' if root else '65534:65534') and config.get('Entrypoint')==['python3'] and config.get('Cmd')==command,'container executable/user differs')
    labels=config.get('Labels') or {};content.require(labels.get('org.mingos.developer.nonce')==nonce and labels.get('org.mingos.developer.role')==label,'owned container labels differ')
    for key,want in {'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'NanoCpus':2000000000,'Memory':4294967296,'PidsLimit':128,'CapDrop':['ALL']}.items():content.require(host.get(key)==want,'isolation differs: '+key)
    content.require((host.get('CapAdd') or []) in ([['CHOWN'],['CAP_CHOWN']] if root else [[]]),'capabilities differ')
    content.require(host.get('SecurityOpt') in (['no-new-privileges'],['no-new-privileges:true']),'no-new-privileges missing')
    for key in ('Binds','VolumesFrom','Devices','DeviceRequests','Links','Tmpfs','PortBindings','ExtraHosts'):content.require(host.get(key) in (None,[],{}),'extra attachment: '+key)
    content.require(host.get('AutoRemove') is False and host.get('RestartPolicy')=={'Name':'no','MaximumRetryCount':0},'retention/restart differs')
    requested,effective=host.get('Mounts') or [],item.get('Mounts') or []
    content.require(len(requested)==len(mounts)==len(effective),'mount count differs')
    for m in mounts:
        matches=[v for v in requested if v.get('Target')==m['target']]
        actual=[v for v in effective if v.get('Destination')==m['target']]
        content.require(len(matches)==len(actual)==1,'mount destination differs')
        want,got=matches[0],actual[0]
        content.require(want.get('Type')==got.get('Type')==m['type'] and bool(want.get('ReadOnly',False))==m['readonly'] and got.get('RW')==(not m['readonly']),'mount mode/type differs')
        if m['type']=='volume':
            expected={'NoCopy':True,**({'Subpath':m['subpath']} if m.get('subpath') else {})}
            content.require(want.get('Source')==got.get('Name')==m['source'] and want.get('VolumeOptions')==expected,'volume/subpath differs')
        else:content.require(original.effective_helper_source_matches(want.get('Source'),m['source']) and original.effective_helper_source_matches(got.get('Source'),m['source']),'bind path differs')
    for key,value in (env or {}).items():content.require((config.get('Env') or []).count(key+'='+value)==1,'explicit environment pin differs')
    if finished:
        state=item.get('State',{})
        content.require(state.get('Status')=='exited' and state.get('Running') is False and type(state.get('ExitCode')) is int and state['ExitCode']==0 and state.get('OOMKilled') is False,'stage did not exit0')

class Engine:
    def __init__(self,evidence,nonce,docker='docker'):
        self.evidence,self.nonce,self.docker=evidence,nonce,docker;self.volumes={};self.containers=[]
    def call(self,args,label,timeout=180,**kwargs):return self.evidence.run(label,[self.docker,*args],timeout,**kwargs)
    def inspect(self,cid,label):
        value=worker._strict_receipt(self.call(['inspect',cid],label))
        content.require(isinstance(value,list) and len(value)==1 and value[0].get('Id')==cid,'exact inspect required');return value[0]
    def register(self,name,label,owned=False):
        content.require(NAME.fullmatch(name) is not None,'canonical volume name required')
        value=worker._strict_receipt(self.call(['volume','inspect',name],label))
        content.require(isinstance(value,list) and len(value)==1 and value[0].get('Name')==name and value[0].get('Driver')=='local','existing local volume required')
        labels=value[0].get('Labels') or {}
        if owned:content.require(labels.get('org.mingos.developer.nonce')==self.nonce,'fresh volume ownership differs')
        if name in self.volumes:content.require(labels==self.volumes[name],'volume labels changed')
        self.volumes[name]=labels;return value[0]
    def new_volume(self,role):
        name='developer-cache-'+self.nonce+'-'+role
        names=self.call(['volume','ls','--format','{{.Name}}'],role+'-existing-volumes').decode().splitlines()
        content.require(name not in names,'fresh volume already exists')
        returned=self.call(['volume','create','--label','org.mingos.developer.nonce='+self.nonce,name],role+'-volume-create').decode().strip()
        content.require(returned==name,'created volume name differs');self.register(name,role+'-volume-inspect',True);return name
    def fixed_image(self):
        value=worker._strict_receipt(self.call(['image','inspect',IMAGE],'fixed-image-inspect'))
        content.require(isinstance(value,list) and len(value)==1 and value[0].get('Id')==IMAGE and value[0].get('Os')=='linux' and value[0].get('Architecture')=='amd64' and value[0].get('RootFS',{}).get('Layers') and not value[0].get('Config',{}).get('Volumes'),'fixed local Linux helper image required')
    def container(self,label,mounts,command,root=False,env=None):
        for i,name in enumerate(sorted({m['source'] for m in mounts if m['type']=='volume'})):
            content.require(name in self.volumes,'unregistered volume');self.register(name,label+'-volume-before-'+str(i))
        argv=['create','--pull','never','--network','none','--read-only','--user','0:0' if root else '65534:65534','--cpus','2','--memory','4g','--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges','--label','org.mingos.developer.nonce='+self.nonce,'--label','org.mingos.developer.role='+label]
        if root:argv+=['--cap-add','CHOWN']
        for key,value in (env or {}).items():argv+=['--env',key+'='+value]
        for m in mounts:argv+=['--mount',mount_text(m)]
        argv+=['--entrypoint','python3',IMAGE,*command]
        cid=self.call(argv,label+'-create').decode().strip();content.require(plan.HEX64.fullmatch(cid) is not None,'exact created cid required')
        self.containers.append({'cid':cid,'role':label});self.evidence.receipt['containers']=self.containers
        item=self.inspect(cid,label+'-inspect-before');confinement(item,cid,self.nonce,label,mounts,command,root,env)
        error=None;raw=b''
        try:raw=self.call(['start','--attach',cid],label+'-run',1800)
        except Exception as caught:error=caught
        after=self.inspect(cid,label+'-inspect-after');confinement(after,cid,self.nonce,label,mounts,command,root,env)
        was_running=after.get('State',{}).get('Running')
        if was_running:
            try:self.call(['stop','--time','10',cid],label+'-stop-owned',60)
            finally:after=self.inspect(cid,label+'-inspect-after-stop')
            confinement(after,cid,self.nonce,label,mounts,command,root,env)
            content.require(after['State'].get('Running') is False,'owned stage still running')
        if error:raise error
        content.require(not was_running,'attached stage unexpectedly remained running')
        confinement(after,cid,self.nonce,label,mounts,command,root,env,True);return cid,raw

def start_retained(engine,cid,label,validate,start_args,**kwargs):
    """Preserve final inspection and stop only a freshly revalidated owned CID."""
    error=None;raw=b''
    try:raw=engine.call(start_args,label+'-start',1800,**kwargs)
    except Exception as caught:error=caught
    item=engine.inspect(cid,label+'-inspect-after')
    def validate_config(value):
        normalized=copy.deepcopy(value);normalized['State']={'Status':'created','Running':False}
        validate([normalized])
    validate_config(item)
    was_running=item.get('State',{}).get('Running')
    if was_running:
        try:engine.call(['stop','--time','10',cid],label+'-stop-owned',60)
        finally:item=engine.inspect(cid,label+'-inspect-after-stop')
        validate_config(item);content.require(item.get('State',{}).get('Running') is False,'owned worker still running')
    if error:raise error
    content.require(not was_running,'attached worker unexpectedly remained running')
    completed=original._one_inspection(content.encoded([item]),cid,True);validate(completed)
    return raw

def same_path_fd_stamp(path_stamp,fd_stamp):
    # Windows path stat reports creation time as ctime; fstat reports change
    # time in this runtime. Each side still retains its own complete stamp.
    return path_stamp[:-1]==fd_stamp[:-1] if sys.platform=='win32' else path_stamp==fd_stamp

def snapshot_helpers(output):
    target=output/'harness';target.mkdir(mode=0o700);records={}
    paths=[HERE/'developer_cache_worker.py',Path(archive.__file__),Path(worker.__file__)]
    for source in paths:
        source=original._checked_path(source,True);before=content.stamp(source.lstat())
        with source.open('rb') as stream:
            opened=content.stamp(original._opened_regular(stream))
            content.require(same_path_fd_stamp(before,opened),'helper source changed before open')
            raw=stream.read(512*1024+1)
            content.require(len(raw)<=512*1024 and content.stamp(original._opened_regular(stream))==opened
                and content.stamp(source.lstat())==before,'helper source changed during copy')
        compile(raw,source.name,'exec')
        original._write_exclusive(target/source.name,raw);records[source.name]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    original._directory_sync(target)
    original._check_helpers(target,records)
    return target,records
def run_harness(engine,harness,hashes,*args,**kwargs):
    def checked():
        original._check_helpers(harness,hashes)
        return {name:content.stamp((harness/name).lstat()) for name in ('',*hashes)}
    before=checked()
    try:return engine.container(*args,**kwargs)
    finally:content.require(checked()==before,'helper snapshot identity changed during worker')

def work(output,docker,action):
    output=Path(output).absolute();original._checked_path(output.parent,False)
    content.require(not output.exists(),'new absent output/evidence required')
    receipt={'schemaVersion':1,'profile':content.PROFILE,'stage':action,'status':'failed','nonce':secrets.token_hex(16),'operations':[],**content.FLAGS}
    evidence=original.Evidence(output,receipt);engine=Engine(evidence,receipt['nonce'],docker)
    return output,receipt,evidence,engine
def finish(output,receipt,evidence,status,error=None):
    receipt.update(status=status,resourcesPreserved=True,guestStarted=False)
    if error:receipt['error']=repr(error)
    original._write_exclusive(output/'result.json',content.encoded(receipt));evidence.snapshot('complete' if error is None else 'failure')

def export(cargo_volume,gradle_volume,baseline,output,docker='docker'):
    output,receipt,evidence,engine=work(output,docker,'export')
    try:
        baseline=original._checked_path(baseline,True);frozen_baseline=baseline.read_bytes()
        harness,hashes=snapshot_helpers(output);receipt['helperSnapshot']=hashes
        inputs=output/'baseline';inputs.mkdir();original._write_exclusive(inputs/'gradle-baseline.json',frozen_baseline)
        engine.fixed_image();engine.register(cargo_volume,'original-cargo-inspect');engine.register(gradle_volume,'original-gradle-inspect')
        target=engine.new_volume('export');receipt['exportVolume']=target
        init="import os; fd=os.open('/out',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); assert not os.listdir(fd); os.fchmod(fd,0o700); os.fsync(fd); os.fchown(fd,65534,65534); os.fsync(fd); os.close(fd)"
        engine.container('initialize-export',[mount('volume',target,'/out')],['-I','-B','-c',init],True)
        mounts=[mount('volume',cargo_volume,'/cargo',True,'cargo/registry'),mount('volume',gradle_volume,'/gradle',True),mount('bind',inputs,'/baseline',True),mount('bind',harness,'/harness',True),mount('volume',target,'/out')]
        cid,raw=run_harness(engine,harness,hashes,'export',mounts,['-I','-B','/harness/developer_cache_worker.py','export'])
        result=worker._strict_receipt(raw);content.require(result.get('status')=='developer-cache-exported-verified','actual export result missing')
        engine.call(['cp',cid+':/out',str(output/'exported')],'copy-export',1800)
        descriptor,verified=content.verify_export(output/'exported',result['descriptorSha256'])
        content.require(baseline.read_bytes()==frozen_baseline,'original baseline changed')
        receipt.update(descriptorSha256=result['descriptorSha256'],archive=descriptor['archive'],originalVolumes={'cargo':cargo_volume,'gradle':gradle_volume},validationKind='actual-docker',archiveVerifiedOnHost=True)
        finish(output,receipt,evidence,'developer-cache-exported-verified');return receipt
    except Exception as error:finish(output,receipt,evidence,'failed',error);raise

def restore_to_anonymous(engine,inputs,descriptor_sha,output,receipt):
    descriptor,prepared=content.verify_export(inputs,descriptor_sha)
    with (Path(inputs)/'dependencies.tar').open('rb') as source:private,prepared=original.private_copy_archive(source,output,descriptor['archive']['sha256'])
    # Existing helper freezer and worker contract stay entirely unchanged.
    helper,hashes=original._freeze_helpers(engine.evidence);receipt['restoreHelperSnapshot']=hashes
    nonce=engine.nonce
    cid=engine.call(plan.creator_command(nonce,engine.docker)[1:],'creator-create').decode().strip()
    content.require(plan.HEX64.fullmatch(cid) is not None,'creator cid required')
    receipt['creatorId']=cid
    before=original._one_inspection(engine.call(['inspect',cid],'creator-inspect'),cid)
    allocated=plan.validate_creator_inspection(before,cid,nonce);receipt.update(allocated)
    def creator_check(value):content.require(plan.validate_creator_inspection(value,cid,nonce)==allocated,'creator identity changed')
    start_retained(engine,cid,'creator',creator_check,['start','--attach',cid])
    volume=allocated['volumeName'];engine.register(volume,'restored-volume-inspect')
    for readonly,role in ((False,'restore'),(True,'verify')):
        original._check_helpers(helper,hashes)
        command=original.worker_command(nonce,volume,helper,readonly,prepared['archiveSha256'])
        engine.register(volume,role+'-volume-before-create')
        current=engine.call(command,role+'-create').decode().strip();content.require(plan.HEX64.fullmatch(current) is not None,'worker cid required')
        receipt[role+'ContainerId']=current
        before=original._one_inspection(engine.call(['inspect',current],role+'-inspect'),current)
        original.validate_worker_inspection(before,current,nonce,volume,helper,command,readonly)
        def validate(value):original.validate_worker_inspection(value,current,nonce,volume,helper,command,readonly)
        start=['start','--attach','--interactive',current]
        if readonly:raw=start_retained(engine,current,role,validate,start,input_bytes=worker._json_bytes(receipt['restore']))
        else:
            with private.open('rb') as stream:
                content.require(archive._hash(stream)==(prepared['archiveSha256'],prepared['archiveBytes']),'private copy changed')
                raw=start_retained(engine,current,role,validate,start,stdin=stream)
        original._check_helpers(helper,hashes)
        value=worker._strict_receipt(raw)
        if not readonly:receipt['restore']=original._worker_result(value,prepared,nonce)
        else:
            content.require(value.get('status')=='payload-readonly-verified' and value.get('statvfsReadOnly') is True,'actual RO verifier receipt required')
            for key in ('nonce','archiveSha256','archiveBytes','payloadBytes','files','rootIdentity','payloadIdentity','controlIdentity','leaseIdentity'):content.require(value.get(key)==receipt['restore'].get(key),'independent restore receipt differs: '+key)
            receipt['readonlyVerify']=value
    content.verify_export(inputs,descriptor_sha)
    return volume,descriptor

def restore(inputs,descriptor_sha,output,docker='docker'):
    # External pin and full strict archive validation precede every Docker call.
    inputs=original._checked_path(inputs,False);descriptor,_=content.verify_export(inputs,descriptor_sha)
    output,receipt,evidence,engine=work(output,docker,'restore')
    try:
        receipt.update(descriptorSha256=descriptor_sha,validationKind='actual-docker')
        engine.fixed_image();restored,descriptor=restore_to_anonymous(engine,inputs,descriptor_sha,output,receipt)
        harness,hashes=snapshot_helpers(output);receipt['projectionHelperSnapshot']=hashes
        cargo,gradle=engine.new_volume('cargo'),engine.new_volume('gradle');receipt.update(cargoVolume=cargo,gradleVolume=gradle)
        targets=[mount('volume',cargo,'/cargo-consumer'),mount('volume',gradle,'/gradle-consumer')]
        engine.container('initialize-consumers',targets,['-I','-B','-c',INIT],True)
        common=[mount('volume',restored,'/restored',True),mount('bind',inputs,'/inputs',True),mount('bind',harness,'/harness',True)]
        env={'DEVELOPER_DESCRIPTOR_SHA256':descriptor_sha}
        _,raw=run_harness(engine,harness,hashes,'project-consumers',common+targets,['-I','-B','/harness/developer_cache_worker.py','project'],env=env)
        receipt['projection']=worker._strict_receipt(raw);content.require(receipt['projection'].get('status')=='consumer-projection-verified','projection receipt missing')
        readonly=[{**m,'readonly':True} for m in targets]
        _,raw=run_harness(engine,harness,hashes,'verify-consumers',common+readonly,['-I','-B','/harness/developer_cache_worker.py','verify-consumers'],env=env)
        receipt['consumerVerify']=worker._strict_receipt(raw)
        content.require(receipt['consumerVerify'].get('status')=='consumer-readonly-content-verified' and receipt['consumerVerify'].get('statvfsReadOnly') is True,'independent consumer RO result missing')
        content.verify_export(inputs,descriptor_sha)
        finish(output,receipt,evidence,'developer-cache-restored-consumers-readonly-verified');return receipt
    except Exception as error:finish(output,receipt,evidence,'failed',error);raise

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='stage',required=True)
    p=sub.add_parser('export')
    p.add_argument('--cargo-volume',required=True);p.add_argument('--gradle-volume',required=True);p.add_argument('--baseline',type=Path,required=True)
    p=sub.add_parser('restore');p.add_argument('--inputs',type=Path,required=True);p.add_argument('--descriptor-sha256',required=True)
    for p in sub.choices.values():p.add_argument('--output',type=Path,required=True);p.add_argument('--docker',default='docker')
    a=parser.parse_args()
    result=export(a.cargo_volume,a.gradle_volume,a.baseline,a.output,a.docker) if a.stage=='export' else restore(a.inputs,a.descriptor_sha256,a.output,a.docker)
    print(json.dumps({k:v for k,v in result.items() if k not in ('operations','restore','readonlyVerify')},indent=2))
if __name__=='__main__':main()
