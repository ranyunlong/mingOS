"""Local, externally pinned S2 proof consumption and S3 harness freezing.

This module does not invoke a process, admit a live daemon, transfer an input,
or build an application. The S2 verdict is point-in-time independent evidence;
subsequent orchestration still needs the original ready reader and live admission.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat


CHUNK_BYTES = 1024 * 1024
MAX_JSON_BYTES = 32 * 1024 * 1024
OBSERVER_CODE_SHA256 = '53ee6b3c0848d0c42b961fbcf668948e8546377123f22feeedf7e20174ec7d95'
WITNESS_CODE_SHA256 = 'ca827d6d84cf3e34fd1c5cc8b74bff5af2923b633fc2724606be276d263edb1f'
HELPER_NAMES = frozenset(('wsl_environment.py', 'wsl_environment_guest.py', 'docker_transport.py',
                          'development_image_migration.py', 'qualification_image_archive.py'))
GATE_NAMES = (
    'reviewed-committed-frozen-closure', 'source-and-affected-controls',
    's1-provenance-and-strict-input-pins', 'explicit-endpoint-and-complete-command-records',
    'new-distro-vhd-daemon-store', 'rootfs-bundle-tools-and-binaries',
    'historical-fsynced-first-empty-and-order',
    'three-fixed-index-config-ordered-rootfs-identities',
    'actual-daemon-environment-and-helper-trust', 'actual-containerd-namespace-pair-and-cli',
    'private-mount-propagation-visible-tmpfs-and-sockets',
    'private-cgroups-and-unchanged-shared-controls', 'managed-containerd-identity-and-paths',
    'configured-and-effective-retained-smoke', 'complete-old-resource-preservation',
    'dns-equivalence-set-and-schema', 'independent-standalone-before-after-witness',
    'public-observe-and-explicit-linux-exec', 'store-completeness-and-retained-state',
    'sealed-evidence-exact-membership-and-pins', 'final-receipt-and-scope-flags',
)
_VERDICT_REFS = frozenset(('environmentReceipt', 'codePins', 'request', 'observerResult',
    'observerController', 'standaloneWitness', 'standaloneBefore', 'standaloneAfter',
    'publicObserveReceipt', 'publicExecReceipt', 'evidenceManifest', 'frozenControls',
    'sourceReview', 'markdownReport'))
_CLOSURE_REFS = frozenset(('environmentReceipt', 'codePins', 'request', 'repositoryBeforeLaunch',
                         'frozenManifest', 'sourceReview', 'frozenControls'))
_EXCLUSIONS = frozenset(('environment/distro', 'qualification-evidence'))


def _object(value, names):
    if type(value) is not dict or set(value) != set(names):
        raise ValueError('object fields differ: ' + ','.join(sorted(names)))
    return value


def _text(value, *, empty=False):
    if type(value) is not str or (not empty and not value):
        raise ValueError('string required')
    return value


def _int(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('nonnegative integer required')
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('finite number required')


def _same(value, expected):
    """Compare proof graphs without Python's bool/integer equality aliases."""
    if type(value) is not type(expected):
        return False
    if type(value) is dict:
        return set(value) == set(expected) and all(_same(value[k], expected[k]) for k in expected)
    if type(value) is list:
        return len(value) == len(expected) and all(_same(a, b) for a, b in zip(value, expected))
    return value == expected


def _literal(value, expected):
    if not _same(value, expected):
        raise ValueError('literal differs: ' + str(expected))


def _hex(value, length=64):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{%d}' % length, value):
        raise ValueError('lowercase hexadecimal digest required')
    return value


def _relative(value):
    value = _text(value)
    if ('\\' in value or ':' in value or value.startswith('/')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in value.split('/'))
            or PurePosixPath(value).as_posix() != value):
        raise ValueError('canonical relative POSIX name required')
    for part in value.split('/'):
        stem = part.split('.', 1)[0].upper()
        if stem in ('CON', 'PRN', 'AUX', 'NUL') or re.fullmatch('(COM|LPT)[1-9]', stem):
            raise ValueError('nonportable reserved name')
    return value


def _unique_names(names):
    seen = set()
    for name in names:
        _relative(name)
        if name.casefold() in seen:
            raise ValueError('aliased relative names')
        seen.add(name.casefold())


def _path(value):
    value = _text(os.fspath(value))
    if (not Path(value).is_absolute() or os.path.normpath(value) != value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('canonical absolute host path required')
    p = Path(value)
    for ancestor in (*reversed(p.parents), p):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('linked or reparse path forbidden')
    return p


def _path_text(value):
    """Validate guest provenance structurally; never inspect the host filesystem."""
    value = _text(value)
    if (not value.startswith('/') or value == '/' or value.endswith('/')
            or '\\' in value or '//' in value
            or any(p in ('.', '..') for p in value.split('/'))
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('canonical absolute POSIX guest file path required')


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _open(path):
    path = _path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError('single-link regular input required')
    directory_fds = []
    fd = None
    try:
        if os.open in os.supports_dir_fd and hasattr(os, 'O_NOFOLLOW'):
            directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            directory_fds.append(directory)
            for name in path.parts[1:-1]:
                directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                directory_fds.append(directory)
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        else:
            fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        opened = os.fstat(fd)
        # Windows lstat and fstat can expose different ctime meanings. Compare
        # identity/size across APIs, and each API's complete stamp to itself.
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (before.st_dev, before.st_ino, before.st_size) !=
                   (opened.st_dev, opened.st_ino, opened.st_size)):
            raise ValueError('input changed before opening')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            yield stream
            if _stamp(os.fstat(fd)) != _stamp(opened) or _stamp(_path(path).lstat()) != _stamp(before):
                raise ValueError('input changed while open')
    finally:
        if fd is not None:
            os.close(fd)
        for directory in reversed(directory_fds):
            os.close(directory)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def _decode(raw):
    def reject_constant(value):
        raise ValueError('non-finite JSON: ' + value)
    return json.loads(raw, object_pairs_hook=_pairs, parse_constant=reject_constant)


def _filepin(value):
    _object(value, ('bytes', 'sha256'))
    _int(value['bytes']); _hex(value['sha256'])
    return value


def _ref(value, *, full=True):
    _object(value, ('path', 'bytes', 'sha256') if full else ('path', 'sha256'))
    _path(value['path']); _hex(value['sha256'])
    if full:
        _int(value['bytes'])
    return value


def _pin_of(ref):
    return {k: ref[k] for k in ('bytes', 'sha256')}


class _Reader:
    """Hash bounded streams; retain metadata stamps for an end-of-read check."""
    def __init__(self):
        self.observed = {}

    def read(self, ref, *, json_value=False, full=True):
        _ref(ref, full=full)
        path = _path(ref['path'])
        digest = hashlib.sha256()
        size = 0
        chunks = [] if json_value else None
        with _open(path) as stream:
            before = _stamp(os.fstat(stream.fileno()))
            while chunk := stream.read(CHUNK_BYTES):
                size += len(chunk); digest.update(chunk)
                if chunks is not None:
                    if size > MAX_JSON_BYTES:
                        raise ValueError('JSON control record exceeds limit')
                    chunks.append(chunk)
            if digest.hexdigest() != ref['sha256'] or (full and size != ref['bytes']):
                raise ValueError('input pin differs: ' + str(path))
        key = str(path)
        if key in self.observed and self.observed[key] != before:
            raise ValueError('input changed between reads')
        self.observed[key] = before
        if json_value:
            return _decode(b''.join(chunks))
        return {'path': key, 'bytes': size, 'sha256': digest.hexdigest()}

    def recheck(self):
        for name, expected in self.observed.items():
            with _open(name) as stream:
                if _stamp(os.fstat(stream.fileno())) != expected:
                    raise ValueError('input changed during qualification')


def _flags(value, false_names, true_names=()):
    for key in false_names:
        _literal(value.get(key), False)
    for key in true_names:
        _literal(value.get(key), True)


def _codepins(value, *, exact=True):
    if type(value) is not dict or (exact and set(value) != HELPER_NAMES):
        raise ValueError('helper code map differs')
    _unique_names(value)
    for name, pin in value.items():
        if '/' in name:
            raise ValueError('helper basename required')
        _filepin(pin)


def _spec(value):
    _object(value, ('schemaVersion', 's2Acceptance', 'environment', 'handoff',
                    'cacheDescriptorSha256', 'harness', 'generation', 'limits'))
    _literal(value['schemaVersion'], 1)
    _ref(value['s2Acceptance'], full=False); _ref(value['environment'], full=False)
    handoff = _object(value['handoff'], ('path', 'sha256', 'bytes', 'manifestSha256', 'sourceDescriptorSha256'))
    _ref({k: handoff[k] for k in ('path', 'bytes', 'sha256')})
    _hex(handoff['manifestSha256']); _hex(handoff['sourceDescriptorSha256'])
    _hex(value['cacheDescriptorSha256'])
    _object(value['harness'], ('path', 'manifestSha256'))
    _path(value['harness']['path']); _hex(value['harness']['manifestSha256'])
    if type(value['generation']) is not str or not re.fullmatch('[a-z0-9][a-z0-9-]{0,47}', value['generation']):
        raise ValueError('invalid S3 generation')
    limits = _object(value['limits'], ('transferTimeoutSeconds', 'phaseTimeoutSeconds',
                                     'hostReserveBytes', 'linuxReserveBytes', 'exportMaxBytes'))
    for amount in limits.values():
        _int(amount, 1)


def _walk(root, exclusions=()):
    root = _path(root)
    if not root.is_dir():
        raise ValueError('directory input required')
    directories, files = {}, {}
    pending = [root]
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            name = child.relative_to(root).as_posix()
            _relative(name)
            info = _path(child).lstat()
            if name in exclusions:
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError('excluded directory type differs')
                continue
            if stat.S_ISDIR(info.st_mode):
                directories[name] = stat.S_IMODE(info.st_mode)
                pending.append(child)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                files[name] = stat.S_IMODE(info.st_mode)
            else:
                raise ValueError('linked or special tree entry')
    _unique_names([*directories, *files])
    return directories, files


def _inventory(reader, ref, generation, distro):
    value = reader.read(ref, json_value=True)
    _object(value, ('schemaVersion', 'kind', 'status', 'generation', 'distro', 'pathBase',
                    'directories', 'exclusions', 'files', 'modeSemantics',
                    'formalQualification', 'finalSystemAccepted', 'coldBuildExecuted', 'p01Completed'))
    _literal(value['schemaVersion'], 1)
    _literal(value['kind'], 'p01-s2-retained-evidence-inventory')
    _literal(value['status'], 'sealed-awaiting-independent-review')
    _literal(value['generation'], generation); _literal(value['distro'], distro)
    _text(value['modeSemantics'])
    _flags(value, ('formalQualification', 'finalSystemAccepted', 'coldBuildExecuted', 'p01Completed'))
    root = _path(value['pathBase'])
    if Path(ref['path']) != root / 'qualification-evidence/manifest.json':
        raise ValueError('evidence inventory location differs')
    exclusions = value['exclusions']
    if type(exclusions) is not list or len(exclusions) != 2:
        raise ValueError('evidence exclusions differ')
    excluded = []
    for entry in exclusions:
        _object(entry, ('path', 'reason')); _relative(entry['path']); _text(entry['reason'])
        excluded.append(entry['path'])
    if set(excluded) != _EXCLUSIONS or len(set(excluded)) != len(excluded):
        raise ValueError('evidence exclusions differ')
    if type(value['directories']) is not list or type(value['files']) is not dict:
        raise ValueError('evidence inventory collections required')
    _unique_names([*value['directories'], *value['files']])
    actual_dirs, actual_files = _walk(root, _EXCLUSIONS)
    if set(actual_dirs) != set(value['directories']) or set(actual_files) != set(value['files']):
        raise ValueError('immutable evidence membership differs')
    for name, pin in value['files'].items():
        _filepin(pin)
        reader.read({'path': str(root / name), **pin})
    return value


def _member(inventory, ref):
    path, root = _path(ref['path']), _path(inventory['pathBase'])
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError('required evidence ref is outside inventory') from error
    if inventory['files'].get(relative) != _pin_of(ref):
        raise ValueError('referenced evidence is not an exact inventory member')


def _verdict(value):
    _object(value, ('schemaVersion', 'stage', 'status', 'generation', 'distro', 'reviewedCommit',
                    'refs', 'checkedGates', 'dnsEquivalenceRecordsExpected', 'dnsEquivalenceRecordsObserved',
                    'unresolvedItems', 'scopeLimitations', 'formalQualification', 'finalSystemAccepted',
                    'coldBuildExecuted', 'p01Completed', 'resourcesPreserved'))
    _literal(value['schemaVersion'], 1); _literal(value['stage'], 'P01-S2'); _literal(value['status'], 'accepted')
    _text(value['generation']); _text(value['distro']); _hex(value['reviewedCommit'], 40)
    _object(value['refs'], _VERDICT_REFS)
    for ref in value['refs'].values():
        _ref(ref)
    _literal(value['dnsEquivalenceRecordsExpected'], 0); _literal(value['dnsEquivalenceRecordsObserved'], 0)
    _flags(value, ('formalQualification', 'finalSystemAccepted', 'coldBuildExecuted', 'p01Completed'), ('resourcesPreserved',))
    if type(value['unresolvedItems']) is not list or value['unresolvedItems']:
        raise ValueError('unresolved qualification items')
    if type(value['scopeLimitations']) is not list:
        raise ValueError('qualification scope array required')
    for text in value['scopeLimitations']:
        _text(text)
    gates = value['checkedGates']
    if type(gates) is not list or len(gates) != len(GATE_NAMES):
        raise ValueError('complete gate set required')
    names = []
    for gate in gates:
        _object(gate, ('name', 'result')); _text(gate['name']); _text(gate['result'])
        names.append(gate['name'])
        allowed = ('passed', 'passed-empty-no-equivalence-applied') if gate['name'] == 'dns-equivalence-set-and-schema' else ('passed',)
        if gate['result'] not in allowed:
            raise ValueError('gate did not pass')
    if set(names) != set(GATE_NAMES) or len(set(names)) != len(names):
        raise ValueError('unknown, missing or duplicate gate')


def _handle(value, generation):
    _object(value, ('pid', 'starttime', 'exeSha256', 'namespaces', 'socket',
                    'containerdNamespaces', 'daemonId', 'generation'))
    _int(value['pid'], 1); _text(value['starttime']); _hex(value['exeSha256'])
    _literal(value['generation'], generation); _text(value['daemonId'])
    _object(value['namespaces'], ('mnt', 'net', 'cgroup'))
    for name in value['namespaces'].values(): _text(name)
    _object(value['socket'], ('device', 'inode', 'uid', 'gid'))
    for amount in value['socket'].values(): _int(amount)
    _literal(value['socket']['uid'], 0); _literal(value['socket']['gid'], 0)
    _object(value['containerdNamespaces'], ('Containers', 'Plugins'))
    _literal(value['containerdNamespaces']['Containers'], generation)
    _literal(value['containerdNamespaces']['Plugins'], generation + '-plugins')


def _proof(reader, acceptance, environment):
    _object(acceptance, ('schemaVersion', 'stage', 'status', 'environmentReceiptSha256',
                         'reviewedSourceClosure', 'actualVerifierRecord'))
    _literal(acceptance['schemaVersion'], 1); _literal(acceptance['stage'], 'P01-S2'); _literal(acceptance['status'], 'accepted')
    _hex(acceptance['environmentReceiptSha256'])
    closure = reader.read(acceptance['reviewedSourceClosure'], json_value=True, full=False)
    verdict = reader.read(acceptance['actualVerifierRecord'], json_value=True, full=False)
    _object(closure, (*_CLOSURE_REFS, 'schemaVersion', 'kind', 'reviewedCommit', 'generation',
                      'distro', 'files', 'hostModeSemantics'))
    _literal(closure['schemaVersion'], 1); _literal(closure['kind'], 'p01-s2-reviewed-source-closure')
    _hex(closure['reviewedCommit'], 40); _text(closure['generation']); _text(closure['distro']); _text(closure['hostModeSemantics'])
    _verdict(verdict)
    for key in ('generation', 'distro', 'reviewedCommit'):
        _literal(closure[key], verdict[key])
    for name in _CLOSURE_REFS:
        _ref(closure[name])
        reader.read(closure[name])
    for name in _VERDICT_REFS:
        reader.read(verdict['refs'][name])
    for name in ('environmentReceipt', 'codePins', 'request', 'sourceReview', 'frozenControls'):
        if closure[name] != verdict['refs'][name]:
            raise ValueError('repeated proof reference differs: ' + name)
    expected_env = closure['environmentReceipt']
    if ({k: expected_env[k] for k in ('path', 'sha256')} != environment
            or expected_env['sha256'] != acceptance['environmentReceiptSha256']):
        raise ValueError('qualification environment binding differs')
    gen, distro = closure['generation'], closure['distro']
    inventory = _inventory(reader, verdict['refs']['evidenceManifest'], gen, distro)
    for name in ('environmentReceipt', 'codePins', 'request', 'repositoryBeforeLaunch', 'frozenManifest'):
        _member(inventory, closure[name])
    for name in ('observerResult', 'observerController', 'standaloneWitness', 'standaloneBefore',
                 'standaloneAfter', 'publicObserveReceipt', 'publicExecReceipt'):
        _member(inventory, verdict['refs'][name])
    pins = reader.read(closure['codePins'], json_value=True); _codepins(pins)
    frozen = reader.read(closure['frozenManifest'], json_value=True)
    _object(frozen, ('root', 'files')); _path(frozen['root']); _object(frozen['files'], HELPER_NAMES)
    _object(closure['files'], HELPER_NAMES)
    for name in HELPER_NAMES:
        entry = closure['files'][name]
        _object(entry, ('bytes', 'sha256', 'public', 'frozen', 'hostMode'))
        _filepin({k: entry[k] for k in ('bytes', 'sha256')}); _int(entry['hostMode'])
        original = _object(frozen['files'][name], ('bytes', 'sha256', 'mode'))
        _int(original['mode']); _filepin({k: original[k] for k in ('bytes', 'sha256')})
        if _pin_of(entry) != pins[name] or _pin_of(original) != pins[name] or entry['hostMode'] != original['mode']:
            raise ValueError('helper pins or host provenance modes differ')
        for role in ('public', 'frozen'):
            ref = entry[role]; _ref(ref); _member(inventory, ref); reader.read(ref)
            if _pin_of(ref) != pins[name] or Path(ref['path']).name != name:
                raise ValueError('helper source bytes differ')
        if Path(entry['frozen']['path']) != Path(frozen['root']) / name:
            raise ValueError('frozen helper root differs')
    dirs, files = _walk(Path(frozen['root']))
    if dirs or set(files) != HELPER_NAMES | {'manifest.json'}:
        raise ValueError('frozen helper closure differs')
    request = reader.read(closure['request'], json_value=True)
    _object(request, ('generation', 'distro', 'reviewedHead', 'archivePreparationResult', 'archivePreparationPin',
                      'inputAdmission', 'argv', 'codePins', 'createdAt', 'formalQualification', 'finalSystemAccepted', 'coldBuildExecuted'))
    _literal(request['generation'], gen); _literal(request['distro'], distro); _literal(request['reviewedHead'], closure['reviewedCommit'])
    _path(request['archivePreparationResult']); _path(request['inputAdmission']); _number(request['createdAt'])
    _codepins(request['codePins']); _literal(request['codePins'], pins)
    if type(request['argv']) is not list or not request['argv']:
        raise ValueError('original explicit argv required')
    for arg in request['argv']: _text(arg)
    _flags(request, ('formalQualification', 'finalSystemAccepted', 'coldBuildExecuted'))
    _filepin(request['archivePreparationPin'])
    reader.read({'path': request['archivePreparationResult'], **request['archivePreparationPin']})
    admission_name = _path(request['inputAdmission']).relative_to(Path(inventory['pathBase'])).as_posix()
    if admission_name not in inventory['files']:
        raise ValueError('original input admission missing from inventory')
    repo = reader.read(closure['repositoryBeforeLaunch'], json_value=True)
    _object(repo, ('reviewedCommit', 'head', 'gitStatusShort', 'trackedWorktreeAndIndexStatus', 'observedAt'))
    _literal(repo['head'], closure['reviewedCommit']); _literal(repo['reviewedCommit'], closure['reviewedCommit'])
    _literal(repo['gitStatusShort'], ''); _literal(repo['trackedWorktreeAndIndexStatus'], ''); _number(repo['observedAt'])
    controls = reader.read(closure['frozenControls'], json_value=True)
    _object(controls, ('status', 'container', 'interpreter', 'testsRun', 'passed', 'skips', 'failures', 'errors',
                       'codePins', 'actualWSLCreated', 'formalQualification', 'finalSystemAccepted', 'resourcesPreserved'))
    _literal(controls['status'], 'passed'); _text(controls['container'])
    _object(controls['interpreter'], ('python', 'tomllib')); _text(controls['interpreter']['python']); _path_text(controls['interpreter']['tomllib'])
    for key in ('testsRun', 'passed', 'failures', 'errors'): _int(controls[key])
    _literal(controls['failures'], 0); _literal(controls['errors'], 0)
    if type(controls['skips']) is not list:
        raise ValueError('control skips array required')
    for skip in controls['skips']:
        if type(skip) is not list or len(skip) != 2: raise ValueError('control skip pair required')
        for text in skip: _text(text)
    if controls['testsRun'] != controls['passed'] + len(controls['skips']):
        raise ValueError('control counts differ')
    _codepins(controls['codePins'], exact=False)
    if any(controls['codePins'].get(name) != pins[name] for name in HELPER_NAMES):
        raise ValueError('control source closure differs')
    _flags(controls, ('actualWSLCreated', 'formalQualification', 'finalSystemAccepted'), ('resourcesPreserved',))
    collector = reader.read(verdict['refs']['observerResult'], json_value=True)
    _object(collector, ('status', 's2Accepted', 'p01Completed', 'formal', 'formalQualification', 'finalSystemAccepted',
                        'coldBuildExecuted', 'resourcesPreserved', 'independentReviewRequired'))
    _literal(collector['status'], 'independent-public-followups-and-readonly-evidence-collected')
    _flags(collector, ('s2Accepted', 'p01Completed', 'formal', 'formalQualification', 'finalSystemAccepted', 'coldBuildExecuted'),
           ('resourcesPreserved', 'independentReviewRequired'))
    for role, literal, pin_name in (('observerController', OBSERVER_CODE_SHA256, 'controller-pin.json'),
                                    ('standaloneWitness', WITNESS_CODE_SHA256, 'witness-pin.json')):
        ref = verdict['refs'][role]
        _literal(ref['sha256'], literal)
        pin_path = Path(ref['path']).parent / pin_name
        rel = pin_path.relative_to(Path(inventory['pathBase'])).as_posix()
        if rel not in inventory['files']: raise ValueError('independent code pin missing')
        pin = reader.read({'path': str(pin_path), **inventory['files'][rel]}, json_value=True)
        _filepin(pin)
        if pin != _pin_of(ref): raise ValueError('independent code identity differs')
    env = reader.read(expected_env, json_value=True)
    if type(env) is not dict: raise ValueError('environment object required')
    _literal(env.get('schemaVersion'), 1); _literal(env.get('generation'), gen); _literal(env.get('distro'), distro)
    _literal(env.get('status'), 'environment-ready'); _literal(env.get('phase'), 'environment-ready')
    _flags(env, ('formalQualification', 'finalSystemAccepted', 'coldBuildExecuted'), ('resourcesPreserved',))
    if not _same(env.get('helpers'), frozen): raise ValueError('environment frozen helper graph differs')
    if env.get('guestCodeRoot') != '/root/' + gen + '-code': raise ValueError('guest code root differs')
    _handle(env.get('handle'), gen)
    if env.get('load', {}).get('completedLoads') != ['rust', 'tools', 'kotlin']:
        raise ValueError('environment load closure differs')
    old = env.get('oldResources', {})
    if old.get('status') != 'preserved' or type(old.get('equivalences')) is not list or old['equivalences']:
        raise ValueError('staged accepted DNS equivalence set must be empty')
    _followups(reader, verdict['refs'], env)
    return {'closure': closure, 'verdict': verdict, 'environment': env, 'inventory': inventory}


def _followups(reader, refs, env):
    handle = env['handle']
    identity = {k: handle[k] for k in ('pid', 'starttime', 'exeSha256', 'namespaces')}
    for name in ('standaloneBefore', 'standaloneAfter'):
        value = reader.read(refs[name], json_value=True)
        if (type(value) is not dict or not _same(value.get('identity'), identity) or not _same(value.get('identityAfter'), identity)
                or not _same(value.get('socket'), handle['socket']) or value.get('socketIsSocket') is not True
                or value.get('info', {}).get('ID') != handle['daemonId']
                or not _same(value.get('info', {}).get('Containerd', {}).get('Namespaces'), handle['containerdNamespaces'])):
            raise ValueError('independent witness identity crosslink differs')
    for name, operation in (('publicObserveReceipt', 'observed'), ('publicExecReceipt', 'executed')):
        value = reader.read(refs[name], json_value=True)
        if type(value) is not dict: raise ValueError('public followup object required')
        for key in ('generation', 'distro', 'helpers', 'handle', 'guestCodeRoot'):
            if not _same(value.get(key), env[key]): raise ValueError('public followup identity differs')
        _literal(value.get('schemaVersion'), 1); _literal(value.get('status'), 'environment-ready')
        _literal(value.get('operationStatus'), operation)
        _flags(value, ('formalQualification', 'finalSystemAccepted', 'coldBuildExecuted'), ('resourcesPreserved',))
        result = value.get('result')
        if type(result) is not dict: raise ValueError('public followup result required')
        if operation == 'observed':
            observed = result.get('handle')
            if type(observed) is not dict or any(not _same(observed.get(k), v) for k, v in handle.items() if k != 'generation'):
                raise ValueError('public observe handle differs')
        else:
            _literal(result.get('exitCode'), 0)
            _text(result.get('stdout'), empty=True); _text(result.get('stderr'), empty=True)


def _parents_declared(directories, files):
    _unique_names([*directories, *files])
    for name in [*directories, *files]:
        parent = PurePosixPath(name).parent
        while parent.as_posix() != '.':
            if parent.as_posix() not in directories:
                raise ValueError('undeclared parent directory')
            parent = parent.parent


def _harness(reader, spec):
    root = _path(spec['path'])
    manifest_ref = {'path': str(root / 'manifest.json'), 'sha256': spec['manifestSha256']}
    value = reader.read(manifest_ref, json_value=True, full=False)
    _object(value, ('schemaVersion', 'kind', 'checkout', 'directories', 'files'))
    _literal(value['schemaVersion'], 1); _literal(value['kind'], 'p01-s3-harness-input')
    checkout = _object(value['checkout'], ('path', 'reviewedCommit', 'dirtyFiles'))
    checkout_root = _path(checkout['path']); _hex(checkout['reviewedCommit'], 40)
    if not checkout_root.is_dir() or type(checkout['dirtyFiles']) is not dict:
        raise ValueError('checkout provenance required')
    _unique_names(checkout['dirtyFiles'])
    for name, pin in checkout['dirtyFiles'].items():
        path = _path(checkout_root / name)
        if pin is None:
            if path.exists(): raise ValueError('recorded deletion exists')
        else:
            _filepin(pin); reader.read({'path': str(path), **pin})
    directories, files = value['directories'], value['files']
    if type(directories) is not dict or type(files) is not dict or not files or 'manifest.json' in files:
        raise ValueError('explicit harness closure required')
    _parents_declared(directories, files)
    actual_dirs, actual_files = _walk(root)
    if set(actual_dirs) != set(directories) or set(actual_files) != set(files) | {'manifest.json'}:
        raise ValueError('harness membership differs')
    for name, entry in directories.items():
        _object(entry, ('hostMode', 'transferMode')); _int(entry['hostMode']); _literal(entry['transferMode'], 0o555)
        if actual_dirs[name] != entry['hostMode']: raise ValueError('source directory host mode differs')
    for name, entry in files.items():
        _object(entry, ('bytes', 'sha256', 'hostMode', 'transferMode'))
        _filepin(_pin_of(entry)); _int(entry['hostMode']); _int(entry['transferMode'])
        if entry['transferMode'] not in (0o444, 0o555) or actual_files[name] != entry['hostMode']:
            raise ValueError('source file mode/transfer contract differs')
        reader.read({'path': str(root / name), **_pin_of(entry)})
    return value


def qualify_source_build(spec: Path, spec_sha256: str) -> dict:
    """Read-only local proof gate. The supplied spec hash is the trust input."""
    return _qualify_source_build(spec, spec_sha256)[0]


def _qualify_source_build(spec, spec_sha256):
    """Keep protected-input metadata private to this qualification execution."""
    reader = _Reader()
    spec_ref = {'path': str(_path(spec)), 'sha256': _hex(spec_sha256)}
    value = reader.read(spec_ref, json_value=True, full=False)
    _spec(value)
    acceptance = reader.read(value['s2Acceptance'], json_value=True, full=False)
    proof = _proof(reader, acceptance, value['environment'])
    handoff = {k: value['handoff'][k] for k in ('path', 'bytes', 'sha256')}
    reader.read(handoff)
    harness = _harness(reader, value['harness'])
    reader.recheck()
    result = {'schemaVersion': 1, 'status': 'source-build-qualified-local',
            'specRef': spec_ref, 'spec': value,
            's2Acceptance': value['s2Acceptance'], 'environment': value['environment'],
            'reviewedSourceClosure': acceptance['reviewedSourceClosure'],
            'actualVerifierRecord': acceptance['actualVerifierRecord'],
            's2Generation': proof['closure']['generation'], 's2Distro': proof['closure']['distro'],
            'reviewedS2Commit': proof['closure']['reviewedCommit'],
            'evidenceFiles': len(proof['inventory']['files']), 'evidenceDirectories': len(proof['inventory']['directories']),
            'harnessManifestSha256': value['harness']['manifestSha256'], 'checkout': harness['checkout'],
            'formalQualification': False, 'finalSystemAccepted': False,
            'coldBuildExecuted': False, 'guestStarted': False, 'resourcesPreserved': True}
    protected = {'files': tuple(Path(name) for name in reader.observed),
                 'inventory': Path(proof['inventory']['pathBase']),
                 'closures': (Path(proof['environment']['helpers']['root']), Path(value['harness']['path'])),
                 'dirty': tuple(Path(harness['checkout']['path']) / name
                                for name in harness['checkout']['dirtyFiles'])}
    return result, protected


def _overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def _protect_freeze_output(output, protected):
    for path in (*protected['files'], *protected['dirty']):
        if output == path or output in path.parents:
            raise ValueError('freeze output overlaps protected proof or checkout provenance')
    if any(_overlap(output, root) for root in protected['closures']):
        raise ValueError('freeze output changes an exact input closure')
    root = protected['inventory']
    if output == root or output in root.parents:
        raise ValueError('freeze output overlaps evidence root')
    if root in output.parents and not any(
            root / name in output.parents for name in _EXCLUSIONS):
        raise ValueError('freeze output changes immutable evidence membership')


def _sync_directory(path):
    if os.name != 'nt':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0))
        try: os.fsync(fd)
        finally: os.close(fd)


def _write_json(stream, value):
    stream.seek(0); stream.truncate()
    stream.write((json.dumps(value, indent=2, sort_keys=True) + '\n').encode())
    stream.flush(); os.fsync(stream.fileno())


def freeze_harness(spec: dict, qualification: dict, *, output_directory: Path) -> dict:
    """Requalify, then exclusively copy the explicitly pinned local harness."""
    output = _path(output_directory)
    if type(qualification) is not dict or qualification.get('status') != 'source-build-qualified-local':
        raise ValueError('local qualification result required')
    ref = _ref(qualification.get('specRef'), full=False)
    fresh, protected = _qualify_source_build(Path(ref['path']), ref['sha256'])
    if not _same(fresh, qualification) or not _same(fresh['spec'], spec):
        raise ValueError('caller qualification/spec differs from revalidated proof')
    _protect_freeze_output(output, protected)
    source = _path(spec['harness']['path'])
    reader = _Reader()
    manifest = _harness(reader, spec['harness'])
    output.mkdir()
    _sync_directory(output.parent)
    result = {'schemaVersion': 1, 'status': 'creating', 'phase': 'local-harness-copy',
              'qualification': fresh, 'files': {}, 'directories': {},
              'formalQualification': False, 'finalSystemAccepted': False,
              'coldBuildExecuted': False, 'guestStarted': False, 'resourcesPreserved': True}
    with (output / 'freeze-result.json').open('xb') as receipt:
        _write_json(receipt, result)
        try:
            target = output / 'harness'; target.mkdir()
            result['root'] = {'path': str(target), 'hostMode': stat.S_IMODE(target.stat().st_mode), 'transferMode': 0o555}
            for name in sorted(manifest['directories'], key=lambda x: (len(PurePosixPath(x).parts), x)):
                p = target / name; p.mkdir()
                result['directories'][name] = {'hostMode': stat.S_IMODE(p.stat().st_mode), 'transferMode': 0o555}
                _sync_directory(p.parent)
            copy_names = [*manifest['files'], 'manifest.json']
            for name in copy_names:
                original = source / name; destination = target / name
                digest = hashlib.sha256(); size = 0
                with _open(original) as incoming, destination.open('xb') as outgoing:
                    while chunk := incoming.read(CHUNK_BYTES):
                        outgoing.write(chunk); digest.update(chunk); size += len(chunk)
                    outgoing.flush(); os.fsync(outgoing.fileno())
                pin = {'bytes': size, 'sha256': digest.hexdigest()}
                expected = _pin_of(manifest['files'][name]) if name != 'manifest.json' else {'sha256': spec['harness']['manifestSha256'], 'bytes': size}
                if pin != expected: raise ValueError('harness input changed during copy')
                observed = reader.read({'path': str(destination), **pin})
                if name != 'manifest.json':
                    result['files'][name] = {**_pin_of(observed), 'hostMode': stat.S_IMODE(destination.stat().st_mode),
                                             'transferMode': manifest['files'][name]['transferMode']}
                else:
                    result['manifest'] = observed
                _sync_directory(destination.parent)
            _harness(reader, spec['harness']); reader.recheck()
            dirs, files = _walk(target)
            if set(dirs) != set(manifest['directories']) or set(files) != set(manifest['files']) | {'manifest.json'}:
                raise ValueError('frozen local copy membership differs')
            result['status'] = 'source-build-harness-frozen-local'; result['phase'] = 'complete'
            _write_json(receipt, result); _sync_directory(output)
            return result
        except BaseException as error:
            result['status'] = 'failed'; result['error'] = repr(error)
            _write_json(receipt, result); _sync_directory(output)
            raise


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    parser.error('only local Python qualify_source_build/freeze_harness APIs are implemented at this stage')


if __name__ == '__main__':
    main()
