"""Separately frozen P01 S3 INPUT transport; accepted S2 helpers remain unchanged.

The original pinned host harness manifest authorizes exactly this adapter role.
No export/seal implementation or arbitrary public uploader is provided here.
"""
from contextlib import contextmanager, ExitStack
import errno
from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shutil
import stat
import subprocess
import sys
import time
import uuid

HASH_CHUNK = 1024 * 1024
PIPE_CHUNK = 64 * 1024
CONTROL_CAP = 64 * 1024
STDERR_CAP = 1024 * 1024
ADAPTER_ROLE = 'scripts/boot/wsl_environment_transfer.py'
POLICIES = frozenset(('RECEIVER_CONTROL', 'EXPORT_ARCHIVE', 'COMPLETION_CONTROL'))
RECORD_KEYS = frozenset(('schemaVersion','operationNonce','action','status','child','bytes',
    'sha256','sealSha256','error','possibleSurvivors','observations'))


def _hex(value, length=64):
    if type(value) is not str or re.fullmatch('[0-9a-f]{'+str(length)+'}',value) is None:
        raise ValueError('invalid lowercase digest/nonce')
    return value


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum: raise ValueError('invalid integer')
    return value


def _remaining(deadline):
    if type(deadline) not in (float,int) or not math.isfinite(deadline): raise ValueError('invalid local deadline')
    remaining=deadline-time.monotonic()
    if remaining<=0: raise TimeoutError('operation deadline expired')
    return remaining


def _encode(value):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    if len(raw)>CONTROL_CAP: raise ValueError('control record exceeds cap')
    return raw


def _decode(raw):
    if len(raw)>CONTROL_CAP:raise ValueError('control record exceeds cap')
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate JSON key')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs)


def _stamp(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns,info.st_mode,info.st_nlink)


def _hash(stream):
    digest=hashlib.sha256();size=0
    while chunk:=stream.read(HASH_CHUNK):digest.update(chunk);size+=len(chunk)
    return {'bytes':size,'sha256':digest.hexdigest()}


@contextmanager
def _pinned_source(path, sha256, size, *, deadline=None):
    import source_build
    _hex(sha256);_integer(size)
    path=source_build._path(path)
    path_before=_stamp(path.lstat())
    with source_build._open(path) as incoming:
        before=_stamp(os.fstat(incoming.fileno()))
        if (_hash(incoming) if deadline is None else _host_hash(incoming,deadline))!={'bytes':size,'sha256':sha256}:raise ValueError('source size/hash mismatch')
        incoming.seek(0)
        try:yield incoming
        finally:
            incoming.seek(0)
            pin=_hash(incoming) if deadline is None else _host_hash(incoming,deadline)
            if (before!=_stamp(os.fstat(incoming.fileno())) or path_before!=_stamp(path.lstat())
                    or pin!={'bytes':size,'sha256':sha256}):raise ValueError('source changed during transfer')


def _local_context(environment_receipt,spec,spec_sha256,timeout):
    import source_build
    q=source_build.qualify_source_build(spec,spec_sha256)
    value=q['spec']
    if (source_build._path(environment_receipt)!=source_build._path(q['environment']['path'])
            or type(timeout) is not int or not 0<timeout<=value['limits']['transferTimeoutSeconds']):
        raise ValueError('receipt or timeout differs from qualified context')
    reader=source_build._Reader()
    reader.read(q['environment'],full=False)
    manifest=source_build._harness(reader,value['harness']);reader.recheck()
    manifest_path=Path(value['harness']['path'])/'manifest.json'
    with source_build._open(manifest_path) as stream:metadata=_hash(stream)
    total=value['handoff']['bytes']+sum(v['bytes'] for v in manifest['files'].values())+metadata['bytes']
    count=len(manifest['files'])+2
    return {'qualification':q,'spec':value,'manifest':manifest,'manifestPin':metadata,
        'inputBytes':total,'memberCount':count,'timeout':timeout,
        'base':'/var/lib/'+q['s2Generation']+'/s3/'+value['generation']}


def _capacity_required(context,retained=0):
    _integer(retained)
    planned=2*context['inputBytes']
    if retained>planned:raise ValueError('retained input accounting exceeds plan')
    # Separate complete per-member stdout/stderr and bounded command/operation records.
    overhead=(context['memberCount']+8)*(STDERR_CAP+8*CONTROL_CAP)
    return planned-retained+overhead+context['spec']['limits']['linuxReserveBytes']


def _check_capacity(context,available,retained=0):
    _integer(available)
    if available<_capacity_required(context,retained):raise ValueError('insufficient aggregate admitted input capacity')


def _frozen_harness(context,frozen_harness):
    import source_build
    root=source_build._path(frozen_harness)
    reader=source_build._Reader()
    with source_build._open(root/'freeze-result.json') as stream:
        raw=stream.read(source_build.MAX_JSON_BYTES+1)
    if len(raw)>source_build.MAX_JSON_BYTES:raise ValueError('freeze result too large')
    result=source_build._decode(raw)
    if (result.get('status')!='source-build-harness-frozen-local' or result.get('phase')!='complete'
            or not source_build._same(result.get('qualification'),context['qualification'])
            or result.get('root',{}).get('path')!=str(root/'harness')
            or result.get('root',{}).get('transferMode')!=0o555):raise ValueError('frozen harness context differs')
    dirs,files=source_build._walk(root/'harness');manifest=context['manifest']
    if set(dirs)!=set(manifest['directories']) or set(files)!=set(manifest['files'])|{'manifest.json'}:
        raise ValueError('frozen harness closure differs')
    if set(result.get('files',{}))!=set(manifest['files']) or set(result.get('directories',{}))!=set(dirs):
        raise ValueError('freeze result membership differs')
    members={}
    for name,entry in manifest['files'].items():
        expected=source_build._pin_of(entry)
        reader.read({'path':str(root/'harness'/name),**expected})
        recorded=result['files'][name]
        if (source_build._pin_of(recorded)!=expected or recorded.get('transferMode')!=entry['transferMode']
                or recorded.get('hostMode')!=files[name]):raise ValueError('freeze member differs')
        members[name]={'path':str(root/'harness'/name),**expected,'mode':entry['transferMode']}
    for name,mode in dirs.items():
        if result['directories'][name]!={'hostMode':mode,'transferMode':0o555}:raise ValueError('freeze directory differs')
    metadata=context['manifestPin']
    original=reader.read({'path':str(root/'harness'/'manifest.json'),**metadata})
    if not source_build._same(result.get('manifest'),original):raise ValueError('freeze manifest differs')
    reader.recheck()
    members['manifest.json']={**original,'mode':0o444}
    return members


class TransferFailure(ValueError,TimeoutError):
    """Bounded failure facts survive secondary persistence/cancellation errors."""
    def __init__(self,error,*,facts=None):
        super().__init__(_error_text(error));self.facts=dict(getattr(error,'facts',{}))
        if facts:
            for key,value in facts.items():
                if key=='possibleSurvivors':self.facts[key]=bool(value or self.facts.get(key))
                elif key in ('signals','ownedChildren','survivorIdentities','persistenceErrors'):
                    merged=list(self.facts.get(key,[]))
                    for item in value:
                        if key=='persistenceErrors':item=str(item).encode()[:512].decode(errors='ignore')
                        if item not in merged:merged.append(item)
                    self.facts[key]=merged[:8] if key=='persistenceErrors' else merged
                else:self.facts[key]=value
        self.possible_survivors=bool(self.facts.get('possibleSurvivors',getattr(error,'possible_survivors',False)))
        self.facts['possibleSurvivors']=self.possible_survivors
        _encode(self.facts)

class TransferFileExistsFailure(TransferFailure,FileExistsError):pass

def _failure(cause,**facts):
    kind=TransferFileExistsFailure if isinstance(cause,FileExistsError) else TransferFailure
    return kind(cause,facts=facts)


def _error_text(error):
    # Ignoring an incomplete trailing codepoint never grows the declared byte cap.
    return str(error).encode('utf-8',errors='replace')[:4096].decode('utf-8',errors='ignore') or type(error).__name__


def validate_operation_record(record):
    if type(record) is not dict or set(record)!=RECORD_KEYS:raise ValueError('operation record keys differ')
    if type(record['schemaVersion']) is not int or record['schemaVersion']!=1:raise ValueError('operation schema differs')
    _hex(record['operationNonce'],32);_integer(record['bytes'])
    if record['action'] not in ('receive','export') or record['status'] not in ('started','succeeded','failed'):
        raise ValueError('operation action/status differs')
    for name in ('sha256','sealSha256'):
        if record[name] is not None:_hex(record[name])
    if record['action']=='receive' and record['sealSha256'] is not None:raise ValueError('input seal forbidden')
    child=record['child']
    if child is not None:
        if type(child) is not dict or set(child)!= {'pid','starttime','pgid','sid'}:raise ValueError('child keys differ')
        for name in ('pid','pgid','sid'):_integer(child[name],1)
        if child['pid']!=child['pgid'] or child['pid']!=child['sid']:raise ValueError('dedicated child session required')
        if type(child['starttime']) is not str or re.fullmatch('[0-9]+',child['starttime']) is None:raise ValueError('invalid child starttime')
    if type(record['possibleSurvivors']) is not bool:raise ValueError('survivor flag must be boolean')
    if record['error'] is not None and (type(record['error']) is not str or not record['error'] or len(record['error'].encode())>4096):raise ValueError('error exceeds cap')
    if (record['status']=='failed') != (record['error'] is not None):raise ValueError('status/error invariant differs')
    refs=record['observations']
    if type(refs) is not list or len(refs)>32:raise ValueError('too many observation refs')
    for ref in refs:
        if type(ref) is not dict or set(ref)!= {'path','bytes','sha256'}:raise ValueError('observation ref differs')
        if (type(ref['path']) is not str or not ref['path'].startswith('/') or ref['path'].startswith('//') or ref['path']=='/' or len(ref['path'].encode())>4096
                or '\\' in ref['path'] or '\x00' in ref['path'] or PurePosixPath(ref['path']).as_posix()!=ref['path']
                or any(part in ('.','..') for part in ref['path'].split('/')[1:])):raise ValueError('invalid observation path')
        _integer(ref['bytes']);_hex(ref['sha256'])
    if record['status']=='succeeded' and (child is None or record['sha256'] is None or record['possibleSurvivors'] or record['error'] is not None):
        raise ValueError('incomplete success record')
    _encode(record)
    return record


def _proc_fields(pid):
    raw=Path('/proc',str(pid),'stat').read_text()
    fields=raw.rsplit(')',1)[1].split()
    return {'pid':pid,'starttime':fields[19],'pgid':int(fields[2]),'sid':int(fields[3])}


def _process_identity_work(pid,*,guest_deadline):
    _integer(pid,1);_remaining(guest_deadline)
    proc=Path('/proc')/str(pid)
    before=_proc_fields(pid)
    namespaces={name:os.readlink(proc/'ns'/name) for name in ('mnt','net','cgroup')}
    with (proc/'exe').open('rb',buffering=0) as executable:
        first=_stamp(os.fstat(executable.fileno()));digest=hashlib.sha256()
        while chunk:=executable.read(HASH_CHUNK):_remaining(guest_deadline);digest.update(chunk)
        if first!=_stamp(os.fstat(executable.fileno())) or first!=_stamp((proc/'exe').stat()):raise ValueError('executable identity changed')
    if before!=_proc_fields(pid) or namespaces!={name:os.readlink(proc/'ns'/name) for name in namespaces}:
        raise ValueError('process identity changed')
    _remaining(guest_deadline)
    return {'pid':pid,'starttime':before['starttime'],'exeSha256':digest.hexdigest(),'namespaces':namespaces}


def bounded_process_identity(pid,*,guest_deadline,operation_directory):
    return _fork_job(lambda:_process_identity_work(pid,guest_deadline=guest_deadline),
        deadline=guest_deadline,evidence_path=Path(operation_directory)/('identity-worker-'+uuid.uuid4().hex+'.json'))


def _socket_identity(path):
    info=os.stat(path,follow_symlinks=False)
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=0 or info.st_gid!=0:raise ValueError('root-owned real socket required')
    return {'device':info.st_dev,'inode':info.st_ino,'uid':info.st_uid,'gid':info.st_gid}


# Frozen two-stage handshake: no command executes before parent kernel-identity
# comparison AND the launcher's exclusive fsynced identity receipt acknowledge.
_LAUNCHER = r'''import json,os,sys
ready=int(sys.argv[1]);ack=int(sys.argv[2]);identity_path=sys.argv[3]
os.write(ready,b'H')
raw=b''
while not raw.endswith(b'\n'):
    piece=os.read(ack,4096)
    if not piece:raise SystemExit(120)
    raw+=piece
    if len(raw)>65536:raise SystemExit(121)
record=json.loads(raw)
fd=os.open(identity_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
try:
    view=memoryview(raw)
    while view:
        n=os.write(fd,view)
        if n<=0:raise OSError('identity write stalled')
        view=view[n:]
    os.fsync(fd)
finally:os.close(fd)
os.write(ready,b'R')
if os.read(ack,1)!=b'E':raise SystemExit(122)
os.close(ready);os.close(ack)
argv=json.loads(sys.argv[4]);os.execv(argv[0],argv)
'''


def _owned(child):
    try:return _proc_fields(child['pid'])==child and os.getpgid(child['pid'])==child['pid'] and os.getsid(child['pid'])==child['pid']
    except (ProcessLookupError,FileNotFoundError):return False


def _cancel(process,child,*,deadline):
    import signal
    signals=[];possible=False
    for sig,seconds in ((signal.SIGTERM,2),(signal.SIGKILL,3)):
        if process.poll() is not None:break
        if time.monotonic()>=deadline or child is None or not _owned(child):possible=True;break
        try:os.killpg(child['pid'],sig);signals.append(sig.name)
        except ProcessLookupError:break
        try:process.wait(timeout=max(0,min(seconds,deadline-time.monotonic())))
        except subprocess.TimeoutExpired:possible=True
    if process.poll() is None:possible=True
    return signals,possible


def _byte(fd,deadline):
    os.set_blocking(fd,False)
    with selectors.DefaultSelector() as selected:
        selected.register(fd,selectors.EVENT_READ)
        while True:
            if not selected.select(_remaining(deadline)):raise TimeoutError('launcher handshake stalled')
            try:
                value=os.read(fd,1)
                if not value:raise ValueError('launcher disconnected')
                return value
            except BlockingIOError:pass


def _launch(argv,input_file,directory,deadline):
    if not isinstance(argv,list) or not argv or any(type(v) is not str or '\x00' in v for v in argv) or not argv[0].startswith('/'):
        raise ValueError('absolute binary argv required')
    ready_r,ready_w=os.pipe();ack_r,ack_w=os.pipe()
    process=None;child=None
    try:
        process=subprocess.Popen([sys.executable,'-I','-B','-c',_LAUNCHER,str(ready_w),str(ack_r),
            str(directory/'child.identity.json'),json.dumps(argv)],stdin=input_file if input_file is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,shell=False,start_new_session=True,pass_fds=(ready_w,ack_r,*_owner_pass_fds()),bufsize=0)
        os.close(ready_w);ready_w=None;os.close(ack_r);ack_r=None
        handshake=min(deadline,time.monotonic()+5)
        if _byte(ready_r,handshake)!=b'H':raise ValueError('launcher handshake differs')
        child=_proc_fields(process.pid)
        owner_registration=_register_owner(child,deadline)
        if not _owned(child) or child['pgid']!=process.pid or child['sid']!=process.pid:raise ValueError('launcher session ownership differs')
        record={**child,'ownerRegistration':owner_registration,'ownerParent':_proc_fields(os.getpid()),'operationNonce':uuid.uuid4().hex,'argv':argv,'launcherSha256':hashlib.sha256(_LAUNCHER.encode()).hexdigest()}
        os.write(ack_w,_encode(record))
        if _byte(ready_r,handshake)!=b'R' or not _owned(child):raise ValueError('launcher registration differs')
        os.write(ack_w,b'E')
        return process,child,owner_registration
    except BaseException:
        if process is not None:_cancel(process,child,deadline=_shared_end(deadline,True))
        raise
    finally:
        for fd in (ready_r,ready_w,ack_r,ack_w):
            if fd is not None:os.close(fd)


def _write_all(fd,data,deadline):
    view=memoryview(data)
    while view:
        _remaining(deadline)
        try:written=os.write(fd,view)
        except InterruptedError:continue
        if written<=0:raise OSError('write made no progress')
        view=view[written:]


def _exclusive_bytes(path,data):
    parent=_safe_dir(str(Path(path).parent),root_owned=False,create=False)
    try:fd=os.open(Path(path).name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600,dir_fd=parent)
    finally:os.close(parent)
    try:
        view=memoryview(data)
        while view:
            written=os.write(fd,view)
            if written<=0:raise OSError('write made no progress')
            view=view[written:]
        os.fsync(fd)
    finally:os.close(fd)


def _supervise(argv,*,input_file,output_policy,guest_deadline,operation_directory):
    """Linux registered-command supervisor. Capped binary pipes, no S2 capture."""
    if output_policy not in POLICIES:raise ValueError('unknown trusted output policy')
    _remaining(guest_deadline)
    directory=Path(operation_directory)
    if input_file is not None:
        from docker_transport import _stdin
        _stdin(input_file)
        if not stat.S_ISREG(os.fstat(input_file.fileno()).st_mode) or not input_file.seekable():raise ValueError('seekable regular rb spool required')
    process=None;child=None;owner_registration=None;failure=None;stdout=bytearray();stderr=bytearray();signals=[];possible=False
    forwarding=output_policy=='EXPORT_ARCHIVE';pending=deque();forwarded=0;forward_hash=hashlib.sha256();old_blocking=None
    if forwarding:
        sink=os.fstat(1)
        if not (stat.S_ISFIFO(sink.st_mode) or stat.S_ISSOCK(sink.st_mode)):
            raise ValueError('binary archive fd1 must be a pipe/socket; regular-file work belongs to supervised host sink')
        old_blocking=os.get_blocking(1);os.set_blocking(1,False)
    try:
        process,child,owner_registration=_launch(argv,input_file,directory,guest_deadline)
        with selectors.DefaultSelector() as selected:
            for stream,name in ((process.stdout,'stdout'),(process.stderr,'stderr')):
                os.set_blocking(stream.fileno(),False);selected.register(stream,selectors.EVENT_READ,name)
            while selected.get_map() or pending:
                _remaining(guest_deadline)
                if pending:
                    try:
                        written=os.write(1,pending[0])
                        if written<=0:raise OSError('binary fd1 made no progress')
                        forward_hash.update(pending[0][:written]);forwarded+=written
                        pending[0]=pending[0][written:]
                        if not pending[0]:pending.popleft()
                    except BlockingIOError:pass
                ready=selected.select(min(.01 if forwarding else .1,_remaining(guest_deadline)))
                for key,_ in ready:
                    if forwarding and key.data=='stdout' and len(pending)>=2:
                        continue
                    try:data=os.read(key.fd,PIPE_CHUNK)
                    except BlockingIOError:continue
                    if not data:selected.unregister(key.fileobj);key.fileobj.close();continue
                    if forwarding and key.data=='stdout':
                        pending.append(memoryview(data));continue
                    target=stdout if key.data=='stdout' else stderr
                    cap=CONTROL_CAP if key.data=='stdout' else STDERR_CAP
                    if len(target)+len(data)>cap:
                        target.extend(data[:cap-len(target)])
                        raise ValueError(key.data+' exceeds bounded channel cap')
                    target.extend(data)
                if forwarding and len(pending)>=2:time.sleep(min(.001,_remaining(guest_deadline)))
            process.wait(timeout=_remaining(guest_deadline))
        if process.returncode:raise ValueError('command exited nonzero: '+str(process.returncode))
    except BaseException as error:
        failure=error
        if process is not None:
            signals,possible=_cancel(process,child,deadline=_shared_end(guest_deadline,True))
            if process.poll() is not None and isinstance(error,TimeoutError):possible=True
    finally:
        if process is not None:
            for stream in (process.stdout,process.stderr):
                if stream is not None and not stream.closed:stream.close()
        if old_blocking is not None:os.set_blocking(1,old_blocking)
        # Evidence persistence is delegated to a cancellable worker below.
        result={'status':'failed' if failure else 'succeeded','child':child,'exitCode':None if process is None else process.poll(),
            'possibleSurvivors':possible,'ownedChildren':[] if child is None else [child],'signals':signals,'stdoutBytes':forwarded if forwarding else len(stdout),
            'stdoutSha256':forward_hash.hexdigest() if forwarding else hashlib.sha256(stdout).hexdigest(),
            'error':None if failure is None else str(failure)[:4096]}
        try:
            end=_shared_end(guest_deadline,True) if failure else guest_deadline
            _persist_files(directory,{'stdout.log':bytes(stdout),'stderr.log':bytes(stderr),'process-result.json':_encode(result)},deadline=end)
            if child is not None and process.returncode is not None and not _retire_owner(child,owner_registration,end):
                result.update(status='failed',possibleSurvivors=True,error='completed command retains possible group descendants')
                _persist_files(directory,{'retirement-failed.json':_encode(result)},deadline=end)
                failure=_failure(failure or result['error'],**result)
        except BaseException as persistence:
            failure=_failure(failure or persistence,**result,persistenceErrors=[_error_text(persistence)])
    if failure is not None:raise _failure(failure,**result)
    return {**result,'stdout':bytes(stdout),'stderr':bytes(stderr)}


def _fork_job(function,*,deadline,evidence_path):
    """Supervise blocking local disk/hash work; no blocking parent disk/reap."""
    deadline=min(deadline,_shared_end(deadline));_remaining(deadline)
    ready_r,ready_w=os.pipe();ack_r,ack_w=os.pipe();result_r,result_w=os.pipe()
    pid=os.fork()
    if pid==0:
        try:
            os.close(ready_r);os.close(ack_w);os.close(result_r);os.setsid()
            os.write(ready_w,b'H')
            raw=b''
            while not raw.endswith(b'\n'):
                piece=os.read(ack_r,4096)
                if not piece:os._exit(120)
                raw+=piece
                if len(raw)>CONTROL_CAP:os._exit(121)
            registration_raw=bytes(raw);registration=_decode(raw)
            _exclusive_bytes(evidence_path,raw)
            os.write(ready_w,b'R')
            if os.read(ack_r,1)!=b'E':os._exit(122)
            os.close(ready_w);os.close(ack_r)
            value=function()
            raw=_encode({'ok':True,'value':value})
        except BaseException as error:
            raw=_encode({'ok':False,'type':type(error).__name__,'error':str(error)[:4096]})
        try:
            _exclusive_bytes(Path(str(evidence_path)+'.terminal.json'),_encode({'child':{key:registration[key] for key in ('pid','starttime','pgid','sid')},'ownerRegistration':registration['ownerRegistration'],'registrationSha256':hashlib.sha256(registration_raw).hexdigest(),'status':'succeeded' if _decode(raw)['ok'] else 'failed'}))
            os.write(result_w,raw)
        finally:os._exit(0)
    os.close(ready_w);os.close(ack_r);os.close(result_w)
    child=None;owner_registration=None;result=bytearray();reaped=False
    try:
        if _byte(ready_r,min(deadline,time.monotonic()+5))!=b'H':raise ValueError('disk worker handshake failed')
        child=_proc_fields(pid)
        owner_registration=_register_owner(child,deadline)
        if not _owned(child):raise ValueError('disk worker identity differs')
        os.write(ack_w,_encode({**child,'ownerRegistration':owner_registration,'ownerParent':_proc_fields(os.getpid()),'operationNonce':uuid.uuid4().hex,'status':'registered'}))
        if _byte(ready_r,min(deadline,time.monotonic()+5))!=b'R' or not _owned(child):
            raise ValueError('disk worker durable registration differs')
        os.write(ack_w,b'E')
        os.set_blocking(result_r,False)
        with selectors.DefaultSelector() as selected:
            selected.register(result_r,selectors.EVENT_READ)
            while True:
                if not selected.select(_remaining(deadline)):raise TimeoutError('disk worker stalled')
                try:chunk=os.read(result_r,PIPE_CHUNK)
                except BlockingIOError:continue
                if not chunk:break
                result.extend(chunk)
                if len(result)>CONTROL_CAP:raise ValueError('disk worker response exceeds cap')
        while True:
            got,status=os.waitpid(pid,os.WNOHANG)
            if got:reaped=True;break
            _remaining(deadline);time.sleep(.005)
        value=_decode(result)
        if os.waitstatus_to_exitcode(status)!=0:raise ValueError('disk worker terminal exit differs')
        if not _retire_owner(child,owner_registration,deadline):raise _failure('completed disk worker retains possible group descendants',child=child,possibleSurvivors=True)
        if not value['ok']:
            cls={'ValueError':ValueError,'FileExistsError':FileExistsError,'TimeoutError':TimeoutError}.get(value['type'],OSError)
            raise cls(value['error'])
        return value['value']
    except BaseException as error:
        import signal
        end=_shared_end(deadline,True);signals=[]
        for sig,seconds in ((signal.SIGTERM,2),(signal.SIGKILL,3)):
            if reaped or time.monotonic()>=end:break
            if child is None or not _owned(child):break
            try:os.killpg(pid,sig);signals.append(sig.name)
            except ProcessLookupError:break
            phase_end=min(end,time.monotonic()+seconds)
            while time.monotonic()<phase_end:
                got,_=os.waitpid(pid,os.WNOHANG)
                if got:reaped=True;break
                time.sleep(min(.005,max(0,phase_end-time.monotonic())))
        raise _failure(error,child=child,ownedChildren=[] if child is None else [child],signals=signals,possibleSurvivors=not reaped)
    finally:
        for fd in (ready_r,ack_w,result_r):os.close(fd)


def _persist_files(directory,files,*,deadline):
    def write():
        for name,data in files.items():_exclusive_bytes(Path(directory)/name,data)
        return None
    if os.name=='posix':return _fork_job(write,deadline=deadline,evidence_path=Path(directory)/('persistence-worker-'+uuid.uuid4().hex+'.json'))
    # The Windows host path uses its independently supervised host worker.
    raise ValueError('guest persistence requires Linux')


def stage_input(stream,path,sha256,size,*,guest_deadline,filesystem=None,required_available=0):
    _hex(sha256);_integer(size);_remaining(guest_deadline)
    path=Path(path)
    def copy():
        parent=_safe_dir(str(path.parent),root_owned=False,create=False)
        parent_stamp=_stamp(os.fstat(parent))
        try:
            if filesystem is not None:
                space=os.fstatvfs(parent)
                if (parent_stamp[0],parent_stamp[1])!=(filesystem['device'],filesystem['inode']) or space.f_bavail*space.f_frsize<required_available:raise ValueError('actual spool target changed or lacks capacity')
            fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
            try:
                digest=hashlib.sha256();count=0
                while True:
                    chunk=stream.read(min(HASH_CHUNK,size-count+1))
                    if not chunk:break
                    _write_all(fd,chunk,guest_deadline);digest.update(chunk);count+=len(chunk)
                    if count>size:raise ValueError('input exceeds expected size sentinel')
                if count!=size or digest.hexdigest()!=sha256:raise ValueError('input size/hash mismatch')
                os.fsync(fd);first=_stamp(os.fstat(fd))
            finally:os.close(fd)
            fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
            with os.fdopen(fd,'rb') as incoming:
                if (_stamp(os.fstat(incoming.fileno()))!=first or _hash(incoming)!={'bytes':size,'sha256':sha256}
                        or _stamp(os.stat(path.name,dir_fd=parent,follow_symlinks=False))!=first):
                    raise ValueError('spool identity changed')
            # Directory size/mtime legitimately changes when creating a child;
            # device/inode/type identity must still match its canonical path.
            current=os.stat(path.parent,follow_symlinks=False)
            if (current.st_dev,current.st_ino,stat.S_IFMT(current.st_mode))!=(parent_stamp[0],parent_stamp[1],stat.S_IFMT(parent_stamp[5])):
                raise ValueError('spool parent identity changed')
            return str(path)
        finally:os.close(parent)

    return Path(_fork_job(copy,deadline=guest_deadline,evidence_path=path.parent/('spool-worker-'+uuid.uuid4().hex+'.json')))


def _native_job(process):
    """Owned Windows Job keeps blocking native descendants under the host owner."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    class Basic(ctypes.Structure):
        _fields_=[('processTime',ctypes.c_longlong),('jobTime',ctypes.c_longlong),('flags',wintypes.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class IO(ctypes.Structure):_fields_=[(name,ctypes.c_ulonglong) for name in ('readOps','writeOps','otherOps','readBytes','writeBytes','otherBytes')]
    class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('processMemory',ctypes.c_size_t),('jobMemory',ctypes.c_size_t),('peakProcess',ctypes.c_size_t),('peakJob',ctypes.c_size_t)]
    kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.LPVOID,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateJobObjectW(None,None)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    limits=Extended();limits.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    try:
        if not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)):raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.AssignProcessToJobObject(handle,int(process._handle)):raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:kernel.CloseHandle(handle);raise
    return lambda:kernel.CloseHandle(handle)

_HOST_TRANSACTION_WORKER=False
_HOST_ABSOLUTE_CANCEL_END=None
_HOST_CANCEL_MAP=None

def _host_cancel_end(deadline,*,start=False):
    global _HOST_ABSOLUTE_CANCEL_END
    import struct
    end=deadline+5
    if _HOST_ABSOLUTE_CANCEL_END is not None:end=min(end,_HOST_ABSOLUTE_CANCEL_END)
    if _HOST_CANCEL_MAP is not None:end=min(end,struct.unpack('d',_HOST_CANCEL_MAP[:8])[0])
    if start:
        end=min(end,time.monotonic()+5)
        if _HOST_ABSOLUTE_CANCEL_END is not None:_HOST_ABSOLUTE_CANCEL_END=end
        # Only the owned transaction writes this shared endpoint. The outer
        # parent reads it before cancelling or persisting ambiguous evidence.
        if _HOST_TRANSACTION_WORKER and _HOST_CANCEL_MAP is not None:_HOST_CANCEL_MAP[:8]=struct.pack('d',end)
    return end


def _pump_host(argv,*,input_file,deadline,input_bytes=None,pass_fds=()):
    """Native binary descriptor forwarding with independent bounded host deadline.

    os.set_blocking supports Windows anonymous pipes on supported Python (3.12+).
    No shell, text wrapping, communicate or bulk parent-file I/O is used.
    """
    if not isinstance(argv,list) or not argv or not os.path.isabs(argv[0]):raise ValueError('absolute host executable required')
    if input_file is not None and input_bytes is not None:raise ValueError('two stdin sources')
    if input_file is not None:
        from docker_transport import _stdin
        _stdin(input_file)
    if input_bytes is not None and len(input_bytes)>2*STDERR_CAP:raise ValueError('host control input exceeds cap')
    deadline=min(deadline,_host_cancel_end(deadline));_remaining(deadline)
    process=subprocess.Popen(argv,stdin=subprocess.PIPE if input_bytes is not None else input_file if input_file is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,shell=False,start_new_session=os.name=='posix' and not _HOST_TRANSACTION_WORKER,**({'pass_fds':pass_fds} if os.name=='posix' else {}))
    job=None;host_child=None
    out=bytearray();err=bytearray();channels={process.stdout.fileno():(process.stdout,out,CONTROL_CAP),process.stderr.fileno():(process.stderr,err,STDERR_CAP)}
    pending=memoryview(input_bytes) if input_bytes is not None else None
    try:
        job=_native_job(process)
        if os.name=='posix' and not _HOST_TRANSACTION_WORKER:host_child=_proc_fields(process.pid)
        for fd in channels:os.set_blocking(fd,False)
        if pending is not None:os.set_blocking(process.stdin.fileno(),False)
        while channels or process.poll() is None or pending is not None:
            _remaining(min(deadline,_host_cancel_end(deadline)));progress=False
            if pending is not None:
                try:
                    if pending:
                        n=os.write(process.stdin.fileno(),pending[:PIPE_CHUNK])
                        if n<=0:raise OSError('host control write made no progress')
                        pending=pending[n:];progress=True
                    else:process.stdin.close();pending=None
                except BlockingIOError:pass
            for fd,(stream,target,cap) in list(channels.items()):
                try:data=os.read(fd,PIPE_CHUNK)
                except BlockingIOError:continue
                if not data:stream.close();del channels[fd];progress=True;continue
                if len(target)+len(data)>cap:raise ValueError('host channel exceeds cap')
                target.extend(data);progress=True
            if not progress:time.sleep(min(.005,_remaining(min(deadline,_host_cancel_end(deadline)))))
        if process.returncode:raise ValueError('host invocation exited nonzero: '+str(process.returncode))
        return {'stdout':bytes(out),'stderr':bytes(err),'exitCode':process.returncode}
    except BaseException as error:
        # Popen's owned native process handle authorizes this exact process only;
        # Linux descendants retain their own independent guest watchdog budget.
        cancel_end=_host_cancel_end(deadline,start=True)
        if process.poll() is None and host_child is not None:
            _cancel(process,host_child,deadline=cancel_end)
        elif process.poll() is None:
            process.terminate()
            end=min(cancel_end,time.monotonic()+2)
            while process.poll() is None and time.monotonic()<end:time.sleep(.005)
            if process.poll() is None:
                process.kill();end=cancel_end
                while process.poll() is None and time.monotonic()<end:time.sleep(.005)
        error.partial_stdout=bytes(out);error.partial_stderr=bytes(err)
        failure=_failure(error,possibleSurvivors=True,hostChild=host_child,hostStdoutBytes=len(out),hostStderrBytes=len(err))
        for line in bytes(err).splitlines():
            for prefix in (b'P01_TRANSFER_FAILURE: ',b'P01_OWNER_FAILURE: '):
                if line.startswith(prefix):
                    try:failure=_failure(failure,**_decode(line[len(prefix):]))
                    except (ValueError,TypeError,KeyError):pass
        failure.partial_stdout=bytes(out);failure.partial_stderr=bytes(err)
        raise failure
    finally:
        if job is not None:job()
        for stream in (process.stdin if input_bytes is not None else None,process.stdout,process.stderr):
            if stream is not None and not stream.closed:stream.close()


_HOST_WRITER = r'''import os,sys
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
try:
 while b:=sys.stdin.buffer.read(65536):
  v=memoryview(b)
  while v:
   n=os.write(fd,v)
   if n<=0:raise OSError('zero write')
   v=v[n:]
 os.fsync(fd)
finally:os.close(fd)
'''


def _host_write(path,data,deadline):
    _pump_host([sys.executable,'-I','-B','-c',_HOST_WRITER,str(path)],input_file=None,input_bytes=data,deadline=deadline)


def _adapter_pin(context):
    entry=context['manifest']['files'].get(ADAPTER_ROLE)
    if type(entry) is not dict or entry.get('transferMode') not in (0o444,0o555):raise ValueError('fixed adapter manifest role missing')
    pin={'bytes':entry['bytes'],'sha256':entry['sha256']}
    # Installed code is checked independently from the original pinned host copy.
    for path in (Path(__file__).absolute(),Path(context['spec']['harness']['path'])/ADAPTER_ROLE):
        with _pinned_source(path,pin['sha256'],pin['bytes']):pass
    if pin['bytes']>HASH_CHUNK:raise ValueError('frozen bootstrap adapter exceeds code bound')
    return pin


def _safe_dir(path,*,exclusive=False,root_owned=True,create=True):
    """Descriptor-relative nofollow directory traversal; retained on every error."""
    value=PurePosixPath(path)
    if not value.is_absolute() or value.as_posix()!=str(path) or any(v in ('..','.') for v in value.parts):raise ValueError('canonical absolute guest directory required')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for i,name in enumerate(value.parts[1:]):
            terminal=i==len(value.parts)-2
            if create:
                try:os.mkdir(name,0o700,dir_fd=fd)
                except FileExistsError:
                    if exclusive and terminal:raise
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            info=os.fstat(child)
            if not stat.S_ISDIR(info.st_mode) or root_owned and (info.st_uid!=0 or info.st_gid!=0):
                os.close(child);raise ValueError('directory ownership differs')
            os.close(fd);fd=child
        return fd
    except BaseException:os.close(fd);raise


def _safe_read(path,cap=CONTROL_CAP,*,root_owned=True):
    parent=_safe_dir(str(Path(path).parent),root_owned=root_owned,create=False)
    try:fd=os.open(Path(path).name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
    finally:os.close(parent)
    try:
        first=os.fstat(fd)
        if (not stat.S_ISREG(first.st_mode) or first.st_nlink!=1 or first.st_size>cap
                or root_owned and (first.st_uid!=0 or first.st_gid!=0)):raise ValueError('unsafe control/code file')
        raw=bytearray()
        while len(raw)<=cap:
            chunk=os.read(fd,min(HASH_CHUNK,cap+1-len(raw)))
            if not chunk:break
            raw.extend(chunk)
        if len(raw)>cap or _stamp(first)!=_stamp(os.fstat(fd)) or _stamp(first)!=_stamp(os.stat(path,follow_symlinks=False)):
            raise ValueError('control/code file identity changed')
        return bytes(raw)
    finally:os.close(fd)


def _nsenter(handle,argv):
    import wsl_environment_guest as accepted
    return ['/usr/bin/nsenter','--target',str(handle['pid']),'--mount','--net','--cgroup',
        '/usr/bin/env','-i',*[key+'='+value for key,value in accepted.daemon_environment(handle['generation']).items()],*argv]


def _observe(handle,deadline,directory):
    import wsl_environment_guest as accepted
    before=bounded_process_identity(handle['pid'],guest_deadline=deadline,operation_directory=directory)
    paths=accepted.paths(handle['generation'])
    endpoint=paths['run']+'/docker.sock'
    socket_before=_socket_identity(endpoint)
    op=Path(directory)/('docker-info-'+uuid.uuid4().hex);os.close(_safe_dir(str(op),exclusive=True))
    result=_supervise(_nsenter(handle,['/usr/local/bin/docker','--host','unix://'+endpoint,
        '--config',paths['base']+'/client','info','--format','{{json .}}']),input_file=None,
        output_policy='RECEIVER_CONTROL',guest_deadline=deadline,operation_directory=op)
    info=_decode(result['stdout'])
    namespaces=accepted.validate_containerd_namespaces(info,handle['generation'])
    if type(info.get('ID')) is not str or not info['ID']:raise ValueError('actual daemon ID required')
    after=bounded_process_identity(handle['pid'],guest_deadline=deadline,operation_directory=directory)
    if before!=after or socket_before!=_socket_identity(endpoint):raise ValueError('daemon identity changed during info')
    observed={**before,'socket':socket_before,'daemonId':info['ID'],'containerdNamespaces':namespaces}
    accepted.validate_handle(handle,observed)
    return observed


def _management(handle,deadline,directory):
    import wsl_environment_guest as accepted
    path=accepted.paths(handle['generation'])['cgroup']+'/management/cgroup.procs'
    before=_proc_fields(os.getpid());pid=os.getpid()
    def move():
        parent=_safe_dir(str(Path(path).parent))
        try:fd=os.open('cgroup.procs',os.O_WRONLY|os.O_NOFOLLOW,dir_fd=parent)
        finally:os.close(parent)
        try:_write_all(fd,(str(pid)+'\n').encode(),deadline)
        finally:os.close(fd)
        return None
    _fork_job(move,deadline=deadline,evidence_path=Path(directory)/('management-move-'+uuid.uuid4().hex+'.json'))
    if before!=_proc_fields(pid):raise ValueError('adapter identity changed during cgroup move')
    def membership():
        with open(path,'rb') as incoming:raw=incoming.read(CONTROL_CAP+1)
        with open('/proc/'+str(pid)+'/cgroup','rb') as incoming:actual=incoming.read(CONTROL_CAP+1)
        if len(raw)>CONTROL_CAP or len(actual)>CONTROL_CAP:raise ValueError('membership observation exceeds cap')
        if str(pid).encode() not in raw.splitlines() or not any(line.endswith(('/'+handle['generation']+'/management').encode()) for line in actual.splitlines()):
            raise ValueError('actual management membership differs')
        return None
    _fork_job(membership,deadline=deadline,evidence_path=Path(directory)/('management-check-'+uuid.uuid4().hex+'.json'))


def _admit(handle,deadline,directory):
    first=_observe(handle,deadline,directory)
    _management(handle,deadline,directory)
    second=_observe(handle,deadline,directory)
    _persist_files(directory,{'admission.json':_encode({'status':'admission-succeeded','before':first,'after':second})},deadline=deadline)
    return first,second


def stream_admitted(handle,argv,*,input_file,output_policy,guest_deadline,operation_directory):
    """New bounded admission path; it never calls accepted _entered/capture."""
    import wsl_environment_guest as accepted
    if output_policy not in POLICIES:raise ValueError('unknown trusted output policy')
    directory=Path(operation_directory)
    _admit(handle,guest_deadline,directory)
    result=_supervise(_nsenter(handle,argv),input_file=input_file,output_policy=output_policy,
        guest_deadline=guest_deadline,operation_directory=directory)
    post=_observe(handle,guest_deadline,directory)
    accepted.validate_handle(handle,post)
    _persist_files(directory,{'post-admission.json':_encode(post)},deadline=guest_deadline)
    return result


# Fixed admitted command. Receiving authority comes only from host-generated
# qualified request; no shell, candidate command or incomplete harness import.
_RECEIVER = r'''import hashlib,json,os,stat,sys,types
root,pins,path,sha,size,mode,allocation=sys.argv[1:];pins=json.loads(pins);allocation=json.loads(allocation)
if set(pins)!={'wsl_environment.py','wsl_environment_guest.py','docker_transport.py','development_image_migration.py','qualification_image_archive.py'}:raise ValueError('accepted helper closure differs')
parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for part in root.split('/')[1:]:
  child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);s=os.fstat(child)
  if s.st_uid or s.st_gid:raise ValueError('helper directory owner differs')
  os.close(parent);parent=child
 sources={}
 for name,pin in pins.items():
  fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
  try:
   first=os.fstat(fd)
   if not stat.S_ISREG(first.st_mode) or first.st_nlink!=1 or first.st_uid or first.st_gid or first.st_size!=pin['bytes']:raise ValueError('helper identity differs')
   h=hashlib.sha256();n=0;raw=bytearray()
   while b:=os.read(fd,1048576):
    h.update(b);n+=len(b);raw.extend(b)
    if n>1048576:raise ValueError('helper exceeds code bound')
   last=os.fstat(fd)
   if n!=pin['bytes'] or h.hexdigest()!=pin['sha256'] or (first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns,first.st_ctime_ns)!=(last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns,last.st_ctime_ns):raise ValueError('accepted helper changed')
   sources[name]=bytes(raw)
   if sum(map(len,sources.values()))>2097152:raise ValueError('helper aggregate bound')
  finally:os.close(fd)
finally:os.close(parent)
for name in ('qualification_image_archive.py','docker_transport.py','wsl_environment_guest.py'):
 m=types.ModuleType(name[:-3]);m.__file__=root+'/'+name;sys.modules[name[:-3]]=m
 exec(compile(sources[name],m.__file__,'exec'),m.__dict__)
from wsl_environment_guest import receive_file
# The accepted receiver remains byte-identical. Its actual descriptor-relative
# opens are additionally constrained to the observed role root and backing store.
real_open=os.open;positions={};target=allocation['target']
def bound_open(name,flags,*args,**kw):
 parent=kw.get('dir_fd');position=name if parent is None else positions.get(parent,'').rstrip('/')+'/'+name
 if flags&os.O_CREAT:
  s=os.fstat(parent);space=os.fstatvfs(parent)
  if s.st_dev!=target['device'] or space.f_bavail*space.f_frsize<allocation['requiredAvailable']:raise ValueError('actual receiver target device/capacity differs')
 fd=real_open(name,flags,*args,**kw)
 try:
  s=os.fstat(fd)
  if stat.S_ISDIR(s.st_mode):
   position=position.rstrip('/') or '/';positions[fd]=position
   if position==target['directory'] and (s.st_dev,s.st_ino)!=(target['device'],target['inode']):raise ValueError('receiver role root replaced')
   if position.startswith(target['directory']+'/') and s.st_dev!=target['device']:raise ValueError('receiver child filesystem differs')
  return fd
 except BaseException:os.close(fd);raise
os.open=bound_open
print(json.dumps(receive_file(path,sha,int(size),int(mode),sys.stdin.buffer)))
'''



def _guest_context(request):
    nonce=_hex(request['nonce'],32)
    for key in ('s2Generation','generation'):
        if type(request[key]) is not str or re.fullmatch('[a-z0-9][a-z0-9-]{0,47}',request[key]) is None:raise ValueError('invalid generation')
    if type(request['duration']) is not int or request['duration']<=0:raise ValueError('positive duration required')
    base='/opt/'+request['s2Generation']+'/s3-admission-observations/'+request['generation']
    return base+'/'+nonce


def _guest_load_helpers(request):
    import types
    root='/root/'+request['s2Generation']+'-code'
    if request['codeRoot']!=root:raise ValueError('accepted helper root differs')
    names={'wsl_environment.py','wsl_environment_guest.py','docker_transport.py','development_image_migration.py','qualification_image_archive.py'}
    if set(request['helpers'])!=names:raise ValueError('accepted helper closure differs')
    sources={}
    for name in sorted(names):
        pin=request['helpers'][name];raw=_safe_read(root+'/'+name,HASH_CHUNK)
        if len(raw)!=pin['bytes'] or hashlib.sha256(raw).hexdigest()!=pin['sha256']:raise ValueError('accepted helper substitution')
        sources[name]=raw
        if sum(map(len,sources.values()))>2*HASH_CHUNK:raise ValueError('accepted helper code exceeds aggregate buffer budget')
    # Only these two helpers are imported for receive/admission. Their import of
    # qualification_image_archive is already pinned and populated from bytes.
    for name in ('qualification_image_archive.py','docker_transport.py','wsl_environment_guest.py'):
        module=types.ModuleType(name[:-3]);module.__file__=root+'/'+name;sys.modules[name[:-3]]=module
        exec(compile(sources[name],module.__file__,'exec'),module.__dict__)


def _context_identity(request):
    return {key:request[key] for key in ('specRef','environment','acceptance','generation','s2Generation','adapter','manifestPin')}



def _latch(request,error,deadline):
    base='/opt/'+request['s2Generation']+'/s3-admission-observations/'+request['generation']
    def write():
        os.close(_safe_dir(base))
        path=Path(base)/'generation-failed.json'
        if not path.exists():_exclusive_bytes(path,_encode({'nonce':request['nonce'],'context':_context_identity(request),'error':str(error)[:4096]}))
        return None
    _fork_job(write,deadline=deadline,evidence_path=Path(_guest_context(request))/('latch-worker-'+uuid.uuid4().hex+'.json'))


def _target_space(path):
    """Observe the actual nofollow allocation directory, or nearest existing one."""
    parts=PurePosixPath(path)
    if not parts.is_absolute() or parts.as_posix()!=str(path) or '..' in parts.parts:raise ValueError('canonical allocation target required')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);resolved=''
    try:
        for name in parts.parts[1:]:
            try:child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            except FileNotFoundError:break
            info=os.fstat(child)
            if info.st_uid or info.st_gid:os.close(child);raise ValueError('allocation directory owner differs')
            os.close(fd);fd=child;resolved+='/'+name
        info=os.fstat(fd);space=os.fstatvfs(fd)
        import ctypes
        raw=ctypes.create_string_buffer(256)
        if ctypes.CDLL(None,use_errno=True).fstatfs(fd,raw)!=0:raise OSError(ctypes.get_errno(),'fstatfs failed')
        return {'available':space.f_bavail*space.f_frsize,'device':info.st_dev,'filesystemType':ctypes.c_long.from_buffer(raw).value,
            'directory':resolved or '/','inode':info.st_ino}
    finally:os.close(fd)


def _guest_capacity(request,base,retained,deadline,role='handoff'):
    directory=_guest_context(request)
    actual=_payload_command(request,{'action':'capacity','role':role},deadline,directory)
    if actual['available']<request['requiredBytes']-actual['retained']:
        raise ValueError('insufficient actual admitted bavail')
    def management_space():
        return _target_space('/var/lib/'+request['s2Generation']+'/s3/'+request['generation']+'/staging/'+role)
    management=_fork_job(management_space,deadline=deadline,evidence_path=Path(directory)/('spool-capacity-'+uuid.uuid4().hex+'.json'))
    if (management['device'],management['filesystemType'])!=(actual['device'],actual['filesystemType']) or management['available']<request['requiredBytes']-actual['retained']:
        raise ValueError('management spool filesystem/capacity differs from authoritative destination')
    return {**actual,'managementSpool':management}


def _guest_install(request,deadline,directory):
    handle=request['handle'];_guest_load_helpers(request)
    _admit(handle,deadline,Path(directory))
    session={'context':_context_identity(request),'deadline':deadline,'request':request}
    _persist_files(directory,{'session.json':_encode(session)},deadline=deadline)
    _guest_capacity(request,'/var/lib/'+request['s2Generation'],0,deadline,request['role'])
    return {'status':'admission-succeeded','nonce':request['nonce'],'deadline':deadline}


def _read_session(request,directory):
    session=_decode(_safe_read(str(Path(directory)/'session.json')))
    if session['context']!=_context_identity(request) or session['request']!=request:raise ValueError('admission context changed')
    _remaining(session['deadline']);return session['deadline']


def _guest_receive(request,member,directory):
    deadline=_read_session(request,directory);_guest_load_helpers(request)
    # One new exact observation/cgroup/observation sequence for this interpreter
    # precedes any payload generation creation or incoming pipe staging.
    evidence=Path(directory)/('receive-'+uuid.uuid4().hex);os.close(_safe_dir(str(evidence),exclusive=True))
    _admit(request['handle'],deadline,evidence)
    role=member['role']
    if role not in ('handoff','harness'):raise ValueError('fixed input role required')
    allowed=request['members'][role]
    if member['name'] not in allowed or member['pin']!=allowed[member['name']]:raise ValueError('input member authority differs')
    _guest_capacity(request,None,0,deadline,role)
    prepared=_payload_command(request,{'action':'prepare','role':role,'first':member['first']},deadline,directory)
    base=prepared['base']
    def allocate_spool():
        spool_root=base+'/staging'
        os.close(_safe_dir(spool_root))
        context_path=Path(spool_root)/'context.json'
        if context_path.exists():
            if _decode(_safe_read(str(context_path)))!=_context_identity(request):raise ValueError('management spool context differs')
        else:_exclusive_bytes(context_path,_encode(_context_identity(request)))
        if member['first']:os.close(_safe_dir(spool_root+'/'+role,exclusive=True))
        else:os.close(_safe_dir(spool_root+'/'+role,create=False))
        return None
    _fork_job(allocate_spool,deadline=deadline,evidence_path=evidence/('spool-allocation-'+uuid.uuid4().hex+'.json'))
    capacity=_guest_capacity(request,base,prepared['retained'],deadline,role)
    pin=member['pin'];ordinal=member['ordinal']
    stage=Path(base)/'staging'/role/('member-'+str(ordinal)+'.partial')
    record={'schemaVersion':1,'operationNonce':request['nonce'],'action':'receive','status':'started','child':None,
        'bytes':0,'sha256':None,'sealSha256':None,'error':None,'possibleSurvivors':False,'observations':[]}
    try:
        spool=stage_input(sys.stdin.buffer,stage,pin['sha256'],pin['bytes'],guest_deadline=deadline,filesystem=capacity['managementSpool'],required_available=request['requiredBytes']-capacity['retained'])
        destination=base+'/inputs/'+role+'/'+member['name']
        child_dir=evidence/'payload';os.close(_safe_dir(str(child_dir),exclusive=True))
        argv=['/usr/bin/python3','-I','-B','-c',_RECEIVER,request['codeRoot'],json.dumps(request['helpers']),destination,pin['sha256'],str(pin['bytes']),str(pin['mode']),json.dumps({'target':capacity['targets']['destination'],'requiredAvailable':request['requiredBytes']-capacity['retained']})]
        with spool.open('rb') as incoming:
            result=stream_admitted(request['handle'],argv,input_file=incoming,output_policy='RECEIVER_CONTROL',guest_deadline=deadline,operation_directory=child_dir)
        expected={'path':destination,'sha256':pin['sha256'],'bytes':pin['bytes'],'mode':pin['mode']}
        if _decode(result['stdout'])!=expected:raise ValueError('receiver completion differs')
        def observation_refs():
            refs=[]
            for path in (evidence/'admission.json',child_dir/'admission.json',child_dir/'post-admission.json'):
                raw=_safe_read(str(path));refs.append({'path':str(path),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
            return refs
        refs=_fork_job(observation_refs,deadline=deadline,evidence_path=evidence/('observation-refs-'+uuid.uuid4().hex+'.json'))
        record.update(status='succeeded',child=result['child'],bytes=pin['bytes'],sha256=pin['sha256'],observations=refs)
        validate_operation_record(record)
        _payload_command(request,{'action':'record','role':role,'ordinal':ordinal,'name':member['name'],'record':record},deadline,directory)
        return record
    except BaseException as error:
        record.update(status='failed',child=getattr(error,'facts',{}).get('child'),error=_error_text(error),possibleSurvivors=bool(getattr(error,'possible_survivors',False)))
        validate_operation_record(record)
        end=_shared_end(deadline,True)
        try:_persist_files(evidence,{'failed.json':_encode(record),'failure-facts.json':_encode(getattr(error,'facts',{}))},deadline=end)
        except BaseException as persistence:error=_failure(error,persistenceErrors=[_error_text(persistence)])
        try:_latch(request,error,end)
        except BaseException as persistence:error=_failure(error,persistenceErrors=[_error_text(persistence)])
        raise error


_FINALIZE = "import hashlib,json,os,stat,sys\nbase,files,dirs=sys.argv[1:];files=json.loads(files);dirs=json.loads(dirs)\ndef stamp(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)\ndef identity(s):return (s.st_dev,s.st_ino,stat.S_IFMT(s.st_mode))\nheld=[];links=[];handles={};file_handles={};actual={}\ndef directory(parent,name):\n fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)\n held.append(fd);s=os.fstat(fd)\n if s.st_uid or s.st_gid:raise ValueError('directory owner differs')\n links.append((parent,name,fd));return fd\ndef verify_links():\n for parent,name,fd in links:\n  if identity(os.stat(name,dir_fd=parent,follow_symlinks=False))!=identity(os.fstat(fd)):raise ValueError('directory replaced')\ndef scan(fd,prefix=''):\n expected={n.split('/')[0] for n in files if n.startswith(prefix) for n in [n[len(prefix):]]}\n expected|={n[len(prefix):].split('/')[0] for n in dirs if n.startswith(prefix)}\n if set(os.listdir(fd))!=expected:raise ValueError('completed input closure differs')\n for name in sorted(expected):\n  path=prefix+name;s=os.stat(name,dir_fd=fd,follow_symlinks=False)\n  if path in dirs:\n   child=directory(fd,name);handles[path]=child;scan(child,path+'/')\n  else:\n   if path not in files or not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_uid or s.st_gid:raise ValueError('unsafe file member')\n   f=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd);held.append(f);first=os.fstat(f)\n   if stamp(s)!=stamp(first):raise ValueError('member replaced before open')\n   h=hashlib.sha256();count=0\n   while b:=os.read(f,1048576):h.update(b);count+=len(b)\n   if stamp(first)!=stamp(os.fstat(f)) or stamp(first)!=stamp(os.stat(name,dir_fd=fd,follow_symlinks=False)):raise ValueError('member replaced during hash')\n   actual[path]={'bytes':count,'sha256':h.hexdigest(),'mode':stat.S_IMODE(first.st_mode)}\n   file_handles[path]=(fd,name,f,stamp(first))\n verify_links()\ndef recheck():\n verify_links()\n for path,(parent,name,fd,first) in file_handles.items():\n  if first!=stamp(os.fstat(fd)) or first!=stamp(os.stat(name,dir_fd=parent,follow_symlinks=False)):raise ValueError('completed member replaced')\n for path,fd in handles.items():\n  prefix=path+'/' if path else ''\n  expected={n[len(prefix):].split('/')[0] for n in list(files)+dirs if n.startswith(prefix)}\n  if set(os.listdir(fd))!=expected:raise ValueError('completed member set changed')\ntry:\n if not base.startswith('/') or any(v in ('','.','..') for v in base.split('/')[1:]):raise ValueError('canonical root required')\n current=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);held.append(current)\n for part in base.split('/')[1:]:current=directory(current,part)\n handles['']=current;scan(current)\n if actual!=files or set(handles)-{''}!=set(dirs):raise ValueError('completed input closure differs')\n recheck()\n for path in sorted(handles,key=lambda n:(n.count('/'),len(n),n),reverse=True):\n  recheck();os.fchmod(handles[path],0o555);os.fsync(handles[path])\n recheck()\n if any(stat.S_IMODE(os.fstat(fd).st_mode)!=0o555 for fd in handles.values()):raise ValueError('completed directory mode differs')\n print(json.dumps({'status':'input-closure-complete','files':len(actual),'directories':len(dirs)}))\nfinally:\n for fd in reversed(held):os.close(fd)\n"


def _guest_finalize(request,role,directory):
    deadline=_read_session(request,directory);_guest_load_helpers(request)
    base='/var/lib/'+request['s2Generation']+'/s3/'+request['generation']
    evidence=Path(directory)/('finalize-'+uuid.uuid4().hex);os.close(_safe_dir(str(evidence),exclusive=True))
    dirs=request['directories'] if role=='harness' else []
    argv=['/usr/bin/python3','-I','-B','-c',_FINALIZE,base+'/inputs/'+role,json.dumps(request['members'][role]),json.dumps(dirs)]
    result=stream_admitted(request['handle'],argv,input_file=None,output_policy='COMPLETION_CONTROL',guest_deadline=deadline,operation_directory=evidence)
    expected={'status':'input-closure-complete','files':len(request['members'][role]),'directories':len(dirs)}
    if _decode(result['stdout'])!=expected:raise ValueError('aggregate completion changed')
    _payload_command(request,{'action':'complete','role':role},deadline,directory)
    return expected

# Inline installer is deliberately independent of the untransferred guest harness.
# Its dedicated process group is verified before the installer receives an ack;
# the installed adapter has its own independently enforced nested work deadline.
_BOOTSTRAP_BODY = "import hashlib,stat\nr=json.loads(sys.argv[1]);base='/opt/'+r['s2Generation']+'/s3-admission-observations/'+r['generation'];directory=base+'/'+r['nonce']\ndef mkdir(path,exclusive=False):\n fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n try:\n  parts=path.split('/')[1:]\n  for i,name in enumerate(parts):\n   if not name or name in ('.','..'):raise ValueError('unsafe evidence path')\n   try:os.mkdir(name,0o700,dir_fd=fd)\n   except FileExistsError:\n    if exclusive and i==len(parts)-1:raise\n   n=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);s=os.fstat(n)\n   if s.st_uid or s.st_gid:raise ValueError('evidence owner differs')\n   os.close(fd);fd=n\n  return fd\n except BaseException:os.close(fd);raise\nparent=mkdir(base)\ntry:\n if 'generation-failed.json' in os.listdir(parent):raise ValueError('failed generation cannot retry')\n # An incomplete earlier public operation is a generation latch as well.\n for name in os.listdir(parent):\n  if name!=r['nonce'] and len(name)==32 and all(c in '0123456789abcdef' for c in name):\n   prior=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)\n   try:\n    if 'public-complete.json' not in os.listdir(prior):raise ValueError('unfinished generation cannot retry')\n    completed=os.open('public-complete.json',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=prior)\n    try:\n     meta=os.fstat(completed)\n     if not stat.S_ISREG(meta.st_mode) or meta.st_nlink!=1 or meta.st_uid or meta.st_gid or meta.st_size>65536:raise ValueError('unsafe provisional history')\n     prior_record=json.loads(os.read(completed,65537))\n    finally:os.close(completed)\n    eligible=[entry for entry in r.get('hostHistory',[]) if entry['operationNonce']==name]\n    if len(eligible)!=1:raise ValueError('prior host success was not observed by this live host owner')\n    witness=eligible[0]\n    expected={'status':'provisional','context':witness['context'],'role':witness['role'],'nonce':name,'hostReceipt':witness['preparedReceipt']}\n    if prior_record!=expected:raise ValueError('provisional guest and final host history differ')\n   finally:os.close(prior)\nfinally:os.close(parent)\nroot=mkdir(directory)\ndef write(name,data):\n fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=root)\n try:\n  v=memoryview(data)\n  while v:\n   n=os.write(fd,v)\n   if n<=0:raise OSError('zero write')\n   v=v[n:]\n  os.fsync(fd)\n finally:os.close(fd)\nfd=os.open('adapter.py',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o400,dir_fd=root)\nh=hashlib.sha256();n=0\ntry:\n while True:\n  b=sys.stdin.buffer.read(min(1048576,r['adapter']['bytes']-n+1))\n  if not b:break\n  n+=len(b)\n  if n>r['adapter']['bytes']:raise ValueError('adapter too long')\n  h.update(b);v=memoryview(b)\n  while v:\n   k=os.write(fd,v)\n   if k<=0:raise OSError('adapter write stalled')\n   v=v[k:]\n if n!=r['adapter']['bytes'] or h.hexdigest()!=r['adapter']['sha256']:raise ValueError('adapter substitution')\n os.fsync(fd);first=os.fstat(fd)\nfinally:os.close(fd)\nfd=os.open('adapter.py',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=root)\ntry:\n s=os.fstat(fd);code=b''\n while b:=os.read(fd,1048576):code+=b\n if (s.st_dev,s.st_ino,s.st_size)!=(first.st_dev,first.st_ino,first.st_size) or hashlib.sha256(code).hexdigest()!=r['adapter']['sha256']:raise ValueError('installed adapter changed')\nfinally:os.close(fd);os.close(root)\nnamespace={'__name__':'p01_frozen_adapter','__file__':directory+'/adapter.py'}\nexec(compile(code,namespace['__file__'],'exec'),namespace)\nresult=namespace['_guest_install'](r,deadline,directory)\nos.write(1,(json.dumps(result)+'\\n').encode())\n"


_ENTRY = r'''import hashlib,json,os,stat,sys
r=json.loads(sys.argv[1]);action=sys.argv[2];argument=json.loads(sys.argv[3])
directory='/opt/'+r['s2Generation']+'/s3-admission-observations/'+r['generation']+'/'+r['nonce']
fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 for name in directory.split('/')[1:]:
  n=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);s=os.fstat(n)
  if s.st_uid or s.st_gid:raise ValueError('adapter parent ownership changed')
  os.close(fd);fd=n
 source=os.open('adapter.py',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)
 try:
  first=os.fstat(source)
  if not stat.S_ISREG(first.st_mode) or first.st_nlink!=1 or first.st_uid or first.st_gid or first.st_size!=r['adapter']['bytes']:raise ValueError('adapter identity differs')
  code=b''
  while b:=os.read(source,1048576):
   code+=b
   if len(code)>1048576:raise ValueError('adapter exceeds bound')
  if hashlib.sha256(code).hexdigest()!=r['adapter']['sha256'] or (os.fstat(source).st_dev,os.fstat(source).st_ino,os.fstat(source).st_size,os.fstat(source).st_mtime_ns,os.fstat(source).st_ctime_ns)!=(first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns,first.st_ctime_ns):raise ValueError('adapter bytes changed')
 finally:os.close(source)
finally:os.close(fd)
namespace={'__name__':'p01_frozen_adapter','__file__':directory+'/adapter.py'}
exec(compile(code,namespace['__file__'],'exec'),namespace)
try:
 if action=='filesystem':result=namespace['_guest_filesystem'](r,argument,directory)
 elif action=='receive':result=namespace['_guest_receive'](r,argument,directory)
 elif action=='finalize':result=namespace['_guest_finalize'](r,argument,directory)
 elif action=='complete':
  deadline=namespace['_read_session'](r,directory)
  namespace['_persist_files'](directory,{'public-complete.json':namespace['_encode']({'status':'provisional','context':namespace['_context_identity'](r),'role':r['role'],'nonce':r['nonce'],'hostReceipt':argument})},deadline=deadline)
  result={'status':'succeeded','nonce':r['nonce']}
 else:raise ValueError('unknown fixed action')
 os.write(1,namespace['_encode'](result))
except BaseException as error:
 try:os.write(2,b'P01_TRANSFER_FAILURE: '+namespace['_encode']({'error':namespace['_error_text'](error),**getattr(error,'facts',{})}))
 except BaseException:pass
 try:namespace['_latch'](r,error,namespace['_shared_end'](deadline,True))
 except BaseException:pass
 raise
'''


# Live history adds only rejection; every call still qualifies and admits afresh.
import threading
_HOST_PROCESS=os.getpid();_HOST_INSTANCE=object();_HOST_HISTORY={};_HOST_GATE=threading.RLock()
_LAST_HOST_WITNESS=None

def _discard_host_history():
    global _HOST_PROCESS,_HOST_INSTANCE,_HOST_GATE
    _HOST_HISTORY.clear();_HOST_PROCESS=os.getpid();_HOST_INSTANCE=object();_HOST_GATE=threading.RLock()
if hasattr(os,'register_at_fork'):os.register_at_fork(after_in_child=_discard_host_history)

@contextmanager
def _host_public_guard():
    if os.getpid()!=_HOST_PROCESS:_discard_host_history()
    if not _HOST_GATE.acquire(blocking=False):raise ValueError('concurrent INPUT calls are rejected')
    try:
        try:yield
        except BaseException:_HOST_HISTORY.clear();raise
    finally:
        try:_HOST_GATE.release()
        except BaseException:_HOST_HISTORY.clear();raise

def _host_context(context):
    q=context['qualification']
    return {'specRef':q['specRef'],'environment':q['environment'],'acceptance':q['s2Acceptance'],
        'generation':context['spec']['generation'],'s2Generation':q['s2Generation'],'adapter':context['adapter'],'manifestPin':context['manifestPin']}

def _history_key(context):return hashlib.sha256(_encode(_host_context(context))).hexdigest()

def _observed_host_history(context):
    return [entry['evidence'] for entry in _HOST_HISTORY.get(_history_key(context),[]) if entry['process']==os.getpid() and entry['instance'] is _HOST_INSTANCE]

def _publish_host_witness(context,witness):
    key=_history_key(context)
    if witness['context']!=_host_context(context):raise ValueError('observed host witness context differs')
    entries=_HOST_HISTORY.setdefault(key,[])
    if any(entry['evidence']['role']==witness['role'] for entry in entries):raise ValueError('completed role cannot retry')
    entries.append({'process':os.getpid(),'instance':_HOST_INSTANCE,'evidence':witness})
    if len(_HOST_HISTORY)>64:
        for old in list(_HOST_HISTORY):
            if old!=key:del _HOST_HISTORY[old];break

def _host_ref(output,name,deadline):
    import source_build
    path=Path(output)/name
    with source_build._open(path) as incoming:
        first=_stamp(os.fstat(incoming.fileno()));named=_stamp(os.stat(path,follow_symlinks=False))
        if (first[0],first[1],first[2],first[5],first[6])!=(named[0],named[1],named[2],named[5],named[6]):raise ValueError('host evidence path identity differs')
        pin=_hash(incoming)
        if _stamp(os.fstat(incoming.fileno()))!=first or _stamp(os.stat(path,follow_symlinks=False))!=named:raise ValueError('host evidence changed during hash')
    return {'path':str(path),**pin}

def _read_host_ref(ref,deadline):
    if type(ref) is not dict or set(ref)!= {'path','bytes','sha256'} or ref['bytes']>CONTROL_CAP:raise ValueError('host evidence ref differs')
    with _pinned_source(ref['path'],ref['sha256'],ref['bytes'],deadline=deadline) as incoming:return _decode(incoming.read(CONTROL_CAP+1))

def _verify_host_history(context,deadline):
    import source_build
    verified=[]
    for entry in context.get('_hostHistory',[]):
        if entry['context']!=_host_context(context):raise ValueError('predecessor host context differs')
        output=Path(entry['receipt']['path']).parent
        for terminal in ('failed.json','ambiguous.json'):
            try:
                with source_build._open(output/terminal):raise ValueError('predecessor terminal failure retained')
            except FileNotFoundError:pass
        prepared=_read_host_ref(entry['preparedReceipt'],deadline)
        receipt=_read_host_ref(entry['receipt'],deadline);ack=_read_host_ref(entry['acknowledgement'],deadline)
        closure=_read_host_ref(entry['closure'],deadline);index=_read_host_ref(entry['index'],deadline)
        expected={key:entry[key] for key in ('context','role','operationNonce','preparedReceipt','receipt','acknowledgement','closure')}
        if (prepared!=receipt or entry['preparedReceipt']['path']==entry['receipt']['path'] or index!=expected or receipt.get('status')!='input-transfer-complete' or receipt.get('role')!=entry['role']
                or receipt.get('context')!=entry['context'] or receipt.get('operationNonce')!=entry['operationNonce']
                or ack!={'status':'succeeded','nonce':entry['operationNonce']} or receipt.get('closure')!=closure):raise ValueError('predecessor durable completion differs')
        verified.append(entry)
    return verified


def _new_host_operation(context,output_directory,deadline):
    import source_build
    import wsl_environment
    # Reuse Task1's private protected-input metadata without changing its APIs.
    ref=context['qualification']['specRef']
    fresh,protected=source_build._qualify_source_build(Path(ref['path']),ref['sha256'])
    if not source_build._same(fresh,context['qualification']):raise ValueError('qualified proof changed')
    output=source_build._path(output_directory);source_build._protect_freeze_output(output,protected)
    ready=wsl_environment._read_ready(Path(context['qualification']['environment']['path']))
    if ready['generation']!=fresh['s2Generation'] or ready['distro']!=fresh['s2Distro']:raise ValueError('ready context differs')
    required=(context['memberCount']+8)*(STDERR_CAP+8*CONTROL_CAP)+context['spec']['limits']['hostReserveBytes']
    if shutil.disk_usage(output.parent).free<required:raise ValueError('insufficient actual host input evidence capacity')
    output.mkdir()
    nonce=uuid.uuid4().hex
    request={'nonce':nonce,'role':context['role'],'duration':context['timeout'],'s2Generation':fresh['s2Generation'],
        'generation':context['spec']['generation'],'specRef':fresh['specRef'],'environment':fresh['environment'],
        'acceptance':fresh['s2Acceptance'],'adapter':context['adapter'],'manifestPin':context['manifestPin'],
        'handle':ready['handle'],'helpers':ready['helpers']['files'],'codeRoot':ready['guestCodeRoot'],
        'requiredBytes':_capacity_required(context),'directories':list(context['manifest']['directories']),
        'members':{'handoff':{'handoff.tar':{'bytes':context['spec']['handoff']['bytes'],
            'sha256':context['spec']['handoff']['sha256'],'mode':0o444}},
            'harness':{name:{'bytes':entry['bytes'],'sha256':entry['sha256'],'mode':entry['transferMode']}
                for name,entry in context['manifest']['files'].items()}}}
    request['members']['harness']['manifest.json']={**context['manifestPin'],'mode':0o444}
    request['hostHistory']=_verify_host_history(context,deadline)
    _encode(request)
    prefix=[ready['wslExecutable'],'-d',ready['distro'],'--user','root','--exec','/usr/bin/python3','-I','-B']
    bootstrap=[*prefix,'-c',_BOOTSTRAP,json.dumps(request,separators=(',',':'))]
    if os.name=='nt' and len(subprocess.list2cmdline(bootstrap))>=32760:raise ValueError('frozen WSL argv exceeds native command bound')
    _host_write(output/'request.json',_encode(request),deadline)
    _host_write(output/'bootstrap.py',_BOOTSTRAP.encode(),deadline)
    _host_write(output/'entry.py',_ENTRY.encode(),deadline)
    _host_write(output/'bootstrap.argv.json',_encode(bootstrap),deadline)
    with _pinned_source(Path(__file__).absolute(),context['adapter']['sha256'],context['adapter']['bytes'],deadline=deadline) as adapter:
        _pump_host([sys.executable,'-I','-B','-c',_HOST_WRITER,str(output/'adapter.py')],input_file=adapter,deadline=deadline)
    return output,request,prefix,bootstrap


def _host_call(output,key,argv,incoming,deadline):
    _host_write(output/(key+'.argv.json'),_encode(argv),deadline)
    try:result=_pump_host(argv,input_file=incoming,deadline=deadline)
    except BaseException as error:
        # Preserve independently bounded partial channels through a cancellable
        # writer. Failure here remains failure, even when persistence also fails.
        for name,data in (('stdout',getattr(error,'partial_stdout',b'')),('stderr',getattr(error,'partial_stderr',b''))):
            try:_host_write(output/(key+'.'+name+'.log'),data,_host_cancel_end(deadline,start=True))
            except BaseException:pass
        raise
    for name in ('stdout','stderr'):_host_write(output/(key+'.'+name+'.log'),result[name],deadline)
    return _decode(result['stdout'])


def _run_transfer_inner(context,role,members,output_directory,*,deadline=None):
    if deadline is None:deadline=time.monotonic()+context['timeout']
    context['role']=role
    sources=ExitStack()
    incoming_sources={}
    try:
        for name,member in members.items():
            incoming_sources[name]=sources.enter_context(_pinned_source(Path(member['path']),member['sha256'],member['bytes'],deadline=deadline))
        output,request,prefix,bootstrap=_new_host_operation(context,output_directory,deadline)
    except BaseException:
        sources.close();raise

    result={'schemaVersion':1,'status':'started','role':role,'context':_context_identity(request),
        'operationNonce':request['nonce'],'resourcesPreserved':True,'guestStarted':False,'members':[]}
    try:
        _host_write(output/'started.json',_encode(result),deadline)
        with _pinned_source(output/'adapter.py',context['adapter']['sha256'],context['adapter']['bytes'],deadline=deadline) as adapter:
            admitted=_host_call(output,'admission',bootstrap,adapter,deadline)
        if admitted.get('status')!='admission-succeeded' or admitted.get('nonce')!=request['nonce']:raise ValueError('admission completion differs')
        result['guestStarted']=True
        for ordinal,name in enumerate(sorted(members)):
            member=members[name]
            argument={'role':role,'name':name,'pin':request['members'][role][name],'ordinal':ordinal,'first':ordinal==0}
            argv=[*prefix,'-c',_ENTRY,json.dumps(request,separators=(',',':')),'receive',json.dumps(argument,separators=(',',':')),str(max(1,math.ceil(_remaining(deadline))))]
            received=_host_call(output,'member-'+str(ordinal),argv,incoming_sources[name],deadline)
            validate_operation_record(received)
            if (received['status']!='succeeded' or received['operationNonce']!=request['nonce']
                    or received['bytes']!=member['bytes'] or received['sha256']!=member['sha256']):raise ValueError('input completion changed')
            result['members'].append({'name':name,'bytes':member['bytes'],'sha256':member['sha256']})
        sources.close()
        finalize=[*prefix,'-c',_ENTRY,json.dumps(request,separators=(',',':')),'finalize',json.dumps(role),str(max(1,math.ceil(_remaining(deadline))))]
        result['closure']=_host_call(output,'finalize',finalize,None,deadline)
        result['status']='input-transfer-complete'
        _host_write(output/'prepared-result.json',_encode(result),deadline)
        prepared=_host_ref(output,'prepared-result.json',deadline)
        complete=[*prefix,'-c',_ENTRY,json.dumps(request,separators=(',',':')),'complete',json.dumps(prepared,separators=(',',':')),str(max(1,math.ceil(_remaining(deadline))))]
        if _host_call(output,'complete',complete,None,deadline)!={'status':'succeeded','nonce':request['nonce']}:
            raise ValueError('public completion changed')
        _host_write(output/'result.json',_encode(result),deadline)
        receipt=_host_ref(output,'result.json',deadline)
        witness={'context':_context_identity(request),'role':role,'operationNonce':request['nonce'],'preparedReceipt':prepared,'receipt':receipt,
            'acknowledgement':_host_ref(output,'complete.stdout.log',deadline),'closure':_host_ref(output,'finalize.stdout.log',deadline)}
        _host_write(output/'eligibility.json',_encode(witness),deadline)
        witness['index']=_host_ref(output,'eligibility.json',deadline)
        global _LAST_HOST_WITNESS
        _LAST_HOST_WITNESS=witness
        return result
    except BaseException as error:
        try:sources.close()
        except BaseException as recheck_error:error=_failure(error,persistenceErrors=['source recheck: '+_error_text(recheck_error)])
        result.update(status='failed',error=_error_text(error),possibleSurvivors=bool(getattr(error,'possible_survivors',False)),failureFacts=getattr(error,'facts',{}))
        try:_host_write(output/'failed.json',_encode(result),_host_cancel_end(deadline,start=True))
        except BaseException:pass
        try:_host_write(output/'result.json',_encode(result),_host_cancel_end(deadline,start=True))
        except BaseException:pass
        raise


_HOST_TRANSACTION = r'''import hashlib,json,os,stat,sys
payload=sys.stdin.buffer.read(2097153)
if len(payload)>2097152:raise ValueError('host transaction request exceeds cap')
p=json.loads(payload);sys.path.insert(0,p['helperRoot'])
fd=os.open(p['module'],os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0))
try:
 first=os.fstat(fd)
 if not stat.S_ISREG(first.st_mode) or first.st_nlink!=1 or first.st_size!=p['context']['adapter']['bytes']:raise ValueError('worker adapter identity differs')
 code=bytearray()
 while b:=os.read(fd,1048576):
  code.extend(b)
  if len(code)>1048576:raise ValueError('worker adapter exceeds bound')
 last=os.fstat(fd)
 if hashlib.sha256(code).hexdigest()!=p['context']['adapter']['sha256'] or (first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns,first.st_ctime_ns)!=(last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns,last.st_ctime_ns):raise ValueError('worker adapter changed')
finally:os.close(fd)
namespace={'__name__':'p01_host_transaction','__file__':p['module']}
exec(compile(code,p['module'],'exec'),namespace)
namespace['_HOST_TRANSACTION_WORKER']=True
namespace['_HOST_ABSOLUTE_CANCEL_END']=p['deadline']+5
import mmap
namespace['_HOST_CANCEL_MAP']=mmap.mmap(-1,8,tagname=p['cancelMap']) if os.name=='nt' else mmap.mmap(p['cancelMap'],8)
try:
 result=namespace['_run_transfer_inner'](p['context'],p['role'],p['members'],p['output'],deadline=p['deadline'])
 value={'ok':True,'result':result,'witness':namespace.get('_LAST_HOST_WITNESS')}
except BaseException as error:
 value={'ok':False,'error':str(error)[:4096],'facts':getattr(error,'facts',{}),'possibleSurvivors':bool(getattr(error,'possible_survivors',False))}
os.write(1,namespace['_encode'](value))
'''

def _execute_host_transaction(context,role,members,output_directory,deadline):
    helper=sys.modules.get('source_build')
    payload={'module':str(Path(__file__).absolute()),'helperRoot':str(Path(helper.__file__).parent) if helper else str(Path(__file__).parent),
        'context':context,'role':role,'members':members,'output':str(output_directory),'deadline':deadline}
    import mmap,struct
    global _HOST_CANCEL_MAP
    descriptor=None;mapping=None
    try:
        if os.name=='nt':
            payload['cancelMap']='Local\\p01-cancel-'+uuid.uuid4().hex
            mapping=mmap.mmap(-1,8,tagname=payload['cancelMap'])
        else:
            descriptor=os.memfd_create('p01-host-cancel',0);os.ftruncate(descriptor,8)
            payload['cancelMap']=descriptor;mapping=mmap.mmap(descriptor,8)
        mapping[:8]=struct.pack('d',deadline+5);_HOST_CANCEL_MAP=mapping
        result=_pump_host([sys.executable,'-I','-B','-c',_HOST_TRANSACTION],input_file=None,input_bytes=_encode(payload),deadline=deadline,pass_fds=() if descriptor is None else (descriptor,))
        value=_decode(result['stdout'])
    finally:
        # Carry the worker's original cancellation endpoint beyond map lifetime.
        global _HOST_ABSOLUTE_CANCEL_END
        if mapping is not None:
            observed=struct.unpack('d',mapping[:8])[0]
            if _HOST_ABSOLUTE_CANCEL_END is not None:_HOST_ABSOLUTE_CANCEL_END=min(_HOST_ABSOLUTE_CANCEL_END,observed)
            _HOST_CANCEL_MAP=None;mapping.close()
        if descriptor is not None:os.close(descriptor)
    if value.get('ok') is not True:
        facts={**value.get('facts',{}),'possibleSurvivors':bool(value.get('possibleSurvivors',False) or value.get('facts',{}).get('possibleSurvivors'))}
        raise _failure(value.get('error','host transaction failed'),**facts)
    return value

def _run_transfer(context,role,members,output_directory):
    global _HOST_ABSOLUTE_CANCEL_END
    with _host_public_guard():
        deadline=time.monotonic()+context['timeout'];previous_end=_HOST_ABSOLUTE_CANCEL_END
        _HOST_ABSOLUTE_CANCEL_END=deadline+5
        try:
            context={**context,'_hostHistory':_observed_host_history(context)}
            value=_execute_host_transaction(context,role,members,output_directory,deadline)
            result=value['result'];witness=value.get('witness')
            if witness is None:raise ValueError('host successful-return evidence absent')
            if (witness['role']!=role or witness['operationNonce']!=result['operationNonce']
                    or witness['receipt']['sha256']!=hashlib.sha256(_encode(result)).hexdigest()):raise ValueError('observed final host receipt differs')
            _remaining(deadline)
            _publish_host_witness(context,witness)
            return result
        except BaseException as error:
            # The owned worker may have committed final bytes before its reply
            # was lost. A separate terminal record must never imply success.
            try:_host_write(Path(output_directory)/'ambiguous.json',_encode({'status':'ambiguous','error':_error_text(error),'possibleSurvivors':bool(getattr(error,'possible_survivors',False))}),_host_cancel_end(deadline,start=True))
            except BaseException:pass
            raise
        finally:_HOST_ABSOLUTE_CANCEL_END=previous_end


def transfer_environment(environment_receipt,source,destination,sha256,size,mode,*,spec,spec_sha256,output_directory,timeout):
    """Transfer only the original pinned handoff to its qualified fixed role."""
    with _host_public_guard():
        import source_build
        context=_local_context(environment_receipt,spec,spec_sha256,timeout)
        handoff=context['spec']['handoff']
        if (source_build._path(source)!=source_build._path(handoff['path'])
                or destination!=context['base']+'/inputs/handoff/handoff.tar'
                or type(size) is not int or size!=handoff['bytes'] or sha256!=handoff['sha256']
                or type(mode) is not int or mode!=0o444):raise ValueError('fixed original handoff role required')
        context['adapter']=_adapter_pin(context)
        return _run_transfer(context,'handoff',{'handoff.tar':{**handoff,'mode':0o444}},output_directory)


def transfer_harness(environment_receipt,frozen_harness,*,spec,spec_sha256,output_directory,timeout):
    """Own the complete frozen harness's directory/file/finalization lifecycle."""
    with _host_public_guard():
        context=_local_context(environment_receipt,spec,spec_sha256,timeout)
        members=_frozen_harness(context,frozen_harness)
        context['adapter']=_adapter_pin(context)
        return _run_transfer(context,'harness',members,output_directory)


def _payload_filesystem(root,context,members,directories,action,argument,*,capacity_root,eligible_history=None):
    """Trusted filesystem control, executed only by a registered nsenter child.

    The low-level root argument supports isolated controls; the guest dispatcher
    below always derives it from the original qualified generation.
    """
    root=Path(root);role=argument.get('role')
    if role not in ('handoff','harness'):raise ValueError('fixed filesystem role required')
    def json_at(path):return _decode(_safe_read(str(path)))
    def history():
        retained=0
        if not root.exists():return 0
        fd=_safe_dir(str(root),create=False);os.close(fd)
        if json_at(root/'context.json')!=context:raise ValueError('generation context differs')
        ops_fd=_safe_dir(str(root/'operations'),create=False)
        try:siblings=os.listdir(ops_fd)
        finally:os.close(ops_fd)
        if set(siblings)-{'handoff','harness'}:raise ValueError('unexpected operation role')
        for sibling in siblings:
            op=root/'operations'/sibling
            owner=json_at(op/'owner.json')
            if owner.get('context')!=context:raise ValueError('sibling owner context differs')
            if sibling==role:
                if owner.get('nonce')!=argument['nonce']:raise ValueError('role belongs to another operation')
            else:
                completed=json_at(op/'complete.json')
                if completed.get('status')!='succeeded' or completed.get('context')!=context:raise ValueError('failed/incomplete sibling')
                if eligible_history is not None and not any(entry['role']==sibling and entry['operationNonce']==owner['nonce'] and entry['context']==context for entry in eligible_history):raise ValueError('sibling lacks observed successful host history')
            names=sorted(members[sibling]);seen=set()
            op_fd=_safe_dir(str(op),create=False)
            try:operation_names=os.listdir(op_fd)
            finally:os.close(op_fd)
            for name in operation_names:
                if name in ('owner.json','complete.json'):continue
                match=re.fullmatch('member-([0-9]+).json',name)
                if not match:raise ValueError('unexpected operation record')
                ordinal=int(match[1])
                if ordinal>=len(names) or ordinal in seen:raise ValueError('member ordinal differs')
                seen.add(ordinal);record=json_at(op/name);validate_operation_record(record)
                pin=members[sibling][names[ordinal]]
                if (record['status']!='succeeded' or record['bytes']!=pin['bytes'] or record['sha256']!=pin['sha256']
                        or record['operationNonce']!=owner['nonce']):raise ValueError('member record differs')
                retained+=2*record['bytes']
            if sibling!=role and len(seen)!=len(names):raise ValueError('incomplete sibling members')
        return retained
    retained=history()
    if action=='capacity':
        destination=_target_space(str(root/'inputs'/role));spool=_target_space(str(root/'staging'/role))
        if (destination['device'],destination['filesystemType'])!=(spool['device'],spool['filesystemType']):raise ValueError('spool and destination backing filesystems differ')
        return {**destination,'available':min(destination['available'],spool['available']),'retained':retained,'targets':{'destination':destination,'spool':spool}}
    if action=='prepare':
        if not root.exists():
            os.close(_safe_dir(str(root),exclusive=True));_exclusive_bytes(root/'context.json',_encode(context))
            for name in ('inputs','operations'):os.close(_safe_dir(str(root/name),exclusive=True))
        role_dir=root/'operations'/role
        if argument['first']:
            os.close(_safe_dir(str(role_dir),exclusive=True))
            _exclusive_bytes(role_dir/'owner.json',_encode({'context':context,'nonce':argument['nonce']}))
            os.close(_safe_dir(str(root/'inputs'/role),exclusive=True))
            if role=='harness':
                for name in sorted(directories,key=lambda n:(len(PurePosixPath(n).parts),n)):
                    os.close(_safe_dir(str(root/'inputs'/role/name),exclusive=True))
        elif not role_dir.exists():raise ValueError('missing active role')
        return {'base':str(root),'retained':retained}
    role_dir=root/'operations'/role
    if action=='record':
        ordinal=_integer(argument['ordinal']);names=sorted(members[role])
        if ordinal>=len(names) or names[ordinal]!=argument['name']:raise ValueError('record member role differs')
        record=validate_operation_record(argument['record']);pin=members[role][argument['name']]
        if record['status']!='succeeded' or record['bytes']!=pin['bytes'] or record['sha256']!=pin['sha256'] or record['operationNonce']!=argument['nonce']:
            raise ValueError('record does not bind original member')
        _exclusive_bytes(role_dir/('member-'+str(ordinal)+'.json'),_encode(record))
        return {'status':'recorded'}
    if action=='complete':
        op_fd=_safe_dir(str(role_dir),create=False)
        try:completed_members=[name for name in os.listdir(op_fd) if re.fullmatch('member-[0-9]+.json',name)]
        finally:os.close(op_fd)
        if len(completed_members)!=len(members[role]):raise ValueError('missing aggregate member')
        _exclusive_bytes(role_dir/'complete.json',_encode({'status':'succeeded','context':context}))
        return {'status':'succeeded'}
    raise ValueError('unknown closed filesystem action')


def _guest_filesystem(request,argument,directory):
    _read_session(request,directory)
    base='/var/lib/'+request['s2Generation']+'/s3/'+request['generation']
    return _payload_filesystem(base,_context_identity(request),request['members'],request['directories'],
        argument['action'],{**argument,'nonce':request['nonce']},capacity_root='/var/lib/'+request['s2Generation'],eligible_history=request.get('hostHistory',[]))


def _payload_command(request,argument,deadline,directory):
    evidence=Path(directory)/('filesystem-'+uuid.uuid4().hex);os.close(_safe_dir(str(evidence),exclusive=True))
    argv=['/usr/bin/python3','-I','-B','-c',_ENTRY,json.dumps(request,separators=(',',':')),
        'filesystem',json.dumps(argument,separators=(',',':')),str(max(1,math.ceil(_remaining(deadline)))),*map(str,_owner_pass_fds())]
    result=stream_admitted(request['handle'],argv,input_file=None,output_policy='COMPLETION_CONTROL',
        guest_deadline=deadline,operation_directory=evidence)
    return _decode(result['stdout'])

_ENTRY_BODY = _ENTRY
_OWNER_RUNTIME = 'import array,hashlib,json,mmap,os,signal,socket,struct,sys,time\n\ndef _owner_fields(pid):\n with open(\'/proc/\'+str(pid)+\'/stat\') as f:parts=f.read(65537).rsplit(\')\',1)[1].split()\n return {\'pid\':pid,\'starttime\':parts[19],\'pgid\':int(parts[2]),\'sid\':int(parts[3])}\ndef _owner_live(identity):\n try:return _owner_fields(identity[\'pid\'])==identity\n except (FileNotFoundError,ProcessLookupError):return False\ndef _owner_state(identity):\n try:\n  with open(\'/proc/\'+str(identity[\'pid\'])+\'/stat\') as f:state=f.read(65537).rsplit(\')\',1)[1].split()[0]\n  if not _owner_live(identity):return \'identity-lost\'\n  return \'zombie\' if state==\'Z\' else \'live\'\n except (FileNotFoundError,ProcessLookupError):return \'gone\'\ndef _owner_config():\n value=os.environ.get(\'P01_OWNER_CONFIG\')\n return None if value is None else tuple(map(int,value.split(\',\')))\ndef _shared_end(work_deadline,start=False):\n config=_owner_config()\n if config is None:return min(work_deadline+5,time.monotonic()+5) if start else work_deadline+5\n fd=config[1]\n # One aligned shared double; shrinking writes are serialized with bounded flock.\n import fcntl\n while True:\n  try:fcntl.lockf(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break\n  except BlockingIOError:\n   if time.monotonic()>=work_deadline+5:return work_deadline+5\n   time.sleep(.001)\n try:\n  raw=os.pread(fd,8,0);end=struct.unpack(\'d\',raw)[0]\n  if start:\n   end=min(end,time.monotonic()+5,work_deadline+5);os.pwrite(fd,struct.pack(\'d\',end),0)\n  return min(end,work_deadline+5)\n finally:fcntl.lockf(fd,fcntl.LOCK_UN)\ndef _owner_request(action,identity,deadline,registration=None):\n config=_owner_config()\n if config is None:return None\n if time.monotonic()>=deadline:raise TimeoutError(\'owner request deadline\')\n request={\'action\':action,\'child\':identity,\'parent\':_owner_fields(os.getpid()),\'nonce\':os.urandom(16).hex(),\'registration\':registration}\n raw=(json.dumps(request,separators=(\',\',\':\'))+\'\\n\').encode()\n read,write=os.pipe();channel=socket.socket(fileno=os.dup(config[0]));channel.setblocking(False)\n try:\n  while True:\n   try:channel.sendmsg([raw],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array(\'i\',[write]))]);break\n   except BlockingIOError:\n    if time.monotonic()>=deadline:raise TimeoutError(\'owner registration deadline\')\n    time.sleep(.001)\n  os.close(write);write=None;os.set_blocking(read,False)\n  while True:\n   try:\n    reply=os.read(read,1)\n    if reply not in (b\'E\',b\'R\'):raise ValueError(\'owner request rejected\')\n    return request[\'nonce\'] if action==\'register\' else reply==b\'E\'\n   except BlockingIOError:\n    if time.monotonic()>=deadline:raise TimeoutError(\'owner acknowledgement deadline\')\n    time.sleep(.001)\n finally:\n  channel.close();os.close(read)\n  if write is not None:os.close(write)\ndef _register_owner(identity,deadline):return _owner_request(\'register\',identity,deadline)\ndef _retire_owner(identity,registration,deadline):\n if _owner_config() is None:return True\n return _owner_request(\'retire\',identity,deadline,registration)\ndef _owner_pass_fds():\n config=_owner_config();return () if config is None else config\n\ndef _owner_guard(body,request,directory,deadline,*,session=False):\n # Ambient environment never authorizes an owner. Only an internal fixed ENTRY\n # argv carrying both inherited descriptors can reuse the authenticated owner.\n inherited=None\n if session and len(sys.argv)==7:inherited=(int(sys.argv[5]),int(sys.argv[6]))\n if inherited is not None:\n  for fd in inherited:os.fstat(fd)\n  os.environ[\'P01_OWNER_CONFIG\']=\',\'.join(map(str,inherited))\n  exec(compile(body,\'<frozen-owned-entry>\',\'exec\'),globals());return\n os.environ.pop(\'P01_OWNER_CONFIG\',None)\n channel,worker_channel=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)\n channel.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1);channel.setblocking(False)\n cancel=os.memfd_create(\'p01-cancellation\',0);os.ftruncate(cancel,8);os.pwrite(cancel,struct.pack(\'d\',deadline+5),0)\n ready_r,ready_w=os.pipe();ack_r,ack_w=os.pipe();pid=os.fork()\n if pid==0:\n  try:\n   channel.close();os.close(ready_r);os.close(ack_w);os.setsid()\n   config=(worker_channel.fileno(),cancel)\n   for fd in config:os.set_inheritable(fd,True)\n   os.environ[\'P01_OWNER_CONFIG\']=\',\'.join(map(str,config))\n   def interrupted(signum,frame):raise TimeoutError(\'outer owner requested cancellation\')\n   signal.signal(signal.SIGUSR1,interrupted)\n   os.write(ready_w,b\'H\');raw=b\'\'\n   while not raw.endswith(b\'\\n\'):\n    b=os.read(ack_r,4096)\n    if not b:raise ValueError(\'owner disconnected before registration\')\n    raw+=b\n    if len(raw)>65536:raise ValueError(\'registration exceeds cap\')\n   identity=json.loads(raw)\n   # Blocking metadata/durability is done only in this owned child.\n   fd=os.open(\'/\',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)\n   try:\n    parts=directory.split(\'/\')[1:]\n    for ordinal,name in enumerate(parts):\n     if not name or name in (\'.\',\'..\'):raise ValueError(\'unsafe guard directory\')\n     try:os.mkdir(name,0o700,dir_fd=fd)\n     except FileExistsError:\n      if not session and ordinal==len(parts)-1:raise\n     n=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);info=os.fstat(n)\n     if info.st_uid or info.st_gid:raise ValueError(\'guard directory owner differs\')\n     os.close(fd);fd=n\n    out=os.open((\'entry-child-\' if session else \'bootstrap-owner-\')+str(os.getpid())+\'.json\',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)\n    try:\n     view=memoryview(raw)\n     while view:\n      count=os.write(out,view)\n      if count<=0:raise OSError(\'identity persistence stalled\')\n      view=view[count:]\n     os.fsync(out)\n    finally:os.close(out)\n    if session:\n     source=os.open(\'session.json\',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=fd)\n     try:\n      data=b\'\'\n      while b:=os.read(source,65536):\n       data+=b\n       if len(data)>65536:raise ValueError(\'session exceeds cap\')\n      deadline=min(deadline,float(json.loads(data)[\'deadline\']))\n     finally:os.close(source)\n   finally:os.close(fd)\n   os.write(ready_w,(str(deadline)+\'\\n\').encode())\n   if os.read(ack_r,1)!=b\'E\':raise ValueError(\'owner did not acknowledge durable registration\')\n   os.close(ready_w);os.close(ack_r)\n   globals()[\'deadline\']=deadline\n   exec(compile(body,\'<frozen-owned-body>\',\'exec\'),globals());os._exit(0)\n  except BaseException as error:\n   try:\n    facts={\'error\':str(error).encode(\'utf-8\',errors=\'replace\')[:4096].decode(\'utf-8\',errors=\'ignore\'),**getattr(error,\'facts\',{})}\n    raw=(json.dumps(facts,separators=(\',\',\':\'))+\'\\n\').encode()\n    if len(raw)>65500:raw=b\'{"error":"failure facts exceeded cap","possibleSurvivors":true}\\n\'\n    os.write(2,b\'P01_TRANSFER_FAILURE: \'+raw)\n   finally:os._exit(1)\n worker_channel.close();os.close(ready_w);os.close(ack_r);os.set_blocking(ready_r,False)\n identities={};parents={};registrations={};nonces=set();signals=[];signaled=set();supervisor=None;status=None;failure=None\n summary={\'registered\':0,\'retired\':0,\'peakActive\':0,\'historySha256\':\'0\'*64}\n def history(action,value):\n  raw=json.dumps({\'action\':action,**value},sort_keys=True,separators=(\',\',\':\')).encode()\n  summary[\'historySha256\']=hashlib.sha256(bytes.fromhex(summary[\'historySha256\'])+raw).hexdigest()\n def registered(child,parent,nonce):\n  identities[child[\'pid\']]=child;parents[child[\'pid\']]=parent;registrations[child[\'pid\']]=nonce;nonces.add(nonce)\n  summary[\'registered\']+=1;summary[\'peakActive\']=max(summary[\'peakActive\'],len(identities))\n  history(\'register\',{\'child\':child,\'parent\':parent,\'nonce\':nonce})\n def drain():\n  while True:\n   try:raw,ancillary,flags,_=channel.recvmsg(4096,socket.CMSG_SPACE(4)+socket.CMSG_SPACE(12))\n   except BlockingIOError:return\n   if not raw:return\n   replies=[];credentials=None\n   for level,kind,data in ancillary:\n    if level==socket.SOL_SOCKET and kind==socket.SCM_RIGHTS:\n     a=array.array(\'i\');a.frombytes(data[:len(data)//a.itemsize*a.itemsize]);replies.extend(a)\n    if level==socket.SOL_SOCKET and kind==socket.SCM_CREDENTIALS:credentials=struct.unpack(\'3i\',data[:12])\n   try:\n    if flags or len(replies)!=1 or credentials is None:raise ValueError(\'owner registration channel invalid\')\n    value=json.loads(raw);parent=value[\'parent\'];child=value[\'child\'];action=value[\'action\']\n    if credentials[0]!=parent[\'pid\'] or parent[\'pid\'] not in identities or not _owner_live(parent) or identities[parent[\'pid\']]!=parent:raise ValueError(\'unowned registration parent\')\n    if action==\'register\':\n     if len(identities)>=64:raise ValueError(\'owner active registration channel full\')\n     if child[\'pid\'] in identities or not _owner_live(child) or child[\'pid\']!=child[\'sid\'] or child[\'pid\']!=child[\'pgid\']:raise ValueError(\'unowned registered child\')\n     with open(\'/proc/\'+str(child[\'pid\'])+\'/stat\') as f:ppid=int(f.read(65537).rsplit(\')\',1)[1].split()[1])\n     if ppid!=parent[\'pid\'] or value[\'nonce\'] in nonces:raise ValueError(\'registered parent/nonce mismatch\')\n     registered(child,parent,value[\'nonce\']);os.write(replies[0],b\'E\')\n    elif action==\'retire\':\n     key=child[\'pid\']\n     if key==pid or identities.get(key)!=child or parents.get(key)!=parent or registrations.get(key)!=value[\'registration\']:raise ValueError(\'unowned retirement parent/identity/nonce\')\n     # A trusted registered parent requests this only after actual reap and\n     # durable terminal evidence. Kernel disappearance plus group ESRCH is\n     # required independently; a dead leader with descendants stays owned.\n     empty=False\n     if _owner_state(child)==\'gone\':\n      try:os.killpg(child[\'pgid\'],0)\n      except ProcessLookupError:empty=True\n      except PermissionError:pass\n     if empty:\n      history(\'retire\',{\'child\':child,\'parent\':parent,\'nonce\':value[\'registration\']})\n      nonces.discard(registrations.pop(key));del parents[key];del identities[key]\n      signaled.difference_update([item for item in signaled if item[0]==key and item[1]==child[\'starttime\']])\n      summary[\'retired\']+=1\n     os.write(replies[0],b\'E\' if empty else b\'R\')\n    else:raise ValueError(\'unknown owner request action\')\n   finally:\n    for fd in replies:os.close(fd)\n def signal_child(identity,sig):\n  if _owner_state(identity)!=\'live\' or (identity[\'pid\'],identity[\'starttime\'],sig) in signaled:return\n  if identity[\'pid\']!=identity[\'pgid\'] or identity[\'pid\']!=identity[\'sid\']:return\n  try:\n   if _owner_live(identity):os.killpg(identity[\'pid\'],sig);signaled.add((identity[\'pid\'],identity[\'starttime\'],sig));signals.append({\'child\':identity,\'signal\':sig.name});del signals[:-128]\n  except ProcessLookupError:pass\n try:\n  raw=b\'\';stage=0\n  while True:\n   drain()\n   if time.monotonic()>=deadline:raise TimeoutError(\'outer guard work deadline\')\n   try:b=os.read(ready_r,1)\n   except BlockingIOError:time.sleep(.002);continue\n   if stage==0:\n    if b!=b\'H\':raise ValueError(\'guard handshake disconnected\')\n    supervisor=_owner_fields(pid)\n    if supervisor[\'pgid\']!=pid or supervisor[\'sid\']!=pid:raise ValueError(\'guard session differs\')\n    registered(supervisor,None,request[\'nonce\']);os.write(ack_w,(json.dumps({**supervisor,\'nonce\':request[\'nonce\']})+\'\\n\').encode());stage=1\n   else:\n    if not b:raise ValueError(\'guard registration disconnected\')\n    raw+=b\n    if len(raw)>64:raise ValueError(\'guard deadline control cap\')\n    if raw.endswith(b\'\\n\'):\n     deadline=min(deadline,float(raw));os.pwrite(cancel,struct.pack(\'d\',deadline+5),0)\n     if time.monotonic()>=deadline or not _owner_live(supervisor):raise TimeoutError(\'original guest deadline expired\')\n     os.write(ack_w,b\'E\');break\n  while True:\n   drain();got,value=os.waitpid(pid,os.WNOHANG)\n   if got:status=os.waitstatus_to_exitcode(value);break\n   if time.monotonic()>=deadline:raise TimeoutError(\'outer guard work deadline\')\n   time.sleep(.002)\n  if status:raise ValueError(\'owned supervisor failed: \'+str(status))\n  # No independently registered live worker may outlast a successful supervisor.\n  if any(k!=pid for k in identities):raise ValueError(\'unretired registered worker outlived supervisor\')\n except BaseException as error:\n  failure=error\n  end=min(deadline+5,time.monotonic()+5,struct.unpack(\'d\',os.pread(cancel,8,0))[0]);os.pwrite(cancel,struct.pack(\'d\',end),0)\n  if supervisor is not None and _owner_live(supervisor):\n   try:os.kill(supervisor[\'pid\'],signal.SIGUSR1)\n   except ProcessLookupError:pass\n  term_end=min(end,time.monotonic()+2)\n  while time.monotonic()<end:\n   try:drain()\n   except BaseException:pass\n   phase=signal.SIGTERM if time.monotonic()<term_end else signal.SIGKILL\n   for k,identity in list(identities.items()):\n    if k!=pid:signal_child(identity,phase)\n   live=[v for k,v in identities.items() if k!=pid and _owner_state(v)==\'live\']\n   if not live and supervisor is not None:signal_child(supervisor,phase)\n   if status is None:\n    try:\n     got,value=os.waitpid(pid,os.WNOHANG)\n     if got:status=os.waitstatus_to_exitcode(value)\n    except ChildProcessError:status=1\n   if status is not None and not live:break\n   time.sleep(.005)\n  survivors=[]\n  for v in identities.values():\n   state=_owner_state(v);possible=state in (\'live\',\'identity-lost\')\n   if not possible:\n    try:os.killpg(v[\'pgid\'],0);possible=True\n    except ProcessLookupError:pass\n    except PermissionError:possible=True\n   if possible:survivors.append(v)\n  facts={\'error\':str(error)[:4096],\'ownedChildren\':list(identities.values()),\'survivorIdentities\':survivors,\n    \'possibleSurvivors\':bool(survivors),\'signals\':signals[-128:],\'ownershipSummary\':{**summary,\'activeCount\':len(identities)}}\n  raw=(json.dumps(facts,separators=(\',\',\':\'))+\'\\n\').encode()\n  if len(raw)>65500:raw=(json.dumps({\'error\':\'owner ledger exceeds evidence cap\',\'possibleSurvivors\':True})+\'\\n\').encode()\n  try:os.write(2,b\'P01_OWNER_FAILURE: \'+raw)\n  except OSError:pass\n finally:\n  for fd in (ready_r,ack_w,cancel):os.close(fd)\n  channel.close()\n if failure is not None:raise SystemExit(1)\n os.write(2,b\'P01_OWNER_SUMMARY: \'+(json.dumps({**summary,\'activeCount\':len(identities)},separators=(\',\',\':\'))+\'\\n\').encode())\n raise SystemExit(status or 0)\n'
exec(compile(_OWNER_RUNTIME,'<frozen-owner-runtime>','exec'),globals())
_ENTRY = 'BODY='+repr(_ENTRY_BODY)+'\n'+_OWNER_RUNTIME+"\nr=json.loads(sys.argv[1]);duration=int(sys.argv[4]);deadline=time.monotonic()+min(duration,r['duration'])\nif duration<=0:raise ValueError('positive remaining duration required')\ndirectory='/opt/'+r['s2Generation']+'/s3-admission-observations/'+r['generation']+'/'+r['nonce']\n_owner_guard(BODY,r,directory,deadline,session=True)\n"
_BOOTSTRAP = 'BODY='+repr(_BOOTSTRAP_BODY)+'\n'+_OWNER_RUNTIME+"\nr=json.loads(sys.argv[1]);deadline=time.monotonic()+r['duration']\nif type(r['duration']) is not int or r['duration']<=0:raise ValueError('positive duration required')\ndirectory='/opt/'+r['s2Generation']+'/s3-admission-observations/'+r['generation']+'/'+r['nonce']\n_owner_guard(BODY,r,directory,deadline)\n"

_HOST_HASH = r'''import hashlib,json,os,stat,sys
first=os.fstat(0)
if not stat.S_ISREG(first.st_mode):raise ValueError('regular input descriptor required')
h=hashlib.sha256();n=0
while b:=sys.stdin.buffer.read(1048576):h.update(b);n+=len(b)
last=os.fstat(0)
if (first.st_dev,first.st_ino,first.st_size,first.st_mtime_ns,first.st_ctime_ns)!=(last.st_dev,last.st_ino,last.st_size,last.st_mtime_ns,last.st_ctime_ns):raise ValueError('input changed during supervised hash')
print(json.dumps({'bytes':n,'sha256':h.hexdigest()}))
'''


def _host_hash(incoming,deadline):
    incoming.seek(0)
    result=_pump_host([sys.executable,'-I','-B','-c',_HOST_HASH],input_file=incoming,deadline=deadline)
    value=_decode(result['stdout'])
    if type(value) is not dict or set(value)!= {'bytes','sha256'}:raise ValueError('hash worker response differs')
    _integer(value['bytes']);_hex(value['sha256'])
    return value
