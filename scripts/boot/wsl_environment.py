"""Exclusive host inputs and streaming rootfs preparation for P01 WSL."""
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import time
from contextlib import contextmanager

BUNDLE_BYTES=86055881
BUNDLE_SHA256='d8db66739d2e28d4933786d73e918d9be643a67fbd835db1bf740d650a259e70'
BINARY_PINS={
'containerd':'8260dd2b405520c10d44f1b75ace6b58e2579d10113cadc6d6b8df533ff68f6e',
'dockerd':'809c45f959337b17be8b413d96fdcaad826393eef643b4e476a5df918d25a162',
'containerd-shim-runc-v2':'60a23e7d1d8f60b2f7ae8046cd656066c9dea065c3e0da3ce61cf2fdf173afd3',
'docker-proxy':'3d6e5640bc6ba0196d209b089585eb9acaf1554640159aad1393439b5e0ac131',
'runc':'8275e9aa7ef1e33585432f67d1bdd51c367621e3295d59cb9d7efaacb4b30731',
'ctr':'634f62481d6e68fc993258ed0cb6995eb10dc08b2372c4e97dcc7f0586c6ff73',
'docker':'9c46230f41e4263b3a9d93ae9284722709923c50d138bc64eed2bb6c02af0581',
'docker-init':'5ccb076690ac3d060511c63d02194bd5cefaba8dbf225fd60b26283eb1055e4b'}
WSL_CONF=b'[boot]\nsystemd=false\n\n[automount]\nenabled=false\nmountFsTab=false\n\n[interop]\nenabled=false\nappendWindowsPath=false\n\n[user]\ndefault=root\n'

def _safe_path(path):
    path=Path(path)
    if not path.is_absolute() or any(p=='..' for p in path.parts) or any(ord(c)<32 for c in str(path)):
        raise ValueError('absolute path without traversal/control required')
    for item in [path,*path.parents]:
        try: info=item.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise ValueError('linked/reparse path forbidden: '+str(item))
    return path

@contextmanager
def _open_input(path):
    path=_safe_path(path)
    before=path.lstat()
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or (before.st_dev,before.st_ino)!=(info.st_dev,info.st_ino):
            raise ValueError('input must be an unchanged single-link regular file')
        _safe_path(path)
        with os.fdopen(fd,'rb',closefd=False) as stream:
            yield stream
            after=_safe_path(path).lstat()
            if (after.st_dev,after.st_ino)!=(info.st_dev,info.st_ino): raise ValueError('input path identity changed')
    finally: os.close(fd)

def _stamp(stream):
    s=os.fstat(stream.fileno()); return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def _hash(stream):
    stream.seek(0); digest=hashlib.sha256(); size=0
    while data:=stream.read(1024*1024): digest.update(data); size+=len(data)
    stream.seek(0); return {'sha256':digest.hexdigest(),'bytes':size}

def _unchanged(stream,stamp,pin):
    if _stamp(stream)!=stamp or _hash(stream)!=pin: raise ValueError('input changed while held open')

def _json(stream,value):
    stream.write((json.dumps(value,indent=2)+'\n').encode()); stream.flush(); os.fsync(stream.fileno())

def freeze_inputs(paths,destination):
    destination=_safe_path(destination); destination.mkdir()
    manifest={'files':{},'root':str(destination)}
    for source in paths:
        source=_safe_path(source); name=source.name
        if name in manifest['files'] or name=='manifest.json': raise ValueError('duplicate/reserved basename')
        with _open_input(source) as incoming:
            stamp=_stamp(incoming); pin=_hash(incoming)
            with (destination/name).open('xb') as outgoing:
                while data:=incoming.read(1024*1024): outgoing.write(data)
                outgoing.flush(); os.fsync(outgoing.fileno())
            _unchanged(incoming,stamp,pin)
        manifest['files'][name]={**pin,'mode':stat.S_IMODE(source.stat().st_mode)}
    with (destination/'manifest.json').open('xb') as output: _json(output,manifest)
    return manifest

def verify_frozen(directory,manifest):
    directory=_safe_path(directory)
    expected=set(manifest['files'])|{'manifest.json'}
    if {p.name for p in directory.iterdir()}!=expected: raise ValueError('frozen closure differs')
    for child in directory.iterdir():
        _safe_path(child)
        if not child.is_file(): raise ValueError('nonregular frozen input')
    for name,pin in manifest['files'].items():
        if PurePosixPath(name).name!=name or name in ('.','..'): raise ValueError('invalid manifest name')
        with _open_input(Path(directory)/name) as stream:
            if _hash(stream)!={k:pin[k] for k in ('sha256','bytes')}: raise ValueError('frozen helper changed')

def validate_parameters(generation,distro,paths=()):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,47}',generation) or not re.fullmatch('MingOS-P01-[A-Za-z0-9-]+',distro):
        raise ValueError('invalid generation/distro')
    for path in paths: _safe_path(path)

def capture(directory,key,argv,*,timeout,stdin=None,environment=None):
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*',key): raise ValueError('invalid operation key')
    directory=_safe_path(directory)
    handles=[]
    try:
        for suffix in ('command.json','stdout.log','stderr.log','result.json'):
            handles.append((directory/(key+'.'+suffix)).open('xb'))
        command,stdout,stderr,result=handles
        pin=None
        if stdin is not None:
            position=stdin.tell(); digest=hashlib.sha256(); count=0
            while data:=stdin.read(1024*1024): digest.update(data); count+=len(data)
            pin={'sha256':digest.hexdigest(),'bytes':count}; stdin.seek(position)
        _json(command,{'argv':list(argv),'timeout':timeout,'stdin':pin,'started':time.time()})
        record={'exitCode':None,'processMayRemain':False}
        try:
            completed=subprocess.run(argv,stdin=stdin,stdout=stdout,stderr=stderr,timeout=timeout,shell=False,env=environment)
            record['exitCode']=completed.returncode
            return completed
        except (OSError,subprocess.TimeoutExpired) as error:
            record['error']=repr(error); record['processMayRemain']=isinstance(error,subprocess.TimeoutExpired)
            raise
        finally:
            for log in (stdout,stderr): log.flush(); os.fsync(log.fileno())
            record['finished']=time.time(); _json(result,record)
    finally:
        for handle in handles: handle.close()

def _member_name(name):
    if '\\' in name or name.startswith('/') or any(ord(c)<32 for c in name): raise ValueError('unsafe tar name')
    while name.startswith('./'): name=name[2:]
    name=name.rstrip('/')
    if not name or any(p in ('','.','..') for p in name.split('/')): raise ValueError('unsafe tar name')
    return name

def _link_target(name,target,hard=False):
    if '\\' in target or not target or any(ord(c)<32 for c in target): raise ValueError('unsafe link')
    parts=[] if target.startswith('/') or hard else name.split('/')[:-1]
    for part in target.split('/'):
        if part in ('','.'): continue
        if part=='..':
            if not parts: raise ValueError('link escapes root')
            parts.pop()
        else: parts.append(part)
    return '/'.join(parts)

def prepare_rootfs(export_path,bundle_path,output_path):
    export_path=_safe_path(export_path); bundle_path=_safe_path(bundle_path); output_path=_safe_path(output_path)
    # Claim the output before touching input contents; failed generations remain claimed.
    with output_path.open('xb') as output, _open_input(export_path) as exported, _open_input(bundle_path) as bundled:
        export_stamp=_stamp(exported); export_pin=_hash(exported)
        bundle_stamp=_stamp(bundled); bundle_pin=_hash(bundled)
        if bundle_pin!={'bytes':BUNDLE_BYTES,'sha256':BUNDLE_SHA256}: raise ValueError('Docker bundle pin mismatch')
        binaries={}; seen=set()
        with tarfile.open(fileobj=bundled,mode='r:*') as archive:
            for member in archive:
                name=_member_name(member.name)
                if name in seen: raise ValueError('duplicate bundle member')
                seen.add(name)
                if name=='docker' and member.isdir(): continue
                leaf=name.removeprefix('docker/')
                if name!='docker/'+leaf or leaf not in BINARY_PINS or not member.isreg() or member.size>128*1024*1024:
                    raise ValueError('unexpected bundle member')
                with archive.extractfile(member) as data:
                    digest=hashlib.sha256()
                    while chunk:=data.read(1024*1024): digest.update(chunk)
                if digest.hexdigest()!=BINARY_PINS[leaf]: raise ValueError('binary pin mismatch')
                binaries[leaf]=member
            if set(binaries)!=set(BINARY_PINS): raise ValueError('incomplete bundle')
            replacements={'etc/wsl.conf'}|{'usr/local/bin/'+name for name in binaries}
            required_parents={'etc','usr','usr/local','usr/local/bin'}
            observed={}
            with tarfile.open(fileobj=output,mode='w|',format=tarfile.PAX_FORMAT) as target:
                with tarfile.open(fileobj=exported,mode='r|*') as incoming:
                    for member in incoming:
                        name=_member_name(member.name)
                        if name in observed: raise ValueError('duplicate rootfs member')
                        if not (member.isreg() or member.isdir() or member.issym() or member.islnk()): raise ValueError('special rootfs member')
                        observed[name]=member
                        if member.issym() or member.islnk(): _link_target(name,member.linkname,member.islnk())
                        if name in required_parents and not member.isdir(): raise ValueError('replacement parent is not directory')
                        if name in replacements:
                            if not member.isreg(): raise ValueError('replacement is not regular')
                            continue
                        member.name=name
                        target.addfile(member,incoming.extractfile(member) if member.isreg() else None)
                # No archive member may traverse a nondirectory ancestor, regardless of order.
                for name in observed:
                    for parent in PurePosixPath(name).parents:
                        if str(parent) in observed and not observed[str(parent)].isdir(): raise ValueError('member traverses link/file')
                for name in sorted(required_parents):
                    if name not in observed:
                        info=tarfile.TarInfo(name); info.type=tarfile.DIRTYPE; info.mode=0o755; target.addfile(info)
                info=tarfile.TarInfo('etc/wsl.conf'); info.size=len(WSL_CONF); info.mode=0o644; target.addfile(info,io.BytesIO(WSL_CONF))
                for name,member in sorted(binaries.items()):
                    info=tarfile.TarInfo('usr/local/bin/'+name); info.size=member.size; info.mode=0o755
                    with archive.extractfile(member) as data: target.addfile(info,data)
        _unchanged(exported,export_stamp,export_pin); _unchanged(bundled,bundle_stamp,bundle_pin)
        output.flush(); os.fsync(output.fileno())
    with output_path.open('rb') as stream: rootfs_pin=_hash(stream)
    return {'export':export_pin,'bundle':bundle_pin,'rootfs':rootfs_pin,'binaries':dict(BINARY_PINS)}


def validate_tools_image(image,verified):
    expected={'Id':verified['imageId'],'Architecture':'amd64','Os':'linux',
              'RootFS':{'Type':'layers','Layers':verified['diffIds']},
              'Config':verified['configObject'].get('config')}
    descriptor=image.get('Descriptor',{})
    if (any(image.get(k)!=v for k,v in expected.items()) or descriptor.get('digest')!=verified['imageId']
            or descriptor.get('mediaType')!='application/vnd.oci.image.index.v1+json'):
        raise ValueError('outer Tools index/platform/config/rootfs differs from verified archive')


def _one(values):
    if not isinstance(values,list) or len(values)!=1: raise ValueError('one inspection required')
    return values[0]


def _decode_wsl(data):
    encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else ('utf-16-le' if b'\0' in data else 'utf-8-sig')
    return data.decode(encoding).replace('\x00','').lstrip('\ufeff')


def _create_environment(*,generation,distro,output_directory,wsl_executable,
                        outer_docker,docker_bundle,archive_root,complete=False):
    """Initial private daemon stage only. A returned receipt remains creating.

    All failed generations are retained; this API never retries a claimed output.
    Full load/smoke and independent observation belong to the later acceptance stage.
    """
    import development_image_migration as migration
    output=_safe_path(output_directory)
    validate_parameters(generation,distro,[output,wsl_executable,docker_bundle,archive_root])
    output.mkdir()  # Claim first, before any subprocess or mutable resource.
    receipt={'schemaVersion':1,'generation':generation,'distro':distro,'status':'creating',
             'phase':'preflight','formalQualification':False,'finalSystemAccepted':False,
             'coldBuildExecuted':False,'resourcesPreserved':True,'commands':[]}
    receipt['wslExecutable']=str(wsl_executable)
    receipt['outputDirectory']=str(output)
    sink=(output/'environment-result.json').open('xb')
    def persist():
        sink.seek(0); sink.truncate(); _json(sink,receipt)
    def command(key,argv,timeout=60,stdin=None,environment=None):
        receipt['commands'].append(key); persist()
        result=capture(output,key,argv,timeout=timeout,stdin=stdin,environment=environment)
        if result.returncode: raise ValueError('command failed: '+key)
        return (output/(key+'.stdout.log')).read_bytes()
    def docker(key,args,timeout=60):
        from docker_transport import _environment
        return command(key,outer_docker.argv(args),timeout,environment=_environment())
    def inspect_many(key,kind,ids):
        result=[]
        for offset in range(0,len(ids),64):
            result.extend(json.loads(docker(key+'-'+str(offset//64),[kind,'inspect',*ids[offset:offset+64]])))
        return result
    persist()
    try:
        if outer_docker is None or not outer_docker.endpoint.startswith('npipe:////./pipe/'):
            raise ValueError('explicit outer npipe endpoint required')
        with _open_input(docker_bundle) as bundle:
            if _hash(bundle)!={'bytes':BUNDLE_BYTES,'sha256':BUNDLE_SHA256}: raise ValueError('Docker bundle pin mismatch')
        archives=migration.verify_all(archive_root); receipt['archives']=archives
        tools=migration.verify_one('tools',archive_root)
        validate_tools_image(_one(json.loads(docker('tools-inspect',['image','inspect',migration.FIXED['tools']['imageId']]))),tools)
        command('wsl-status',[str(wsl_executable),'--status'])
        distros=_decode_wsl(command('wsl-list',[str(wsl_executable),'--list','--quiet'])).splitlines()
        if distro.casefold() in {name.strip().casefold() for name in distros}: raise ValueError('target distro already exists')
        here=Path(__file__).resolve().parent
        helpers=[here/name for name in ('wsl_environment.py','wsl_environment_guest.py','docker_transport.py','development_image_migration.py','qualification_image_archive.py')]
        manifest=freeze_inputs(helpers,output/'frozen'); verify_frozen(output/'frozen',manifest)
        receipt['helpers']=manifest
        outer={'endpoint':outer_docker.endpoint,
               'executable':outer_docker.executable,'configDirectory':outer_docker.config_directory,
               'info':json.loads(docker('outer-info',['info','--format','{{json .}}'])),
               'context':docker('outer-context',['context','show']).decode().strip()}
        outer['imageIds']=sorted(set(docker('outer-image-ids',['image','ls','-q','--no-trunc']).decode().splitlines()))
        container_ids=sorted(set(docker('outer-container-ids',['ps','-a','-q','--no-trunc']).decode().splitlines()))
        volume_ids=sorted(set(docker('outer-volume-names',['volume','ls','-q']).decode().splitlines()))
        outer['images']=inspect_many('outer-images','image',outer['imageIds'])
        outer['containers']=inspect_many('outer-containers','container',container_ids)
        outer['volumes']=inspect_many('outer-volumes','volume',volume_ids)
        receipt['outer']=outer; receipt['phase']='export'; persist()
        identifier=docker('export-create',['create','--pull','never','--network','none','--restart','no',
                    '--label','mingos.p01.generation='+generation,migration.FIXED['tools']['imageId']]).decode().strip()
        if not re.fullmatch('[0-9a-f]{64}',identifier): raise ValueError('full created container ID required')
        receipt['exportContainerId']=identifier; persist()
        inspection=_one(json.loads(docker('export-inspect',['container','inspect',identifier])))
        if (inspection.get('Id')!=identifier or inspection.get('State',{}).get('Running') is not False
                or inspection.get('Mounts')!=[] or inspection.get('Image')!=tools['imageId']
                or inspection.get('Config',{}).get('Image')!=tools['imageId']
                or inspection.get('HostConfig',{}).get('RestartPolicy')!={'Name':'no','MaximumRetryCount':0}
                or inspection.get('HostConfig',{}).get('AutoRemove') is not False):
            raise ValueError('export container must be stopped, mount-free and fixed Tools')
        export=output/'export.tar'
        # The explicit --output is new and never reused; capture preserves command logs.
        if export.exists(): raise FileExistsError(export)
        docker('export',['export','--output',str(export),identifier],300)
        receipt['rootfs']=prepare_rootfs(export,docker_bundle,output/'rootfs.tar')
        receipt['phase']='import';persist(); verify_frozen(output/'frozen',manifest)
        command('wsl-import',[str(wsl_executable),'--import',distro,str(output/'distro'),str(output/'rootfs.tar'),'--version','2'],300)
        vhds=list((output/'distro').glob('*.vhdx'))
        if len(vhds)!=1: raise ValueError('one imported VHD required')
        value=vhds[0].stat(); receipt['vhd']={'path':str(vhds[0]),'bytes':value.st_size,'device':value.st_dev,'inode':value.st_ino}
        receipt['phase']='launch';persist()
        helper=output/'launch-guest.py'
        data=(output/'frozen/wsl_environment_guest.py').read_bytes()+b'\nif __name__ == "__main__":\n import sys\n print(json.dumps(launch_environment(sys.argv[1])))\n'
        with helper.open('xb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
        digest=hashlib.sha256(data).hexdigest(); remote='/root/'+generation+'-launch.py'
        bootstrap=('import os,sys,hashlib; p=sys.argv[1]; n=int(sys.argv[2]); h=sys.argv[3]; '
                   'd=sys.stdin.buffer.read(n+1); '
                   'assert len(d)==n and hashlib.sha256(d).hexdigest()==h; '
                   'root=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); '
                   'parent=os.open("root",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root); '
                   'f=os.open(p.split("/")[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,448,dir_fd=parent); '
                   's=os.fdopen(f,"wb"); s.write(d); s.flush(); os.fsync(s.fileno()); s.close(); '
                   'os.fsync(parent); os.close(parent); os.close(root)')
        with helper.open('rb') as stream:
            command('guest-helper-transfer',[str(wsl_executable),'-d',distro,'--user','root','--exec',
                '/usr/bin/python3','-c',bootstrap,remote,str(len(data)),digest],stdin=stream)
        receipt['launcherPin']={'sha256':digest,'bytes':len(data),'path':remote}
        # Check retained guest bytes immediately before the script-file invocation.
        command('guest-helper-verify',[str(wsl_executable),'-d',distro,'--user','root','--exec','/usr/bin/python3','-c',
            'import hashlib,sys; assert hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest()==sys.argv[2]',remote,digest])
        handle=json.loads(command('guest-launch',[str(wsl_executable),'-d',distro,'--user','root','--exec','/usr/bin/python3',remote,generation],90))
        if handle.get('generation')!=generation: raise ValueError('guest handle generation differs')
        receipt['handle']=handle;receipt['phase']='empty-daemon-observed';persist()
        if complete:
            _finish_create(receipt,output,archive_root,manifest,persist,command,docker,inspect_many)
        return receipt
    except BaseException as error:
        receipt['status']='failed';receipt['error']=repr(error); persist();raise
    finally: sink.close()

HELPER_NAMES=('wsl_environment.py','wsl_environment_guest.py','docker_transport.py','development_image_migration.py','qualification_image_archive.py')


def create_empty_environment(*,generation,distro,output_directory,wsl_executable,outer_docker,docker_bundle,archive_root):
    """Internal lifecycle stage; intentionally never returns environment-ready."""
    return _create_environment(generation=generation,distro=distro,output_directory=output_directory,
        wsl_executable=wsl_executable,outer_docker=outer_docker,docker_bundle=docker_bundle,archive_root=archive_root)


def create_environment(*,generation,distro,output_directory,wsl_executable,outer_docker,docker_bundle,archive_root):
    """Create once and carry the same held receipt through every acceptance phase."""
    return _create_environment(generation=generation,distro=distro,output_directory=output_directory,
        wsl_executable=wsl_executable,outer_docker=outer_docker,docker_bundle=docker_bundle,
        archive_root=archive_root,complete=True)


TRANSFER_BOOTSTRAP='''import os,sys,hashlib
path=sys.argv[1];size=int(sys.argv[2]);expected=sys.argv[3];mode=int(sys.argv[4]);parts=path.split("/")[1:]
if not path.startswith("/") or any(p in ("",".","..") for p in parts):raise ValueError("invalid transfer path")
parent=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
for part in parts[:-1]:
 child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);os.close(parent);parent=child
fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,384,dir_fd=parent)
try:
 digest=hashlib.sha256();count=0
 while True:
  chunk=sys.stdin.buffer.read(min(1048576,size-count+1))
  if not chunk:break
  count+=len(chunk)
  if count>size:raise ValueError("transfer too long")
  digest.update(chunk);view=memoryview(chunk)
  while view:
   written=os.write(fd,view)
   if written<=0:raise OSError("short write")
   view=view[written:]
 if count!=size or digest.hexdigest()!=expected:raise ValueError("transfer pin differs")
 os.fchmod(fd,mode);os.fsync(fd)
finally:os.close(fd)
os.fsync(parent);os.close(parent)
'''


def _guest_prefix(receipt):
    return [receipt['wslExecutable'],'-d',receipt['distro'],'--user','root','--exec','/usr/bin/python3','-I']


def _transfer(receipt,command,key,source,destination,pin,mode=0o444):
    with _open_input(source) as stream:
        stamp=_stamp(stream)
        if _hash(stream)!={k:pin[k] for k in ('sha256','bytes')}:raise ValueError('transfer source pin differs')
        command(key,[*_guest_prefix(receipt),'-c',TRANSFER_BOOTSTRAP,destination,str(pin['bytes']),pin['sha256'],str(mode)],300,stdin=stream)
        _unchanged(stream,stamp,{k:pin[k] for k in ('sha256','bytes')})


def _invoke_guest(receipt,output,key,request,persist,*,timeout=120):
    from wsl_environment_guest import TRUSTED_DISPATCH
    manifest=receipt['helpers'];verify_frozen(Path(manifest['root']),manifest)
    if set(manifest['files'])!=set(HELPER_NAMES):raise ValueError('full five module closure required')
    request={**request,'generation':receipt['generation'],'handle':receipt['handle'],
             'codeRoot':receipt['guestCodeRoot'],'pins':manifest['files']}
    argv=[*_guest_prefix(receipt),'-c',TRUSTED_DISPATCH,receipt['guestCodeRoot'],
          json.dumps(manifest['files']),'--stdin-request']
    request_path=output/(key+'.request.json')
    with request_path.open('xb') as stream:_json(stream,request)
    receipt['commands'].append(key);persist()
    with _open_input(request_path) as stream:
        result=capture(output,key,argv,timeout=timeout,stdin=stream)
    # Parse and preserve guest evidence even on a guest error/partial loader failure.
    raw=(output/(key+'.stdout.log')).read_bytes()
    try:envelope=json.loads(raw)
    except (ValueError,UnicodeDecodeError) as error:raise ValueError('guest returned no valid evidence envelope') from error
    receipt.setdefault('guestOperations',{})[key]=envelope;persist()
    evidence=envelope.get('evidence',{})
    if 'load-result.json' in evidence:
        receipt['load']=json.loads(evidence['load-result.json']);persist()
    if result.returncode or envelope.get('status')!='ok':raise ValueError('guest operation failed: '+key)
    return envelope['result']


def validate_smoke_result(result):
    from wsl_environment_guest import validate_effective,validate_smoke_inspect,validate_smoke_state,SMOKE_SCRIPT
    try:
        validate_effective(result['actualEffective'])
        for key in ('inspectBefore','inspectAfter'):validate_smoke_inspect(result[key],result['id'])
        if result['inspectBefore']['State']['Running'] is not False:raise ValueError('smoke began running')
        validate_smoke_state(result['inspectAfter']['State'])
        if result['script']!={'sha256':hashlib.sha256(SMOKE_SCRIPT).hexdigest(),'bytes':len(SMOKE_SCRIPT)}:
            raise ValueError('smoke script pin differs')
    except (KeyError,TypeError) as error:raise ValueError('incomplete smoke result') from error


def _outer_after(docker,inspect_many):
    result={'info':json.loads(docker('after-outer-info',['info','--format','{{json .}}'])),
            'context':docker('after-outer-context',['context','show']).decode().strip()}
    for name,args,kind in [('images',['image','ls','-q','--no-trunc'],'image'),
                           ('containers',['ps','-a','-q','--no-trunc'],'container'),
                           ('volumes',['volume','ls','-q'],'volume')]:
        identifiers=sorted(set(docker('after-outer-'+name+'-ids',args).decode().splitlines()))
        result[name]=inspect_many('after-outer-'+name,kind,identifiers)
    return result


def compare_outer(before,after,export_id):
    if before['info'].get('ID')!=after['info'].get('ID') or before['context']!=after['context']:
        raise ValueError('outer daemon/context changed')
    changes={};equivalences=[]
    dns_policy_version=(before['info'].get('ServerVersion')=='29.8.1'
                        and after['info'].get('ServerVersion')=='29.8.1')
    for kind,key,fields in [('images','Id',('Id','Descriptor','Architecture','Os','RootFS','Config')),
                            ('containers','Id',('Id','Image','Config','HostConfig','Mounts','State')),
                            ('volumes','Name',('Name','CreatedAt','Driver','Options','Labels'))]:
        old={v[key]:v for v in before[kind]};new={v[key]:v for v in after[kind]}
        for identifier,value in old.items():
            if identifier not in new:
                raise ValueError('old outer resource changed: '+kind+'/'+identifier)
            for field in fields:
                left=value.get(field);right=new[identifier].get(field)
                if left==right:continue
                if (kind=='containers' and field=='HostConfig' and dns_policy_version
                        and isinstance(left,dict) and isinstance(right,dict)
                        and 'Dns' in left and 'Dns' in right
                        and ((left['Dns'] is None and type(right['Dns']) is list and right['Dns']==[])
                             or (right['Dns'] is None and type(left['Dns']) is list and left['Dns']==[]))):
                    # Compare copies only; raw inspections remain preservation evidence.
                    left_rest=dict(left);right_rest=dict(right)
                    del left_rest['Dns'];del right_rest['Dns']
                    if left_rest==right_rest:
                        equivalences.append({
                            'policyId':'moby-29.8.1-hostconfig-dns-null-empty-v1',
                            'containerId':identifier,'jsonPath':'/containers/'+identifier+'/HostConfig/Dns',
                            'before':None if left['Dns'] is None else [],
                            'after':None if right['Dns'] is None else [],
                            'beforeServerVersion':before['info']['ServerVersion'],
                            'afterServerVersion':after['info']['ServerVersion'],
                            'semanticSource':{
                                'archiveSha256':'94be9d6940b613676335fc494e617b4acf98676435b3744f32649ed72114bd58',
                                'reference':'official Moby docker-v29.8.1; daemon/container/container.go readHostConfig/InitDNSHostConfig; daemon/container_operations.go positive-length DNS override'}})
                        continue
                raise ValueError('old outer resource changed: '+kind+'/'+identifier)
        added=set(new)-set(old);changes[kind]=sorted(added)
        if added!=({export_id} if kind=='containers' else set()):
            raise ValueError('inconclusive: unowned outer resource additions: '+kind)
        if kind=='containers':
            export=new[export_id]
            if (export.get('State',{}).get('Running') is not False or export.get('Mounts')!=[]
                    or export.get('Config',{}).get('Labels',{}).get('mingos.p01.generation') is None):
                raise ValueError('owned export container changed')
    return {'status':'preserved','additions':changes,'unknownConcurrentChanges':False,
            'equivalences':equivalences}


def _finish_create(receipt,output,archive_root,manifest,persist,command,docker,inspect_many):
    import development_image_migration as migration
    generation=receipt['generation'];base='/opt/'+generation;code='/root/'+generation+'-code'
    receipt['guestCodeRoot']=code
    receipt['inner']={'executable':'/usr/local/bin/docker','endpoint':'unix:///run/'+generation+'/docker.sock',
        'configDirectory':base+'/client','dataRoot':'/var/lib/'+generation+'/docker','execRoot':'/run/'+generation+'/exec',
        'containerdNamespace':generation,'containerdPluginsNamespace':generation+'-plugins'}
    receipt['phase']='helper-transfer';persist()
    # Existing /opt/<generation> was created exclusively by this generation's launcher.
    command('guest-directories',[*_guest_prefix(receipt),'-c',
        'import os,sys; os.mkdir(sys.argv[1],448); os.mkdir(sys.argv[2],448)',code,base+'/archives'])
    for name,pin in manifest['files'].items():_transfer(receipt,command,'transfer-'+name,output/'frozen'/name,code+'/'+name,pin)
    receipt['phase']='archive-transfer';persist()
    for role,entry in migration.FIXED.items():
        _transfer(receipt,command,'transfer-'+role,Path(archive_root)/entry['file'],base+'/archives/'+entry['file'],
                  {'sha256':entry['sha256'],'bytes':entry['bytes']})
    receipt['phase']='load';persist()
    receipt['load']=_invoke_guest(receipt,output,'guest-load',{'action':'load','archiveRoot':base+'/archives'},persist,timeout=5600)
    if (receipt['load'].get('completedLoads')!=['rust','tools','kotlin']
            or receipt['load'].get('verifiedInspections')!=['rust','tools','kotlin']
            or receipt['load'].get('status')!='development-toolchain-load-inspect-verified'):
        raise ValueError('exact three-role load completion required')
    receipt['phase']='smoke';persist()
    receipt['smoke']=_invoke_guest(receipt,output,'guest-smoke',{'action':'smoke'},persist,timeout=240)
    validate_smoke_result(receipt['smoke'])
    receipt['phase']='observe';persist()
    receipt['observation']=_invoke_guest(receipt,output,'guest-observe',{'action':'observe','load':receipt['load'],'smoke':receipt['smoke']},persist)
    from wsl_environment_guest import validate_effective,validate_handle
    validate_handle(receipt['handle'],receipt['observation']['handle']);validate_effective(receipt['observation']['actualEffective'])
    validate_namespace_observation(receipt['observation'],generation)
    receipt['firstEmpty']=receipt['observation']['firstEmpty']
    receipt['privateEvidence']=receipt['observation']['privateEvidence']
    receipt['phase']='outer-preservation';persist()
    receipt['outerAfter']=_outer_after(docker,inspect_many);persist()
    receipt['oldResources']=compare_outer(receipt['outer'],receipt['outerAfter'],receipt['exportContainerId'])
    receipt['status']='environment-ready';receipt['phase']='environment-ready';persist()


def validate_namespace_observation(observation,generation):
    from wsl_environment_guest import validate_info,validate_live_info
    try:
        validate_live_info(observation['info'],generation)
        validate_info(observation['firstEmpty']['info'],generation)
    except (KeyError,TypeError) as error:
        raise ValueError('missing actual containerd namespace observation') from error


def _read_ready(path):
    with _open_input(path) as stream:
        value=json.load(stream)
    if value.get('schemaVersion')!=1 or value.get('status')!='environment-ready' or value.get('phase')!='environment-ready':
        raise ValueError('only completed environment-ready receipts are usable')
    validate_parameters(value['generation'],value['distro'],[value['wslExecutable'],value['helpers']['root']])
    verify_frozen(Path(value['helpers']['root']),value['helpers'])
    if value.get('guestCodeRoot')!='/root/'+value['generation']+'-code':raise ValueError('guest code root differs')
    if value.get('load',{}).get('completedLoads')!=['rust','tools','kotlin']:raise ValueError('incomplete load receipt')
    if value.get('oldResources',{}).get('status')!='preserved':raise ValueError('missing resource preservation')
    from wsl_environment_guest import validate_info,validate_containerd_namespaces
    validate_info(value.get('firstEmpty',{}).get('info'),value['generation'])
    validate_namespace_observation(value.get('observation',{}),value['generation'])
    validate_containerd_namespaces({'Containerd':{'Namespaces':value.get('handle',{}).get('containerdNamespaces')}},value['generation'])
    validate_smoke_result(value['smoke'])
    return value


def _followup(environment_receipt,output_directory,action,argv=None,timeout=30):
    output=_safe_path(output_directory);output.mkdir()
    receipt={'schemaVersion':1,'status':'creating','phase':'receipt-validation','resourcesPreserved':True,
             'formalQualification':False,'finalSystemAccepted':False,'coldBuildExecuted':False,'commands':[]}
    with (output/'environment-result.json').open('xb') as sink:
        def persist():sink.seek(0);sink.truncate();_json(sink,receipt)
        persist()
        try:
            source=_read_ready(environment_receipt)
            for key in ('generation','distro','wslExecutable','helpers','handle','guestCodeRoot','load','smoke'):
                receipt[key]=source[key]
            receipt['phase']=action;persist()
            request={'action':action,'load':source['load'],'smoke':source['smoke']}
            if action=='exec':
                if (not isinstance(argv,list) or not argv or any(not isinstance(v,str) or '\x00' in v for v in argv)
                        or not argv[0].startswith('/') or '\\' in argv[0] or type(timeout) is not int or timeout<=0):
                    raise ValueError('absolute Linux executable argv and positive timeout required')
                request.update(argv=argv,timeout=timeout)
            result=_invoke_guest(receipt,output,'guest-'+action,request,persist,timeout=timeout+90 if action=='exec' else 120)
            if action=='observe':
                from wsl_environment_guest import validate_effective,validate_handle
                validate_handle(source['handle'],result['handle']);validate_effective(result['actualEffective'])
                validate_namespace_observation(result,source['generation'])
            receipt['result']=result;receipt['status']='environment-ready';receipt['operationStatus']='observed' if action=='observe' else 'executed';persist();return receipt
        except BaseException as error:
            receipt['status']='failed';receipt['error']=repr(error);persist();raise


def observe_environment(environment_receipt,output_directory):
    return _followup(environment_receipt,output_directory,'observe')


def exec_environment(environment_receipt,argv,*,output_directory,timeout):
    return _followup(environment_receipt,output_directory,'exec',argv,timeout)


def main(argv=None):
    import argparse
    from docker_transport import DockerTransport
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='action',required=True)
    create=commands.add_parser('create')
    for name in ('generation','distro','output-directory','wsl-executable','outer-docker-executable','outer-docker-endpoint',
                 'outer-docker-config','docker-bundle','archive-root'):create.add_argument('--'+name,required=True)
    for action in ('observe','exec'):
        sub=commands.add_parser(action);sub.add_argument('--environment-receipt',type=Path,required=True)
        sub.add_argument('--output-directory',type=Path,required=True)
        if action=='exec':sub.add_argument('--timeout',type=int,required=True);sub.add_argument('argv',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv)
    try:
        if args.action=='create':
            result=create_environment(generation=args.generation,distro=args.distro,output_directory=Path(args.output_directory),
                wsl_executable=Path(args.wsl_executable),outer_docker=DockerTransport(args.outer_docker_executable,args.outer_docker_endpoint,args.outer_docker_config),
                docker_bundle=Path(args.docker_bundle),archive_root=Path(args.archive_root))
        elif args.action=='observe':result=observe_environment(args.environment_receipt,args.output_directory)
        else:
            linux=args.argv[1:] if args.argv[:1]==['--'] else args.argv
            result=exec_environment(args.environment_receipt,linux,output_directory=args.output_directory,timeout=args.timeout)
        print(json.dumps(result));return 0
    except BaseException as error:
        failed={'schemaVersion':1,'status':'failed','phase':'request','error':repr(error),'resourcesPreserved':True}
        path=Path(args.output_directory)/'environment-result.json'
        # A failed claim must never report an older occupant's receipt as this request.
        if not isinstance(error,FileExistsError) and path.is_file():
            try:failed=json.loads(path.read_bytes())
            except (OSError,ValueError):pass
        print(json.dumps(failed));return 1


if __name__=='__main__':raise SystemExit(main())
