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
