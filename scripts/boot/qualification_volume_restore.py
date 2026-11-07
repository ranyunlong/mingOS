"""Host coordinator: fresh anonymous cache restore with retained evidence.

No import-time actions. This module has no resource-removal operation. A failed
stage stops orchestration; commands, full logs, private copies and containers
are retained. Success remains below formal system qualification.
"""
import argparse
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys

import qualification_volume_archive as archive
import qualification_volume_plan as plan
import qualification_volume_worker as worker

EXPECTED_IMAGE_IDS = {
    'rust': 'sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130',
    'tools': 'sha256:2440560d810a834637cfeeb1057f7f471c40caa8e532645e00e063cbc8ec639a',
    'openssl': 'sha256:3646133f4b4d2e007dd3eee28d9db6336c29f6ccb31b9f8a49b88bc0a70051be',
}
MAX_LOG_JSON = worker.MAX_RECEIPT
BOOTSTRAP = "import sys;sys.path.insert(0,'/helper');from qualification_volume_worker import main;raise SystemExit(main())"


class RestoreError(ValueError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


def _checked_path(path, regular=None):
    path = Path(path).absolute()
    for node in (*reversed(path.parents), path):
        info = node.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('path contains a link/reparse point')
    info = path.lstat()
    if regular is True and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ValueError('regular singlelink asset required')
    if regular is False and not stat.S_ISDIR(info.st_mode):
        raise ValueError('literal directory required')
    return path


def _asset(root, relative):
    return _checked_path(root.joinpath(*archive.safe_path(relative).split('/')), regular=True)


def _stream_identity(info):
    return (info.st_dev, info.st_ino, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _opened_regular(source):
    info = os.fstat(source.fileno())
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or getattr(info, 'st_file_attributes', 0) & 0x400):
        raise ValueError('opened regular singlelink source required')
    return info


def _directory_sync(path):
    # File bytes are fsynced on every platform. Windows does not expose a
    # portable stdlib directory fsync; the receipt says so explicitly.
    if os.name == 'nt':
        return False
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(fd)
        return True
    finally:
        os.close(fd)


def _write_exclusive(path, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags, 0o600)
    try:
        worker._write_all(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    _directory_sync(Path(path).parent)


def private_copy_archive(source, output_root, expected_sha256):
    """Retain one exclusive copy; validate both source and opened copy bytes."""
    if not isinstance(expected_sha256, str) or not archive.HEX.fullmatch(expected_sha256):
        raise ValueError('exact lowercase archive SHA-256 required')
    output_root = _checked_path(output_root, regular=False)
    before = _opened_regular(source)
    path = output_root / 'archive-privatecopy.tar'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0), 0o600)
    digest, count = hashlib.sha256(), 0
    try:
        source.seek(0)
        while True:
            raw = source.read(1024 ** 2)
            if not raw:
                break
            count += len(raw)
            if count > archive.MAX_ARCHIVE:
                raise ValueError('archive byte limit exceeded')
            worker._write_all(fd, raw)
            digest.update(raw)
        os.fsync(fd)
        copied_identity = _stream_identity(os.fstat(fd))
    finally:
        os.fsync(fd)
        os.close(fd)
        _directory_sync(output_root)
    if digest.hexdigest() != expected_sha256 or _stream_identity(_opened_regular(source)) != _stream_identity(before):
        raise ValueError('source archive changed or SHA-256 differs while freezing private copy')
    with _checked_path(path, regular=True).open('rb') as frozen:
        if _stream_identity(_opened_regular(frozen)) != copied_identity:
            raise ValueError('private archive identity changed before reopen')
        verified = archive.verify_volume_archive(frozen, expected_sha256)
        if _stream_identity(_opened_regular(frozen)) != copied_identity:
            raise ValueError('private archive identity changed during reopen')
    return path, verified


class Evidence:
    def __init__(self, root, receipt):
        self.root = Path(root).absolute()
        _checked_path(self.root.parent, regular=False)
        self.root.mkdir(mode=0o700)
        self.receipt, self.sequence = receipt, 0
        self.receipt['receiptRoot'] = str(self.root)
        self.receipt['hostDirectoryFsync'] = os.name != 'nt'
        self.snapshot('initial')

    def snapshot(self, label):
        self.sequence += 1
        path = self.root / ('receipt-%04d-%s.json' % (self.sequence, label))
        _write_exclusive(path, json.dumps(self.receipt, separators=(',', ':'), sort_keys=True, allow_nan=False).encode())
        return path

    def run(self, key, command, timeout, stdin=None, input_bytes=None):
        number = len(self.receipt['operations']) + 1
        outpath = self.root / ('operation-%03d.stdout.log' % number)
        errpath = self.root / ('operation-%03d.stderr.log' % number)
        op = {'key': key, 'command': command, 'exitCode': None, 'timedOut': False,
              'stdoutPath': str(outpath), 'stderrPath': str(errpath)}
        self.receipt['operations'].append(op)
        self.snapshot('before-command')
        failure, result = None, None
        with outpath.open('xb') as output, errpath.open('xb') as error:
            try:
                kwargs = {'input': input_bytes} if input_bytes is not None else {'stdin': stdin if stdin is not None else subprocess.DEVNULL}
                result = subprocess.run(command, stdout=output, stderr=error, timeout=timeout, check=False, **kwargs)
                op['exitCode'] = result.returncode
            except subprocess.TimeoutExpired as caught:
                op['timedOut'], op['error'], failure = True, str(caught), caught
            except OSError as caught:
                op['error'], failure = str(caught), caught
            finally:
                output.flush(); os.fsync(output.fileno())
                error.flush(); os.fsync(error.fileno())
        for label, path in (('stdout', outpath), ('stderr', errpath)):
            op[label + 'Bytes'] = path.stat().st_size
            with path.open('rb') as stream:
                preview = stream.read(65537)
            op[label] = preview[:65536].decode('utf-8', 'replace')
            op[label + 'Truncated'] = len(preview) > 65536
        self.snapshot('after-command')
        if failure is not None:
            raise ValueError('Docker command failed or timed out; full partial logs retained: ' + key) from failure
        if result.returncode != 0:
            raise ValueError('Docker command failed exit %s: %s' % (result.returncode, key))
        with outpath.open('rb') as stream:
            raw = stream.read(MAX_LOG_JSON + 1)
        if len(raw) > MAX_LOG_JSON:
            raise ValueError('Docker stdout exceeds JSON receipt limit; full log retained')
        return raw


def _outer_manifest(root, manifest):
    try:
        relative = Path(manifest).absolute().relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError('manifest must be inside AssetRoot') from error
    manifest = _asset(root, relative)
    with manifest.open('rb') as source:
        before = _opened_regular(source)
        raw = source.read(16385)
        if _stream_identity(_opened_regular(source)) != _stream_identity(before):
            raise ValueError('manifest changed while reading')
    if len(raw) > 16384:
        raise ValueError('outer manifest exceeds 16 KiB')
    value = archive.strict_json(raw)
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'images', 'volume'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(value['images'], list) or len(value['images']) != 3):
        raise ValueError('fixed restore manifest and volume required')
    seen = set()
    for entry in value['images']:
        if (not isinstance(entry, dict) or set(entry) != {'key', 'archive', 'archiveSha256', 'imageId'}
                or not isinstance(entry['key'], str) or entry['key'] not in EXPECTED_IMAGE_IDS
                or entry['key'] in seen or entry['imageId'] != EXPECTED_IMAGE_IDS[entry['key']]
                or not isinstance(entry['archiveSha256'], str) or not archive.HEX.fullmatch(entry['archiveSha256'])):
            raise ValueError('image differs from fixed qualification contract')
        seen.add(entry['key'])
    volume = value['volume']
    if (not isinstance(volume, dict) or set(volume) != {'name', 'archive', 'archiveSha256'}
            or not isinstance(volume['name'], str)
            or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}', volume['name'])
            or not isinstance(volume['archiveSha256'], str) or not archive.HEX.fullmatch(volume['archiveSha256'])):
        raise ValueError('strict volume provenance entry required')
    return value, raw, manifest


def _freeze_helpers(evidence):
    directory = evidence.root / 'helper-snapshot'
    if any(char in str(directory) for char in (',', '\n', '\r')):
        raise ValueError('helper snapshot path cannot be represented by Docker --mount')
    directory.mkdir(mode=0o700)
    result = {}
    for name in ('qualification_volume_archive.py', 'qualification_volume_worker.py'):
        path = _checked_path(Path(__file__).with_name(name), regular=True)
        with path.open('rb') as source:
            before = _opened_regular(source)
            raw = source.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024 or _stream_identity(_opened_regular(source)) != _stream_identity(before):
                raise ValueError('helper source changed or exceeds limit')
        compile(raw, name, 'exec')
        _write_exclusive(directory / name, raw)
        result[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    _directory_sync(directory)
    return directory, result


def _check_helpers(directory, expected):
    directory = _checked_path(directory, regular=False)
    if {path.name for path in directory.iterdir()} != set(expected):
        raise ValueError('private helper snapshot inventory changed')
    for name, record in expected.items():
        with _checked_path(directory / name, regular=True).open('rb') as source:
            before = _opened_regular(source)
            raw = source.read(512 * 1024 + 1)
            if (len(raw) != record['bytes'] or hashlib.sha256(raw).hexdigest() != record['sha256']
                    or _stream_identity(_opened_regular(source)) != _stream_identity(before)):
                raise ValueError('private helper snapshot bytes changed')


def worker_command(nonce, volume, helper_directory, readonly, archive_sha256):
    if (not isinstance(nonce, str) or not plan.HEX32.fullmatch(nonce)
            or not isinstance(volume, str) or not plan.HEX64.fullmatch(volume)
            or not isinstance(archive_sha256, str) or not archive.HEX.fullmatch(archive_sha256)):
        raise ValueError('exact worker nonce, anonymous volume and archive hash required')
    helper_directory = str(Path(helper_directory).absolute())
    if ',' in helper_directory or '\n' in helper_directory or '\r' in helper_directory:
        raise ValueError('helper snapshot path cannot be represented by Docker --mount')
    role = 'verify' if readonly else 'restore'
    volume_mount = 'type=volume,source=' + volume + ',target=/restore,volume-nocopy' + (',readonly' if readonly else '')
    command = ['create', '--pull', 'never', '-i', '--network', 'none', '--read-only',
        '--user', '65534:65534', '--cpus', '2', '--memory', '4g', '--pids-limit', '128',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--label', 'org.mingos.restore.nonce=' + nonce, '--label', 'org.mingos.restore.role=' + role,
        '--mount', volume_mount,
        '--mount', 'type=bind,source=' + helper_directory + ',target=/helper,readonly',
        '--entrypoint', 'python3', plan.HELPER_IMAGE, '-I', '-B', '-c', BOOTSTRAP,
        'verify-readonly' if readonly else 'restore', '--root', '/restore']
    if not readonly:
        command += ['--archive-sha256', archive_sha256, '--nonce', nonce]
    return command


def _one_inspection(raw, cid, exited=False):
    value = worker._strict_receipt(raw)
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict) or value[0].get('Id') != cid:
        raise ValueError('exact container inspection required')
    state = value[0].get('State')
    if not isinstance(state, dict) or state.get('Running') is not False:
        raise ValueError('container must be stopped at admission or completion')
    if exited:
        if (state.get('Status') != 'exited' or type(state.get('ExitCode')) is not int or state['ExitCode'] != 0
                or any(state.get(key) is not False for key in ('OOMKilled', 'Dead', 'Restarting'))):
            raise ValueError('exact container must exit successfully before next stage')
        value = copy.deepcopy(value)
        value[0]['State'] = {'Status': 'created', 'Running': False}
    elif state.get('Status') != 'created':
        raise ValueError('newly created container required before start')
    return value


def effective_helper_source_matches(actual, requested):
    """Only the exact request or Docker Desktop's one explicit drive mapping.

    Do not resolve arbitrary paths, case-fold Linux components, collapse '..',
    accept UNC paths, or accept a prefix/substring match. The same exact
    translation is allowed in Docker Desktop's stored requested mount.
    """
    if not isinstance(actual, str) or not isinstance(requested, str):
        return False
    if actual == requested:
        return True
    if not re.match(r'^[A-Za-z]:[\\/]', requested):
        return False
    components = requested[3:].replace('\\', '/').split('/')
    if any(part in ('', '.', '..') or ':' in part or any(ord(char) < 32 for char in part) for part in components):
        return False
    mapped = '/run/desktop/mnt/host/' + requested[0].lower() + '/' + '/'.join(components)
    return actual == mapped


def validate_worker_inspection(value, cid, nonce, volume, helper_directory, command, readonly):
    if (not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict)
            or value[0].get('Id') != cid):
        raise ValueError('one exact worker inspection required')
    item = value[0]
    config, host = item.get('Config'), item.get('HostConfig')
    if not isinstance(config, dict) or not isinstance(host, dict):
        raise ValueError('worker config objects required')
    labels = config.get('Labels') or {}
    image_pos = command.index(plan.HELPER_IMAGE)
    if (item.get('Image') != plan.HELPER_IMAGE or config.get('Image') != plan.HELPER_IMAGE
            or config.get('User') != '65534:65534' or config.get('Entrypoint') != ['python3']
            or config.get('Cmd') != command[image_pos + 1:]
            or not isinstance(labels, dict) or labels.get('org.mingos.restore.nonce') != nonce
            or labels.get('org.mingos.restore.role') != ('verify' if readonly else 'restore')):
        raise ValueError('worker exact identity/process differs')
    if (host.get('NetworkMode') != 'none' or host.get('ReadonlyRootfs') is not True
            or host.get('Privileged') is not False or host.get('AutoRemove') is not False
            or host.get('CapDrop') != ['ALL'] or host.get('CapAdd') not in (None, [])
            or host.get('SecurityOpt') not in (['no-new-privileges'], ['no-new-privileges:true'])
            or host.get('NanoCpus') != 2 * 10 ** 9 or host.get('Memory') != 4 * 1024 ** 3
            or host.get('PidsLimit') != 128
            or host.get('RestartPolicy') != {'Name': 'no', 'MaximumRetryCount': 0}):
        raise ValueError('worker confinement differs')
    for key in ('Binds', 'VolumesFrom', 'Links', 'Devices', 'DeviceRequests', 'Tmpfs', 'PortBindings', 'ExtraHosts'):
        if host.get(key) not in (None, [], {}):
            raise ValueError('unexpected worker attachment')
    requested = host.get('Mounts')
    if not isinstance(requested, list) or len(requested) != 2:
        raise ValueError('exact volume and readonly helper request required')
    volume_request = {'Type': 'volume', 'Source': volume, 'Target': '/restore', 'VolumeOptions': {'NoCopy': True}}
    if readonly:
        volume_request['ReadOnly'] = True
    helper_request = {'Type': 'bind', 'Source': str(helper_directory), 'Target': '/helper', 'ReadOnly': True}
    requested_helper = dict(requested[1]) if isinstance(requested[1], dict) else {}
    actual_source = requested_helper.pop('Source', None)
    if (requested[0] != volume_request or not effective_helper_source_matches(actual_source, str(helper_directory))
            or requested_helper != {key: value for key, value in helper_request.items() if key != 'Source'}):
        raise ValueError('worker requested mounts differ')
    mounts = item.get('Mounts')
    if not isinstance(mounts, list) or len(mounts) != 2 or any(not isinstance(m, dict) for m in mounts):
        raise ValueError('exactly two effective worker mounts required')
    by_target = {m.get('Destination'): m for m in mounts}
    if set(by_target) != {'/restore', '/helper'}:
        raise ValueError('worker effective mount destinations differ')
    actual = by_target['/restore']
    helper = by_target['/helper']
    if (actual.get('Type') != 'volume' or actual.get('Name') != volume or actual.get('Driver') != 'local'
            or actual.get('RW') is not (not readonly) or helper.get('Type') != 'bind'
            or not effective_helper_source_matches(helper.get('Source'), str(helper_directory))
            or helper.get('RW') is not False or helper.get('Propagation') not in (None, '', 'rprivate')):
        raise ValueError('worker effective mount identity or readonly state differs')


def _worker_result(value, prepared, nonce):
    fields = {'schemaVersion', 'status', 'nonce', 'formalQualification', 'finalSystemAccepted',
        'archiveSha256', 'archiveBytes', 'embeddedManifestSha256', 'payloadBytes', 'rootIdentity',
        'payloadIdentity', 'controlIdentity', 'leaseIdentity', 'privateArchiveIdentity', 'files', 'completionReceipt'}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1
            or value.get('status') != 'payload-restored'
            or value.get('formalQualification') is not False or value.get('finalSystemAccepted') is not False
            or value.get('nonce') != nonce or value.get('archiveSha256') != prepared['archiveSha256']
            or any(type(value.get(key)) is not int for key in ('archiveBytes', 'payloadBytes'))
            or value.get('archiveBytes') != prepared['archiveBytes']
            or value.get('payloadBytes') != prepared['payloadBytes']
            or value.get('embeddedManifestSha256') != prepared['manifestSha256']):
        raise ValueError('restore worker receipt differs from verified input')
    expected = {e['path']: dict(e) for e in prepared['entries']}
    files = value.get('files')
    if not isinstance(files, list) or len(files) != len(expected):
        raise ValueError('restore worker inventory differs')
    seen = set()
    def valid_identity(identity, mode):
        return (isinstance(identity, dict) and set(identity) == {'dev', 'ino', 'uid', 'gid', 'mode'}
            and all(type(v) is int for v in identity.values()) and identity['dev'] >= 0 and identity['ino'] > 0
            and identity['uid'] == 65534 and identity['gid'] == 65534 and identity['mode'] == mode)
    for item in files:
        if not isinstance(item, dict) or item.get('path') not in expected or item['path'] in seen:
            raise ValueError('restore worker inventory path differs')
        seen.add(item['path'])
        if {k: v for k, v in item.items() if k != 'identity'} != expected[item['path']]:
            raise ValueError('restore worker file metadata differs')
        if not valid_identity(item.get('identity'), item['mode']):
            raise ValueError('restore worker file identity differs')
    for key in ('rootIdentity', 'payloadIdentity', 'controlIdentity'):
        identity = value.get(key)
        if not valid_identity(identity, 0o700):
            raise ValueError('restore worker private root identity differs')
    for key in ('leaseIdentity', 'privateArchiveIdentity'):
        if not valid_identity(value.get(key), 0o600):
            raise ValueError('restore worker control identity differs')
    core = dict(value)
    completed = core.pop('completionReceipt', {})
    raw = worker._json_bytes(core)
    if (not isinstance(completed, dict) or set(completed) != {'sha256', 'bytes', 'identity'}
            or not valid_identity(completed.get('identity'), 0o600)
            or completed.get('sha256') != hashlib.sha256(raw).hexdigest() or completed.get('bytes') != len(raw)):
        raise ValueError('completion receipt hash differs')
    return value


def restore_volume(asset_root, manifest, receipt_root, docker='docker'):
    receipt = {'schemaVersion': 1, 'status': 'failed', 'phase': 'manifest',
        'formalQualification': False, 'finalSystemAccepted': False,
        'loadImages': False, 'restoreVolume': True, 'operations': [], 'nonce': secrets.token_hex(16)}
    evidence = None
    try:
        prefix = [docker] if isinstance(docker, str) else list(docker)
        if not prefix or any(not isinstance(item, str) or not item for item in prefix):
            raise ValueError('Docker executable command required')
        receipt.update(assetRoot=str(asset_root), manifest=str(manifest))
        evidence = Evidence(receipt_root, receipt)
        root = _checked_path(asset_root, regular=False)
        value, raw_manifest, manifest = _outer_manifest(root, manifest)
        receipt.update(assetRoot=str(root), manifest=str(manifest), manifestSha256=hashlib.sha256(raw_manifest).hexdigest(),
            provenanceName=value['volume']['name'], subpath='payload', controlSubpath='control', helperImageId=plan.HELPER_IMAGE)
        with ExitStack() as stack:
            receipt['phase'] = 'archive-validation'
            frozen_sources = []
            for entry in value['images']:
                path = _asset(root, entry['archive'])
                source = stack.enter_context(path.open('rb'))
                before = _opened_regular(source)
                actual, count = archive._hash(source)
                if actual != entry['archiveSha256'] or _stream_identity(_opened_regular(source)) != _stream_identity(before):
                    raise ValueError('image archive hash or opened identity differs: ' + entry['key'])
                frozen_sources.append((source, _stream_identity(before), actual, count))
            source = stack.enter_context(_asset(root, value['volume']['archive']).open('rb'))
            private, prepared = private_copy_archive(source, evidence.root, value['volume']['archiveSha256'])
            receipt['privateCopy'] = {'path': str(private), 'sha256': prepared['archiveSha256'], 'bytes': prepared['archiveBytes']}
            receipt.update(archiveSha256=prepared['archiveSha256'], archiveBytes=prepared['archiveBytes'], embeddedManifestSha256=prepared['manifestSha256'])
            helper_directory, helper_hashes = _freeze_helpers(evidence)
            receipt['helperSnapshot'] = {'path': str(helper_directory), 'files': helper_hashes}
            if _outer_manifest(root, manifest)[1] != raw_manifest:
                raise ValueError('manifest changed before Docker admission')
            for opened, identity, expected_hash, expected_bytes in frozen_sources:
                if _stream_identity(_opened_regular(opened)) != identity or archive._hash(opened) != (expected_hash, expected_bytes):
                    raise ValueError('image archive changed before Docker admission')
            private_open = stack.enter_context(_checked_path(private, regular=True).open('rb'))
            private_identity = _stream_identity(_opened_regular(private_open))
            if archive._hash(private_open) != (prepared['archiveSha256'], prepared['archiveBytes']):
                raise ValueError('private copy changed before Docker admission')
            evidence.snapshot('inputs-verified')
            receipt['phase'] = 'helper-image-inspect'
            image = worker._strict_receipt(evidence.run('helper-image-inspect', prefix + ['image', 'inspect', plan.HELPER_IMAGE], 60))
            if (not isinstance(image, list) or len(image) != 1 or image[0].get('Id') != plan.HELPER_IMAGE
                    or image[0].get('Os') != 'linux' or image[0].get('Architecture') != 'amd64'
                    or image[0].get('Config', {}).get('Volumes')):
                raise ValueError('exact local Linux helper image without implicit volumes required')
            nonce = receipt['nonce']
            _check_helpers(helper_directory, helper_hashes)
            if (_stream_identity(_opened_regular(private_open)) != private_identity
                    or archive._hash(private_open) != (prepared['archiveSha256'], prepared['archiveBytes'])):
                raise ValueError('private copy changed before creator admission')
            receipt['phase'] = 'creator-create'
            raw_cid = evidence.run('creator-create', prefix + plan.creator_command(nonce)[1:], 180)
            creator = raw_cid.decode('ascii').strip()
            if not plan.HEX64.fullmatch(creator):
                raise ValueError('Docker did not return exact creator CID')
            receipt['creatorId'] = creator
            receipt['phase'] = 'creator-inspect'
            inspected = _one_inspection(evidence.run('creator-inspect', prefix + ['container', 'inspect', creator], 60), creator)
            allocated = plan.validate_creator_inspection(inspected, creator, nonce)
            receipt.update(allocated)
            volume = allocated['volumeName']
            receipt['phase'] = 'creator-initialize'
            evidence.run('creator-start', prefix + ['start', '--attach', creator], 180)
            completed = _one_inspection(evidence.run('creator-final-inspect', prefix + ['container', 'inspect', creator], 60), creator, exited=True)
            if plan.validate_creator_inspection(completed, creator, nonce) != allocated:
                raise ValueError('creator volume identity changed after initialization')
            for readonly, role in ((False, 'restore'), (True, 'verify')):
                _check_helpers(helper_directory, helper_hashes)
                receipt['phase'] = role + '-create'
                command = worker_command(nonce, volume, helper_directory, readonly, prepared['archiveSha256'])
                cid = evidence.run(role + '-create', prefix + command, 180).decode('ascii').strip()
                if not plan.HEX64.fullmatch(cid) or cid in {receipt.get('creatorId'), receipt.get('restoreContainerId')}:
                    raise ValueError('new exact distinct worker CID required')
                receipt['verifyContainerId' if readonly else 'restoreContainerId'] = cid
                receipt['phase'] = role + '-inspect'
                inspected = _one_inspection(evidence.run(role + '-inspect', prefix + ['container', 'inspect', cid], 60), cid)
                validate_worker_inspection(inspected, cid, nonce, volume, helper_directory, command, readonly)
                receipt['phase'] = role + '-start'
                if readonly:
                    raw = evidence.run('verify-start', prefix + ['start', '--attach', '--interactive', cid], 1800,
                        input_bytes=worker._json_bytes(receipt['restore']))
                else:
                    if (_stream_identity(_opened_regular(private_open)) != private_identity
                            or archive._hash(private_open) != (prepared['archiveSha256'], prepared['archiveBytes'])):
                        raise ValueError('private copy changed before payload stream admission')
                    private_open.seek(0)
                    raw = evidence.run('restore-start', prefix + ['start', '--attach', '--interactive', cid], 1800, stdin=private_open)
                completed = _one_inspection(evidence.run(role + '-final-inspect', prefix + ['container', 'inspect', cid], 60), cid, exited=True)
                validate_worker_inspection(completed, cid, nonce, volume, helper_directory, command, readonly)
                _check_helpers(helper_directory, helper_hashes)
                result = worker._strict_receipt(raw)
                if readonly:
                    if (not isinstance(result, dict) or result.get('status') != 'payload-readonly-verified'
                            or type(result.get('schemaVersion')) is not int or result['schemaVersion'] != 1
                            or result.get('statvfsReadOnly') is not True or result.get('formalQualification') is not False
                            or result.get('finalSystemAccepted') is not False):
                        raise ValueError('independent readonly verifier did not establish its boundary')
                    for key in ('nonce', 'archiveSha256', 'archiveBytes', 'payloadBytes', 'files', 'rootIdentity',
                                'payloadIdentity', 'controlIdentity', 'leaseIdentity'):
                        if result.get(key) != receipt['restore'].get(key):
                            raise ValueError('independent readonly verifier receipt differs: ' + key)
                    receipt['readonlyVerify'] = result
                else:
                    receipt['restore'] = _worker_result(result, prepared, nonce)
                evidence.snapshot(role + '-verified')
            receipt['status'], receipt['phase'] = 'volume-restored-readonly-verified', 'complete'
            evidence.snapshot('success')
            return receipt
    except Exception as error:
        receipt['status'] = 'failed'
        receipt['failure'] = {'phase': receipt['phase'], 'error': str(error)}
        if evidence is not None:
            try:
                evidence.snapshot('failure')
            except Exception as write_error:
                receipt['receiptPersistenceError'] = str(write_error)
        raise RestoreError(str(error), receipt) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--receipt-root', type=Path)
    args = parser.parse_args()
    output = args.receipt_root
    if output is None:
        parent = Path(__file__).resolve().parents[2] / 'artifacts' / 'qualification-restore'
        parent.mkdir(parents=True, exist_ok=True)
        output = parent / secrets.token_hex(16)
    try:
        result = restore_volume(args.asset_root, args.manifest, output)
    except RestoreError as error:
        print(json.dumps(error.receipt, separators=(',', ':')))
        print('QUALIFICATION_ASSET_RESTORE_BLOCKED: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
