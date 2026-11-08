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


def _entered(generation,pid,args,*,timeout=10,stdin=None):
    p=paths(generation)
    command=['/usr/bin/nsenter','--target',str(pid),'--mount','--net','--cgroup',
                           '/usr/bin/env','-i',*[k+'='+v for k,v in daemon_environment(generation).items()],
                           *args]
    key='operation-'+str(time.time_ns()); started=time.time()
    pin=None
    if stdin is not None:
        from docker_transport import _stdin
        _stdin(stdin);position=stdin.tell();digest=hashlib.sha256();size=0
        while chunk:=stdin.read(1024*1024):digest.update(chunk);size+=len(chunk)
        stdin.seek(position);pin={'sha256':digest.hexdigest(),'bytes':size}
    durable_json(Path(p['base'],key+'.command.json'),{'argv':command,'timeout':timeout,'stdin':pin,'started':started})
    record={'exitCode':None,'processMayRemain':False}
    try:
        stdout_path=Path(p['base'],key+'.stdout.log');stderr_path=Path(p['base'],key+'.stderr.log')
        with open(stdout_path,'xb') as stdout,open(stderr_path,'xb') as stderr:
            try:
                result=subprocess.run(command,stdin=stdin,stdout=stdout,stderr=stderr,timeout=timeout,check=False)
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


def admit_environment(handle,argv,*,timeout=30,stdin=None):
    generation=handle['generation']; p=paths(generation)
    validate_handle(handle,observe_handle(generation,handle['pid']))
    if not isinstance(argv,list) or not argv or any(not isinstance(v,str) or '\x00' in v for v in argv):
        raise ValueError('explicit Linux argv required')
    # Move this helper first: nsenter must inherit management membership.
    Path(p['cgroup'],'management','cgroup.procs').write_text(str(os.getpid())+'\n')
    validate_handle(handle,observe_handle(generation,handle['pid']))
    if stdin is None:return _entered(generation,handle['pid'],argv,timeout=timeout)
    return _entered(generation,handle['pid'],argv,timeout=timeout,stdin=stdin)


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

# Kept in this pinned module: no additional unpinned executable helper.
SMOKE_SCRIPT = b'''import json,os
from pathlib import Path
status=dict(line.split(":",1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
routes=Path("/proc/net/route").read_text().splitlines()[1:]
print(json.dumps({"uid":[int(v) for v in status["Uid"].split()],"gid":[int(v) for v in status["Gid"].split()],"capabilities":{k:status[k].strip() for k in ("CapInh","CapPrm","CapEff","CapBnd","CapAmb")},"noNewPrivs":int(status["NoNewPrivs"]),"rootReadOnly":bool(os.statvfs("/").f_flag & os.ST_RDONLY),"cpuMax":Path("/sys/fs/cgroup/cpu.max").read_text().strip(),"memoryMax":Path("/sys/fs/cgroup/memory.max").read_text().strip(),"pidsMax":Path("/sys/fs/cgroup/pids.max").read_text().strip(),"interfaces":sorted(p.name for p in Path("/sys/class/net").iterdir()),"routes":routes,"desktopPathsVisible":any(Path(p).exists() for p in ("/mnt/wsl/docker-desktop","/run/docker.sock","/var/run/docker.sock","/run/containerd/containerd.sock"))}))
'''


def validate_effective(value):
    try:
        quota,period=map(int,value['cpuMax'].split())
        valid=(period>0 and quota==2*period and value['uid']==[65534]*4 and value['gid']==[65534]*4
               and set(value['capabilities'])=={'CapInh','CapPrm','CapEff','CapBnd','CapAmb'}
               and all(int(v,16)==0 for v in value['capabilities'].values())
               and value['noNewPrivs']==1 and value['rootReadOnly'] is True
               and value['memoryMax']=='4294967296' and value['pidsMax']=='256'
               and value['interfaces']==['lo'] and value['routes']==[] and value['desktopPathsVisible'] is False)
    except (KeyError,TypeError,ValueError,AttributeError):valid=False
    if not valid:raise ValueError('actual container controls differ')


def validate_smoke_state(state):
    if (state.get('Running') is not False or type(state.get('ExitCode')) is not int
            or state['ExitCode']!=0 or state.get('OOMKilled') is not False or state.get('Status')!='exited'):
        raise ValueError('smoke container did not exit cleanly')


def validate_smoke_inspect(value,identifier):
    import development_image_migration as migration
    host=value.get('HostConfig',{});config=value.get('Config',{})
    expected={'NetworkMode':'none','ReadonlyRootfs':True,'Init':True,'NanoCpus':2000000000,
              'Memory':4294967296,'MemorySwap':8589934592,'PidsLimit':256,'CapDrop':['ALL'],
              'AutoRemove':False,'RestartPolicy':{'Name':'no','MaximumRetryCount':0},'CgroupnsMode':'private'}
    if (value.get('Id')!=identifier or value.get('Image')!=migration.FIXED['tools']['imageId']
            or value.get('Mounts')!=[] or any(host.get(k)!=v for k,v in expected.items())
            or host.get('Binds') not in (None,[]) or host.get('Mounts') not in (None,[])
            or host.get('CapAdd') not in (None,[]) or host.get('Privileged') is not False
            or host.get('SecurityOpt')!=['no-new-privileges'] or config.get('User')!='65534:65534'
            or config.get('Image')!=migration.FIXED['tools']['imageId'] or config.get('OpenStdin') is not True
            or config.get('Entrypoint')!=['/usr/bin/python3'] or config.get('Cmd')!=['-']):
        raise ValueError('smoke Docker configuration differs')


def inner_transport(generation):
    from docker_transport import DockerTransport
    p=paths(generation)
    return DockerTransport('/usr/local/bin/docker','unix://'+p['run']+'/docker.sock',p['base']+'/client')


def load_archives(generation,root,result_path):
    """Called only by the pinned dispatcher after namespace admission."""
    import development_image_migration as migration
    transport=inner_transport(generation)
    def recorded_runner(receipt,key,command,timeout,**kwargs):
        pin=None;source=kwargs.get('stdin')
        if source is not None:
            position=source.tell();digest=hashlib.sha256();size=0
            while chunk:=source.read(1024*1024):digest.update(chunk);size+=len(chunk)
            source.seek(position);pin={'sha256':digest.hexdigest(),'bytes':size}
        count=len(receipt['operations']);started=time.time()
        try:return transport.migration_runner(receipt,key,command,timeout,**kwargs)
        finally:
            if len(receipt['operations'])>count:
                receipt['operations'][-1].update(timeout=timeout,stdin=pin,started=started,finished=time.time())
    report=migration.load_all(root,docker=transport.executable,runner=recorded_runner,result_path=result_path)
    if (report.get('status')!='development-toolchain-load-inspect-verified'
            or report.get('completedLoads')!=['rust','tools','kotlin']
            or report.get('verifiedInspections')!=['rust','tools','kotlin']):
        raise ValueError('exact three role load/inspect completion required')
    return report


def run_smoke(handle):
    import development_image_migration as migration
    p=paths(handle['generation']);transport=inner_transport(handle['generation'])
    def docker(args,stdin=None):
        result=admit_environment(handle,transport.argv(args),timeout=60,stdin=stdin)
        if result.returncode:raise ValueError('smoke Docker command failed')
        return result.stdout
    identifier=docker(['create','--pull','never','--network','none','--read-only','--user','65534:65534',
        '--init','--cpus','2','--memory','4g','--memory-swap','8g','--pids-limit','256','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--restart','no','--interactive','--entrypoint','/usr/bin/python3',
        '--label','mingos.p01.generation='+handle['generation'],migration.FIXED['tools']['imageId'],'-']).decode().strip()
    if not re.fullmatch('[0-9a-f]{64}',identifier):raise ValueError('full smoke container ID required')
    durable_json(Path(p['base'],'smoke-created.json'),{'id':identifier})
    before=json.loads(docker(['container','inspect',identifier]))
    if not isinstance(before,list) or len(before)!=1:raise ValueError('one smoke inspection required')
    durable_json(Path(p['base'],'smoke-inspect-before.json'),before[0]);validate_smoke_inspect(before[0],identifier)
    if before[0].get('State',{}).get('Running') is not False:raise ValueError('smoke already running')
    script=Path(p['base'],'smoke.py')
    with script.open('xb') as stream:stream.write(SMOKE_SCRIPT);stream.flush();os.fsync(stream.fileno())
    pin={'sha256':hashlib.sha256(SMOKE_SCRIPT).hexdigest(),'bytes':len(SMOKE_SCRIPT)}
    start_args=['start','--attach','--interactive',identifier]
    start_record={'argv':transport.argv(start_args),'timeout':60,'stdin':pin,
                  'exitCode':None,'processMayRemain':False,'stdout':'','stderr':''}
    attach_error=inspection_error=evidence_error=None
    try:
        with script.open('rb') as stream:
            started=admit_environment(handle,transport.argv(start_args),timeout=60,stdin=stream)
        actual_raw=started.stdout
        start_record.update(exitCode=started.returncode,stdout=started.stdout.decode('utf8','replace'),
                            stderr=started.stderr.decode('utf8','replace'))
        if started.returncode:raise ValueError('smoke attach failed: exitCode='+str(started.returncode))
    except BaseException as error:
        attach_error=error;start_record['error']=repr(error)
        if isinstance(error,subprocess.TimeoutExpired):
            start_record.update(processMayRemain=True,stdout=(error.stdout or b'').decode('utf8','replace'),
                                stderr=(error.stderr or b'').decode('utf8','replace'))
    finally:
        try:durable_json(Path(p['base'],'smoke-start-result.json'),start_record)
        except BaseException as error:evidence_error=error
        # Exactly one bounded read-only attempt, including nonzero attach and timeout paths.
        try:
            after=json.loads(docker(['container','inspect',identifier]))
            if not isinstance(after,list) or len(after)!=1:raise ValueError('one final smoke inspection required')
            durable_json(Path(p['base'],'smoke-inspect-after.json'),after[0])
        except BaseException as error:
            inspection_error=error
            try:durable_json(Path(p['base'],'smoke-inspect-after-error.json'),{'error':repr(error),'timeout':60})
            except BaseException as log_error:
                if evidence_error is None:evidence_error=log_error
    if attach_error is not None:raise attach_error
    if inspection_error is not None:raise inspection_error
    if evidence_error is not None:raise evidence_error
    validate_smoke_inspect(after[0],identifier)
    validate_smoke_state(after[0].get('State',{}))
    actual=json.loads(actual_raw);durable_json(Path(p['base'],'smoke-actual-effective.json'),actual);validate_effective(actual)
    report={'id':identifier,'inspectBefore':before[0],'inspectAfter':after[0],'actualEffective':actual,'script':pin}
    durable_json(Path(p['base'],'smoke-result.json'),report)
    return report


def observe_guest(handle,load,smoke):
    """Read live identities and retained proofs; no load/create/repair operations."""
    import development_image_migration as migration
    generation=handle['generation'];p=paths(generation);transport=inner_transport(generation)
    validate_handle(handle,observe_handle(generation,handle['pid']))
    response=_entered(generation,handle['pid'],transport.argv(['info','--format','{{json .}}']))
    if response.returncode:raise ValueError('live daemon info failed')
    live_info=json.loads(response.stdout);validate_live_info(live_info,generation)
    first=json.loads(Path(p['base'],'first-empty.json').read_text());validate_info(first['info'])
    if set(first['lists'])!={'images','containers','volumes'} or any(v['exitCode']!=0 or v['stdout']!='' for v in first['lists'].values()):raise ValueError('first-empty proof differs')
    before=json.loads(Path(p['base'],'shared-before.json').read_text())
    if shared_controls()!=before:raise ValueError('shared ancestor controls changed')
    images=[]
    for role,entry in migration.FIXED.items():
        result=_entered(generation,handle['pid'],transport.argv(['image','inspect',entry['imageId']]))
        if result.returncode:raise ValueError('loaded image absent: '+role)
        value=json.loads(result.stdout)
        if not isinstance(value,list) or len(value)!=1:raise ValueError('one image required')
        expected=next(v for v in load['images'] if v['role']==role)
        from wsl_environment import validate_tools_image
        validate_tools_image(value[0],expected);images.append(value[0])
    result=_entered(generation,handle['pid'],transport.argv(['container','inspect',smoke['id']]))
    if result.returncode:raise ValueError('smoke absent')
    values=json.loads(result.stdout)
    if not isinstance(values,list) or len(values)!=1:raise ValueError('one smoke required')
    validate_smoke_inspect(values[0],smoke['id']);validate_smoke_state(values[0].get('State',{}))
    actual=json.loads(Path(p['base'],'smoke-actual-effective.json').read_text());validate_effective(actual)
    if actual!=smoke['actualEffective']:raise ValueError('retained smoke proof differs')
    private=json.loads(Path(p['base'],'private-evidence.json').read_text())
    for item in private['containerd']:
        if process_identity(item['pid'])!=item['identity']:raise ValueError('managed containerd identity changed')
        if Path('/proc',str(item['pid']),'cmdline').read_bytes().decode().split('\0')!=item['cmdline']:
            raise ValueError('managed containerd command changed')
    if len(private['containerd'])!=1:raise ValueError('one live managed containerd required')
    return {'handle':observe_handle(generation,handle['pid']),'firstEmpty':first,'images':images,'info':live_info,
            'smokeInspect':values[0],'actualEffective':actual,'sharedControls':before,
            'privateEvidence':private}


def validate_live_info(info,generation):
    # Keep the exact engine/platform/store checks while allowing loaded resource counts.
    validate_info({**info,'Images':0,'Containers':0})
    if (info.get('DockerRootDir')!=paths(generation)['data']+'/docker'
            or type(info.get('Images')) is not int or info['Images']<3
            or type(info.get('Containers')) is not int or info['Containers']<1):
        raise ValueError('live daemon store/configuration differs')


# Host pins are passed as literals to this small bootstrap, never read from a guest manifest.
TRUSTED_DISPATCH = '''import os,sys,json,hashlib,stat
root=sys.argv[1]; pins=json.loads(sys.argv[2]); request=json.loads(sys.stdin.buffer.read() if sys.argv[3]=='--stdin-request' else sys.argv[3])
parent=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
for component in root.split("/")[1:]:
 child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);os.close(parent);parent=child
if set(os.listdir(parent)) != set(pins): raise ValueError("guest closure differs")
for name,pin in pins.items():
 if name != os.path.basename(name): raise ValueError("invalid helper name")
 fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
 with os.fdopen(fd,"rb") as stream:
  value=os.fstat(stream.fileno()); digest=hashlib.sha256();count=0
  if not stat.S_ISREG(value.st_mode) or value.st_nlink!=1: raise ValueError("helper not regular")
  while chunk:=stream.read(1048576):digest.update(chunk);count+=len(chunk)
  if count!=pin["bytes"] or digest.hexdigest()!=pin["sha256"]: raise ValueError("guest helper pin differs")
os.close(parent);sys.dont_write_bytecode=True;sys.path.insert(0,root)
import wsl_environment_guest as guest
guest.dispatch(request)
'''


def operation_evidence(generation):
    base=Path(paths(generation)['base']);result={}
    if base.exists():
        for file in sorted(base.iterdir()):
            if file.is_file() and file.suffix in ('.json','.log'):
                # Logs are copied as text with replacement only in the envelope; originals remain binary.
                result[file.name]=file.read_bytes().decode('utf8','replace')
    return result


def dispatch(request):
    """Trusted host bootstrap entry, including an admitted internal load mode."""
    action=request['action'];handle=request.get('handle');generation=request['generation']
    result={};code=0
    try:
        if action=='load-direct':
            result=load_archives(generation,request['archiveRoot'],Path(paths(generation)['base'],'load-result.json'))
        elif action=='load':
            nested={**request,'action':'load-direct'}
            response=admit_environment(handle,['/usr/bin/python3','-I','-c',TRUSTED_DISPATCH,
                request['codeRoot'],json.dumps(request['pins']),json.dumps(nested)],timeout=5500)
            if response.returncode:raise ValueError('admitted load failed')
            envelope=json.loads(response.stdout)
            if envelope.get('status')!='ok':raise ValueError('admitted load returned failure')
            result=envelope['result']
        elif action=='smoke':result=run_smoke(handle)
        elif action=='observe':result=observe_guest(handle,request['load'],request['smoke'])
        elif action=='exec':
            response=admit_environment(handle,request['argv'],timeout=request['timeout'])
            result={'argv':request['argv'],'exitCode':response.returncode,'stdout':response.stdout.decode('utf8','replace'),'stderr':response.stderr.decode('utf8','replace')}
            if response.returncode:code=1
        else:raise ValueError('unknown guest action')
    except BaseException as error:
        result={'error':repr(error)};code=1
    envelope={'status':'ok' if code==0 else 'failed','result':result,'evidence':operation_evidence(generation)}
    print(json.dumps(envelope),flush=True)
    if code:raise SystemExit(code)
