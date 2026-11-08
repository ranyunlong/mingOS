"""S3 seal/wrapper foundations; source/cache/compiler orchestration is later work.

Local wrapper creation and pin observations confer no S2 authority. Runtime
callers must first qualify the external spec and ready receipt, enter accepted
S2 management namespaces, and pass that qualified handle on EVERY phase check.
The admitted helpers below repeat S2 live admission, never cache its verdict.
"""
import os
from pathlib import Path
import sys

import docker_transport
from docker_transport import DockerTransport
from source_build_seal import _absolute, _integer, _SHA, _write_exclusive, file_pin


def wrapper_environment():
    """Keep the frozen transport policy; additionally drop all CONTAINERD_* keys."""
    return {key: value for key, value in docker_transport._environment().items()
            if not key.upper().startswith('CONTAINERD_')}


def _transport_identity(transport):
    if type(transport) is not DockerTransport:
        raise ValueError('frozen DockerTransport required')
    # Reapply the frozen constructor/argv contract rather than accepting a
    # caller's custom argv implementation or a command string with flags.
    checked = DockerTransport(transport.executable, transport.endpoint, transport.config_directory)
    _absolute(checked.executable)
    _absolute(checked.config_directory)
    checked.argv([])
    return {'executable': checked.executable, 'endpoint': checked.endpoint,
            'configDirectory': checked.config_directory}


def _pinned_files(binary, interpreter):
    return {'transport': file_pin(Path(docker_transport.__file__)),
            'guest': file_pin(Path(__file__)), 'binary': file_pin(binary),
            'interpreter': file_pin(interpreter)}


def create_wrapper(transport: DockerTransport, output: Path) -> dict:
    """Create one absolute executable. This low-level operation is not admission."""
    identity = _transport_identity(transport)
    output = _absolute(output)
    module_root = _absolute(Path(__file__).parent)
    if Path(docker_transport.__file__).parent != module_root:
        raise ValueError('transport must come from the same separately frozen harness')
    # Resolve the trusted running interpreter once; the generated executable is
    # then tied to its regular file, not PATH or ambient Python configuration.
    interpreter = _absolute(Path(sys.executable).resolve(strict=True))
    if any(c.isspace() for c in str(interpreter)):
        raise ValueError('shebang interpreter path cannot contain whitespace')
    pins = _pinned_files(identity['executable'], interpreter)
    if not pins['binary']['mode'] & 0o111 or not pins['interpreter']['mode'] & 0o111:
        raise ValueError('executable Docker binary/interpreter required')
    config = {'identity': identity, 'pins': pins}
    source = ('#!' + str(interpreter) + ' -IBS\n'
              'import sys\n'
              'sys.path.insert(0, ' + repr(str(module_root)) + ')\n'
              'from source_build_guest import _execute_wrapper\n'
              '_execute_wrapper(' + repr(config) + ')\n')
    _write_exclusive(output, source.encode('utf8'), 0o555)
    record = {'schemaVersion': 1, 'kind': 'p01-s3-docker-wrapper',
              'identity': identity, 'pins': {'wrapper': file_pin(output), **pins}}
    wrapper_pins(record)
    return record


def _execute_wrapper(config):
    """The generated script's only entry: inherited binary fds and exec status."""
    # Hash BEFORE argv execution. No subprocess/capture/text decoding occurs.
    for expected in config['pins'].values():
        if file_pin(expected['path']) != expected:
            raise ValueError('frozen wrapper dependency/binary bytes or mode changed')
    identity = config['identity']
    transport = DockerTransport(identity['executable'], identity['endpoint'], identity['configDirectory'])
    argv = transport.argv(sys.argv[1:])
    os.execve(argv[0], argv, wrapper_environment())


def _live_admission(handle, timeout):
    """Repeat accepted S2 semantics; a caller-supplied handle is NOT qualification.

    Precondition: the coordinator has qualified the original external spec,
    called accepted _read_ready, and entered the admitted management context.
    No payload allocation is performed by this check.
    """
    import wsl_environment_guest as s2
    if type(timeout) is not int or not 0 < timeout <= 30:
        raise ValueError('S2 live admission timeout must be an exact integer in 1..30')
    if type(handle) is not dict or 'generation' not in handle:
        raise ValueError('qualified S2 handle required')
    p = s2.paths(handle['generation'])
    before = s2.observe_handle(handle['generation'], handle['pid'])
    s2.validate_handle(handle, before)
    result = s2.admit_environment(handle, ['/usr/bin/true'], timeout=timeout)
    if result.returncode != 0:
        raise ValueError('S2 live admission child failed')
    after = s2.observe_handle(handle['generation'], handle['pid'])
    s2.validate_handle(handle, after)
    # DockerTransport itself does not enter namespaces. The manager that will
    # invoke this wrapper must already inhabit this exact accepted namespace set.
    actual_ns = {name: os.readlink('/proc/self/ns/' + name) for name in ('mnt', 'net', 'cgroup')}
    if actual_ns != handle['namespaces']:
        raise ValueError('wrapper manager is outside admitted S2 namespaces')
    members = Path(p['cgroup'], 'management', 'cgroup.procs').read_text(encoding='ascii').splitlines()
    if str(os.getpid()) not in members:
        raise ValueError('wrapper manager is outside accepted management cgroup')
    return {'generation': handle['generation'], 'before': before, 'after': after,
            'managerNamespaces': actual_ns, 'managerPid': os.getpid()}


def _accepted_identity(handle, identity):
    import wsl_environment_guest as s2
    accepted = _transport_identity(s2.inner_transport(handle['generation']))
    if identity != accepted:
        raise ValueError('wrapper endpoint/config/binary differs from accepted S2 transport')


def wrapper_pins(record: dict, *, handle=None, timeout=30) -> dict:
    """Rehash before/after each phase; handle additionally requires fresh admission.

    Without handle this is ONLY a local byte-control observation. A matching
    record, ready file or previous observation can never grant runtime authority.
    Returned live observations must be retained by the future phase coordinator.
    """
    if (type(record) is not dict or record.get('schemaVersion') != 1
            or type(record.get('schemaVersion')) is not int
            or record.get('kind') != 'p01-s3-docker-wrapper'
            or set(record) not in ({'schemaVersion', 'kind', 'identity', 'pins'},
                                   {'schemaVersion', 'kind', 'identity', 'pins', 'admission'})
            or type(record.get('pins')) is not dict
            or set(record['pins']) != {'wrapper', 'transport', 'guest', 'binary', 'interpreter'}):
        raise ValueError('complete wrapper pin record required')
    identity = record['identity']
    if type(identity) is not dict or set(identity) != {'executable', 'endpoint', 'configDirectory'}:
        raise ValueError('closed wrapper transport identity required')
    _transport_identity(DockerTransport(identity['executable'], identity['endpoint'], identity['configDirectory']))
    if record['pins']['binary']['path'] != identity['executable']:
        raise ValueError('wrapper binary pin/transport differs')
    admission_before = None
    if handle is not None:
        _accepted_identity(handle, identity)
        admission_before = _live_admission(handle, timeout)
    observed = {}
    for role, expected in record['pins'].items():
        if type(expected) is not dict or set(expected) != {'path', 'mode', 'bytes', 'sha256'}:
            raise ValueError('closed file pin required')
        _integer(expected['mode'], 0o7777)
        _integer(expected['bytes'])
        if type(expected['sha256']) is not str or not _SHA.fullmatch(expected['sha256']):
            raise ValueError('lowercase file SHA256 required')
        pin = file_pin(expected['path'])
        if pin != expected:
            raise ValueError('wrapper/module/binary pin changed: ' + role)
        observed[role] = pin
    if handle is not None:
        observed['admission'] = {'beforePins': admission_before,
                                 'afterPins': _live_admission(handle, timeout)}
    return observed


def admitted_wrapper(handle: dict, output: Path, *, timeout=30) -> dict:
    """Generate only inside a freshly admitted, already qualified S2 manager."""
    import wsl_environment_guest as s2
    _live_admission(handle, timeout)
    record = create_wrapper(s2.inner_transport(handle['generation']), output)
    observed = wrapper_pins(record, handle=handle, timeout=timeout)
    record['admission'] = observed['admission']
    return record


def require_actual_runner_result(result: dict) -> dict:
    """Classification rejection gate only; Task5 supplies full compiler replay.

    In particular, the existing actual-docker label merely means no injected
    callable. It is insufficient to accept an external Docker executable seam.
    """
    if (type(result) is not dict or result.get('status') != 'pipeline-completed'
            or result.get('validationKind') != 'actual-docker'
            or result.get('compileExecuted') is False or result.get('synthetic_controls')
            or any(result.get(key) is not False for key in
                   ('formalQualification', 'finalSystemAccepted', 'guestStarted'))):
        raise ValueError('actual nonsynthetic successful runner result required')
    return result


# Task4 is deliberately a private, precondition-bearing phase, like the admitted
# wrapper above. Only Task6's same-live-host qualified coordinator may call it.
# Nothing below turns a dictionary, a ready file, or local pins into authority.
from contextlib import contextmanager
import copy
import hashlib
import json
import re
import secrets
import stat
import threading
from pathlib import PurePosixPath

import developer_cache as _cache
import source_build as _host_contract
import source_build_seal as _seal


def _require(ok, message):
    if not ok:
        raise ValueError('source/cache phase: ' + message)


def _phase_paths(s2_generation, generation):
    import wsl_environment_guest as s2
    s2.paths(s2_generation)
    _require(type(generation) is str and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', generation),
             'canonical S3 generation required')
    base = PurePosixPath(s2.paths(s2_generation)['data']) / 's3' / generation
    return {'archive': base / 'inputs/handoff/handoff.tar',
            'harness': base / 'inputs/harness', 'output': base / 'source-cache'}


def _source_cache_budget(*, archive, harness, delivery, source, cache_archive,
                         cache_payload, gradle, entries, host_reserve, linux_reserve, export_max):
    """Conservative observation budget, never a reservation or a disk quota.

    Byte amounts come from pinned uncompressed inventories. Entries include all
    retained and private trees; 4KiB/entry plus 256MiB covers metadata/log growth.
    Work/output estimates retain each prepare's 8GiB floor and export ceiling.
    Runtime exhaustion still fails; this estimate promises no disk reservation.
    """
    for amount in (archive, harness, delivery, source, cache_archive, cache_payload, gradle, entries):
        _integer(amount)
    for amount in (host_reserve, linux_reserve, export_max):
        _integer(amount); _require(amount > 0, 'positive pinned reserve/export limit required')
    inputs = archive + harness
    items = {'inputArchiveAndHarness': inputs, 'retainedInputSpools': inputs,
             'restoredDelivery': delivery, 'restoredSource': source,
             'cachePrivateArchive': cache_archive, 'cacheRestoredPayload': cache_payload,
             'cacheConsumers': cache_payload, 'kotlinPrivateSources': 2 * source,
             'kotlinPrivateGradleCaches': 2 * gradle,
             'kotlinWorkAndOutputs': 2 * max(8 * 1024**3, export_max),
             'guestCopyAndCanonicalExport': 2 * export_max,
             'metadataAndLogs': 256 * 1024**2 + entries * 4096, 'linuxReserve': linux_reserve}
    total = sum(items.values())
    return {'items': items, 'linuxTotalBytes': total, 'alreadyRetainedBytes': 2 * inputs,
            'linuxAdditionalBytes': total - 2 * inputs,
            # ext4 bavail does not prove that a growing/sparse VHD already has
            # Windows backing. Conservatively allow the entire Linux ledger,
            # plus the host canonical archive/extraction and configured reserve.
            'hostRequiredBytes': total + 2 * export_max + host_reserve,
            'kotlinPrepareMinimumBytes': 8 * 1024**3}


def _observe_host_capacity(spec, spec_sha256, s2_generation, accepted_vhd):
    """Real Windows measurement, called by Task6 BEFORE its first transfer.

    Precondition: original proof/ready validation supplied accepted_vhd. This
    helper measures its file/disk; it neither qualifies nor admits that receipt.
    Active VHD growth is allowed. No VHD bytes are read or changed.
    """
    _require(os.name == 'nt', 'Windows host measurement required')
    import shutil
    _host_contract._hex(spec_sha256)
    _phase_paths(s2_generation, spec['generation'])
    _host_contract._object(accepted_vhd, ('path', 'bytes', 'device', 'inode'))
    for key in ('bytes', 'device', 'inode'):
        _integer(accepted_vhd[key])
    path = _host_contract._path(accepted_vhd['path'])
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'regular single-link VHD required')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        opened = os.fstat(fd)
        available = shutil.disk_usage(path.parent).free
        after = _host_contract._path(path).lstat()
        _require(all((v.st_dev, v.st_ino) == (accepted_vhd['device'], accepted_vhd['inode'])
                     and stat.S_ISREG(v.st_mode) and v.st_nlink == 1
                     and v.st_size >= accepted_vhd['bytes'] for v in (before, opened, after)),
                 'accepted VHD identity changed or shrank')
        _require(after.st_size >= before.st_size, 'VHD shrank during observation')
        return {'specSha256': spec_sha256, 'environment': copy.deepcopy(spec['environment']),
                's2Generation': s2_generation, 'generation': spec['generation'],
                'availableBytes': available, 'vhd': {**accepted_vhd, 'bytes': after.st_size}}
    finally:
        os.close(fd)


def _linux_space(path):
    """Actual descriptor-bound statfs/statvfs plus raw mountinfo; no drvfs guess."""
    import ctypes
    path = _absolute(path)
    fd = _seal._open_absolute(path, directory=True)
    try:
        before = os.fstat(fd); space = os.fstatvfs(fd)
        raw = ctypes.create_string_buffer(256)
        _require(ctypes.CDLL(None, use_errno=True).fstatfs(fd, raw) == 0, 'fstatfs failed')
        mountinfo = Path('/proc/self/mountinfo').read_text(encoding='utf8')
        _seal._same_path(path, fd, directory=True)
        return {'path': str(path), 'device': before.st_dev, 'inode': before.st_ino,
                'filesystemType': ctypes.c_long.from_buffer(raw).value,
                'availableBytes': space.f_bavail * space.f_frsize, 'mountinfo': mountinfo}
    finally:
        os.close(fd)


def _check_source_cache_capacity(budget, host, linux, *, spec_sha256, environment,
                                 s2_generation, generation, expected_device):
    _host_contract._hex(spec_sha256)
    _require(type(host) is dict and host.get('specSha256') == spec_sha256
             and host.get('environment') == environment and host.get('s2Generation') == s2_generation
             and host.get('generation') == generation, 'host observation context differs')
    vhd = host.get('vhd')
    _require(type(vhd) is dict and set(vhd) == {'path', 'bytes', 'device', 'inode'}
             and type(vhd['path']) is str and bool(vhd['path']), 'host VHD observation missing')
    for key in ('bytes', 'device', 'inode'):
        _integer(vhd[key])
    _integer(host.get('availableBytes'))
    _require(host['availableBytes'] >= budget['hostRequiredBytes'], 'host referenced capacity insufficient')
    for key in ('filesystemType', 'device', 'availableBytes', 'inode'):
        _integer(linux.get(key))
    _require(linux['filesystemType'] == 0xef53 and linux['device'] == expected_device
             and type(linux.get('mountinfo')) is str and bool(linux['mountinfo']),
             'actual ext4/device/mountinfo differs')
    _require(linux['availableBytes'] >= budget['linuxAdditionalBytes'], 'actual ext4 bavail insufficient')
    return linux


def _guest_harness(root, manifest_sha256):
    """Recheck exact transferred closure and intended Linux modes independently."""
    _host_contract._hex(manifest_sha256)
    root = _absolute(root)
    inventory, identities = _seal._inventory(root, capture_identity=True)
    _require(inventory['rootMode'] == 0o555, 'harness root must be 0555')
    metadata = inventory['files'].get('manifest.json')
    _require(metadata is not None and metadata['sha256'] == manifest_sha256
             and metadata['mode'] == 0o444 and metadata['bytes'] <= 8 * 1024**2, 'harness manifest pin/mode differs')
    reader = _host_contract._Reader()
    manifest = reader.read({'path': str(root / 'manifest.json'), 'sha256': manifest_sha256}, json_value=True, full=False)
    _host_contract._object(manifest, ('schemaVersion', 'kind', 'checkout', 'directories', 'files'))
    _host_contract._literal(manifest['schemaVersion'], 1)
    _require(manifest['kind'] == 'p01-s3-harness-input', 'harness kind differs')
    _host_contract._parents_declared(manifest['directories'], manifest['files'])
    _require(set(inventory['directories']) == set(manifest['directories'])
             and set(inventory['files']) == set(manifest['files']) | {'manifest.json'}, 'harness closure differs')
    for name, row in manifest['directories'].items():
        _host_contract._object(row, ('hostMode', 'transferMode'))
        _integer(row['hostMode']); _host_contract._literal(row['transferMode'], 0o555)
        _require(inventory['directories'][name]['mode'] == 0o555, 'harness directory mode differs')
    for name, row in manifest['files'].items():
        _host_contract._object(row, ('bytes', 'sha256', 'hostMode', 'transferMode'))
        _integer(row['bytes']); _integer(row['hostMode']); _integer(row['transferMode'])
        _host_contract._hex(row['sha256'])
        _require(row['transferMode'] in (0o444, 0o555)
                 and inventory['files'][name] == {'bytes': row['bytes'], 'sha256': row['sha256'], 'mode': row['transferMode']},
                 'harness file bytes/mode differ')
    reader.recheck()
    return {'manifestSha256': manifest_sha256, 'inventory': inventory, 'identities': identities,
            'bytes': sum(row['bytes'] for row in inventory['files'].values())}


def _source_cache_inputs(spec, archive_path):
    """Pinned S1 allowlist and descriptor bindings before any Docker call."""
    import tarfile
    import import_handoff as original
    import current_source as source
    handoff = spec['handoff']
    for key in ('sha256', 'manifestSha256', 'sourceDescriptorSha256'):
        _host_contract._hex(handoff[key])
    _host_contract._hex(spec['cacheDescriptorSha256']); _integer(handoff['bytes'])
    archive_observed = file_pin(archive_path)
    _require(archive_observed['bytes'] == handoff['bytes'] and archive_observed['mode'] == 0o444,
             'S1 archive size/transfer mode differs')
    with original._archive(Path(archive_path), handoff['sha256']) as incoming:
        incoming.seek(0)
        with tarfile.open(fileobj=incoming, mode='r:') as tar:
            members, expected, dirs, _ = original._allowlist(tar, handoff['manifestSha256'])
            def metadata(name, pin):
                record = expected.get(name)
                _require(record is not None and record['sha256'] == pin and record['bytes'] <= 8 * 1024**2,
                         'external descriptor differs from pinned handoff')
                with tar.extractfile(members['delivery/' + name]) as stream:
                    raw = stream.read(8 * 1024**2 + 1)
                _require(len(raw) == record['bytes'] and source.sha(raw) == pin, 'descriptor bytes differ')
                return source.strict.strict_json(raw)
            descriptor = metadata('current-source/source-descriptor.json', handoff['sourceDescriptorSha256'])
            cache = metadata('rebuild-inputs/cache/developer-inputs.json', spec['cacheDescriptorSha256'])
            prefix = 'rebuild-inputs/cache/'
            names = sorted(name[len(prefix):] for name in expected if name.startswith(prefix))
            _require(names == ['dependencies.tar', 'developer-inputs.json', 'gradle-baseline.json'], 'exact cache siblings required')
            for key, filename in (('archive', 'dependencies.tar'), ('baseline', 'gradle-baseline.json')):
                row = cache[key]; actual = expected[prefix + filename]
                _integer(row['bytes']); _host_contract._hex(row['sha256'])
                _require(row['path'] == filename and row['bytes'] == actual['bytes']
                         and row['sha256'] == actual['sha256'], 'cache sibling handoff binding differs')
            for name in ('sourceBytes', 'fileCount', 'directoryCount'):
                _integer(descriptor[name])
            _require(type(cache.get('original')) is dict and set(cache['original']) == {'cargo', 'gradle'},
                     'two original cache inventories required')
            _cache.content.projected(cache['original'])
            payload = sum(row['size'] for role in cache['original'].values() for row in role.values() if row['type'] == 'file')
            gradle = sum(row['size'] for row in cache['original']['gradle'].values() if row['type'] == 'file')
            _integer(payload); _integer(gradle)
            # Count page overhead for original and private source/cache trees too.
            entries = len(members) + 3 * (descriptor['fileCount'] + descriptor['directoryCount'])
            entries += 4 * sum(len(role) for role in cache['original'].values())
            return {'deliveryBytes': sum(row['bytes'] for row in expected.values()), 'sourceBytes': descriptor['sourceBytes'],
                    'cacheArchiveBytes': cache['archive']['bytes'], 'cachePayloadBytes': payload, 'gradleBytes': gradle,
                    'entries': entries, 'cacheFiles': names, 'cacheDescriptorSha256': spec['cacheDescriptorSha256']}


def _verified_cache_inputs(inputs, descriptor_sha256):
    return _cache.content.verify_export(inputs, descriptor_sha256)


def _bounded_phase_json(path):
    """Read a retained control without links, unbounded allocation or substitution."""
    path_before = _seal._stamp(Path(path).lstat())
    with _host_contract._open(path) as stream:
        before = _seal._stamp(os.fstat(stream.fileno()))
        _require(before[6] <= 8 * 1024**2, 'retained JSON control exceeds limit')
        raw = stream.read(8 * 1024**2 + 1)
        _require(len(raw) <= 8 * 1024**2 and _seal._stamp(os.fstat(stream.fileno())) == before,
                 'retained JSON control changed or exceeds limit')
    _require(_seal._stamp(Path(path).lstat()) == path_before, 'retained JSON path changed')
    return _host_contract._decode(raw), path_before


def _transfer_history_owner(identity):
    _require(identity[4] == identity[5] == 0, 'original transfer history/input must be root-owned')


def _completed_transfers(spec, handle, spec_sha256, context, paths):
    """Observe Task2's original completed histories; never restore host eligibility.

    The host coordinator must still own both successful live-host witnesses.
    Windows refs here are compared as provenance and are never opened on Linux.
    """
    import wsl_environment_transfer as transfer
    _host_contract._object(context, ('specRef', 'environment', 'acceptance', 'generation',
                                      's2Generation', 'adapter', 'manifestPin'))
    _host_contract._object(context['specRef'], ('path', 'sha256'))
    _host_contract._text(context['specRef']['path']); _host_contract._hex(spec_sha256)
    _require(context['specRef']['sha256'] == spec_sha256
             and _host_contract._same(context['environment'], spec['environment'])
             and _host_contract._same(context['acceptance'], spec['s2Acceptance'])
             and context['generation'] == spec['generation']
             and context['s2Generation'] == handle['generation'], 'original transfer context binding differs')
    for name in ('adapter', 'manifestPin'):
        _host_contract._filepin(context[name])
    _require(context['manifestPin']['sha256'] == spec['harness']['manifestSha256'], 'transfer manifest pin differs')
    harness = _guest_harness(paths['harness'], spec['harness']['manifestSha256'])
    for identity in harness['identities'].values(): _transfer_history_owner(identity)
    rows = harness['inventory']['files']
    _require(context['manifestPin'] == {key: rows['manifest.json'][key] for key in ('bytes', 'sha256')}
             and transfer.ADAPTER_ROLE in rows
             and context['adapter'] == {key: rows[transfer.ADAPTER_ROLE][key] for key in ('bytes', 'sha256')},
             'original transfer adapter/manifest bytes differ')
    base = Path(paths['archive']).parent.parent.parent
    _require(Path(paths['harness']) == base / 'inputs/harness', 'transfer input roots differ')
    observations = {'harness': harness, 'controls': {}, 'directories': {}}
    held = []
    def directory(path, expected=None):
        for ancestor in reversed(path.parents):
            parent_fd = (os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                         if ancestor == Path('/') else _seal._open_absolute(ancestor, directory=True))
            try: _transfer_history_owner(_seal._stamp(os.fstat(parent_fd)))
            finally: os.close(parent_fd)
        fd = _seal._open_absolute(path, directory=True); held.append((path, fd))
        info = os.fstat(fd)
        _transfer_history_owner(_seal._stamp(info))
        # The phase's exclusive output legitimately changes the generation root
        # directory timestamps; its inode/type/owner/mode must remain identical.
        observations['directories'][str(path)] = [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid]
        names = set(os.listdir(fd))
        if expected is not None: _require(names == set(expected), 'unexpected/incomplete transfer history directory')
        return names
    def control(path):
        value, identity = _bounded_phase_json(path)
        _transfer_history_owner(identity)
        observations['controls'][str(path)] = {'identity': identity, 'value': value}
        return value
    try:
        directory(base)
        directory(base / 'inputs', ('handoff', 'harness'))
        directory(base / 'inputs/handoff', ('handoff.tar',))
        _require(stat.S_IMODE(os.fstat(held[-1][1]).st_mode) == 0o555, 'handoff closure directory mode differs')
        directory(base / 'staging')
        directory(base / 'operations', ('handoff', 'harness'))
        for name in ('context.json', 'staging/context.json'):
            _require(_host_contract._same(control(base / name), context), 'stored transfer context changed')
        handoff_before = _seal._stamp(Path(paths['archive']).lstat())
        _transfer_history_owner(handoff_before)
        handoff = file_pin(paths['archive'])
        _require(_seal._stamp(Path(paths['archive']).lstat()) == handoff_before,
                 'completed handoff identity changed during hash')
        _require(handoff['bytes'] == spec['handoff']['bytes'] and handoff['sha256'] == spec['handoff']['sha256']
                 and handoff['mode'] == 0o444, 'completed handoff bytes/mode differ')
        observations['handoff'] = {'pin': handoff, 'identity': handoff_before}
        members = {'handoff': {'handoff.tar': handoff}, 'harness': rows}
        nonces = set()
        for role in ('handoff', 'harness'):
            root = base / 'operations' / role
            expected = {'owner.json', 'complete.json'} | {'member-' + str(i) + '.json' for i in range(len(members[role]))}
            directory(root, expected)
            owner = control(root / 'owner.json')
            _host_contract._object(owner, ('context', 'nonce')); transfer._hex(owner['nonce'], 32)
            _require(owner['nonce'] not in nonces and _host_contract._same(owner['context'], context),
                     'distinct original transfer owners required')
            nonces.add(owner['nonce'])
            _require(_host_contract._same(control(root / 'complete.json'), {'status': 'succeeded', 'context': context}),
                     'both original transfer roles must be complete')
            for ordinal, name in enumerate(sorted(members[role])):
                row = members[role][name]
                record = transfer.validate_operation_record(control(root / ('member-' + str(ordinal) + '.json')))
                _require(record['action'] == 'receive' and record['status'] == 'succeeded'
                         and record['operationNonce'] == owner['nonce'] and record['bytes'] == row['bytes']
                         and record['sha256'] == row['sha256'], 'original completed member binding differs')
        for path, fd in held: _seal._same_path(path, fd, directory=True)
        for path, observed in observations['controls'].items():
            _require(_seal._stamp(Path(path).lstat()) == observed['identity'], 'transfer control changed during observation')
        return observations
    finally:
        for _, fd in held: os.close(fd)


def _cache_failure_observation(root):
    """Recover the latest original durable cache receipt after cancellation."""
    root = Path(root)
    if not os.path.lexists(root): return None
    _host_contract._path(root)
    candidates = []
    with os.scandir(root) as entries:
        for count, entry in enumerate(entries):
            _require(count < 10000, 'cache evidence directory exceeds limit')
            match = re.fullmatch(r'receipt-([0-9]+)-[a-zA-Z0-9-]+\.json', entry.name)
            if match: candidates.append((int(match[1]), root / entry.name))
    path = root / 'result.json' if os.path.lexists(root / 'result.json') else (max(candidates)[1] if candidates else None)
    if path is None: return None
    value, _ = _bounded_phase_json(path)
    _require(type(value) is dict, 'cache retained receipt must be an object')
    for op in value.get('operations', []):
        if type(op) is not dict or type(op.get('command')) is not list: continue
        command = op['command']
        if not (command[1:2] == ['create'] or command[1:3] == ['volume', 'create']): continue
        log = Path(op.get('stdoutPath', ''))
        _require(log.parent == root and re.fullmatch(r'operation-[0-9]+\.stdout\.log', log.name),
                 'original cache allocation log path differs')
        if not os.path.lexists(log): continue
        path_before = _seal._stamp(log.lstat())
        with _host_contract._open(log) as stream:
            before = _seal._stamp(os.fstat(stream.fileno())); raw = stream.read(65537)
            _require(len(raw) <= 65536 and before == _seal._stamp(os.fstat(stream.fileno())),
                     'cache allocation log changed or exceeds cap')
        _require(path_before == _seal._stamp(log.lstat()), 'cache allocation log replaced')
        op['retainedAllocationStdout'] = raw.decode('ascii')
        op['retainedAllocationLogIdentity'] = path_before
    return value


def _filtered_source_mapping(manifest, descriptor_sha256):
    _host_contract._hex(descriptor_sha256)
    excluded = {'.git', '.cache', '.gradle', '.kotlin', '.codegraph', '.superpowers', 'artifacts',
                'node_modules', 'target', 'build', 'builds', '__pycache__', 'dist'}
    def included(name):
        _host_contract._relative(name)
        return not any(part in excluded for part in name.split('/'))
    files = {name: row['sha256'] for name, row in sorted(manifest['files'].items()) if included(name)}
    for digest in files.values():
        _host_contract._hex(digest)
    directories = [''] + sorted(name for name in manifest['directories'] if included(name))
    return {'sourceDescriptorSha256': descriptor_sha256, 'snapshot': {'files': files, 'directories': directories}}


def _check_filtered_snapshot(mapping, snapshot):
    expected = _normalized_filtered_snapshot(mapping['snapshot'])
    observed = _normalized_filtered_snapshot(snapshot)
    _require(_host_contract._same(expected, observed), 'filtered Kotlin snapshot differs from full source mapping')
    return snapshot


def _normalized_filtered_snapshot(snapshot):
    """Validate each full collection before ignoring consumer traversal order.

    The unchanged JS consumer recursively visits localeCompare-sorted siblings;
    a global Python path sort is different. Files still compare by exact path
    and hash, and directory uniqueness/parents/collisions are checked first.
    """
    _host_contract._object(snapshot, ('files', 'directories'))
    files, directories = snapshot['files'], snapshot['directories']
    _require(type(files) is dict and type(directories) is list, 'snapshot file/directory collection types differ')
    for name in directories:
        _require(type(name) is str, 'snapshot directory must be a string')
        if name: _host_contract._relative(name)
    _require(directories.count('') == 1, 'snapshot must declare exactly one root directory')
    # The original helper rejects duplicate/aliased names, file/directory
    # collisions, unsafe paths and every missing parent, before any sorting.
    _host_contract._parents_declared([name for name in directories if name], files)
    for digest in files.values(): _host_contract._hex(digest)
    return {'files': files, 'directories': sorted(directories)}


def _full_source_mapping(imported, descriptor_sha256):
    import import_handoff as original
    verified, _ = original._verified_source(Path(imported) / 'delivery', descriptor_sha256)
    original._complete_source(Path(imported) / 'source-repo', verified)
    return {**_filtered_source_mapping(verified['manifest'], descriptor_sha256),
            'fullSourceManifest': verified['manifest'], 'descriptor': verified['descriptor']}


def _cache_result(result, descriptor_sha256):
    _require(type(result) is dict and result.get('status') == 'developer-cache-restored-consumers-readonly-verified'
             and result.get('validationKind') == 'actual-docker' and result.get('descriptorSha256') == descriptor_sha256
             and result.get('resourcesPreserved') is True, 'cache restoration result differs')
    for key in ('formalQualification', 'finalSystemAccepted', 'guestStarted'):
        _require(result.get(key) is False, 'cache scope flags differ')
    volumes = [result.get(key) for key in ('volumeName', 'cargoVolume', 'gradleVolume')]
    _require(all(type(name) is str and _cache.NAME.fullmatch(name) for name in volumes)
             and len(set(volumes)) == 3 and _cache.plan.HEX64.fullmatch(volumes[0]), 'distinct exact cache volume IDs required')
    for key, status in (('restore', 'payload-restored'), ('readonlyVerify', 'payload-readonly-verified'),
                        ('projection', 'consumer-projection-verified'), ('consumerVerify', 'consumer-readonly-content-verified')):
        _require(type(result.get(key)) is dict and result[key].get('status') == status, 'missing cache receipt: ' + key)
    _require(result['readonlyVerify'].get('statvfsReadOnly') is True
             and result['consumerVerify'].get('statvfsReadOnly') is True
             and result['projection'].get('sourceBeforeAfterMatched') is True, 'independent cache RO/projection missing')
    return result


def _manager_identity():
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    status = Path('/proc/self/status').read_text(encoding='ascii')
    caps = {line.split(':', 1)[0]: line.split(':', 1)[1].strip() for line in status.splitlines()
            if line.startswith(('CapInh:', 'CapPrm:', 'CapEff:', 'CapBnd:', 'CapAmb:'))}
    _require(set(caps) == {'CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb'}, 'complete capabilities unavailable')
    return {'resuid': list(os.getresuid()), 'resgid': list(os.getresgid()),
            'fsuid': libc.setfsuid(-1), 'fsgid': libc.setfsgid(-1), 'capabilities': caps}


def _restore_effective_capabilities(before):
    import ctypes
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]
    caps = before['capabilities']; data = (Data * 2)()
    for index in range(2):
        for field, key in (('effective', 'CapEff'), ('permitted', 'CapPrm'), ('inheritable', 'CapInh')):
            setattr(data[index], field, (int(caps[key], 16) >> (32 * index)) & 0xffffffff)
    header = Header(0x20080522, 0)
    _require(ctypes.CDLL(None, use_errno=True).capset(ctypes.byref(header), ctypes.byref(data)) == 0,
             'restoring original effective capability set failed')


@contextmanager
def _cache_manager_identity(record):
    """Use existing root manager authority with file creation owned by UID65534.

    This never grants capabilities. Permitted/inheritable/bounding/ambient stay
    unchanged; payload Docker workers retain their original drop-ALL policy.
    """
    _require(sys.platform == 'linux' and threading.current_thread() is threading.main_thread()
             and os.geteuid() == 0, 'Linux root main-thread manager required')
    import ctypes
    before = _manager_identity(); record['before'] = before
    required = (1 << 1) | (1 << 6) | (1 << 7)  # existing DAC_OVERRIDE, SETGID, SETUID
    _require(int(before['capabilities']['CapEff'], 16) & required == required
             and before['fsuid'] == 0, 'original management capabilities/fsuid insufficient')
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        libc.setfsgid(65534); libc.setfsuid(65534)
        _restore_effective_capabilities(before)
        during = _manager_identity(); record['during'] = during
        _require(during == {**before, 'fsuid': 65534, 'fsgid': 65534}, 'management identity/capabilities differ')
        yield record
    finally:
        libc.setfsuid(before['fsuid']); libc.setfsgid(before['fsgid'])
        _restore_effective_capabilities(before)
        record['after'] = _manager_identity()
        _require(record['after'] == before, 'management identity/capability restoration failed')


def _check_effective_worker(observed, readonly, *, root=False, pids=256):
    _require(observed.get('uid') == (0 if root else 65534) and observed.get('gid') == (0 if root else 65534)
             and observed.get('interfaces') == ['lo'], 'worker UID/GID/interfaces differ')
    text = observed.get('status', '')
    for key in ('CapEff', 'CapPrm'):
        match = re.search(r'^' + key + r':\s*([0-9a-fA-F]+)\s*$', text, re.M)
        _require(match is not None and int(match[1], 16) == (1 if root else 0), 'worker capabilities differ')
    for key, value in (('NoNewPrivs', '1'), ('Seccomp', '2')):
        _require(re.search(r'^' + key + r':\s*' + value + r'\s*$', text, re.M), 'worker NNP/seccomp differs')
    _require(observed.get('memoryMax') == '4294967296' and observed.get('pidsMax') == str(pids), 'effective cgroup memory/pids differ')
    cpu = observed.get('cpuMax', '').split()
    _require(len(cpu) == 2 and all(re.fullmatch(r'[0-9]+', n) for n in cpu)
             and int(cpu[1]) > 0 and int(cpu[0]) == 2 * int(cpu[1]), 'effective CPU quota differs')
    _require(all(observed.get('readOnly', {}).get(path) is True for path in readonly), 'effective worker input must be read-only')
    _require(type(observed.get('mountinfo')) is str and bool(observed['mountinfo']), 'effective mountinfo missing')
    return observed


def _effective_worker(readonly, *, root=False, pids=256):
    observed = {'uid': os.getuid(), 'gid': os.getgid(), 'interfaces': sorted(os.listdir('/sys/class/net')),
                'status': Path('/proc/self/status').read_text(), 'mountinfo': Path('/proc/self/mountinfo').read_text(),
                'memoryMax': Path('/sys/fs/cgroup/memory.max').read_text().strip(),
                'pidsMax': Path('/sys/fs/cgroup/pids.max').read_text().strip(),
                'cpuMax': Path('/sys/fs/cgroup/cpu.max').read_text().strip(), 'readOnly': {}}
    for path in readonly:
        fd = _seal._open_absolute(path, directory=Path(path).is_dir()) if path != '/' else os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try: observed['readOnly'][path] = bool(os.fstatvfs(fd).f_flag & os.ST_RDONLY)
        finally: os.close(fd)
    if pids == 256:
        tmp = os.statvfs('/tmp')
        _require(tmp.f_blocks * tmp.f_frsize <= 64 * 1024**2 and not tmp.f_flag & os.ST_RDONLY
                 and tmp.f_flag & os.ST_NOSUID and tmp.f_flag & os.ST_NODEV, 'bounded nosuid/nodev tmpfs missing')
    return _check_effective_worker(observed, readonly, root=root, pids=pids)


def _initialize_empty_targets(paths):
    _require(sys.platform == 'linux' and os.geteuid() == 0, 'root fresh-target initializer required')
    descriptors = []
    try:
        for path in paths:
            fd = _seal._open_absolute(path, directory=True); descriptors.append(fd)
            info = os.fstat(fd)
            _require(info.st_uid == info.st_gid == 0 and not os.listdir(fd), 'root-owned literal empty target required')
        for fd in descriptors:
            os.fchmod(fd, 0o700); os.fsync(fd); os.fchown(fd, 65534, 65534); os.fsync(fd)
    finally:
        for fd in descriptors: os.close(fd)


def _source_confinement(item, cid, nonce, label, mounts, command, root=False, finished=False):
    host = item.get('HostConfig', {})
    _require(host.get('PidsLimit') == 256 and host.get('Tmpfs') == {'/tmp': 'rw,nosuid,nodev,size=64m,mode=1777'},
             'source worker pids/tmpfs differs')
    # Reuse every original cache inspect check, changing only the two explicitly
    # different source-worker settings. The original 128pids contract is intact.
    checked = copy.deepcopy(item); checked['HostConfig']['PidsLimit'] = 128; checked['HostConfig']['Tmpfs'] = None
    _cache.confinement(checked, cid, nonce, label, mounts, command, root=root, finished=finished)


class _PhaseEngine(_cache.Engine):
    def __init__(self, evidence, nonce, docker, *, timeout):
        super().__init__(evidence, nonce, docker)
        _integer(timeout); _require(timeout > 0, 'positive phase timeout required')
        self.timeout = timeout

    def call(self, args, label, timeout=180, **kwargs):
        return super().call(args, label, min(timeout, self.timeout), **kwargs)

    def new_volume(self, role):
        name = 'developer-cache-' + self.nonce + '-' + role
        self.evidence.receipt.setdefault('pendingOwnedVolumes', []).append(name)
        self.evidence.snapshot('before-volume-allocation')
        value = super().new_volume(role)
        self.evidence.receipt['ownedVolumes'] = sorted(self.volumes)
        self.evidence.snapshot('after-volume-allocation')
        return value

    def container(self, label, mounts, command, root=False, env=None):
        _require(not env, 'source worker ambient overrides forbidden')
        for index, name in enumerate(sorted({m['source'] for m in mounts if m['type'] == 'volume'})):
            _require(name in self.volumes, 'unregistered volume')
            self.register(name, label + '-volume-before-' + str(index))
        argv = ['create', '--pull', 'never', '--network', 'none', '--read-only', '--user', '0:0' if root else '65534:65534',
                '--cpus', '2', '--memory', '4g', '--pids-limit', '256', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
                '--label', 'org.mingos.developer.nonce=' + self.nonce, '--label', 'org.mingos.developer.role=' + label]
        if root: argv += ['--cap-add', 'CHOWN']
        for mount in mounts: argv += ['--mount', _cache.mount_text(mount)]
        argv += ['--entrypoint', 'python3', _cache.IMAGE, *command]
        cid = self.call(argv, label + '-create', min(180, self.timeout)).decode().strip()
        _require(_cache.plan.HEX64.fullmatch(cid), 'exact source worker CID required')
        self.containers.append({'cid': cid, 'role': label}); self.evidence.receipt['containers'] = self.containers
        def validate(item, finished=False):
            _source_confinement(item, cid, self.nonce, label, mounts, command, root, finished)
        validate(self.inspect(cid, label + '-inspect-before'))
        failure = None; raw = b''
        try: raw = self.call(['start', '--attach', cid], label + '-run', self.timeout)
        except BaseException as caught: failure = caught
        after = self.inspect(cid, label + '-inspect-after'); validate(after)
        running = after.get('State', {}).get('Running')
        if running:
            try:
                validate(self.inspect(cid, label + '-inspect-before-stop'))
                self.call(['stop', '--time', '10', cid], label + '-stop-owned', min(60, self.timeout))
            finally:
                after = self.inspect(cid, label + '-inspect-after-stop'); validate(after)
            _require(after['State'].get('Running') is False, 'owned source worker remains running')
        if failure is not None: raise failure
        _require(not running, 'attached source worker remained running')
        validate(after, True)
        return cid, raw


_WORKER_LOADER = "import sys;sys.path.insert(0,'/harness/scripts/boot');import source_build_guest as g;g._worker_main(sys.argv[1:])"


def _worker_command(action, handoff=None):
    result = ['-I', '-B', '-c', _WORKER_LOADER, action]
    if handoff is not None:
        result += [handoff[key] for key in ('sha256', 'manifestSha256', 'sourceDescriptorSha256')]
    return result


def _restore_source(engine, paths, spec):
    volume = engine.new_volume('source-import')
    info = engine.register(volume, 'source-import-volume-path')
    root = _absolute(info['Mountpoint'])
    storage = _linux_space(root); original_storage = _linux_space(paths['archive'].parent)
    _require(storage['filesystemType'] == 0xef53 and storage['device'] == original_storage['device'],
             'fresh import volume is not the original ext4 backing device')
    engine.evidence.receipt['sourceVolumeStorageBefore'] = storage
    mounts = [_cache.mount('volume', volume, '/output'), _cache.mount('bind', paths['harness'], '/harness', True)]
    _, raw = engine.container('initialize-source', mounts, _worker_command('initialize'), root=True)
    initialized = _cache.worker._strict_receipt(raw)
    _require(initialized.get('result', {}).get('status') == 'fresh-output-owned', 'initializer receipt missing')
    _check_effective_worker(initialized['effective'], ['/', '/harness'], root=True)
    mounts += [_cache.mount('bind', paths['archive'], '/archive/handoff.tar', True)]
    _, raw = engine.container('import-source', mounts, _worker_command('import', spec['handoff']))
    imported = _cache.worker._strict_receipt(raw)
    _check_effective_worker(imported['effective'], ['/', '/harness', '/archive/handoff.tar'])
    _require(imported.get('result', {}).get('status') == 'handoff-imported-and-current-source-restored', 'original import result missing')
    engine.evidence.receipt['sourceImportWorker'] = imported
    return root / 'imported', imported['result']


def _reopen_source(engine, paths, spec, imported):
    mounts = [_cache.mount('bind', paths['archive'], '/archive/handoff.tar', True),
              _cache.mount('bind', paths['harness'], '/harness', True), _cache.mount('bind', imported, '/imported', True)]
    _, raw = engine.container('reopen-source', mounts, _worker_command('reopen', spec['handoff']))
    result = _cache.worker._strict_receipt(raw)
    _check_effective_worker(result['effective'], ['/', '/harness', '/archive/handoff.tar', '/imported'])
    engine.evidence.receipt['sourceReopenWorker'] = result
    return result['result']


def _reverify_consumers(engine, paths, inputs, result):
    """Reusable before/after Kotlin RO verification; never compiler work on Cargo."""
    # This engine retains the original cache 128pids contract unchanged.
    cache_engine = _cache.Engine(engine.evidence, engine.nonce, engine.docker)
    cache_engine.containers = engine.containers
    for key in ('volumeName', 'cargoVolume', 'gradleVolume'):
        cache_engine.register(result[key], 'reverify-' + key)
    mounts = [_cache.mount('bind', paths['harness'], '/harness', True), _cache.mount('bind', inputs, '/inputs', True),
              _cache.mount('volume', result['volumeName'], '/restored', True),
              _cache.mount('volume', result['cargoVolume'], '/cargo-consumer', True),
              _cache.mount('volume', result['gradleVolume'], '/gradle-consumer', True)]
    _, raw = cache_engine.container('reverify-cache-consumers', mounts,
                                   _worker_command('cache-verify') + [result['descriptorSha256']])
    observed = _cache.worker._strict_receipt(raw)
    _check_effective_worker(observed['effective'], ['/', '/harness', '/inputs', '/restored', '/cargo-consumer', '/gradle-consumer'], pids=128)
    value = observed['result']
    _require(value.get('status') == 'consumer-readonly-content-verified' and value.get('statvfsReadOnly') is True,
             'independent cache reverify missing')
    return observed


def _worker_main(argv):
    import import_handoff as original
    _require(argv and argv[0] in ('initialize', 'import', 'reopen', 'cache-verify'), 'fixed source/cache worker action required')
    action = argv[0]
    readonly = ['/', '/harness']
    if action in ('import', 'reopen'): readonly += ['/archive/handoff.tar']
    if action == 'reopen': readonly += ['/imported']
    if action == 'cache-verify': readonly += ['/inputs', '/restored', '/cargo-consumer', '/gradle-consumer']
    effective = _effective_worker(readonly, root=action == 'initialize', pids=128 if action == 'cache-verify' else 256)
    if action == 'initialize':
        _require(len(argv) == 1, 'initializer arguments differ')
        _initialize_empty_targets([Path('/output')]); result = {'status': 'fresh-output-owned'}
    elif action == 'cache-verify':
        _require(len(argv) == 2, 'cache verifier arguments differ')
        descriptor, _ = _verified_cache_inputs(Path('/inputs'), argv[1])
        _require(_cache.content.scan('/restored/payload') == _cache.content.projected(descriptor['original'])[0], 'restored cache payload differs')
        result = {**_cache.content.verify_consumers('/cargo-consumer', '/gradle-consumer', descriptor), 'statvfsReadOnly': True}
    else:
        _require(len(argv) == 4, 'all three original external S1 pins required')
        function = original.import_restore if action == 'import' else original.reopen
        result = function('/archive/handoff.tar', *argv[1:], '/output/imported' if action == 'import' else '/imported')
    after = _effective_worker(readonly, root=action == 'initialize', pids=128 if action == 'cache-verify' else 256)
    print(json.dumps({'result': result, 'effective': effective, 'effectiveAfter': after}, sort_keys=True))


def _source_cache_phase(spec, handle, wrapper_record, *, spec_sha256, transfer_context, host_observation, output):
    """Private admitted-manager phase; NEVER a public authorization or S3 success.

    Required caller preconditions: original external proof/spec qualification,
    accepted _read_ready, completed transfer closures and same-live-host witness.
    Task6 must establish all of them; they cannot be reconstructed from this
    helper's arguments. It independently rechecks pins/closures/live admission.
    """
    paths = _phase_paths(handle['generation'], spec['generation'])
    _require(_absolute(output) == paths['output'], 'fixed exclusive phase output required')
    _require(not os.path.lexists(output), 'new absent nonretryable phase output required')
    transfers = _completed_transfers(spec, handle, spec_sha256, transfer_context, paths)
    initial_wrapper = wrapper_pins(wrapper_record, handle=handle)
    inputs = _source_cache_inputs(spec, paths['archive'])
    harness = _guest_harness(paths['harness'], spec['harness']['manifestSha256'])
    reader = _host_contract._Reader(); host = reader.read(host_observation, json_value=True, full=False); reader.recheck()
    limits = spec['limits']
    budget = _source_cache_budget(archive=spec['handoff']['bytes'], harness=harness['bytes'],
        delivery=inputs['deliveryBytes'], source=inputs['sourceBytes'], cache_archive=inputs['cacheArchiveBytes'],
        cache_payload=inputs['cachePayloadBytes'], gradle=inputs['gradleBytes'], entries=inputs['entries'],
        host_reserve=limits['hostReserveBytes'], linux_reserve=limits['linuxReserveBytes'], export_max=limits['exportMaxBytes'])
    linux = _linux_space(paths['archive'].parent)
    completed_growth = 0
    def capacity(path):
        remaining = {**budget, 'linuxAdditionalBytes': budget['linuxAdditionalBytes'] - completed_growth}
        return _check_source_cache_capacity(remaining, host, _linux_space(path), spec_sha256=spec_sha256,
            environment=spec['environment'], s2_generation=handle['generation'], generation=spec['generation'], expected_device=linux['device'])
    capacity(paths['output'].parent); capacity(paths['harness'])
    state = {'schemaVersion': 1, 'status': 'started', 'phase': 'admission', 'nonce': secrets.token_hex(16),
             'specSha256': spec_sha256, 'environment': spec['environment'], 'generation': spec['generation'],
             's2Generation': handle['generation'], 'endpoint': wrapper_record['identity']['endpoint'],
             'hostObservationRef': host_observation, 'hostObservationScope': 'referenced-Windows-preflight-not-live-Linux-measurement',
             'transferClosure': transfers, 'initialWrapperObservation': initial_wrapper,
             'capacityBudget': budget, 'linuxBefore': linux, 'operations': [], 'ownedVolumes': [], 'containers': [],
             'cargoReservedFor': 'S4', 'resourcesPreserved': True, 'formalQualification': False,
             'finalSystemAccepted': False, 'coldBuildExecuted': False, 'guestStarted': False}
    evidence = _cache.original.Evidence(output, state)
    engine = _PhaseEngine(evidence, state['nonce'], wrapper_record['pins']['wrapper']['path'], timeout=limits['phaseTimeoutSeconds'])
    def checkpoint(label):
        state['phase'] = label
        state.setdefault('wrapperObservations', []).append(wrapper_pins(wrapper_record, handle=handle))
        _require(_completed_transfers(spec, handle, spec_sha256, transfer_context, paths) == transfers,
                 'completed transfer context/closure identity changed during phase')
        _require(_source_cache_inputs(spec, paths['archive']) == inputs, 'pinned source/cache inputs changed during phase')
        _require(_guest_harness(paths['harness'], spec['harness']['manifestSha256']) == harness, 'frozen harness changed during phase')
        reader.recheck(); capacity(paths['output'].parent)
        evidence.snapshot(label)
    def retain_cache(value):
        state['cache'] = value
        names = [value[key] for key in ('volumeName', 'cargoVolume', 'gradleVolume') if type(value.get(key)) is str]
        state['ownedVolumes'] = sorted(set(state['ownedVolumes']) | set(names))
        state['cacheContainers'] = {key: value[key] for key in ('creatorId', 'restoreContainerId', 'verifyContainerId', 'containers') if key in value}
        # Durable after-command snapshots can precede assignment of returned IDs
        # to the original in-memory receipt. Retain those exact observed IDs too.
        allocated = {'volumes': [], 'containers': [], 'possibleVolumes': [], 'possibleContainers': []}
        for op in value.get('operations', []):
            if type(op) is not dict: continue
            command, raw = op.get('command'), op.get('retainedAllocationStdout', op.get('stdout'))
            if type(command) is not list or type(raw) is not str: continue
            returned = raw.strip()
            if len(command) > 2 and command[1:3] == ['volume', 'create'] and _cache.NAME.fullmatch(returned):
                allocated['volumes' if op.get('exitCode') == 0 else 'possibleVolumes'].append(returned)
            elif len(command) > 1 and command[1] == 'create' and _cache.plan.HEX64.fullmatch(returned):
                allocated['containers' if op.get('exitCode') == 0 else 'possibleContainers'].append(returned)
        state['cacheAllocationObservations'] = allocated
        state['ownedVolumes'] = sorted(set(state['ownedVolumes']) | set(allocated['volumes']))
        evidence.snapshot('cache-resources')
    try:
        checkpoint('before-import'); engine.fixed_image()
        imported, restored = _restore_source(engine, paths, spec)
        completed_growth += inputs['deliveryBytes'] + inputs['sourceBytes']
        state['accountedCompletedGrowthBytes'] = completed_growth
        state['ownedVolumes'] = sorted(set(state['ownedVolumes']) | set(engine.volumes))
        state['sourceImport'] = restored; state['sourceRepo'] = str(imported / 'source-repo')
        for name in tuple(engine.volumes):
            info = engine.register(name, 'owned-source-volume-capacity'); capacity(_absolute(info['Mountpoint']))
        mapping = _full_source_mapping(imported, spec['handoff']['sourceDescriptorSha256'])
        checkpoint('before-independent-source-reopen')
        reopened = _reopen_source(engine, paths, spec, imported)
        _require(reopened.get('status') == 'handoff-and-source-readonly-reopened'
                 and all(reopened.get(key) == spec['handoff'][source] for key, source in
                     (('archiveSha256', 'sha256'), ('handoffSha256', 'manifestSha256'), ('sourceDescriptorSha256', 'sourceDescriptorSha256'))),
                 'independent full S1 reopen missing')
        state['sourceReopen'] = reopened
        _require(_full_source_mapping(imported, spec['handoff']['sourceDescriptorSha256']) == mapping, 'full source changed after reopen')
        cache_inputs = imported / 'delivery/rebuild-inputs/cache'
        _verified_cache_inputs(cache_inputs, spec['cacheDescriptorSha256'])
        checkpoint('before-cache-restore')
        state['cacheManagerIdentity'] = {}
        with _cache_manager_identity(state['cacheManagerIdentity']):
            evidence.snapshot('cache-manager-entered')
            result = _cache.restore(cache_inputs, spec['cacheDescriptorSha256'], Path(output) / 'cache-restore', docker=engine.docker)
        retain_cache(result); _cache_result(result, spec['cacheDescriptorSha256'])
        completed_growth += inputs['cacheArchiveBytes'] + 2 * inputs['cachePayloadBytes']
        state['accountedCompletedGrowthBytes'] = completed_growth
        for key in ('volumeName', 'cargoVolume', 'gradleVolume'):
            info = engine.register(result[key], 'cache-volume-capacity-' + key); capacity(_absolute(info['Mountpoint']))
        checkpoint('before-independent-cache-reverify')
        state['cacheReverify'] = _reverify_consumers(engine, paths, cache_inputs, result)
        _verified_cache_inputs(cache_inputs, spec['cacheDescriptorSha256'])
        _require(_full_source_mapping(imported, spec['handoff']['sourceDescriptorSha256']) == mapping, 'full source changed after cache restore')
        state['sourceMapping'] = mapping
        checkpoint('source-cache-complete')
        state['linuxAfter'] = capacity(paths['output'].parent)
        state['status'] = 'source-cache-phase-completed'
        _cache.original._write_exclusive(Path(output) / 'result.json', _cache.content.encoded(state))
        evidence.snapshot('completed')
        return state
    except BaseException as error:
        state['status'] = 'failed'; state['error'] = repr(error)
        state['ownedVolumes'] = sorted(set(state['ownedVolumes']) | set(engine.volumes))
        if 'cache' not in state:
            state['cacheResourceObservationIncomplete'] = True
            state['cacheEvidenceDirectory'] = str(Path(output) / 'cache-restore')
            try:
                observed = _cache_failure_observation(Path(output) / 'cache-restore')
                if observed is not None: retain_cache(observed)
            except BaseException as observation_error: state['cacheObservationError'] = repr(observation_error)
        _cache.original._write_exclusive(Path(output) / 'result.json', _cache.content.encoded(state))
        evidence.snapshot('failed')
        raise
