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
    return data.decode('utf-16-le' if b'\0' in data else 'utf-8-sig').replace('\x00','')


def create_empty_environment(*,generation,distro,output_directory,wsl_executable,
                             outer_docker,docker_bundle,archive_root):
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
        return receipt
    except BaseException as error:
        receipt['status']='failed';receipt['error']=repr(error); persist();raise
    finally: sink.close()
