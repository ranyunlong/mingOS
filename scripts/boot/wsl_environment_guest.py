"""Linux binary receiver. Failed destinations are deliberately retained."""
import hashlib
import os
from pathlib import PurePosixPath
import re
import json
import subprocess
import time
import stat
from pathlib import Path


def paths(generation):
    if not re.fullmatch('[a-z0-9][a-z0-9-]{0,47}',generation):
        raise ValueError('invalid generation')
    return {name:prefix+generation for name,prefix in
            [('base','/opt/'),('run','/run/'),('data','/var/lib/'),('cgroup','/sys/fs/cgroup/')]}


def daemon_environment(generation):
    return {'PATH':'/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root',
            'LANG':'C.UTF-8','TMPDIR':paths(generation)['run']+'/tmp'}


def daemon_config(generation):
    p=paths(generation)
    return {'data-root':p['data']+'/docker','exec-root':p['run']+'/exec',
            'pidfile':p['run']+'/dockerd.pid','hosts':['unix://'+p['run']+'/docker.sock'],
            'group':'root','features':{'containerd-snapshotter':True},
            'exec-opts':['native.cgroupdriver=cgroupfs'],'cgroup-parent':'/workloads',
            'containerd-namespace':generation,'containerd-plugins-namespace':generation+'-plugins',
            'default-cgroupns-mode':'private','bridge':'none','iptables':False,
            'ip6tables':False,'ip-forward':False,'ip-masq':False,'userland-proxy':False}


def durable_json(path,value):
    with open(path,'x',encoding='utf8') as stream:
        json.dump(value,stream,indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def shared_controls():
    return {name:Path('/sys/fs/cgroup',name).read_text() for name in
            ('cgroup.controllers','cgroup.subtree_control')}


def boundary_script(generation):
    p=paths(generation)
    environment=' '.join(key+'='+value for key,value in daemon_environment(generation).items())
    return '''set -eu
mount --make-rprivate /
mount -t cgroup2 -o nosuid,nodev,noexec,nsdelegate cgroup2 /sys/fs/cgroup
printf '%s\\n' "$$" > /sys/fs/cgroup/management/cgroup.procs
test -z "$(cat /sys/fs/cgroup/cgroup.procs)"
for controller in cpu memory pids; do
    grep -qw "$controller" /sys/fs/cgroup/cgroup.controllers
done
printf '+cpu +memory +pids\\n' > /sys/fs/cgroup/cgroup.subtree_control
printf '+cpu +memory +pids\\n' > /sys/fs/cgroup/workloads/cgroup.subtree_control
mount -t tmpfs -o mode=0700,nosuid,nodev,noexec p01-private-wsl /mnt/wsl
mount -t tmpfs -o mode=0700,nosuid,nodev,noexec p01-private-wslg /mnt/wslg
test ! -e /mnt/wsl/docker-desktop
test ! -e /run/docker.sock
test ! -e /var/run/docker.sock
test ! -e /run/containerd/containerd.sock
exec env -i '''+environment+' /usr/local/bin/dockerd --config-file '+p['base']+'/daemon.json\n'


def validate_info(info):
    expected={'ServerVersion':'29.8.1','OSType':'linux','CgroupVersion':'2',
              'CgroupDriver':'cgroupfs','Driver':'overlayfs','Images':0,'Containers':0}
    if (not info.get('ID') or any(info.get(k)!=v for k,v in expected.items())
            or info.get('Architecture') not in ('amd64','x86_64')
            or ['driver-type','io.containerd.snapshotter.v1'] not in info.get('DriverStatus',[])):
        raise ValueError('inner daemon identity/store contract differs')


def validate_handle(handle,observed):
    for key in ('pid','starttime','exeSha256','namespaces','socket','daemonId'):
        if key not in handle or key not in observed or handle[key]!=observed[key]:
            raise ValueError('stale environment handle: '+key)


def process_identity(pid):
    proc=Path('/proc')/str(pid)
    # comm can contain spaces and closing parentheses; fields begin after final ')'.
    fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
    return {'pid':pid,'starttime':fields[19],
            'exeSha256':hashlib.sha256((proc/'exe').read_bytes()).hexdigest(),
            'namespaces':{name:os.readlink(proc/'ns'/name) for name in ('mnt','net','cgroup')}}


def _entered(generation,pid,args,*,timeout=10):
    p=paths(generation)
    command=['/usr/bin/nsenter','--target',str(pid),'--mount','--net','--cgroup',
                           '/usr/bin/env','-i',*[k+'='+v for k,v in daemon_environment(generation).items()],
                           *args]
    key='operation-'+str(time.time_ns()); started=time.time()
    durable_json(Path(p['base'],key+'.command.json'),{'argv':command,'timeout':timeout,'stdin':None,'started':started})
    record={'exitCode':None,'processMayRemain':False}
    try:
        stdout_path=Path(p['base'],key+'.stdout.log');stderr_path=Path(p['base'],key+'.stderr.log')
        with open(stdout_path,'xb') as stdout,open(stderr_path,'xb') as stderr:
            try:
                result=subprocess.run(command,stdout=stdout,stderr=stderr,timeout=timeout,check=False)
            finally:
                for stream in (stdout,stderr): stream.flush();os.fsync(stream.fileno())
        record['exitCode']=result.returncode
        result.stdout=stdout_path.read_bytes();result.stderr=stderr_path.read_bytes()
        return result
    except BaseException as error:
        record['error']=repr(error);record['processMayRemain']=isinstance(error,subprocess.TimeoutExpired)
        raise
    finally:
        record['finished']=time.time(); durable_json(Path(p['base'],key+'.result.json'),record)


def _docker(generation,pid,args,*,timeout=10):
    p=paths(generation)
    return _entered(generation,pid,['/usr/local/bin/docker','--host','unix://'+p['run']+'/docker.sock',
                                  '--config',p['base']+'/client',*args],timeout=timeout)


def observe_handle(generation,pid):
    p=paths(generation); result=process_identity(pid)
    value=os.stat(p['run']+'/docker.sock',follow_symlinks=False)
    if not stat.S_ISSOCK(value.st_mode): raise ValueError('daemon endpoint is not socket')
    if value.st_uid!=0 or value.st_gid!=0: raise ValueError('daemon socket owner differs')
    result['socket']={'device':value.st_dev,'inode':value.st_ino,'uid':value.st_uid,'gid':value.st_gid}
    response=_docker(generation,pid,['info','--format','{{json .}}'])
    if response.returncode: raise ValueError('daemon info failed')
    result['daemonId']=json.loads(response.stdout)['ID']
    return result


def admit_environment(handle,argv,*,timeout=30):
    generation=handle['generation']; p=paths(generation)
    validate_handle(handle,observe_handle(generation,handle['pid']))
    if not isinstance(argv,list) or not argv or any(not isinstance(v,str) or '\x00' in v for v in argv):
        raise ValueError('explicit Linux argv required')
    # Move this helper first: nsenter must inherit management membership.
    Path(p['cgroup'],'management','cgroup.procs').write_text(str(os.getpid())+'\n')
    validate_handle(handle,observe_handle(generation,handle['pid']))
    return _entered(generation,handle['pid'],argv,timeout=timeout)


def launch_environment(generation,*,ready_timeout=30):
    if not 0<ready_timeout<=30: raise ValueError('readiness bound must be <=30 seconds')
    p=paths(generation); before=shared_controls()
    for key in ('base','run','data','cgroup'): Path(p[key]).mkdir(mode=0o700)
    for name in ('management','workloads'): Path(p['cgroup'],name).mkdir()
    for name in ('tmp',): Path(p['run'],name).mkdir(mode=0o700)
    Path(p['base'],'client').mkdir(mode=0o700)
    durable_json(Path(p['base'],'shared-before.json'),before)
    durable_json(Path(p['base'],'host-before.json'),{'mountinfo':Path('/proc/self/mountinfo').read_text(),
        'namespaces':{n:os.readlink('/proc/self/ns/'+n) for n in ('mnt','net','cgroup')},
        'sockets':{n:Path(n).exists() for n in ('/run/docker.sock','/run/containerd/containerd.sock')}})
    durable_json(Path(p['base'],'daemon.json'),daemon_config(generation))
    script=Path(p['base'],'launch-private.sh')
    with script.open('x') as stream:
        stream.write(boundary_script(generation)); stream.flush(); os.fsync(stream.fileno())
    def enter_generation():
        Path(p['cgroup'],'cgroup.procs').write_text(str(os.getpid())+'\n')
    launch_command=['/usr/bin/unshare','--mount','--net','--cgroup','--propagation','private','/bin/sh',str(script)]
    durable_json(Path(p['base'],'launch.command.json'),{'argv':launch_command,'environment':daemon_environment(generation),'stdin':None,'started':time.time()})
    with open(Path(p['base'],'daemon.stdout.log'),'xb') as stdout,open(Path(p['base'],'daemon.stderr.log'),'xb') as stderr:
        process=subprocess.Popen(launch_command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                    env=daemon_environment(generation),start_new_session=True,preexec_fn=enter_generation)
    durable_json(Path(p['base'],'launcher.json'),{'pid':process.pid,'resourcesPreserved':True})
    try:
        deadline=time.monotonic()+ready_timeout
        while time.monotonic()<deadline:
            if process.poll() is not None: raise ValueError('daemon exited before readiness')
            try:
                response=_docker(generation,process.pid,['info','--format','{{json .}}'],timeout=max(.001,deadline-time.monotonic()))
                if response.returncode==0: break
            except (OSError,subprocess.TimeoutExpired): pass
            time.sleep(min(.2,max(0,deadline-time.monotonic())))
        else: raise TimeoutError('daemon not ready within 30 seconds')
        if time.monotonic()>deadline: raise TimeoutError('daemon readiness exceeded bound')
        info=json.loads(response.stdout); validate_info(info)
        empty={}
        for name,args in [('images',['image','ls','-q','--no-trunc']),('containers',['ps','-a','-q','--no-trunc']),('volumes',['volume','ls','-q'])]:
            result=_docker(generation,process.pid,args)
            empty[name]={'exitCode':result.returncode,'stdout':result.stdout.decode(),'stderr':result.stderr.decode()}
            if result.returncode or result.stdout!=b'': raise ValueError('first store not empty: '+name)
        if shared_controls()!=before: raise ValueError('shared controls changed')
        check=_entered(generation,process.pid,['/bin/sh','-eu','-c',
            'test ! -e /run/docker.sock; test ! -e /run/containerd/containerd.sock; test ! -e /mnt/wsl/docker-desktop; test "$(cat /sys/fs/cgroup/cgroup.procs)" = ""; test -w "$TMPDIR"; test "$(stat -c %a "$TMPDIR")" = 700'])
        if check.returncode: raise ValueError('private boundary verification failed')
        environ=Path('/proc',str(process.pid),'environ').read_bytes().split(b'\0')
        actual=dict(v.decode().split('=',1) for v in environ if v)
        if actual!=daemon_environment(generation): raise ValueError('daemon inherited unexpected environment')
        handle=observe_handle(generation,process.pid); handle['generation']=generation
        for name,identifier in handle['namespaces'].items():
            if identifier==os.readlink('/proc/self/ns/'+name): raise ValueError('namespace not private: '+name)
        if handle['exeSha256']!=hashlib.sha256(Path('/usr/local/bin/dockerd').read_bytes()).hexdigest():
            raise ValueError('daemon executable differs')
        # Preserve first-empty proof before returning any usable handle.
        durable_json(Path(p['base'],'first-empty.json'),{'info':info,'lists':empty,'environment':actual})
        durable_json(Path(p['base'],'shared-after.json'),shared_controls())
        evidence={'mountinfo':Path('/proc',str(process.pid),'mountinfo').read_text(),'daemonCmdline':Path('/proc',str(process.pid),'cmdline').read_bytes().decode().split('\0'),'containerd':[]}
        for child in Path('/proc').iterdir():
            if not child.name.isdigit(): continue
            try:
                command=(child/'cmdline').read_bytes().decode().split('\0')
                if command[0].endswith('/containerd') and (child/'status').read_text().split('PPid:\t')[1].splitlines()[0]==str(process.pid):
                    import tomllib
                    if '--config' not in command: raise ValueError('managed containerd config required')
                    config_path=command[command.index('--config')+1]
                    if not config_path.startswith(p['run']+'/'): raise ValueError('containerd config outside generation')
                    config=tomllib.loads(Path(config_path).read_text())
                    for key,prefix,value in [('root',p['data'],config.get('root')),
                                             ('state',p['run'],config.get('state')),
                                             ('grpc.address',p['run'],config.get('grpc',{}).get('address'))]:
                        if not isinstance(value,str) or not value.startswith(prefix+'/'):
                            raise ValueError('managed containerd path differs: '+key)
                    evidence['containerd'].append({'pid':int(child.name),'cmdline':command,'config':config,'identity':process_identity(int(child.name))})
            except (FileNotFoundError,ProcessLookupError,PermissionError,IndexError): continue
        if len(evidence['containerd'])!=1: raise ValueError('one owned containerd required')
        durable_json(Path(p['base'],'private-evidence.json'),evidence)
        durable_json(Path(p['base'],'empty-handle.json'),handle)
        return handle
    except BaseException as error:
        failure={'error':repr(error),'processMayRemain':process.poll() is None,'resourcesPreserved':True}
        try: failure['sharedControlsAfter']=shared_controls()
        except Exception as observation_error: failure['sharedControlsObservationError']=repr(observation_error)
        durable_json(Path(p['base'],'launch-failed.json'),failure)
        raise

def receive_file(path, sha256, size, mode, stream):
    if not isinstance(path,str) or not path.startswith('/') or any(x in path for x in ('\x00','\\')):
        raise ValueError('absolute guest path required')
    parts=path.split('/')[1:]
    if not parts or any(p in ('','.','..') for p in parts): raise ValueError('invalid path')
    if not re.fullmatch('[0-9a-f]{64}',sha256) or type(size) is not int or size<0 or type(mode) is not int or mode & ~0o777:
        raise ValueError('invalid transfer pin or mode')
    parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent); parent=child
        fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        try:
            digest=hashlib.sha256(); count=0
            while True:
                data=stream.read(min(1024*1024,size-count+1))
                if not data: break
                count+=len(data)
                if count>size: raise ValueError('transfer too long')
                digest.update(data); view=memoryview(data)
                while view:
                    written=os.write(fd,view)
                    if written<=0: raise OSError('write made no progress')
                    view=view[written:]
            if count!=size or digest.hexdigest()!=sha256: raise ValueError('transfer size/hash mismatch')
            os.fchmod(fd,mode); os.fsync(fd)
        finally: os.close(fd)
        os.fsync(parent)
    finally: os.close(parent)
    return {'path':path,'sha256':sha256,'bytes':size,'mode':mode}
