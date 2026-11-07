#!/usr/bin/env python3
"""Offline Kotlin Docker orchestrator. Import/--help never run Docker."""
import argparse,hashlib,json,os,re,shutil,subprocess,uuid
from pathlib import Path
IMAGE='sha256:5fba561826e826a26e5179d8b58425d0b5125d679e7dc64459d38bad0c6e417f'
REQUIRED_SOURCE=('scripts/boot/build-linux-host-kotlin.mjs','scripts/boot/linux-host-kotlin.init.gradle','gradle/wrapper/gradle-wrapper.jar','gradle/wrapper/gradle-wrapper.properties','settings.gradle.kts','build.gradle.kts')
PROFILES={'developer':['text-entry-demo','animated-demo'],'uninstall':['read-file','image-viewer','text-entry-demo','uninstall'],'update':['read-file','image-viewer','text-entry-demo','old','new','absent-write']}
TMPFS='rw,nosuid,nodev,size=1g,mode=1777'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    with Path(path).open('x',encoding='utf8',newline='\n') as stream:
        json.dump(value,stream,indent=2,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())
def path_check(path,exists=True):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts or any(c in str(path) for c in ',\n\r\x00'):raise ValueError('canonical absolute path required')
    for p in (*reversed(path.parents),path):
        if p.exists() and (p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400):raise ValueError('symlink/reparse path forbidden')
    if exists and not path.exists():raise ValueError('missing input: '+str(path))
    return path

def validate(options):
    o=dict(options)
    if o.get('profile') not in PROFILES:raise ValueError('explicit developer, update or uninstall profile required')
    for name in ('repo','baseline'):o[name]=path_check(o[name])
    o['output']=path_check(o['output'],False)
    if not o['repo'].is_dir() or not o['baseline'].is_file():raise ValueError('source directory and baseline file required')
    if o['output'].exists():raise ValueError('new absent output directory required; failed generations are nonretryable')
    if not o['output'].parent.is_dir():raise ValueError('output parent must exist')
    if o['output'].is_relative_to(o['repo']) or o['repo'].is_relative_to(o['output']):raise ValueError('output/source overlap forbidden')
    if o['baseline'].is_relative_to(o['output']):raise ValueError('baseline/output overlap forbidden')
    for name in REQUIRED_SOURCE:
        p=path_check(o['repo']/name)
        if not p.is_file():raise ValueError('required source input missing')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,200}',o['original_cache']):raise ValueError('named original cache volume required')
    value=json.loads(o['baseline'].read_text(encoding='utf-8-sig'))
    if value.get('schemaVersion')!=1 or not isinstance(value.get('files'),dict) or not value['files']:raise ValueError('nonempty dependency baseline required')
    for name,digest in value['files'].items():
        if not isinstance(name,str) or name.startswith('/') or '\\' in name or ':' in name or any(p in ('','.','..') for p in name.split('/')) or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('invalid dependency baseline file')
    return o

def mount(kind,source,target,readonly=False,subpath=None):
    value=f'type={kind},source={source},target={target}'
    if readonly:value+=',readonly'
    if kind=='volume':value+=',volume-nocopy'
    if subpath:value+=',volume-subpath='+subpath
    return value

def expected_inspection(argv,cid):
    """Expected model also consumed by fake controls; real inspect checked independently."""
    one=lambda flag:argv[argv.index(flag)+1]
    labels=dict(argv[i+1].split('=',1) for i,x in enumerate(argv) if x=='--label')
    mounts=[]
    for i,x in enumerate(argv):
        if x=='--mount':
            fields={};flags=set()
            for field in argv[i+1].split(','):
                if '=' in field:k,v=field.split('=',1);fields[k]=v
                else:flags.add(field)
            record={'Type':fields['type'],'Source':fields['source'],'Target':fields['target'],'ReadOnly':'readonly' in flags}
            if fields['type']=='volume':record['VolumeOptions']={'NoCopy':True,**({'Subpath':fields['volume-subpath']} if 'volume-subpath'in fields else {})}
            mounts.append(record)
    return {'Id':cid,'Image':IMAGE,'Name':one('--name'),'Config':{'User':one('--user'),'Labels':labels},'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'NanoCpus':2000000000,'Memory':4294967296,'PidsLimit':256,'CapDrop':['ALL'],'CapAdd':['CHOWN'] if '--cap-add' in argv else [],'SecurityOpt':['no-new-privileges'],'Tmpfs':{'/tmp':TMPFS},'Mounts':mounts,'Binds':None,'VolumesFrom':None},'Mounts':[{'Type':x['Type'],'Source':x['Source'],**({'Name':x['Source']} if x['Type']=='volume' else {}),'Destination':x['Target'],'RW':not x['ReadOnly']} for x in mounts],'State':{'Running':False,'ExitCode':0,'OOMKilled':False}}

def check_inspection(value,expected,finished=False):
    if value.get('Id')!=expected['Id'] or value.get('Image')!=IMAGE:raise ValueError('actual container identity differs')
    if value.get('Config',{}).get('User')!=expected['Config']['User'] or any(value['Config'].get('Labels',{}).get(k)!=v for k,v in expected['Config']['Labels'].items()):raise ValueError('actual user/ownership labels differ')
    for key,want in expected['HostConfig'].items():
        got=value.get('HostConfig',{}).get(key)
        if key in ('Binds','VolumesFrom','CapAdd'):got=got or [];want=want or []
        if key in ('CapAdd','CapDrop'):got=[x.removeprefix('CAP_') for x in got];want=[x.removeprefix('CAP_') for x in want]
        if key=='Mounts':
            def normalized_mounts(items):
                normalized=[]
                for item in items:
                    value={**item,'ReadOnly':bool(item.get('ReadOnly',False))}
                    if value['Type']=='bind' and re.match(r'^[A-Za-z]:[\\/]',value['Source']):value['Source']='/run/desktop/mnt/host/'+value['Source'][0].lower()+'/'+value['Source'][3:].replace(chr(92),'/')
                    normalized.append(value)
                return normalized
            got=normalized_mounts(got);want=normalized_mounts(want)
        if got!=want:raise ValueError('actual isolation/mount differs: '+key)
    effective=value.get('Mounts',[])
    if len(effective)!=len(expected['HostConfig']['Mounts']):raise ValueError('effective mount count differs')
    for want in expected['HostConfig']['Mounts']:
        matches=[x for x in effective if x.get('Destination')==want['Target'] and x.get('Type')==want['Type'] and x.get('RW')== (not want['ReadOnly'])]
        if len(matches)!=1:raise ValueError('effective mount type/mode differs')
        actual=matches[0]
        if want['Type']=='volume':
            if actual.get('Name')!=want['Source']:raise ValueError('effective mount volume differs')
        else:
            allowed=[want['Source']]
            if re.match(r'^[A-Za-z]:[\\/]',want['Source']):allowed.append('/run/desktop/mnt/host/'+want['Source'][0].lower()+'/'+want['Source'][3:].replace('\\','/'))
            if actual.get('Source') not in allowed:raise ValueError('effective mount bind source differs')
    if finished and (value['State']['Running'] or value['State']['ExitCode']!=0 or value['State']['OOMKilled']):raise ValueError('container failed/OOM/running')

class CapturedDocker:
    def __init__(self,executable,evidence):self.executable=executable;self.evidence=evidence
    def __call__(self,argv,label):
        write(self.evidence/(label+'.command.json'),{'argv':[self.executable,*argv],'timeoutSeconds':1950})
        try:
            p=subprocess.run([self.executable,*argv],capture_output=True,timeout=2 if label=='synthetic-running-run' else 1950)
            (self.evidence/(label+'.stdout.log')).write_bytes(p.stdout);(self.evidence/(label+'.stderr.log')).write_bytes(p.stderr)
            write(self.evidence/(label+'.exit.json'),{'exitCode':p.returncode})
            if p.returncode:raise RuntimeError('Docker command failed: '+label)
            return p.stdout.decode('utf8')
        except subprocess.TimeoutExpired as e:
            (self.evidence/(label+'.stdout.log')).write_bytes(e.stdout or b'');(self.evidence/(label+'.stderr.log')).write_bytes(e.stderr or b'')
            write(self.evidence/(label+'.exit.json'),{'exitCode':None,'timedOut':True});raise

def run(options,docker=None):
    o=validate(options);out=o['output'];out.mkdir();generation='mingos-kotlin-delivery-'+uuid.uuid4().hex;nonce=uuid.uuid4().hex
    volumes={role:generation+'-'+role for role in ('source','cache','work','output')}
    meta={'schemaVersion':1,'generation':generation,'nonce':nonce,'profile':o['profile'],'labels':PROFILES[o['profile']],'image':IMAGE,'originalCacheVolume':o['original_cache'],'volumes':volumes,'runnerSha256':sha(o['repo']/REQUIRED_SOURCE[0]),'initSha256':sha(o['repo']/REQUIRED_SOURCE[1]),'formalQualification':False,'finalSystemAccepted':False}
    payload=Path(__file__).with_name('kotlin_delivery_pipeline.mjs');shutil.copyfile(payload,out/'kotlin_delivery_pipeline.mjs');shutil.copyfile(Path(__file__).with_name('kotlin_delivery_replay.mjs'),out/'kotlin_delivery_replay.mjs');shutil.copyfile(o['baseline'],out/'dependency-baseline.json')
    meta['replayContractsSha256']=sha(out/'kotlin_delivery_replay.mjs');meta['payloadSha256']=sha(out/'kotlin_delivery_pipeline.mjs');meta['baselineManifestSha256']=sha(out/'dependency-baseline.json');write(out/'generation.json',meta)
    call=docker or CapturedDocker(o.get('docker','docker'),out);containers=[];original_contract=None
    def one_json(argv,label):
        values=json.loads(call(argv,label))
        if not isinstance(values,list) or len(values)!=1:raise ValueError('exact single inspect response required')
        return values[0]
    def container(label,mounts,command,root=False):
        # Docker create would auto-create a missing named volume: reject it first.
        mounted_volumes={dict(item.split('=',1) for item in text.split(',') if '=' in item)['source'] for text in mounts if text.startswith('type=volume,')}
        for name in sorted(mounted_volumes):
            value=one_json(['volume','inspect',name],label+'-volume-before-'+('original' if name==o['original_cache'] else next((role for role,v in volumes.items() if v==name),'unknown')))
            if value.get('Name')!=name or value.get('Driver')!='local':raise ValueError('stage volume identity/driver differs')
            if name==o['original_cache']:
                if (value.get('Labels') or {}) != (original_contract.get('Labels') or {}):raise ValueError('original cache labels changed')
            else:
                if name not in volumes.values() or value.get('Labels',{}).get('org.mingos.generation')!=generation or value.get('Labels',{}).get('org.mingos.nonce')!=nonce:raise ValueError('stage volume ownership differs')
        argv=['create','--pull','never','--init','--name',generation+'-'+label,'--label','org.mingos.generation='+generation,'--label','org.mingos.nonce='+nonce,'--network','none','--read-only','--user','0:0' if root else '65534:65534','--cpus','2','--memory','4g','--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:'+TMPFS]
        if root:argv+=['--cap-add','CHOWN']
        for text in mounts:argv+=['--mount',text]
        argv+=['--entrypoint','node',IMAGE,*command]
        cid=call(argv,label+'-create').strip()
        if not re.fullmatch('[0-9a-f]{64}',cid):raise ValueError('exact created container ID required')
        containers.append({'label':label,'cid':cid,'mounts':mounts});expected=expected_inspection(argv,cid)
        check_inspection(one_json(['inspect',cid],label+'-inspect-before'),expected)
        start_error=None;stdout=''
        try:stdout=call(['start','-a',cid],label+'-run')
        except BaseException as error:start_error=error
        # Even timeout/nonzero start retains a final actual inspection.
        after=one_json(['inspect',cid],label+'-inspect-after')
        check_inspection(after,expected)
        was_running=after['State']['Running']
        if was_running:
            # Identity/labels were checked immediately above; stop only our exact CID.
            try:call(['stop','--time','10',cid],label+'-stop-owned')
            finally:after=one_json(['inspect',cid],label+'-inspect-after-stop')
            check_inspection(after,expected)
            if after['State']['Running']:raise RuntimeError('owned container still running after stop')
        if start_error is not None:
            start_error.final_inspection=after;start_error.was_running_after_start=was_running;start_error.container_id=cid
            raise start_error
        check_inspection(after,expected,True)
        return cid,stdout
    try:
        image=one_json(['image','inspect',IMAGE],'fixed-image')
        if image.get('Id')!=IMAGE or image.get('Os')!='linux' or image.get('Architecture')!='amd64' or not image.get('RootFS',{}).get('Layers'):raise ValueError('fixed Linux amd64 Kotlin image required')
        original=one_json(['volume','inspect',o['original_cache']],'original-cache')
        if original.get('Name')!=o['original_cache'] or original.get('Driver')!='local':raise ValueError('existing local original cache required')
        original_contract=original
        existing=call(['volume','ls','--format','{{.Name}}'],'existing-volumes').splitlines()
        for role,name in volumes.items():
            if name in existing:raise ValueError('new volume name already exists')
            call(['volume','create','--label','org.mingos.generation='+generation,'--label','org.mingos.nonce='+nonce,name],role+'-volume-create')
            value=one_json(['volume','inspect',name],role+'-volume-inspect')
            if value.get('Name')!=name or value.get('Driver')!='local' or value.get('Labels',{}).get('org.mingos.generation')!=generation or value.get('Labels',{}).get('org.mingos.nonce')!=nonce:raise ValueError('new local volume ownership differs')
        fresh=[mount('volume',volumes[k],target) for k,target in [('source','/source'),('cache','/deps/gradle'),('work','/work'),('output','/out')]]
        init="const fs=require('node:fs'); for(const p of ['/source','/deps/gradle','/work','/out']){if(fs.readdirSync(p).length)throw Error('literal empty volume required');const fd=fs.openSync(p,fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW);fs.fchmodSync(fd,0o700);fs.fsyncSync(fd);fs.fchownSync(fd,65534,65534);fs.fsyncSync(fd);fs.closeSync(fd)}"
        container('initialize',fresh,['-e',init],True)
        common=[mount('bind',out,'/harness',True),mount('volume',o['original_cache'],'/original-cache',True)]
        container('prepare',common+[mount('bind',o['repo'],'/checkout',True),*fresh],['/harness/kotlin_delivery_pipeline.mjs','prepare'])
        def later(readonly=False):return common+[mount('volume',volumes['source'],'/repo',True,'repo'),mount('volume',volumes['source'],'/snapshot',True),mount('volume',volumes['cache'],'/deps/gradle',readonly),mount('volume',volumes['output'],'/out',readonly),*([] if readonly else [mount('volume',volumes['work'],'/work')])]
        if o.get('synthetic_controls'):
            _,stdout=container('synthetic-readonly',later(True),['/harness/kotlin_delivery_pipeline.mjs','input-readonly-control'])
            lines=[line for line in stdout.splitlines() if line.startswith('SYNTHETIC_CONTROL ')]
            if len(lines)!=1 or json.loads(lines[0][18:]).get('status')!='readonly-input-control-passed':raise ValueError('actual readonly control result required')
            controls={'readonlyInputs':json.loads(lines[0][18:])}
            for name,code,want_running in [('synthetic-failure','process.exit(7)',False),('synthetic-running',"process.stdout.write('synthetic-running-ready\\n');setInterval(()=>{},1000)",True)]:
                try:container(name,later(True),['-e',code])
                except (RuntimeError,subprocess.TimeoutExpired) as error:
                    final=getattr(error,'final_inspection',None)
                    if final is None or final['State']['Running'] or final['State']['OOMKilled'] or getattr(error,'was_running_after_start',None)!=want_running:raise ValueError('synthetic failure/stop final inspection missing') from error
                    if not want_running and final['State']['ExitCode']!=7:raise ValueError('expected synthetic exit7 missing') from error
                    controls[name]={'status':'expected-failure-inspected','containerId':error.container_id,'wasRunningAfterStart':want_running,'finalState':final['State']}
                else:raise ValueError('synthetic expected failure did not occur')
            result={**meta,'status':'synthetic-input-controls-passed','controls':controls,'containers':containers,'resourcesPreserved':True,'validationKind':'fake-control' if docker else 'actual-docker','compileExecuted':False,'guestStarted':False}
            write(out/'result.json',result);return result
        for label in meta['labels']:container('build-'+label,later(),['/harness/kotlin_delivery_pipeline.mjs','build',label])
        cid,stdout=container('verify-all',later(True),['/harness/kotlin_delivery_pipeline.mjs','verify-all'])
        lines=[line for line in stdout.splitlines() if line.startswith('DELIVERY_RESULT ')]
        # Fake controls explicitly return a response rather than asserting real compilation.
        replay=json.loads(lines[-1][16:] if lines else stdout)
        if replay.get('status')!='independent-readonly-replay-verified' or replay.get('originalCacheUnchanged') is not True:raise ValueError('independent replay/original cache stability evidence required')
        call(['cp',cid+':/out',str(out/'packages-and-evidence')],'output-copy')
        result={**meta,'status':'pipeline-completed','containers':containers,'replay':replay,'resourcesPreserved':True,'validationKind':'fake-control' if docker else 'actual-docker','guestStarted':False}
        write(out/'result.json',result);return result
    except BaseException as error:
        write(out/'result.json',{**meta,'status':'failed','error':repr(error),'containers':containers,'resourcesPreserved':True,'guestStarted':False,'validationKind':'fake-control' if docker else 'actual-docker'})
        raise

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repo','output','baseline','original-cache'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--synthetic-controls',action='store_true',help='only clone/RO/nonzero/owned-stop controls; never compile');parser.add_argument('--profile',required=True,choices=tuple(PROFILES));parser.add_argument('--docker',default='docker')
    print(json.dumps(run(vars(parser.parse_args())),default=str))
if __name__=='__main__':main()
