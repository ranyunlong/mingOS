"""Independent S3 image executable observations and regular-byte mirrors.

Expected executable pins come from an admitted fixed-image observation, never a
package report. A second independent container and a reopened regular mirror
are separate checks. This private evidence does not qualify S2 or S4.
"""
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import secrets

import source_build_seal as seal


def _require(condition, message):
    if not condition: raise ValueError(message)


def _logical(path):
    _require(type(path) is str and path.startswith('/') and not path.startswith('//'), 'absolute image path required')
    seal._relative(path[1:])
    return path


def _image_file(root, logical):
    """Resolve image-root links without following a link into the host root."""
    _logical(logical)
    root = Path(root)
    _require(root.is_dir() and not root.is_symlink(), 'regular image/mirror root required')
    resolved, pending, hops = [], logical.split('/')[1:], 0
    while pending:
        component = pending.pop(0)
        if component in ('', '.'): continue
        if component == '..':
            _require(bool(resolved), 'executable symlink escapes image root')
            resolved.pop(); continue
        seal._relative(component)
        candidate = root.joinpath(*resolved, component)
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            hops += 1; _require(hops <= 40, 'executable symlink loop')
            target = os.readlink(candidate)
            if target.startswith('/'): resolved = []
            pending = target.split('/') + pending
        else:
            if pending: _require(stat.S_ISDIR(info.st_mode), 'image path ancestor is not a directory')
            resolved.append(component)
    path = root.joinpath(*resolved)
    _require(stat.S_ISREG(path.lstat().st_mode), 'resolved image executable must be regular')
    return '/' + '/'.join(resolved), path


def _bytes(path):
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'single-link regular tool bytes required')
    digest = hashlib.sha256(); size = 0
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        _require((before.st_dev, before.st_ino, before.st_size) == (opened.st_dev, opened.st_ino, opened.st_size), 'tool changed before open')
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block: break
            size += len(block); _require(size <= opened.st_size, 'tool grew during hashing'); digest.update(block)
        _require(seal._stamp(opened) == seal._stamp(os.fstat(fd)) and seal._stamp(before) == seal._stamp(path.lstat())
                 and size == opened.st_size, 'tool bytes/identity changed during hash')
        return {'bytes': size, 'sha256': digest.hexdigest(), 'mode': stat.S_IMODE(opened.st_mode)}
    finally: os.close(fd)


def _pin(actual, expected):
    _require(type(expected) is dict, 'independent byte pin required')
    for key in ('bytes', 'sha256'):
        _require(actual.get(key) == expected.get(key) and type(actual.get(key)) is type(expected.get(key)), 'independent tool byte pin differs: ' + key)
    seal._integer(expected['bytes']); _require(expected['bytes'] >= 0, 'nonnegative tool size required')
    _require(type(expected['sha256']) is str and seal._SHA.fullmatch(expected['sha256']), 'tool SHA256 required')
    if 'mode' in expected:
        seal._integer(expected['mode'])
        if os.name == 'posix': _require(actual['mode'] == expected['mode'], 'tool POSIX mode differs')


def verify(observation: dict, tools_root: Path, expected: dict) -> dict:
    """Reopen actual image-root bytes or an explicitly regular resolved mirror.

    mirrorLayout is trusted input, never read from a candidate observation. In
    image-root mode logical symlinks are independently resolved. Mirror mode
    contains canonical resolved regular files only; the two actual workers bind
    logical dispatch/resolution, not reconstructed mirror symlinks.
    """
    _require(type(observation) is dict and type(expected) is dict, 'tool observation/expectation objects required')
    _require(observation.get('image') == expected.get('image') and re.fullmatch(r'sha256:[0-9a-f]{64}', expected.get('image', '')), 'fixed image identity differs')
    _require(observation.get('formalQualification') is False and observation.get('finalSystemAccepted') is False, 'tool observation scope flags differ')
    mirror = expected.get('mirrorLayout') == 'resolved-regular-files'
    _require(expected.get('mirrorLayout') in (None, 'resolved-regular-files'), 'unknown tool mirror layout')
    for kind in ('executables', 'lockedFiles'):
        wanted, observed = expected.get(kind), observation.get(kind)
        _require(type(wanted) is dict and type(observed) is dict and set(wanted) == set(observed), 'complete independent tool/lock membership differs')
        if kind == 'executables': _require(bool(wanted), 'nonempty actual executable observation required')
        for name, pin in wanted.items():
            row = observed[name]
            _require(type(row) is dict and type(pin) is dict, 'typed tool records required')
            if kind == 'executables':
                for key in ('path', 'resolvedPath', 'version'):
                    _require(type(pin.get(key)) is str and bool(pin[key]) and row.get(key) == pin[key], 'actual executable identity differs: ' + key)
                logical = _logical(pin['resolvedPath'] if mirror else pin['path'])
            else: logical = _logical(name)
            resolved, path = _image_file(tools_root, logical)
            _require(resolved == (pin['resolvedPath'] if kind == 'executables' else logical), 'actual executable resolution differs')
            if mirror: _require(not path.is_symlink(), 'mirror contains executable symlink')
            _pin(row, pin); _pin(_bytes(path), pin)
            if kind == 'executables' and pin.get('launcher'):
                _require(row.get('launcher') == pin['launcher'], 'Rust launcher/dispatch identity differs')
                launcher = pin['launcher']; resolved_launcher, launcher_path = _image_file(tools_root, _logical(launcher['resolvedPath']))
                _require(resolved_launcher == launcher['resolvedPath'], 'mirror launcher resolution differs')
                _pin(_bytes(launcher_path), launcher)
    node, java = expected['executables'].get('node'), expected['executables'].get('java')
    seen = set()
    for package in observation.get('packageToolchains', []):
        _require(type(package) is dict and type(package.get('label')) is str and package['label'] not in seen, 'unique package toolchain label required')
        seen.add(package['label']); report = package.get('toolchain')
        _require(type(report) is dict and node is not None and java is not None, 'package requires independently observed Node and Java')
        for name, pin in (('node', node), ('java', java)):
            _require(report.get(name + 'Path') == pin['path'] and report.get(name + 'Sha256') == pin['sha256'], 'package executable self-report differs from independent actual bytes')
        _require(report.get('node') == node['version'] and report.get('gradle') == '8.14.5', 'package tool version differs')
        archive = expected['lockedFiles'].get('/deps/gradle/mingos-verified-gradle-8.14.5-bin.zip')
        _require(archive is not None and report.get('gradleDistributionSha256') == archive['sha256'], 'package Gradle distribution differs from locked bytes')
    return {'schemaVersion': 1, 'status': 'toolchain-bytes-verified', 'image': expected['image'],
            'executables': copy.deepcopy(observation['executables']), 'lockedFiles': copy.deepcopy(observation['lockedFiles']),
            'formalQualification': False, 'finalSystemAccepted': False}


class _ImageEngine:
    """Retained fixed-image workers; exact command and final state are evidence."""
    def __init__(self, evidence, nonce, docker, image, entrypoint, *, timeout):
        _require(type(docker) is str and docker.startswith('/') and re.fullmatch(r'sha256:[0-9a-f]{64}', image), 'absolute wrapper/fixed image required')
        _require(entrypoint in ('node', 'python3') and type(timeout) is int and timeout > 0, 'fixed interpreter/positive timeout required')
        self.evidence, self.nonce, self.docker, self.image = evidence, nonce, docker, image
        self.entrypoint, self.timeout = entrypoint, timeout
        self.containers = evidence.receipt.setdefault('containers', [])

    def call(self, args, label, timeout=None):
        return self.evidence.run(label, [self.docker, *args], min(timeout or self.timeout, self.timeout))

    def inspect(self, cid, label):
        value = json.loads(self.call(['inspect', cid], label), object_pairs_hook=seal._pairs)
        _require(type(value) is list and len(value) == 1 and value[0].get('Id') == cid, 'exact actual worker inspection required')
        return value[0]

    def container(self, label, mounts, command):
        import developer_cache as cache
        _require(all(type(m) is dict and m.get('readonly') is True for m in mounts), 'observer/replay inputs must all be RO')
        argv = ['create', '--pull', 'never', '--init', '--name', 'mingos-s3-' + self.nonce + '-' + label,
            '--network', 'none', '--read-only', '--user', '65534:65534', '--cpus', '2', '--memory', '4g',
            '--pids-limit', '256', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--label', 'org.mingos.observer.nonce=' + self.nonce, '--label', 'org.mingos.observer.role=' + label]
        for mount in mounts: argv += ['--mount', cache.mount_text(mount)]
        argv += ['--entrypoint', self.entrypoint, self.image, *command]
        cid = self.call(argv, label + '-create').decode().strip()
        _require(re.fullmatch('[0-9a-f]{64}', cid), 'exact created worker CID required')
        record = {'cid': cid, 'role': label, 'image': self.image, 'command': [self.entrypoint, *command]}
        self.containers.append(record); self.evidence.snapshot('allocated-' + label)
        def check(item, finished=False):
            config, host = item.get('Config', {}), item.get('HostConfig', {})
            _require(item.get('Id') == cid and item.get('Image') == self.image, 'actual worker image/id differs')
            _require(config.get('User') == '65534:65534' and config.get('Entrypoint') == [self.entrypoint]
                     and config.get('Cmd') == command, 'worker command/user differs')
            labels = config.get('Labels') or {}
            _require(labels.get('org.mingos.observer.nonce') == self.nonce and labels.get('org.mingos.observer.role') == label, 'worker ownership differs')
            for key, want in {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
                    'NanoCpus': 2000000000, 'Memory': 4294967296, 'PidsLimit': 256,
                    'Tmpfs': {'/tmp': 'rw,nosuid,nodev,size=64m,mode=1777'}, 'AutoRemove': False,
                    'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}}.items():
                _require(host.get(key) == want, 'worker isolation differs: ' + key)
            _require([value.removeprefix('CAP_') for value in host.get('CapDrop', [])] == ['ALL']
                     and not host.get('CapAdd') and host.get('SecurityOpt') in (['no-new-privileges'], ['no-new-privileges:true']), 'worker capabilities/NNP differ')
            for key in ('Binds', 'VolumesFrom', 'Devices', 'DeviceRequests', 'Links', 'PortBindings', 'ExtraHosts'):
                _require(host.get(key) in (None, [], {}), 'extra worker attachment: ' + key)
            requested, effective = host.get('Mounts') or [], item.get('Mounts') or []
            _require(len(requested) == len(effective) == len(mounts), 'worker mount count differs')
            for mount in mounts:
                request = [m for m in requested if m.get('Target') == mount['target']]
                active = [m for m in effective if m.get('Destination') == mount['target']]
                _require(len(request) == len(active) == 1, 'worker mount destination differs')
                request, active = request[0], active[0]
                _require(request.get('Type') == active.get('Type') == mount['type'] and request.get('ReadOnly') is True
                         and active.get('RW') is False, 'worker effective RO mount differs')
                if mount['type'] == 'volume':
                    _require(request.get('Source') == active.get('Name') == mount['source'] and request.get('VolumeOptions') ==
                             {'NoCopy': True, **({'Subpath': mount['subpath']} if mount.get('subpath') else {})}, 'worker volume/subpath differs')
                else: _require(request.get('Source') == active.get('Source') == str(mount['source']), 'worker bind source differs')
            if finished:
                state = item.get('State', {})
                _require(state.get('Running') is False and type(state.get('ExitCode')) is int and state['ExitCode'] == 0
                         and state.get('OOMKilled') is False, 'worker failed/OOM/running')
        check(self.inspect(cid, label + '-inspect-before'))
        failure = None; raw = b''
        try: raw = self.call(['start', '--attach', cid], label + '-run')
        except BaseException as error: failure = error
        after = self.inspect(cid, label + '-inspect-after'); record['finalInspection'] = after; check(after)
        running = after.get('State', {}).get('Running')
        if running:
            try:
                check(self.inspect(cid, label + '-inspect-before-stop'))
                self.call(['stop', '--time', '10', cid], label + '-stop-owned', 60)
            finally:
                after = self.inspect(cid, label + '-inspect-after-stop'); record['finalInspection'] = after; check(after)
            _require(after.get('State', {}).get('Running') is False, 'owned observer remains running')
        if failure is not None: raise failure
        _require(not running, 'attached observer unexpectedly remained running'); check(after, True)
        return cid, raw


def _mirror_space(engine, root, byte_count):
    budget = getattr(engine, 'mirror_budget', None)
    if type(budget) is not dict: return  # Only the isolated copy primitive lacks a phase budget.
    _require(type(byte_count) is int and byte_count >= 0, 'nonnegative mirror byte count required')
    used = budget.setdefault('usedBytes', 0)
    _require(used + byte_count <= budget['maxBytes'], 'retained executable/lock mirror exceeds pinned output ceiling')
    if 'aggregateRoot' in budget:
        import source_build_guest as guest
        usage = guest._payload_usage(Path(budget['aggregateRoot']))
        _require(usage['bytes'] + byte_count <= budget['aggregateMaxBytes']
                 and usage['files'] + usage['directories'] + 1 <= budget['maxEntries'], 'aggregate retained mirrors/requests/logs exceed pinned phase allowance')
    info = os.statvfs(root)
    _require(info.f_bavail * info.f_frsize >= byte_count + budget['reserveBytes'], 'mirror copy would consume Linux reserve')
    _require(os.stat(root).st_dev == budget['device'], 'mirror copy is not on original ext4 device')


def _copy_observed_tools(engine, cid, observation, root):
    """Copy canonical resolved bytes, never a logical executable symlink."""
    root = Path(root); root.mkdir(mode=0o700)
    files = {}
    for row in observation['executables'].values():
        if row['resolvedPath'] in files: _pin(files[row['resolvedPath']], row)
        files[_logical(row['resolvedPath'])] = row
        if row.get('launcher'):
            launcher = row['launcher']
            if launcher['resolvedPath'] in files: _pin(files[launcher['resolvedPath']], launcher)
            files[_logical(launcher['resolvedPath'])] = launcher
    for name, row in observation['lockedFiles'].items():
        if name in files: _pin(files[name], row)
        files[_logical(name)] = row
    copied = {}
    for index, (logical, row) in enumerate(sorted(files.items())):
        _mirror_space(engine, root, row['bytes'])
        destination = root / logical[1:]; destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _require(not os.path.lexists(destination), 'new absent executable mirror member required')
        engine.call(['cp', cid + ':' + logical, str(destination)], 'mirror-copy-' + str(index), 1800)
        actual = _bytes(destination); _pin(actual, row); copied[logical] = actual
        budget = getattr(engine, 'mirror_budget', None)
        if type(budget) is dict:
            budget['usedBytes'] += actual['bytes']; budget['entries'] = budget.get('entries', 0) + 1
            engine.evidence.snapshot('retained-mirror-member')
            _mirror_space(engine, root, 0)
    return copied


# This literal is frozen as part of this module. Rust/Tools contain Python;
# Kotlin deliberately uses separately frozen Node glue instead.
_PY_OBSERVER = r'''import hashlib,json,os,shutil,stat,subprocess,sys
with open(sys.argv[1],'rb') as request_file:
 raw=request_file.read(33554433); assert len(raw)<=33554432
r=json.loads(raw); exe={}; optional={}
def stable(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_uid,s.st_gid,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def pin(path):
 p=os.path.realpath(path); before=os.stat(p); assert stat.S_ISREG(before.st_mode) and before.st_nlink==1
 h=hashlib.sha256(); n=0
 with open(p,'rb') as f:
  opened=os.fstat(f.fileno()); assert (before.st_dev,before.st_ino)==(opened.st_dev,opened.st_ino)
  while True:
   b=f.read(1048576)
   if not b:break
   n+=len(b); assert n<=opened.st_size; h.update(b)
  assert stable(os.fstat(f.fileno()))==stable(opened)
 assert stable(os.stat(p))==stable(before) and n==before.st_size
 return {'resolvedPath':p,'bytes':n,'sha256':h.hexdigest(),'mode':stat.S_IMODE(before.st_mode)}
for name,config in r['executables'].items():
 logical=shutil.which(name); assert logical, 'required executable absent: '+name
 launcher=pin(logical); actual=launcher['resolvedPath']
 if name in ('rustc','cargo') and shutil.which('rustup'):
  selection=subprocess.run(['rustup','which',name],check=True,capture_output=True,timeout=15)
  actual=selection.stdout.decode().strip(); assert actual.startswith('/')
 version=subprocess.run([actual,'--version'],check=True,capture_output=True,timeout=15)
 row={'path':logical,**pin(actual),'version':(version.stdout+version.stderr).decode().strip()}
 if actual!=launcher['resolvedPath']:row['launcher']=launcher
 exe[name]=row
for name in r.get('optional',[]):
 p=shutil.which(name); optional[name]={'available':p is not None,'path':p}
locks={}
for path in r['lockedFiles']:
 row=pin(path); assert row.pop('resolvedPath')==path; locks[path]=row
roots=['/',*r['readonly']]
eff={'uid':os.getuid(),'gid':os.getgid(),'interfaces':sorted(os.listdir('/sys/class/net')),
 'status':open('/proc/self/status').read(),'mountinfo':open('/proc/self/mountinfo').read(),
 'memoryMax':open('/sys/fs/cgroup/memory.max').read().strip(),'pidsMax':open('/sys/fs/cgroup/pids.max').read().strip(),
 'cpuMax':open('/sys/fs/cgroup/cpu.max').read().strip(),'readOnly':{p:bool(os.statvfs(p).f_flag&os.ST_RDONLY) for p in roots}}
t=os.statvfs('/tmp'); assert t.f_blocks*t.f_frsize<=67108864 and t.f_flag&os.ST_NOSUID and t.f_flag&os.ST_NODEV
print(json.dumps({'schemaVersion':1,'image':r['image'],'executables':exe,'lockedFiles':locks,'optional':optional,
 'effective':eff,'formalQualification':False,'finalSystemAccepted':False}))
'''


def observe(image: str, expected: dict, output: Path) -> dict:
    """Observe twice in independent fixed-image RO workers and reopen mirrors.

    Private admitted-phase callers supply a fixed executable wrapper, frozen
    harness and immutable source/cache mounts. These inputs confer no admission.
    """
    import developer_cache as cache
    import development_image_migration as migration
    import source_build_guest as guest
    role = expected.get('imageRole') if type(expected) is dict else None
    _require(role in migration.FIXED and image == migration.FIXED[role]['imageId'] == expected.get('image'), 'admitted fixed image role required')
    _require(type(expected.get('executables')) is dict and bool(expected['executables']) and type(expected.get('lockedFiles')) is dict,
             'independent executable and locked closure expectations required')
    docker = expected.get('docker'); _require(type(docker) is str and docker.startswith('/'), 'absolute admitted wrapper required')
    mounts = expected.get('mounts'); _require(type(mounts) is list and all(m.get('readonly') is True for m in mounts), 'RO observation mounts required')
    budget = expected.get('mirrorBudget'); _require(type(budget) is dict and all(type(budget.get(key)) is int and budget[key] > 0 for key in ('maxBytes', 'reserveBytes', 'device')), 'explicit retained mirror budget required')
    output = seal._absolute(output); _require(not os.path.lexists(output), 'new absent observation output required')
    state = {'schemaVersion': 1, 'status': 'started', 'image': image, 'role': role, 'nonce': secrets.token_hex(16),
             'operations': [], 'containers': [], 'mirrorBudget': copy.deepcopy(budget),
             'formalQualification': False, 'finalSystemAccepted': False, 'resourcesPreserved': True}
    evidence = cache.original.Evidence(output, state)
    engine = _ImageEngine(evidence, state['nonce'], docker, image, 'node' if role == 'kotlin' else 'python3', timeout=expected['timeoutSeconds'])
    volume_registry = cache.Engine(evidence, state['nonce'], docker)
    engine.mirror_budget = state['mirrorBudget']
    request = {'image': image, 'executables': expected['executables'], 'lockedFiles': expected['lockedFiles'],
               'optional': expected.get('optional', []), 'readonly': [m['target'] for m in mounts] + ['/request/request.json']}
    try:
        encoded = json.dumps(request, separators=(',', ':'), sort_keys=True).encode()
        _require(len(encoded) <= 32 * 1024**2, 'complete tool observation request exceeds bound')
        _mirror_space(engine, output, len(encoded))
        request_path = output / 'request.json'
        cache.original._write_exclusive(request_path, encoded); request_path.chmod(0o444)
        request_pin = seal.file_pin(request_path)
    except BaseException as error:
        state.update(status='failed', error=repr(error))
        cache.original._write_exclusive(output / 'result.json', cache.content.encoded(state)); evidence.snapshot('failed')
        raise
    mounts = [*mounts, cache.mount('bind', request_path, '/request/request.json', True)]
    # Request mount is independently checked by the actual worker as well as
    # outer inspect; its path is fixed and not a dependency-subset selector.
    command = ['/harness/scripts/boot/source_build_replay.mjs', 'observe', '/request/request.json'] if role == 'kotlin' else ['-I', '-B', '-c', _PY_OBSERVER, '/request/request.json']
    try:
        image_record = json.loads(engine.call(['image', 'inspect', image], 'fixed-image-inspect'))
        _require(type(image_record) is list and len(image_record) == 1 and image_record[0].get('Id') == image
                 and image_record[0].get('Os') == 'linux' and image_record[0].get('Architecture') == 'amd64'
                 and image_record[0].get('RootFS', {}).get('Layers') and not image_record[0].get('Config', {}).get('Volumes'), 'fixed actual image inspection differs')
        state['fixedImage'] = image_record[0]
        def worker(label):
            _require(seal.file_pin(request_path) == request_pin, 'frozen observation request changed')
            for index, name in enumerate(sorted({mount['source'] for mount in mounts if mount['type'] == 'volume'})):
                volume_registry.register(name, label + '-volume-before-' + str(index))
            cid, raw = engine.container(label, mounts, command)
            value = json.loads(raw, object_pairs_hook=seal._pairs)
            guest._check_effective_worker(value['effective'], ['/', *request['readonly']])
            _require(seal.file_pin(request_path) == request_pin, 'observation request changed during worker')
            _require(value.get('image') == image and set(value.get('executables', {})) == set(expected['executables'])
                     and set(value.get('lockedFiles', {})) == set(expected['lockedFiles']), 'actual image executable/lock membership differs')
            for logical, pin in expected['lockedFiles'].items():
                _require(value['lockedFiles'][logical]['sha256'] == pin['sha256'], 'locked compiler dependency bytes differ')
                if 'bytes' in pin: _pin(value['lockedFiles'][logical], pin)
            for name, config in expected['executables'].items():
                actual = value['executables'][name]
                if config.get('path'): _require(actual['path'] == config['path'], 'required executable path differs')
                if config.get('versionPattern'): _require(re.search(config['versionPattern'], actual['version']), 'required executable version differs')
            return cid, value
        first_cid, first = worker('observe-first'); state['firstObservation'] = first; evidence.snapshot('first-observed')
        state['mirrorFiles'] = _copy_observed_tools(engine, first_cid, first, output / 'tools-root')
        second_cid, second = worker('observe-second'); state['secondObservation'] = second
        for key in ('image', 'executables', 'lockedFiles', 'optional'):
            _require(first.get(key) == second.get(key), 'independent actual image observation drift: ' + key)
        trusted = {'image': image, 'executables': first['executables'], 'lockedFiles': first['lockedFiles'], 'mirrorLayout': 'resolved-regular-files'}
        state['verified'] = verify(second, output / 'tools-root', trusted)
        state.update(status='independent-image-toolchain-bytes-observed', actualBytes=first,
                     toolsRoot=str(output / 'tools-root'), secondContainerId=second_cid)
        cache.original._write_exclusive(output / 'result.json', cache.content.encoded(state)); evidence.snapshot('complete')
        return state
    except BaseException as error:
        state.update(status='failed', error=repr(error))
        cache.original._write_exclusive(output / 'result.json', cache.content.encoded(state)); evidence.snapshot('failed')
        raise
