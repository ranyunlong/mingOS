"""Fixed developer preview pair driver. No arbitrary paths, retries or review edits."""
import hashlib,json,os,re,stat,sys
from pathlib import Path

SOURCE_FILES=tuple('scripts/boot/'+name+'.py' for name in ('developer_vm','developer_pack','developer_profile','bootlib','infra','data_disk','vm','isolation_profiles'))+(
    'probes/linux-isolation-source-v2/profile.json','probes/linux-isolation-source-v2/probe.rs',
    'crates/ming-app-host/examples/isolation_probe_v2.rs','Cargo.lock','rust-toolchain.toml','developer_vm_driver.py')
FLAGS=dict(diagnosticOnly=True,instrumentedQualification=False,formalQualification=False,finalSystemAccepted=False)
HEX=re.compile(r'[0-9a-f]{64}\Z')

def require(ok,message):
    if not ok:raise ValueError('Developer outer: '+message)

def strict(raw):
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'duplicate JSON key');result[key]=value
        return result
    require(len(raw)<=4*1024**2,'metadata too large')
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda v:(_ for _ in ()).throw(ValueError('nonfinite JSON')))

def identity(info):return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

def stable_bytes(path):
    info=path.lstat();require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'single-link regular source required')
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        # Windows lstat ctime is birth time while fstat can expose change time.
        require(identity(opened)[:6]==identity(info)[:6] and (os.name=='nt' or opened.st_ctime_ns==info.st_ctime_ns),'source replaced before open')
        raw=stream.read(4*1024**2+1)
        require(len(raw)<=4*1024**2 and identity(os.fstat(stream.fileno()))==identity(opened),'source changed while reading')
    require(identity(path.lstat())==identity(info),'source path changed');return raw

def verify_source(root,pin):
    root=Path(root);require(HEX.fullmatch(pin or '') is not None,'external source hash required')
    require(root.is_dir() and not root.is_symlink(),'literal source root required')
    raw=stable_bytes(root/'source-manifest.json');require(hashlib.sha256(raw).hexdigest()==pin,'source manifest pin differs')
    manifest=strict(raw)
    require(type(manifest) is dict and set(manifest)=={'schemaVersion','profile','files'} and type(manifest['schemaVersion']) is int and manifest['schemaVersion']==1 and manifest['profile']=='developer-vm-trusted-source-v1','source manifest identity differs')
    require(type(manifest['files']) is dict and set(manifest['files'])==set(SOURCE_FILES),'closed trusted source set differs')
    expected_files={'source-manifest.json',*SOURCE_FILES};expected_dirs=set()
    for name in expected_files:
        expected_dirs.update(p.as_posix() for p in Path(name).parents if p!=Path('.'))
    before={}
    def walk(directory):
        info=directory.lstat();require(stat.S_ISDIR(info.st_mode),'literal source directories required')
        before[directory]=identity(info)
        for path in directory.iterdir():
            name=path.relative_to(root).as_posix();info=path.lstat()
            if stat.S_ISDIR(info.st_mode):require(name in expected_dirs,'extra source directory');walk(path)
            else:require(name in expected_files and stat.S_ISREG(info.st_mode) and info.st_nlink==1,'extra/linked source file')
    walk(root)
    require({p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}==expected_files,'source inventory differs')
    for name,record in manifest['files'].items():
        require(type(record) is dict and set(record)=={'bytes','sha256'} and type(record['bytes']) is int and HEX.fullmatch(record['sha256'] or '') is not None,'source record differs')
        data=stable_bytes(root/name);require(len(data)==record['bytes'] and hashlib.sha256(data).hexdigest()==record['sha256'],'trusted source bytes differ: '+name)
    require(stable_bytes(root/'source-manifest.json')==raw and all(identity(path.lstat())==value for path,value in before.items()),'source tree changed')
    return manifest

def pair(api,pin,emit):
    require(HEX.fullmatch(pin or '') is not None,'exact pack hash required')
    pack=Path('/pack');work=Path('/work/mingos-boot-infra-20261025');instance=work/'developer-instance.json';runtime=Path('/run/mingos-runtime')
    verified=api.verify(pack,pin)
    require(verified.get('status')=='developer-pack-bytes-verified' and verified.get('manifestSha256')==pin,'pack verifier receipt differs');emit('verify',verified)
    initialized=api.init(pack,pin,work);require(initialized.get('status')=='developer-instance-initialized','init receipt differs');emit('init',initialized)
    generation=None;boots=set();sessions=set()
    for index in (1,2):
        report=api.start(pack,pin,instance,Path('/results/boot-%04d'%index),runtime)
        require(report.get('status')=='developer-session-normal' and report.get('normalBoots')==index,'normal boot receipt differs')
        require(isinstance(report.get('generation'),str) and (generation is None or report['generation']==generation),'generation changed')
        generation=report['generation'];require(report.get('bootId') not in boots and report.get('sessionId') not in sessions,'repeated boot/session')
        boots.add(report.get('bootId'));sessions.add(report.get('sessionId'));emit('boot-%04d'%index,report)
        inspected=api.inspect(instance)
        require(inspected.get('status')=='developer-instance-inspected' and inspected.get('state')=='ready' and inspected.get('normalBoots')==index and inspected.get('generation')==generation,'inspect refused/locked/count differs');emit('inspect-%04d'%index,inspected)
    return dict(schemaVersion=1,status='developer-preview-pair-observed',normalBoots=2,generation=generation,
        sessionDurationMs=15000,normalMarkerReopenVerified=True,applicationPersistenceVerified=False,deliveryAccepted=False,**FLAGS)

def dispatch(api,stage,pin,emit):
    require(stage in ('verify','pair'),'closed verify or pair stage required')
    require(HEX.fullmatch(pin or '') is not None,'exact pack hash required')
    if stage=='pair':return pair(api,pin,emit)
    result=api.verify(Path('/pack'),pin)
    require(result.get('status')=='developer-pack-bytes-verified' and result.get('manifestSha256')==pin,'pack verifier receipt differs')
    emit('verify',result);return result

def main():
    require(len(sys.argv)==4,'fixed stage, source and pack pins required');stage,source_pin,pack_pin=sys.argv[1:]
    require(stage in ('verify','pair'),'closed verify or pair stage required')
    require(sys.platform=='linux' and os.getuid()==65534 and os.getgid()==65534,'Linux UID/GID65534 required')
    require(all(os.statvfs(p).f_flag & os.ST_RDONLY for p in ('/','/repo','/pack')),'source/pack/root readonly required')
    manifest=verify_source('/repo',source_pin)
    sys.path.insert(0,'/repo/scripts/boot');import developer_vm as api
    def emit(action,result):print(json.dumps({'action':action,'result':result},sort_keys=True),flush=True)
    emit('source-verified',{'sourceManifestSha256':source_pin,'fileCount':len(manifest['files']),'pythonExecutable':sys.executable,'pythonVersion':sys.version,**FLAGS})
    if stage=='verify':
        dispatch(api,stage,pack_pin,emit);verify_source('/repo',source_pin);return
    # Exclusive outer attempt on retained results: interrupted pairs cannot be replayed.
    attempt=Path('/results/developer-pair-attempt.json')
    with attempt.open('xb') as stream:
        stream.write(json.dumps({'sourceManifestSha256':source_pin,'packManifestSha256':pack_pin,'retryAllowed':False,**FLAGS}).encode());stream.flush();os.fchmod(stream.fileno(),0o600);os.fsync(stream.fileno())
    result=dispatch(api,stage,pack_pin,emit)
    verify_source('/repo',source_pin);api.verify(Path('/pack'),pack_pin)
    result.update(sourceManifestSha256=source_pin,packManifestSha256=pack_pin)
    with Path('/results/developer-pair-result.json').open('xb') as stream:
        stream.write(json.dumps(result,sort_keys=True).encode());stream.flush();os.fchmod(stream.fileno(),0o600);os.fsync(stream.fileno())
    emit('pair-result',result)

if __name__=='__main__':main()
