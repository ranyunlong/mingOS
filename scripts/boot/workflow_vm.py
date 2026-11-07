"""Workflow VM lifecycle coordinator; no qualification or reset support.

Execute only this trusted installation, with the entire pack mounted read-only.
The pack's code is never imported. Synthetic tests do not prove an actual guest, outer isolation or Linux admission.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import uuid

# This trusted installation is frozen independently of the read-only pack.
TRUSTED_BOOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TRUSTED_BOOT))
from workflow_pack import verify_pack as _verify_pack, strict_json, stamp
from workflow_profile import session_contract, PROFILE
from bootlib import validate_bundle
from infra import regular_file, classify_guest, validate_cold_pair

FLAGS = dict(diagnosticOnly=True, instrumentedQualification=False,
             formalQualification=False, finalSystemAccepted=False)
BUDGETS = dict(guestSeconds=900, driverSeconds=945, outerSeconds=1950)
INSTANCE = 'workflow-instance.json'
DISK_MANIFEST = 'disks/workflow-128.json'
DISK_IMAGE = 'disks/workflow-128.raw'
HEX = re.compile(r'[0-9a-f]{64}\Z')
IDENTITY_KEYS = {'schemaVersion', 'profile', 'generation', 'nonce', 'packManifestSha256',
                 'bundleSha256', 'diskManifest', 'diskUuid', 'diskSerial', *FLAGS}

def require(value, message):
    if not value:
        raise ValueError('Workflow VM: ' + message)

def _platform():
    require(sys.platform == 'linux' and os.geteuid() == 65534 and os.getegid() == 65534,
            'Linux uid/gid65534 required')

def _canonical(path):
    path = Path(path)
    require(path.is_absolute() and path.absolute() == path.resolve(), 'canonical absolute path required')
    return path

def _private(path, directory=False):
    info = Path(path).lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == 65534 and (directory or info.st_nlink == 1)
            and stat.S_IMODE(info.st_mode) in ((0o700,) if directory else (0o400, 0o600)),
            'owned private directory/file required: ' + str(path))
    return info

def _workspace(path, create=False):
    from data_disk import workspace
    path = _canonical(path)
    require(path == Path('/work/mingos-boot-infra-20261025'), 'fixed independent /work workspace required')
    return workspace(path, create=create)

def _create_disk(root, name, size):
    from data_disk import create_disk
    return create_disk(root, name, size)

def _runtime_root():
    return Path('/run/mingos-runtime')

def _output_root():
    return Path('/results')

def _runtime(path):
    from data_disk import runtime_directory
    require(_canonical(path) == _runtime_root(), 'fixed runtime root required')
    return runtime_directory(path)

def _read_only_pack(path):
    require(os.statvfs(_canonical(path)).f_flag & os.ST_RDONLY, 'pack must be mounted read-only')

def _bytes(path, limit=1024*1024, owned=True):
    path = _canonical(path)
    before = _private(path) if owned else regular_file(path)
    require(before.st_size <= limit, 'bounded metadata size exceeded')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        require(stamp(os.fstat(fd)) == stamp(before), 'metadata changed before open')
        chunks, size = [], 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - size))
            if not chunk: break
            size += len(chunk); chunks.append(chunk)
            require(size <= limit, 'metadata grew past limit')
        require(size == before.st_size and stamp(os.fstat(fd)) == stamp(before)
                and stamp(path.lstat()) == stamp(before), 'metadata changed during read')
        return b''.join(chunks)
    finally:
        os.close(fd)

def _json(path, owned=True):
    return strict_json(_bytes(path, owned=owned))

def _sha(raw):
    return hashlib.sha256(raw).hexdigest()

def _hash(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None

def _uuid(value):
    try: return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError): return False

def _write(path, value):
    path = Path(path)
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode() if type(value) is dict else value
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as f:
            f.write(raw); f.flush(); os.fsync(fd)
    finally:
        os.close(fd)
    # Preserve file creation across a crash; failure remains an incomplete state.
    if sys.platform == 'linux':
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)

@contextlib.contextmanager
def _lock(path, *, readonly=False):
    """No creation or repair, including for start's instance lock."""
    import fcntl
    path = _canonical(path)
    before = _private(path)
    fd = os.open(path, (os.O_RDONLY if readonly else os.O_RDWR) | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(stamp(os.fstat(fd)) == stamp(before), 'lock changed before open')
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error: raise ValueError('Workflow VM: busy owned lock') from error
        yield
    finally:
        os.close(fd)

def verify(pack, manifest_sha256):
    return _verify_pack(pack, manifest_sha256)

def _admit(pack, pin):
    verified = verify(pack, pin)
    _platform(); pack = _canonical(pack); _read_only_pack(pack)
    require(verified.get('profile') == PROFILE and verified.get('manifestSha256') == pin,
            'verified independent workflow pack required')
    review_raw = _bytes(pack/'bundle/review.json', owned=False)
    review = strict_json(review_raw); session_contract(review)
    require(review['inputScriptSha256'] is None, 'interactive workflow identity required')
    bundle_raw = _bytes(pack/'bundle/bundle.json', owned=False)
    bundle = strict_json(bundle_raw)
    require(type(bundle) is dict and type(bundle.get('schemaVersion')) is int and bundle['schemaVersion'] == 1
            and type(bundle.get('infraApiVersion')) is int and bundle['infraApiVersion'] == 1
            and bundle.get('payloadProfile') == PROFILE and bundle.get('developerProfile') == PROFILE
            and bundle.get('sessionReview') == review and bundle.get('reviewSha256') == _sha(review_raw)
            and all(bundle.get(key) is value for key,value in FLAGS.items())
            and bundle.get('autostart') == [1,2] and bundle.get('inputDiagnostics') is True
            and bundle.get('diagnosticProfile') == 'input-pointer-ready-v1'
            and bundle.get('workflowBudgets') == BUDGETS
            and bundle.get('controlPolicy') == review['controlPolicy']
            and bundle.get('faultInjection') is None and bundle.get('isolationProfile') == 'source-v2',
            'closed interactive workflow bundle differs')
    for key in ('strictProbeSha256','probeBuildReceiptSha256','probeSourceFreezeSha256','hostProvenance'):
        require(bundle.get(key) == review[key], 'workflow bundle provenance differs: '+key)
    validate_bundle(pack/'bundle', _sha(review_raw))
    return dict(pack=pack, pin=pin, bundleSha256=_sha(bundle_raw))

def _disk(root, identity):
    manifest = _json(root/DISK_MANIFEST)
    require(type(manifest) is dict and set(manifest) == {'schemaVersion','task','format','filesystem',
        'image','sizeMiB','uuid','serial','creationSha256'} and type(manifest['schemaVersion']) is int
        and manifest['schemaVersion'] == 1 and manifest['task'] == 'boot-infra-20261025'
        and manifest['format'] == 'raw' and manifest['filesystem'] == 'ext4'
        and type(manifest['sizeMiB']) is int and manifest['sizeMiB'] == 128
        and manifest['image'] == DISK_IMAGE and _hash(manifest['creationSha256'])
        and manifest['uuid'] == identity['diskUuid'] and manifest['serial'] == identity['diskSerial'],
        'fixed developer disk manifest differs')
    image = _canonical(root/DISK_IMAGE)
    _private(root/'disks', directory=True)
    info = _private(image)
    require(info.st_size == 128*1024**2, 'fixed 128MiB disk size differs')
    fd = os.open(image, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0))
    try:
        require(stamp(os.fstat(fd)) == stamp(info), 'disk changed before identity read')
        os.lseek(fd, 1024, os.SEEK_SET); block = os.read(fd,1024)
        require(len(block) == 1024 and struct.unpack_from('<H',block,56)[0] == 0xef53
                and struct.unpack_from('<I',block,96)[0] & 0x40, 'ext4 superblock/extents required')
        logsize = struct.unpack_from('<I',block,24)[0]
        require(logsize <= 6, 'ext4 block size differs')
        blocks = struct.unpack_from('<I',block,4)[0]
        if struct.unpack_from('<I',block,96)[0] & 0x80:
            blocks += struct.unpack_from('<I',block,336)[0] << 32
        require(blocks * (1024 << logsize) == info.st_size
                and str(uuid.UUID(bytes=block[104:120])) == identity['diskUuid']
                and block[120:136] == b'MINGOS-DATA'.ljust(16,b'\0'), 'on-disk UUID/label/capacity differs')
        require(stamp(os.fstat(fd)) == stamp(info) and stamp(image.lstat()) == stamp(info),
                'disk changed during read-only identity check')
    finally: os.close(fd)
    return dict(manifest, imagePath=str(image))




def _output(path, index, *, fresh=False):
    path = _canonical(path)
    require(path.parent == _canonical(_output_root()) and path.name == 'boot-%04d'%index,
            'fresh fixed /results/boot-NNNN output required')
    _private(path.parent,directory=True)
    if fresh: require(not path.exists(), 'output already exists')
    return path

def _normal(report, identity, index, previous):
    require(type(report) is dict and report.get('status') == 'normal-desktop-poweroff'
        and report.get('bootAttempted') is True and type(report.get('guestExitCode')) is int
        and report['guestExitCode'] == 0 and report.get('kernelPowerdown') is True
        and report.get('bundleIdentity') == identity['bundleSha256'] and report.get('diskUuid') == identity['diskUuid']
        and report.get('diagnosticOnly') is True and report.get('finalSystemAccepted') is False
        and report.get('bootNumber') is None and report.get('inputScriptSha256') is None
        and report.get('formalQualification',False) is False and report.get('instrumentedQualification',False) is False,
        'VM did not provide a normal unqualified developer poweroff')
    boot, state, marker = report.get('bootId'), report.get('lastState'), report.get('marker')
    require(isinstance(boot,str) and 1 <= len(boot) <= 128 and isinstance(state,dict)
        and isinstance(state.get('sessionId'),str) and 1 <= len(state['sessionId']) <= 128
        and type(marker) is dict and set(marker) == {'bootId','counter'}
        and marker['bootId'] == boot and type(marker['counter']) is int and marker['counter'] == index,
        'normal boot/session/marker identity required')
    terminal = report.get('guestResult')
    require(type(terminal) is dict and terminal.get('type') == 'guest-result'
        and type(terminal.get('desktopExitCode')) is int
        and classify_guest([terminal],False) == 'normal-desktop-poweroff' and terminal.get('error') is None
        and all(terminal.get(key) == report.get(key) for key in ('bootId','diskUuid','marker','previousMarker')),
        'missing or inconsistent normal terminal receipt')
    if previous is None:
        require(report.get('previousMarker') is None, 'new disk must have no previous marker')
    else:
        validate_cold_pair(previous,report)
    return report

def _new_session(report, previous):
    require(all(report['bootId'] != earlier['bootId']
                and report['lastState']['sessionId'] != earlier['lastState']['sessionId'] for earlier in previous),
            'boot/session identity was reused in this generation')




def _execution_ok(value):
    require(type(value) is dict and set(value) == {'exitCode','timedOut','processGroupEmpty'}
        and type(value['exitCode']) is int and value['exitCode'] == 0
        and value['timedOut'] is False and value['processGroupEmpty'] is True,
        'supervisor timeout, nonzero exit or owned process residue')

@contextlib.contextmanager
def _new_log(path):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as output:
        try: yield output
        finally:output.flush();os.fsync(output.fileno())

def _interrupted_child(child, attempt, error):
    """Retain interruption and cleanup evidence without replacing the cause."""
    cleanup_errors=[]
    try:os.killpg(child.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    except BaseException as caught:cleanup_errors.append('killpg: '+repr(caught))
    try:child.wait(timeout=5)
    except BaseException as caught:cleanup_errors.append('wait: '+repr(caught))
    empty=False
    try:os.killpg(child.pid,0)
    except ProcessLookupError:empty=not cleanup_errors
    except BaseException as caught:cleanup_errors.append('group probe: '+repr(caught))
    receipt=dict(schemaVersion=1,profile=PROFILE,status='supervisor-interrupted',childPid=child.pid,
        originalError=repr(error)[:4096],cleanupErrors=cleanup_errors,processGroupEmpty=empty,**FLAGS)
    try:_write(attempt/'supervisor-interruption.json',receipt)
    except BaseException as caught:
        error.add_note('supervisor interruption receipt could not be retained: '+repr(caught))

def _execute_vm(command, attempt):
    """Fixed existing VM subprocess; bounded supervisor owns only its process group."""
    with _new_log(attempt/'supervisor.stdout.log') as stdout, _new_log(attempt/'supervisor.stderr.log') as stderr:
        child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
                               start_new_session=True,umask=0o077)
        timed_out=False
        try:
            try: code=child.wait(timeout=945)
            except subprocess.TimeoutExpired:
                timed_out=True
                try: os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                code=child.wait(timeout=5)
            try: os.killpg(child.pid,0)
            except ProcessLookupError: empty=True
            else:
                empty=False
                try: os.killpg(child.pid,signal.SIGKILL)
                except ProcessLookupError: pass
        except BaseException as error:
            _interrupted_child(child,attempt,error)
            raise
        stdout.flush();stderr.flush();os.fsync(stdout.fileno());os.fsync(stderr.fileno())
    return dict(exitCode=code,timedOut=timed_out,processGroupEmpty=empty)

def _latch(root,identity_sha,error,index=None):
    path=root/'failure-latch.json'
    if not os.path.lexists(path):
        _write(path,dict(schemaVersion=1,profile=PROFILE,instanceSha256=identity_sha,index=index,
                        status='generation-locked',error=str(error)[:4096],**FLAGS))


# Appended to an independent copy of reviewed lifecycle safety primitives.
# Docker observations below are supplied by the trusted outer coordinator.
# Their byte binding does not turn them into independent Docker proofs.
import time
import shutil
from developer_pack import open_root, inventory, confirm_inventory, relative

BASE_IDENTITY_KEYS = IDENTITY_KEYS
IDENTITY_KEYS = BASE_IDENTITY_KEYS | {'parent'}
_base_normal = _normal

def _identity_at(root, *, depth=0):
    require(depth <= 8, 'restore ancestry depth limit')
    _private(root, directory=True)
    raw = _bytes(root/INSTANCE); value = strict_json(raw)
    require(type(value) is dict and set(value) == IDENTITY_KEYS
            and type(value['schemaVersion']) is int and value['schemaVersion'] == 1
            and value['profile'] == PROFILE and _uuid(value['generation'])
            and isinstance(value['nonce'],str) and re.fullmatch('[0-9a-f]{32}',value['nonce'])
            and _hash(value['packManifestSha256']) and _hash(value['bundleSha256'])
            and value['diskManifest'] == DISK_MANIFEST and _uuid(value['diskUuid'])
            and value['diskSerial'] == 'MINGDATA'+value['diskUuid'].replace('-','')[:12]
            and all(value[key] is flag for key,flag in FLAGS.items()), 'workflow identity differs')
    require(_bytes(root/'workflow-instance.sha256') == (_sha(raw)+'\n').encode(), 'instance seal differs')
    _private(root/'attempts',directory=True)
    parent=value['parent']
    require(parent is None or (type(parent) is dict and set(parent)=={
        'backupSha256','sourceGeneration','sourceInstanceSha256','sourceDiskSha256','historyTipSha256','normalBoots'}
        and all(_hash(parent[k]) for k in ('backupSha256','sourceInstanceSha256','sourceDiskSha256','historyTipSha256'))
        and _uuid(parent['sourceGeneration']) and type(parent['normalBoots']) is int
        and 1 <= parent['normalBoots'] <= 9999), 'restore parent differs')
    return value, _sha(raw)

def _identity(instance):
    _platform(); instance=_canonical(instance)
    require(instance.name==INSTANCE, 'fixed workflow instance filename required')
    root=_workspace(instance.parent)
    identity,pin=_identity_at(root)
    return root,identity,pin

def _file_hash(path):
    before=_private(path); fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        require(stamp(os.fstat(fd))==stamp(before), 'file changed before hash')
        digest=hashlib.sha256()
        while True:
            chunk=os.read(fd,1024*1024)
            if not chunk: break
            digest.update(chunk)
        require(stamp(os.fstat(fd))==stamp(before) and stamp(path.lstat())==stamp(before),
                'file changed during hash')
        return digest.hexdigest()
    finally: os.close(fd)

def _ready_path(path):
    return Path(str(path)+'.ready')

def _publish(path,value):
    """Publish immutable data only after its bytes and directory entry are durable.

    O_EXCL makes the marker's empty file atomically visible. Seeing the data
    filename alone never indicates that the producer has completed its write.
    Partial data/marker failures are retained, never deleted or repaired.
    """
    _write(path,value)
    _write(_ready_path(path),b'')

def _published(path,*,limit=1024*1024):
    ready=_ready_path(path)
    info=_private(ready)
    require(info.st_size==0 and stat.S_IMODE(info.st_mode)==0o600,'private empty 0600 ready marker required')
    require(stat.S_IMODE(_private(path).st_mode)==0o600,'private 0600 published data required')
    require(_bytes(ready,limit=0)==b'', 'published ready marker must be empty')
    raw=_bytes(path,limit=limit)
    # Marker ownership/type/link/mode and emptiness are checked on both sides
    # of the immutable bounded metadata read.
    require(_bytes(ready,limit=0)==b'', 'published ready marker changed')
    return raw

def _current(runtime):
    deadline=time.monotonic()+60
    path=runtime/'current-container-id'
    while not os.path.lexists(_ready_path(path)):
        require(time.monotonic()<deadline, 'outer current container identity timeout')
        time.sleep(0.1)
    raw=_published(path,limit=65)
    require(re.fullmatch(b'[0-9a-f]{64}\n',raw) is not None, 'full outer container ID required')
    return raw.decode().strip()

def _command(pack,root,output,runtime):
    bootstrap=('import runpy,sys;sys.path.insert(0,'+repr(str(TRUSTED_BOOT))+
        ');runpy.run_path('+repr(str(TRUSTED_BOOT/'vm.py'))+",run_name='__main__')")
    suffix=output.name.removeprefix('boot-')
    return [sys.executable,'-I','-B','-c',bootstrap,'--bundle',str(pack/'bundle'),
        '--review',str(pack/'bundle/review.json'),'--output',str(output),'--timeout','900',
        '--work-root',str(root),'--runtime-root',str(runtime),'--data-manifest',DISK_MANIFEST,
        '--workflow-control',str(runtime/('control-'+suffix)),
        '--diagnostic-split-input','--diagnostic-pointer-handshake']

def _validate_transition(value,operation,identity,pin,tip,disk_sha,prior,current):
    require(type(value) is dict and set(value)=={'request','receipt'},'transition record differs')
    request=value['request'];receipt=value['receipt']
    require(type(request) is dict and set(request)=={'schemaVersion','profile','operation','nonce',
        'generation','instanceSha256','historyTipSha256','diskSha256','priorContainerId','currentContainerId','runtimeNonce'}
        and type(request['schemaVersion']) is int and request['schemaVersion']==1
        and request['profile']==PROFILE and request['operation']==operation
        and request['generation']==identity['generation'] and request['instanceSha256']==pin
        and request['historyTipSha256']==tip and request['diskSha256']==disk_sha
        and request['priorContainerId']==prior and request['currentContainerId']==current
        and _hash(prior) and _hash(current) and prior!=current
        and all(isinstance(request[k],str) and re.fullmatch('[0-9a-f]{32}',request[k])
                for k in ('nonce','runtimeNonce')), 'transition request differs')
    request_pin=_sha((json.dumps(request,sort_keys=True,indent=2,allow_nan=False)+'\n').encode())
    require(type(receipt) is dict and set(receipt)=={'schemaVersion','profile','requestSha256',
        'priorContainerId','currentContainerId','priorState','activeWritableContainerIds',
        'priorInspectSha256','writerScanSha256'} and type(receipt['schemaVersion']) is int
        and receipt['schemaVersion']==1 and receipt['profile']==PROFILE
        and receipt['requestSha256']==request_pin and receipt['priorContainerId']==prior
        and receipt['currentContainerId']==current and receipt['priorState']=='exited'
        and receipt['activeWritableContainerIds']==[current]
        and _hash(receipt['priorInspectSha256']) and _hash(receipt['writerScanSha256']),
        'outer stopped/no-writer receipt differs')
    return value

def _history(root,identity,identity_sha,*,depth=0):
    reports=[]; tip=None; prior_container=None; final_disk=None
    try:
        require(depth<=8, 'restore ancestry depth limit')
        require(not os.path.lexists(root/'failure-latch.json'), 'failure latch exists')
        parent=identity['parent']
        if parent is not None:
            origin=root/'restore-origin'
            manifest=_verify_backup(origin,parent['backupSha256'])
            oldroot=origin/'snapshot'; old,oldsha=_identity_at(oldroot,depth=depth+1)
            oldreports,oldmeta=_history(oldroot,old,oldsha,depth=depth+1)
            require(oldmeta['error'] is None and oldreports
                and parent['sourceGeneration']==old['generation']
                and parent['sourceInstanceSha256']==oldsha
                and parent['historyTipSha256']==oldmeta['tip']
                and parent['sourceDiskSha256']==oldmeta['diskSha256']
                and parent['normalBoots']==len(oldreports)
                and old['diskUuid']==identity['diskUuid']
                and old['packManifestSha256']==identity['packManifestSha256']
                and old['bundleSha256']==identity['bundleSha256'], 'restore ancestry binding differs')
            reports=oldreports; tip=oldmeta['tip']; prior_container=oldmeta['containerId'];final_disk=oldmeta['diskSha256']
        names=sorted(p.name for p in (root/'attempts').iterdir())
        require(len(names)+len(reports)<=9999 and names==['%04d'%n for n in range(1,len(names)+1)],
                'attempt sequence differs')
        transition_names=[]
        for local,name in enumerate(names,1):
            directory=root/'attempts'/name; _private(directory,directory=True)
            require(set(p.name for p in directory.iterdir())=={
                'attempt.json','report.json','result.json','supervisor.stdout.log','supervisor.stderr.log'},
                'incomplete or unexpected attempt members')
            attempt_raw=_bytes(directory/'attempt.json'); attempt=strict_json(attempt_raw)
            expected_keys={'schemaVersion','profile','index','localIndex','nonce','generation','instanceSha256',
                'packManifestSha256','bundleSha256','output','command','budgets','previousResultSha256','containerId',*FLAGS}
            index=len(reports)+1
            require(type(attempt) is dict and set(attempt)==expected_keys
                and type(attempt['schemaVersion']) is int and attempt['schemaVersion']==1
                and attempt['profile']==PROFILE and type(attempt['index']) is int and attempt['index']==index
                and type(attempt['localIndex']) is int and attempt['localIndex']==local
                and isinstance(attempt['nonce'],str) and re.fullmatch('[0-9a-f]{32}',attempt['nonce'])
                and attempt['generation']==identity['generation'] and attempt['instanceSha256']==identity_sha
                and attempt['packManifestSha256']==identity['packManifestSha256']
                and attempt['bundleSha256']==identity['bundleSha256'] and attempt['budgets']==BUDGETS
                and attempt['previousResultSha256']==tip and _hash(attempt['containerId'])
                and all(attempt[k] is v for k,v in FLAGS.items()), 'attempt identity differs')
            if reports:
                transition_name='%04d.json'%index;transition_names.append(transition_name)
                _validate_transition(_json(root/'transitions'/transition_name),'reopen',identity,identity_sha,
                    tip,final_disk,prior_container,attempt['containerId'])
            command=attempt['command']; output=Path(attempt['output'])
            require(type(command) is list and len(command)==23 and all(isinstance(x,str) for x in command)
                and output==Path('/results')/('boot-%04d'%index), 'attempt command/output differs')
            pack=Path(command[6]).parent
            # Archived roots retain commands for their original canonical workspace.
            require(command==_command(pack,Path('/work/mingos-boot-infra-20261025'),output,_runtime_root()),
                'fixed workflow command differs')
            result_raw=_bytes(directory/'result.json'); result=strict_json(result_raw)
            require(type(result) is dict and set(result)=={'schemaVersion','profile','index','status',
                'instanceSha256','attemptSha256','reportSha256','diskSha256','previousResultSha256',
                'containerId','execution','error',*FLAGS}
                and result['schemaVersion']==1 and type(result['schemaVersion']) is int
                and result['profile']==PROFILE and type(result['index']) is int and result['index']==index
                and result['status']=='normal' and result['error'] is None
                and result['instanceSha256']==identity_sha and result['attemptSha256']==_sha(attempt_raw)
                and result['previousResultSha256']==tip and result['containerId']==attempt['containerId']
                and _hash(result['diskSha256']) and all(result[k] is v for k,v in FLAGS.items()),
                'failed, incomplete or mismatched result')
            _execution_ok(result['execution'])
            raw=_bytes(directory/'report.json')
            require(_sha(raw)==result['reportSha256'], 'retained VM report changed')
            report=_base_normal(strict_json(raw),identity,index,reports[-1] if reports else None)
            _new_session(report,reports); reports.append(report)
            tip=_sha(result_raw); prior_container=result['containerId'];final_disk=result['diskSha256']
        if transition_names:
            _private(root/'transitions',directory=True)
            require(sorted(p.name for p in (root/'transitions').iterdir())==transition_names,
                    'transition sequence differs')
        else:require(not (root/'transitions').exists(),'unexpected transitions')
        return reports,dict(error=None,tip=tip,containerId=prior_container,diskSha256=final_disk,localCount=len(names))
    except (OSError,ValueError,KeyError,TypeError) as error:
        return reports,dict(error=str(error),tip=tip,containerId=prior_container,diskSha256=final_disk,localCount=0)

def _audit(root,identity,pin):
    _disk(root,identity)
    reports,meta=_history(root,identity,pin)
    require(meta['error'] is None,'locked workflow history: '+str(meta['error']))
    disk_sha=_file_hash(root/DISK_IMAGE)
    require(not reports or disk_sha==meta['diskSha256'],'completed disk bytes changed')
    return reports,meta,disk_sha

def _gate(operation,root,identity,pin,reports,meta,disk_sha,runtime,current):
    require(reports and _hash(meta['containerId']) and current!=meta['containerId'],
            'transition requires completed prior distinct container')
    request=dict(schemaVersion=1,profile=PROFILE,operation=operation,nonce=uuid.uuid4().hex,
        generation=identity['generation'],instanceSha256=pin,historyTipSha256=meta['tip'],
        diskSha256=disk_sha,priorContainerId=meta['containerId'],currentContainerId=current,
        runtimeNonce=uuid.uuid4().hex)
    path=runtime/'transition-request.json'; _publish(path,request);request_pin=_sha(_published(path))
    deadline=time.monotonic()+60; receipt_path=runtime/'transition-receipt.json'
    while not os.path.lexists(_ready_path(receipt_path)):
        require(time.monotonic()<deadline,'outer stopped/no-writer gate timeout');time.sleep(.1)
    receipt=strict_json(_published(receipt_path))
    require(type(receipt) is dict and receipt.get('requestSha256')==request_pin,'outer gate byte pin differs')
    # Retain the exact observation while the persistent instance lock is held.
    return _validate_transition(dict(request=request,receipt=receipt),operation,identity,pin,
        meta['tip'],disk_sha,meta['containerId'],current)

def init(pack,manifest_sha256,work_root,runtime_root):
    admitted=_admit(pack,manifest_sha256);runtime=_runtime(runtime_root);current=_current(runtime)
    root=_workspace(work_root,create=True)
    disk=_create_disk(root,'workflow-128',128)
    identity=dict(schemaVersion=1,profile=PROFILE,generation=str(uuid.uuid4()),nonce=uuid.uuid4().hex,
        packManifestSha256=manifest_sha256,bundleSha256=admitted['bundleSha256'],diskManifest=DISK_MANIFEST,
        diskUuid=disk['uuid'],diskSerial=disk['serial'],parent=None,**FLAGS)
    _disk(root,identity);(root/'attempts').mkdir(mode=0o700)
    _write(root/'workflow-instance.lock',b'');_write(root/INSTANCE,identity)
    _write(root/'workflow-instance.sha256',(_sha(_bytes(root/INSTANCE))+'\n').encode())
    _write(root/'initialization.json',dict(schemaVersion=1,profile=PROFILE,containerId=current))
    return dict(schemaVersion=1,status='workflow-instance-initialized',generation=identity['generation'],
        instance=str(root/INSTANCE),instanceSha256=_sha(_bytes(root/INSTANCE)),diskUuid=disk['uuid'],
        diskSha256=_file_hash(root/DISK_IMAGE),normalBoots=0,historyTipSha256=None,priorContainerId=None,
        guestBooted=False,**FLAGS)

def inspect(instance):
    root,identity,pin=_identity(instance)
    with _lock(root/'workflow-instance.lock',readonly=True):
        # Re-read immutable identity after EX acquisition.
        check,checkpin=_identity_at(root);require(check==identity and checkpin==pin,'identity changed')
        reports,meta=_history(root,identity,pin)
        try:
            _disk(root,identity);disk_sha=_file_hash(root/DISK_IMAGE)
            require(not reports or meta['diskSha256']==disk_sha,'completed disk bytes changed')
        except (OSError,ValueError) as error:meta['error']=str(error);disk_sha=None
        return dict(schemaVersion=1,status='workflow-instance-inspected',state='locked' if meta['error'] else 'ready',
            reason=meta['error'],generation=identity['generation'],instanceSha256=pin,diskUuid=identity['diskUuid'],
            normalBoots=len(reports),historyTipSha256=meta['tip'],diskSha256=disk_sha,
            priorContainerId=meta['containerId'],guestBooted=False,**FLAGS)

def start(pack,manifest_sha256,instance,output,runtime_root,*,reopen=False):
    root,identity,pin=_identity(instance);runtime=_runtime(runtime_root);current=_current(runtime)
    with _lock(root/'workflow-instance.lock'):
        check,checkpin=_identity_at(root);require(check==identity and checkpin==pin,'identity changed')
        try:
            admitted=_admit(pack,manifest_sha256)
            require(identity['packManifestSha256']==manifest_sha256
                and identity['bundleSha256']==admitted['bundleSha256'],'immutable workflow pack changed')
            reports,meta,disk_sha=_audit(root,identity,pin)
            require(reopen is bool(reports),'completed sessions require explicit reopen; first boot requires start')
            transition=_gate('reopen',root,identity,pin,reports,meta,disk_sha,runtime,current) if reopen else None
        except (OSError,ValueError) as error:_latch(root,pin,error);raise
        index=len(reports)+1;local=meta['localCount']+1
        require(index<=9999,'attempt limit reached');output=_output(output,index,fresh=True)
        directory=root/'attempts'/('%04d'%local);directory.mkdir(mode=0o700)
        attempt=dict(schemaVersion=1,profile=PROFILE,index=index,localIndex=local,nonce=uuid.uuid4().hex,
            generation=identity['generation'],instanceSha256=pin,packManifestSha256=manifest_sha256,
            bundleSha256=admitted['bundleSha256'],output=str(output),command=_command(admitted['pack'],root,output,runtime),
            budgets=dict(BUDGETS),previousResultSha256=meta['tip'],containerId=current,**FLAGS)
        _write(directory/'attempt.json',attempt)
        if transition is not None:
            # Gate evidence is kept separately from the fixed attempt member set.
            (root/'transitions').mkdir(mode=0o700,exist_ok=True)
            _write(root/'transitions'/('%04d.json'%index),transition)
        execution=None;report_sha=None;completed_disk=None
        try:
            execution=_execute_vm(attempt['command'],directory);_execution_ok(execution)
            raw=_bytes(output/'result.json');report_sha=_sha(raw)
            report=_base_normal(strict_json(raw),identity,index,reports[-1] if reports else None)
            _new_session(report,reports);_disk(root,identity)
            completed_disk=_file_hash(root/DISK_IMAGE)
            _write(directory/'report.json',raw)
            result=dict(schemaVersion=1,profile=PROFILE,index=index,status='normal',instanceSha256=pin,
                attemptSha256=_sha(_bytes(directory/'attempt.json')),reportSha256=report_sha,diskSha256=completed_disk,
                previousResultSha256=meta['tip'],containerId=current,execution=execution,error=None,**FLAGS)
            _write(directory/'result.json',result)
        except BaseException as error:
            _latch(root,pin,error,index)
            if not (directory/'result.json').exists():
                _write(directory/'result.json',dict(schemaVersion=1,profile=PROFILE,index=index,status='failed',
                    instanceSha256=pin,attemptSha256=_sha(_bytes(directory/'attempt.json')),reportSha256=report_sha,
                    diskSha256=completed_disk,previousResultSha256=meta['tip'],containerId=current,
                    execution=execution,error=repr(error)[:4096],**FLAGS))
            raise ValueError('Workflow VM: failed generation retained and locked: '+str(error)) from error
        return dict(schemaVersion=1,status='workflow-session-normal',normalBoots=index,generation=identity['generation'],
            output=str(output),bootId=report['bootId'],sessionId=report['lastState']['sessionId'],
            instanceSha256=pin,priorContainerId=current,
            diskSha256=completed_disk,historyTipSha256=_sha(_bytes(directory/'result.json')),
            workflowNormalDiskReopenVerified=index>=2,**FLAGS)

def _inventory(path):
    fd=open_root(path)
    try:
        files,dirs,snapshots=inventory(fd);confirm_inventory(fd,snapshots)
        return files,dirs
    finally:os.close(fd)

def _copy_tree(source,target,files,dirs):
    """Exclusive creates and no-follow descriptor descent; preserve private modes."""
    _private(target,directory=True)
    rootfd=open_root(source)
    try:
        for name,record in sorted(dirs.items(),key=lambda pair:(pair[0].count('/'),pair[0])):
            relative(name);(target/name).mkdir(mode=record['mode'])
        for name,record in sorted(files.items()):
            relative(name);parts=name.split('/');parent=os.dup(rootfd)
            try:
                for part in parts[:-1]:
                    child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                    os.close(parent);parent=child
                src=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
                try:
                    info=os.fstat(src)
                    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'copy source must remain regular')
                    dest=os.open(target/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,record['mode'])
                    try:
                        digest=hashlib.sha256();count=0
                        while True:
                            chunk=os.read(src,1024*1024)
                            if not chunk:break
                            digest.update(chunk);count+=len(chunk)
                            view=memoryview(chunk)
                            while view:view=view[os.write(dest,view):]
                        os.fchmod(dest,record['mode']);os.fsync(dest)
                        require(count==record['bytes'] and digest.hexdigest()==record['sha256']
                            and stamp(os.fstat(src))==stamp(info),'copy source changed')
                    finally:os.close(dest)
                finally:os.close(src)
            finally:os.close(parent)
    finally:os.close(rootfd)
    require(_inventory(target)==(files,dirs),'copied complete inventory differs')

def _verify_backup(root,pin):
    root=_canonical(root);_private(root,directory=True)
    raw=_bytes(root/'backup-manifest.json');require(_hash(pin) and _sha(raw)==pin,'external backup pin differs')
    value=strict_json(raw)
    require(type(value) is dict and set(value)=={'schemaVersion','profile','status','instanceSha256',
        'generation','diskSha256','historyTipSha256','normalBoots','files','directories','transition',*FLAGS}
        and type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['profile']==PROFILE
        and value['status']=='workflow-backup-complete' and _hash(value['instanceSha256'])
        and _hash(value['diskSha256']) and _hash(value['historyTipSha256']) and _uuid(value['generation'])
        and type(value['normalBoots']) is int and 1<=value['normalBoots']<=9999
        and all(value[k] is v for k,v in FLAGS.items()),'backup manifest differs')
    require(set(p.name for p in root.iterdir())=={'backup-manifest.json','snapshot'},'backup members differ')
    require(_inventory(root/'snapshot')==(value['files'],value['directories']),'backup inventory differs')
    identity,identity_pin=_identity_at(root/'snapshot')
    transition=value['transition'];require(type(transition) is dict and type(transition.get('request')) is dict,
                                         'backup transition missing')
    _validate_transition(transition,'backup',identity,identity_pin,value['historyTipSha256'],value['diskSha256'],
        transition['request'].get('priorContainerId'),transition['request'].get('currentContainerId'))
    return value

def backup(instance,backup_root,runtime_root):
    root,identity,pin=_identity(instance);runtime=_runtime(runtime_root);current=_current(runtime)
    target=_canonical(backup_root);require(target==Path('/backup'),'fixed new /backup volume required')
    _private(target,directory=True);require(not list(target.iterdir()),'backup target must be empty')
    with _lock(root/'workflow-instance.lock',readonly=True):
        check,checkpin=_identity_at(root);require(check==identity and checkpin==pin,'identity changed')
        reports,meta,disk_sha=_audit(root,identity,pin)
        transition=_gate('backup',root,identity,pin,reports,meta,disk_sha,runtime,current)
        files,dirs=_inventory(root)
        (target/'snapshot').mkdir(mode=0o700);_copy_tree(root,target/'snapshot',files,dirs)
        require(_inventory(root)==(files,dirs),'source changed during stopped backup')
        value=dict(schemaVersion=1,profile=PROFILE,status='workflow-backup-complete',instanceSha256=pin,
            generation=identity['generation'],diskSha256=disk_sha,historyTipSha256=meta['tip'],normalBoots=len(reports),
            files=files,directories=dirs,transition=transition,**FLAGS)
        _write(target/'backup-manifest.json',value)
        resultpin=_sha(_bytes(target/'backup-manifest.json'));_verify_backup(target,resultpin)
        return dict(schemaVersion=1,status='workflow-backup-complete',backupSha256=resultpin,
            normalBoots=len(reports),generation=identity['generation'],guestBooted=False,**FLAGS)

def restore(pack,manifest_sha256,backup_root,backup_sha256,work_root,runtime_root):
    admitted=_admit(pack,manifest_sha256);runtime=_runtime(runtime_root);current=_current(runtime)
    source=_canonical(backup_root);require(source==Path('/backup'),'fixed read-only /backup required')
    require(os.statvfs(source).f_flag & os.ST_RDONLY,'backup must be mounted read-only')
    manifest=_verify_backup(source,backup_sha256);oldroot=source/'snapshot'
    old,oldpin=_identity_at(oldroot);reports,meta,disk_sha=_audit(oldroot,old,oldpin)
    require(reports and manifest['instanceSha256']==oldpin and manifest['generation']==old['generation']
        and manifest['historyTipSha256']==meta['tip'] and manifest['diskSha256']==disk_sha
        and manifest['normalBoots']==len(reports) and old['packManifestSha256']==manifest_sha256
        and old['bundleSha256']==admitted['bundleSha256'],'restore source identity/history differs')
    # Creating the canonical child is O_EXCL via mkdir; failures are retained.
    root=_workspace(work_root,create=True)
    (root/'restore-origin').mkdir(mode=0o700)
    files,dirs=_inventory(source);_copy_tree(source,root/'restore-origin',files,dirs)
    (root/'disks').mkdir(mode=0o700)
    diskfiles={p:manifest['files'][p] for p in (DISK_IMAGE,DISK_MANIFEST)}
    # Copy the disk through the complete safe copier into an intermediate private tree.
    stage=root/'disk-copy';stage.mkdir(mode=0o700)
    _copy_tree(oldroot,stage,diskfiles,{'disks':{'mode':0o700}})
    for name in (DISK_IMAGE,DISK_MANIFEST):os.rename(stage/name,root/name)
    (stage/'disks').rmdir();stage.rmdir()
    parent=dict(backupSha256=backup_sha256,sourceGeneration=old['generation'],sourceInstanceSha256=oldpin,
        sourceDiskSha256=disk_sha,historyTipSha256=meta['tip'],normalBoots=len(reports))
    identity=dict(old,generation=str(uuid.uuid4()),nonce=uuid.uuid4().hex,parent=parent)
    (root/'attempts').mkdir(mode=0o700);_write(root/'workflow-instance.lock',b'')
    _write(root/INSTANCE,identity);_write(root/'workflow-instance.sha256',(_sha(_bytes(root/INSTANCE))+'\n').encode())
    _write(root/'initialization.json',dict(schemaVersion=1,profile=PROFILE,containerId=current,restoredFrom=backup_sha256))
    _audit(root,identity,_sha(_bytes(root/INSTANCE)))
    return dict(schemaVersion=1,status='workflow-instance-restored',generation=identity['generation'],
        sourceGeneration=old['generation'],normalBoots=len(reports),instance=str(root/INSTANCE),
        instanceSha256=_sha(_bytes(root/INSTANCE)),diskSha256=disk_sha,historyTipSha256=meta['tip'],
        priorContainerId=meta['containerId'],guestBooted=False,**FLAGS)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);actions=parser.add_subparsers(dest='action',required=True)
    for name in ('verify','init','start','inspect','reopen','backup','restore'):
        p=actions.add_parser(name)
        if name in ('verify','init','start','reopen','restore'):
            p.add_argument('--pack',type=Path,required=True);p.add_argument('--manifest-sha256',required=True)
        if name in ('init','restore'):p.add_argument('--work-root',type=Path,required=True)
        if name in ('start','inspect','reopen','backup'):p.add_argument('--instance',type=Path,required=True)
        if name in ('start','reopen'):p.add_argument('--output',type=Path,required=True)
        if name not in ('verify','inspect'):p.add_argument('--runtime-root',type=Path,required=True)
        if name in ('backup','restore'):p.add_argument('--backup-root',type=Path,required=True)
        if name=='restore':p.add_argument('--backup-sha256',required=True)
    args=parser.parse_args(argv)
    try:
        kw=vars(args).copy();action=kw.pop('action')
        if action=='reopen':result=start(**kw,reopen=True)
        else:result=globals()[action](**kw)
        print(json.dumps(result,sort_keys=True,allow_nan=False));return 0
    except (OSError,ValueError,RecursionError) as error:
        print(str(error),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
