"""Developer VM lifecycle coordinator; no qualification or reset support.

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
from developer_pack import verify_pack as _verify_pack, strict_json, stamp
from developer_profile import session_contract, PROFILE
from bootlib import validate_bundle
from infra import regular_file, classify_guest, validate_cold_pair

FLAGS = dict(diagnosticOnly=True, instrumentedQualification=False,
             formalQualification=False, finalSystemAccepted=False)
BUDGETS = dict(desktopMs=15000, bootSeconds=900, supervisionSeconds=945, outerSeconds=1950)
INSTANCE = 'developer-instance.json'
DISK_MANIFEST = 'disks/developer-128.json'
DISK_IMAGE = 'disks/developer-128.raw'
HEX = re.compile(r'[0-9a-f]{64}\Z')
IDENTITY_KEYS = {'schemaVersion', 'profile', 'generation', 'nonce', 'packManifestSha256',
                 'bundleSha256', 'diskManifest', 'diskUuid', 'diskSerial', *FLAGS}

def require(value, message):
    if not value:
        raise ValueError('Developer VM: ' + message)

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
        except BlockingIOError as error: raise ValueError('Developer VM: busy owned lock') from error
        yield
    finally:
        os.close(fd)

def verify(pack, manifest_sha256):
    return _verify_pack(pack, manifest_sha256)

def _admit(pack, pin):
    verified = verify(pack, pin)  # Never consume review or bundled code before the complete verifier.
    _platform(); pack = _canonical(pack); _read_only_pack(pack)
    require(verified.get('profile') == PROFILE and verified.get('manifestSha256') == pin,
            'verified developer pack identity required')
    review_raw = _bytes(pack/'bundle/review.json', owned=False)
    review = strict_json(review_raw); session_contract(review)
    bundle_raw = _bytes(pack/'bundle/bundle.json', owned=False)
    bundle = strict_json(bundle_raw)
    require(type(bundle) is dict and type(bundle.get('schemaVersion')) is int and bundle['schemaVersion'] == 1
            and type(bundle.get('infraApiVersion')) is int and bundle['infraApiVersion'] == 1
            and bundle.get('payloadProfile') == PROFILE and bundle.get('developerProfile') == PROFILE
            and bundle.get('sessionReview') == review and bundle.get('reviewSha256') == _sha(review_raw)
            and all(bundle.get(key) is value for key,value in FLAGS.items())
            and bundle.get('autostart') == [1,2] and bundle.get('inputDiagnostics') is False
            and bundle.get('diagnosticProfile') is None and bundle.get('faultInjection') is None
            and bundle.get('isolationProfile') == 'source-v2', 'closed developer bundle differs')
    for key in ('strictProbeSha256','probeBuildReceiptSha256','probeSourceFreezeSha256','hostProvenance'):
        require(bundle.get(key) == review[key], 'bundle provenance differs: ' + key)
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

def _identity(instance):
    _platform(); instance = _canonical(instance)
    require(instance.name == INSTANCE, 'fixed developer instance filename required')
    root = _workspace(instance.parent)
    raw = _bytes(instance); value = strict_json(raw)
    require(type(value) is dict and set(value) == IDENTITY_KEYS
        and type(value['schemaVersion']) is int and value['schemaVersion'] == 1 and value['profile'] == PROFILE
        and _uuid(value['generation']) and isinstance(value['nonce'],str)
        and re.fullmatch('[0-9a-f]{32}',value['nonce']) is not None
        and _hash(value['packManifestSha256']) and _hash(value['bundleSha256'])
        and value['diskManifest'] == DISK_MANIFEST and _uuid(value['diskUuid'])
        and value['diskSerial'] == 'MINGDATA'+value['diskUuid'].replace('-','')[:12]
        and all(value[key] is flag for key,flag in FLAGS.items()), 'closed developer identity differs')
    require(_bytes(root/'developer-instance.sha256') == (_sha(raw)+'\n').encode(), 'immutable instance seal differs')
    _private(root/'attempts', directory=True)
    return root, value, _sha(raw)

def init(pack, manifest_sha256, work_root):
    admitted = _admit(pack, manifest_sha256)
    root = _workspace(work_root, create=True)
    # O_EXCL at every step; partial initialization is retained and cannot be resumed.
    disk = _create_disk(root, 'developer-128', 128)
    identity = dict(schemaVersion=1, profile=PROFILE, generation=str(uuid.uuid4()), nonce=uuid.uuid4().hex,
        packManifestSha256=manifest_sha256, bundleSha256=admitted['bundleSha256'], diskManifest=DISK_MANIFEST,
        diskUuid=disk['uuid'], diskSerial=disk['serial'], **FLAGS)
    _disk(root, identity)
    (root/'attempts').mkdir(mode=0o700)
    _write(root/'developer-instance.lock', b'')
    _write(root/INSTANCE, identity)
    _write(root/'developer-instance.sha256', (_sha(_bytes(root/INSTANCE))+'\n').encode())
    return dict(schemaVersion=1,status='developer-instance-initialized',instance=str(root/INSTANCE),
        generation=identity['generation'],diskUuid=identity['diskUuid'],guestBooted=False,**FLAGS)

def _disk_lock(root):
    key = hashlib.sha256(os.fsencode(_canonical(root/DISK_IMAGE))).hexdigest()
    return _runtime_root()/('disk-'+key+'.lock')

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

def _history(root, identity, identity_sha):
    """Read-only audit: incomplete, malformed or modified history is locked."""
    reports=[]
    try:
        require(not os.path.lexists(root/'failure-latch.json'), 'failure latch exists')
        names=sorted(p.name for p in (root/'attempts').iterdir())
        require(len(names) <= 9999 and names == ['%04d'%n for n in range(1,len(names)+1)], 'attempt sequence differs')
        for index,name in enumerate(names,1):
            directory=root/'attempts'/name; _private(directory,directory=True)
            attempt_raw=_bytes(directory/'attempt.json'); attempt=strict_json(attempt_raw)
            require(type(attempt) is dict and set(attempt) == {'schemaVersion','profile','index','nonce','generation',
                'instanceSha256','packManifestSha256','bundleSha256','output','command','budgets',*FLAGS}
                and attempt['schemaVersion'] == 1 and type(attempt['schemaVersion']) is int
                and type(attempt['index']) is int and attempt['index'] == index and attempt['profile'] == PROFILE
                and isinstance(attempt['nonce'],str) and re.fullmatch('[0-9a-f]{32}',attempt['nonce'])
                and attempt['instanceSha256'] == identity_sha and attempt['generation'] == identity['generation']
                and attempt['packManifestSha256'] == identity['packManifestSha256']
                and attempt['bundleSha256'] == identity['bundleSha256'] and attempt['budgets'] == BUDGETS
                and all(attempt[key] is value for key,value in FLAGS.items()), 'attempt identity differs')
            output=_output(attempt['output'],index)
            command=attempt['command']
            require(type(command) is list and len(command)==19 and all(isinstance(x,str) for x in command),
                    'fixed attempted VM command structure differs')
            prior_bundle=_canonical(command[6])
            require(prior_bundle.name=='bundle' and command == _command(prior_bundle.parent,root,output,_runtime_root()),
                    'fixed attempted VM command differs')
            result=_json(directory/'result.json')
            require(type(result) is dict and set(result) == {'schemaVersion','profile','index','status','instanceSha256',
                'attemptSha256','reportSha256','execution','error',*FLAGS}
                and result['schemaVersion'] == 1 and type(result['schemaVersion']) is int
                and result['profile'] == PROFILE and type(result['index']) is int and result['index'] == index
                and result['status'] == 'normal' and result['error'] is None and result['instanceSha256'] == identity_sha
                and result['attemptSha256'] == _sha(attempt_raw) and all(result[key] is value for key,value in FLAGS.items()),
                'failed, incomplete or mismatched attempt result')
            _execution_ok(result['execution'])
            raw=_bytes(output/'result.json')
            require(result['reportSha256'] == _sha(raw), 'retained VM result changed')
            report=_normal(strict_json(raw),identity,index,reports[-1] if reports else None)
            _new_session(report,reports);reports.append(report)
        return reports,None
    except (OSError,ValueError,KeyError,TypeError) as error:
        return reports,str(error)

def inspect(instance):
    root,identity,identity_sha=_identity(instance)
    with _lock(root/'developer-instance.lock',readonly=True), _lock(_disk_lock(root),readonly=True):
        _disk(root,identity)
        reports,error=_history(root,identity,identity_sha)
        return dict(schemaVersion=1,status='developer-instance-inspected',state='locked' if error else 'ready',
            reason=error,generation=identity['generation'],diskUuid=identity['diskUuid'],normalBoots=len(reports),
            guestBooted=False,**FLAGS)

def _command(pack, root, output, runtime):
    # Source is trusted installation code; no bundled launcher is executed.
    # -I intentionally ignores cwd/PYTHONPATH/PYTHONHOME. Its removed script
    # directory is restored explicitly for this one trusted local installation.
    bootstrap=('import runpy,sys;sys.path.insert(0,'+repr(str(TRUSTED_BOOT))+
               ');runpy.run_path('+repr(str(TRUSTED_BOOT/'vm.py'))+",run_name='__main__')")
    return [sys.executable,'-I','-B','-c',bootstrap,'--bundle',str(pack/'bundle'),
        '--review',str(pack/'bundle/review.json'),'--output',str(output),'--timeout','900',
        '--work-root',str(root),'--runtime-root',str(runtime),'--data-manifest',DISK_MANIFEST]

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

def start(pack, manifest_sha256, instance, output, runtime_root):
    admitted=_admit(pack,manifest_sha256)
    root,identity,identity_sha=_identity(instance)
    runtime=_runtime(runtime_root)
    with _lock(root/'developer-instance.lock'):
        reports,error=_history(root,identity,identity_sha)
        if error:
            _latch(root,identity_sha,error);raise ValueError('Developer VM: locked generation: '+error)
        try:
            require(identity['packManifestSha256']==manifest_sha256 and identity['bundleSha256']==admitted['bundleSha256'],
                    'pack changed for immutable generation')
            _disk(root,identity)
        except (OSError,ValueError) as error:
            _latch(root,identity_sha,error);raise
        index=len(reports)+1
        require(index<=9999,'attempt limit reached')
        output=_output(output,index,fresh=True)
        # Probe any existing same-image lease without creating or holding it
        # across the child, which must take the same lease itself.
        disk_lock=_disk_lock(root)
        if disk_lock.exists():
            with _lock(disk_lock,readonly=True): pass
        directory=root/'attempts'/('%04d'%index);directory.mkdir(mode=0o700)
        attempt=dict(schemaVersion=1,profile=PROFILE,index=index,nonce=uuid.uuid4().hex,generation=identity['generation'],
            instanceSha256=identity_sha,packManifestSha256=manifest_sha256,bundleSha256=admitted['bundleSha256'],
            output=str(output),command=_command(admitted['pack'],root,output,runtime),budgets=dict(BUDGETS),**FLAGS)
        _write(directory/'attempt.json',attempt)
        execution=None;report_sha=None
        try:
            execution=_execute_vm(attempt['command'],directory);_execution_ok(execution)
            raw=_bytes(output/'result.json');report_sha=_sha(raw)
            report=_normal(strict_json(raw),identity,index,reports[-1] if reports else None)
            _new_session(report,reports)
            result=dict(schemaVersion=1,profile=PROFILE,index=index,status='normal',instanceSha256=identity_sha,
                attemptSha256=_sha(_bytes(directory/'attempt.json')),reportSha256=report_sha,execution=execution,error=None,**FLAGS)
            _write(directory/'result.json',result)
        except BaseException as error:
            # A crash before either write leaves an incomplete append-only attempt;
            # history refuses it even if a separate failure latch was never written.
            _latch(root,identity_sha,error,index)
            if not (directory/'result.json').exists():
                _write(directory/'result.json',dict(schemaVersion=1,profile=PROFILE,index=index,status='failed',
                    instanceSha256=identity_sha,attemptSha256=_sha(_bytes(directory/'attempt.json')),
                    reportSha256=report_sha,execution=execution,error=repr(error)[:4096],**FLAGS))
            raise ValueError('Developer VM: session failed; generation locked: '+str(error)) from error
        return dict(schemaVersion=1,status='developer-session-normal',normalBoots=index,generation=identity['generation'],
            output=str(output),bootId=report['bootId'],sessionId=report['lastState']['sessionId'],
            developerNormalDiskReopenVerified=index>=2,**FLAGS)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest='action',required=True)
    for name in ('verify','init','start','inspect'):
        p=actions.add_parser(name)
        if name!='inspect':
            p.add_argument('--pack',type=Path,required=True);p.add_argument('--manifest-sha256',required=True)
        if name=='init':p.add_argument('--work-root',type=Path,required=True)
        if name in ('start','inspect'):p.add_argument('--instance',type=Path,required=True)
        if name=='start':
            p.add_argument('--output',type=Path,required=True);p.add_argument('--runtime-root',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        if args.action=='verify':result=verify(args.pack,args.manifest_sha256)
        elif args.action=='init':result=init(args.pack,args.manifest_sha256,args.work_root)
        elif args.action=='inspect':result=inspect(args.instance)
        else:result=start(args.pack,args.manifest_sha256,args.instance,args.output,args.runtime_root)
        print(json.dumps(result,sort_keys=True,allow_nan=False));return 0
    except (OSError,ValueError,RecursionError) as error:
        print(str(error),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
