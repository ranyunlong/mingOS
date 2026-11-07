"""Developer cache export/projection; unchanged strict USTAR/restore implementation.

Prototype only. No import-time Docker/build/guest or deletion operations.
"""
import argparse,hashlib,io,json,os,shutil,stat,sys,tarfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
for parent in HERE.parents:
    candidate=parent/'scripts/boot'
    if (candidate/'qualification_volume_archive.py').is_file():sys.path.insert(0,str(candidate));break
import qualification_volume_archive as archive
import qualification_volume_worker as original_worker

PROFILE='developer-cache-v1'
MAPPING={'cargo':'c/<registry-relative>','gradleCaches':'g/<caches-relative>','gradleOther':'d/<gradle-relative>'}
FLAGS={'formalQualification':False,'finalSystemAccepted':False}
def require(ok,message):
    if not ok:raise ValueError('developer cache: '+message)
def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for raw in iter(lambda:stream.read(1024**2),b''):h.update(raw)
    return h.hexdigest()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def save(path,value):
    with Path(path).open('xb') as stream:stream.write(encoded(value));stream.flush();os.fsync(stream.fileno())
def physical(role,path):
    archive.safe_path(path)
    require(role in ('cargo','gradle'),'unknown logical role')
    return 'c/'+path if role=='cargo' else ('g'+path[6:] if path=='caches' or path.startswith('caches/') else 'd/'+path)
def logical(path):
    archive.safe_path(path)
    if path.startswith('c/'):return 'cargo',path[2:]
    if path=='g' or path.startswith('g/'):return 'gradle','caches'+path[1:]
    if path.startswith('d/'):
        relative=path[2:]
        require(relative!='caches' and not relative.startswith('caches/'),'noncanonical Gradle projection')
        return 'gradle',relative
    raise ValueError('unknown projection path')
def target_mode(role,kind,mode):
    require(role in ('cargo','gradle') and kind in ('file','directory'),'unsupported role/type')
    if role=='cargo' and kind=='file' and mode==0o640:return 0o644
    require(mode in (archive.FILE_MODES if kind=='file' else archive.DIRECTORY_MODES),'unsupported original mode')
    return mode
def stamp(info):return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
def scan(root):
    """Stable complete tree (including empty directories), rejecting all links."""
    root=Path(root);require(root.is_dir() and not root.is_symlink(),'literal tree required')
    result={};identities={}
    def walk(path):
        info=path.lstat();before=stamp(info);names=sorted(os.listdir(path));identities[path.relative_to(root).as_posix()]=before
        for leaf in names:
            item=path/leaf;name=item.relative_to(root).as_posix();archive.safe_path(name)
            info=item.lstat();require(not stat.S_ISLNK(info.st_mode),'links forbidden')
            kind='directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else None
            require(kind is not None,'special object forbidden')
            entry={'path':name,'type':kind,'mode':stat.S_IMODE(info.st_mode),'uid':info.st_uid,'gid':info.st_gid}
            if kind=='directory':walk(item)
            else:
                require(info.st_nlink==1,'hardlinks forbidden');first=stamp(info)
                entry.update(size=info.st_size,sha256=sha(item),nlink=1)
                require(stamp(item.lstat())==first,'file changed while hashing');identities[name]=first
            result[name]=entry
        require(os.listdir(path)==names or sorted(os.listdir(path))==names,'directory set changed')
        require(stamp(path.lstat())==before,'directory changed during scan')
    walk(root)
    require({p.relative_to(root).as_posix() for p in root.rglob('*')}==set(result),'tree changed during scan')
    for name,identity in identities.items():require(stamp((root/name).lstat())==identity,'earlier tree entry changed')
    return {name:result[name] for name in sorted(result)}
def file_map(entries):return {name:record['sha256'] for name,record in entries.items() if record['type']=='file'}
def projected(logical_inventory):
    entries={name:{'path':name,'type':'directory','mode':0o755,'uid':65534,'gid':65534} for name in ('c','d')}
    normalizations=[]
    for role in ('cargo','gradle'):
        for name,record in logical_inventory[role].items():
            require(record.get('path')==name and (record.get('uid'),record.get('gid'))==(65534,65534),'original owner/path differs')
            target=physical(role,name);require(target not in entries,'projection collision')
            mode=target_mode(role,record['type'],record['mode'])
            normalized={**record,'path':target,'mode':mode}
            entries[target]=normalized
            if mode!=record['mode']:normalizations.append({'role':role,'path':name,'originalMode':record['mode'],'targetMode':mode})
    archive.validate_manifest({'schemaVersion':1,'uid':65534,'gid':65534,'entries':list(entries.values())})
    return dict(sorted(entries.items())),normalizations
def validate_baseline(value,inventory):
    compact={'schemaVersion','files'}
    complete=compact|{'directories','originalCacheUnchanged','compileExecuted','comparison'}
    require(type(value) is dict and set(value) in (compact,complete)
        and type(value['schemaVersion']) is int and value['schemaVersion']==1,'baseline schema differs')
    require(type(value['files']) is dict and bool(value['files'])
        and value['files']==file_map(inventory),'complete original Gradle baseline differs')
    if set(value)==complete:
        directories=value['directories']
        expected={''}|{name for name,entry in inventory.items() if entry['type']=='directory'}
        require(type(directories) is list and all(type(name) is str for name in directories)
            and len(directories)==len(expected) and set(directories)==expected,
            'complete original Gradle baseline directories differ')
        require(value['originalCacheUnchanged'] is True and value['compileExecuted'] is False,
            'original baseline execution policy differs')
        # comparison is retained as byte-bound provenance, never interpreted or executed.

def export_inputs(cargo,gradle,baseline,output):
    cargo,gradle,baseline,output=map(Path,(cargo,gradle,baseline,output))
    require(output.is_dir() and not list(output.iterdir()),'literal empty output required')
    original={'cargo':scan(cargo),'gradle':scan(gradle)}
    require(original['cargo'] and original['gradle'],'empty input tree forbidden')
    baseline_raw=baseline.read_bytes();sealed=archive.strict_json(baseline_raw)
    validate_baseline(sealed,original['gradle'])
    entries,normalizations=projected(original)
    manifest={'schemaVersion':1,'uid':65534,'gid':65534,'entries':list(entries.values())}
    manifest_raw=encoded(manifest)
    require(len(manifest_raw)<=archive.MAX_MANIFEST,'manifest too large')
    with (output/'dependencies.tar').open('xb') as raw:
        with tarfile.open(fileobj=raw,mode='w',format=tarfile.USTAR_FORMAT) as tar:
            for name,entry in entries.items():
                member=tarfile.TarInfo(name);member.uid=member.gid=65534;member.mode=entry['mode'];member.mtime=0
                member.type=tarfile.DIRTYPE if entry['type']=='directory' else tarfile.REGTYPE
                member.size=entry.get('size',0)
                if entry['type']=='file':
                    role,relative=logical(name)
                    with ((cargo if role=='cargo' else gradle)/relative).open('rb') as source:tar.addfile(member,source)
                else:tar.addfile(member)
            member=tarfile.TarInfo(archive.MANIFEST_NAME);member.uid=member.gid=65534;member.mode=0o600;member.mtime=0;member.size=len(manifest_raw)
            tar.addfile(member,io.BytesIO(manifest_raw))
        raw.flush();os.fsync(raw.fileno())
    require(scan(cargo)==original['cargo'] and scan(gradle)==original['gradle'],'original inputs changed during export')
    require(baseline.read_bytes()==baseline_raw,'baseline changed during export')
    (output/'gradle-baseline.json').write_bytes(baseline_raw)
    with (output/'gradle-baseline.json').open('rb') as stream:os.fsync(stream.fileno())
    descriptor={'schemaVersion':1,'profile':PROFILE,'mapping':MAPPING,'original':original,'normalizations':normalizations,
        'archive':{'path':'dependencies.tar','sha256':sha(output/'dependencies.tar'),'bytes':(output/'dependencies.tar').stat().st_size,'embeddedManifestSha256':hashlib.sha256(manifest_raw).hexdigest()},
        'baseline':{'path':'gradle-baseline.json','sha256':sha(output/'gradle-baseline.json'),'bytes':len(baseline_raw)},**FLAGS}
    save(output/'developer-inputs.json',descriptor)
    verify_export(output,sha(output/'developer-inputs.json'))
    return descriptor
def verify_export(directory,expected_sha256):
    directory=Path(directory);require(archive.HEX.fullmatch(expected_sha256 or '') is not None,'exact external descriptor pin required')
    require(directory.is_dir() and not directory.is_symlink() and {p.name for p in directory.iterdir()}=={'developer-inputs.json','dependencies.tar','gradle-baseline.json'},'exact export directory inventory required')
    identities={}
    for name in ('developer-inputs.json','dependencies.tar','gradle-baseline.json'):
        info=(directory/name).lstat();require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'single-link regular export files required')
        identities[name]=stamp(info)
    require(sha(directory/'developer-inputs.json')==expected_sha256,'external descriptor pin differs')
    raw=(directory/'developer-inputs.json').read_bytes();value=archive.strict_json(raw)
    require(type(value) is dict and set(value)=={'schemaVersion','profile','mapping','original','normalizations','archive','baseline',*FLAGS},'closed developer descriptor required')
    require(type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['profile']==PROFILE and value['mapping']==MAPPING and all(value[k] is False for k in FLAGS),'descriptor identity/policy differs')
    require(type(value['original']) is dict and set(value['original'])=={'cargo','gradle'},'two original trees required')
    entries,normalizations=projected(value['original']);require(value['normalizations']==normalizations,'mode provenance differs')
    for key,name,fields in [('archive','dependencies.tar',{'path','sha256','bytes','embeddedManifestSha256'}),('baseline','gradle-baseline.json',{'path','sha256','bytes'})]:
        record=value[key];require(type(record) is dict and set(record)==fields and record['path']==name,'closed artifact record required')
        path=directory/name;info=path.lstat();require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size==record['bytes'] and sha(path)==record['sha256'],'artifact bytes differ: '+key)
    with (directory/'dependencies.tar').open('rb') as source:plan=archive.verify_volume_archive(source,value['archive']['sha256'])
    require(plan['manifestSha256']==value['archive']['embeddedManifestSha256'],'embedded manifest hash differs')
    require({e['path']:{k:v for k,v in e.items() if k!='offset'} for e in plan['entries']}==entries,'logical/physical complete inventory differs')
    baseline=archive.strict_json((directory/'gradle-baseline.json').read_bytes())
    validate_baseline(baseline,value['original']['gradle'])
    require((directory/'developer-inputs.json').read_bytes()==raw,'descriptor changed during verification')
    require({p.name for p in directory.iterdir()}==set(identities) and all(stamp((directory/name).lstat())==identity for name,identity in identities.items()),'export tree changed during verification')
    return value,plan
def target_inventory(descriptor,role):
    projected(descriptor['original'])
    return {name:{**record,'mode':target_mode(role,record['type'],record['mode'])} for name,record in descriptor['original'][role].items()}
def verify_consumers(cargo_root,gradle_root,descriptor):
    cargo_root,gradle_root=map(Path,(cargo_root,gradle_root))
    for root in (cargo_root,gradle_root,cargo_root/'cargo',cargo_root/'cargo/registry'):
        info=root.lstat();require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode)==0o700 and (info.st_uid,info.st_gid)==(65534,65534),'consumer private root owner/mode differs')
    require({p.name for p in cargo_root.iterdir()}=={'cargo'} and {p.name for p in (cargo_root/'cargo').iterdir()}=={'registry'},'Cargo consumer root layout differs')
    require(scan(cargo_root/'cargo/registry')==target_inventory(descriptor,'cargo'),'Cargo complete byte/mode inventory differs')
    require(scan(gradle_root)==target_inventory(descriptor,'gradle'),'Gradle complete byte/mode inventory differs')
    return {'schemaVersion':1,'status':'consumer-readonly-content-verified','cargoFileCount':len(file_map(descriptor['original']['cargo'])),'gradleFileCount':len(file_map(descriptor['original']['gradle'])),**FLAGS}
def project_consumers(payload,cargo_root,gradle_root,descriptor):
    payload,cargo_root,gradle_root=map(Path,(payload,cargo_root,gradle_root))
    require(cargo_root.is_dir() and gradle_root.is_dir() and not cargo_root.is_symlink() and not gradle_root.is_symlink() and not list(cargo_root.iterdir()) and not list(gradle_root.iterdir()),'literal empty distinct consumer roots required')
    require(cargo_root.resolve()!=gradle_root.resolve() and not cargo_root.resolve().is_relative_to(gradle_root.resolve()) and not gradle_root.resolve().is_relative_to(cargo_root.resolve()),'consumer roots overlap')
    expected,_=projected(descriptor['original']);require(scan(payload)==expected,'complete restored payload differs')
    (cargo_root/'cargo').mkdir(mode=0o700);(cargo_root/'cargo/registry').mkdir(mode=0o700)
    for role,target in [('cargo',cargo_root/'cargo/registry'),('gradle',gradle_root)]:
        inventory=target_inventory(descriptor,role)
        for name,entry in sorted(inventory.items(),key=lambda pair:(len(Path(pair[0]).parts),pair[0])):
            destination=target/name
            if entry['type']=='directory':destination.mkdir(mode=entry['mode']);destination.chmod(entry['mode'])
            else:
                with (payload/physical(role,name)).open('rb') as source,destination.open('xb') as output:
                    shutil.copyfileobj(source,output,1024**2);output.flush();os.fchmod(output.fileno(),entry['mode']);os.fsync(output.fileno())
    result=verify_consumers(cargo_root,gradle_root,descriptor)
    require(scan(payload)==expected,'restored original changed during projection')
    for root in (cargo_root,gradle_root):
        for path in sorted((p for p in root.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True)+[root]:
            fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:os.fsync(fd)
            finally:os.close(fd)
    return {**result,'status':'consumer-projection-verified','sourceBeforeAfterMatched':True}
def readonly(*paths):
    require(all(os.statvfs(p).f_flag & os.ST_RDONLY for p in paths),'real read-only mounts required')
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=('export','project','verify-consumers'));args=parser.parse_args()
    require(sys.platform=='linux' and os.getuid()==65534 and os.getgid()==65534,'Linux UID/GID65534 required')
    readonly('/','/harness')
    if args.stage=='export':
        readonly('/cargo','/gradle','/baseline')
        d=export_inputs('/cargo','/gradle','/baseline/gradle-baseline.json','/out')
        print(json.dumps({'status':'developer-cache-exported-verified','descriptorSha256':sha('/out/developer-inputs.json'),'archive':d['archive'],**FLAGS}))
    else:
        readonly('/inputs');d,_=verify_export('/inputs',os.environ['DEVELOPER_DESCRIPTOR_SHA256'])
        if args.stage=='project':
            readonly('/restored');result=project_consumers('/restored/payload','/cargo-consumer','/gradle-consumer',d)
        else:
            readonly('/restored','/cargo-consumer','/gradle-consumer')
            require(scan('/restored/payload')==projected(d['original'])[0],'restored source differs')
            result={**verify_consumers('/cargo-consumer','/gradle-consumer',d),'statvfsReadOnly':True}
        print(json.dumps(result))
if __name__=='__main__':main()
