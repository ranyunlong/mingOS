#!/usr/bin/env python3
"""Recheck actual raw uninstall boots, signatures, and snapshots without running a guest.

pair(root) consumes the exported layout: inputs/{session-review.json,payload/,
input-boot1.json,input-boot2.json}, bundle/, fixture/, and pair/{boot-1/,boot-2/,
result.json}. Each boot uses vm.py's actual result.json, guest.jsonl, inputs.jsonl,
qmp.jsonl, qmp.json and kernel-serial.log. Desktop outcome bytes come from the
contiguous base64 desktop.log chunks in guest.jsonl, never a success summary.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
REPLAY_MODULES = ('bootlib.py', 'infra.py', 'persistent_uninstall_receipt.py', 'wayland_receipt.py',
                  'bundle.py', 'isolation_profiles.py', 'payload_profiles.py')
EXPORT_MANIFEST = 'file-sha256.json'


def export_require(value, message):
    if not value: raise ValueError('portable export: ' + message)


def export_json(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            export_require(key not in value, 'duplicate JSON key')
            value[key] = item
        return value
    def invalid(value): raise ValueError('portable export: non-finite JSON value')
    def finite(value):
        parsed = float(value)
        export_require(math.isfinite(parsed), 'non-finite JSON number')
        return parsed
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)


def export_path(name):
    export_require(isinstance(name, str) and 1 <= len(name) <= 1024 and '\\' not in name and '\0' not in name,
                   'invalid relative path')
    value = PurePosixPath(name)
    export_require(value.parts and not value.is_absolute() and str(value) == name and all(p not in ('.', '..') for p in value.parts),
                   'noncanonical/escaping relative path')
    return name


def export_digest(value):
    export_require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None, 'invalid SHA256')
    return value


def export_read(path, limit=None, digest_only=False):
    """Read/hash one pinned regular file without loading disk images in memory."""
    path = Path(path).absolute()
    for ancestor in (path, *path.parents):
        info = ancestor.lstat()
        export_require(not ancestor.is_symlink() and not (getattr(info, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)), 'reparse/symlink file ancestry')
    before = path.lstat()
    export_require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                   and (limit is None or before.st_size <= limit), 'unsafe/oversized file: ' + str(path))
    # Windows lstat and CRT fstat expose different ctime meanings. Linux keeps
    # its full metadata-change stamp; Windows uses its explicit birthtime.
    stamp = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns,
                          info.st_ctime_ns if os.name != 'nt' else getattr(info, 'st_birthtime_ns', None))
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        export_require(stamp(os.fstat(stream.fileno())) == stamp(before), 'file changed before read')
        digest, chunks, size = hashlib.sha256(), [], 0
        while True:
            block = stream.read(1024 * 1024)
            if not block: break
            size += len(block); digest.update(block)
            export_require(size <= before.st_size and (limit is None or size <= limit), 'file grew during read')
            if not digest_only: chunks.append(block)
        export_require(size == before.st_size and stamp(os.fstat(stream.fileno())) == stamp(before)
                       and stamp(path.lstat()) == stamp(before), 'file changed during read')
    return digest.hexdigest() if digest_only else b''.join(chunks)


def export_files(root):
    export_require(root.is_dir() and not root.is_symlink(), 'export root must be a real directory')
    found, work, directories = set(), [(root, 0)], 0
    while work:
        directory, depth = work.pop(); directories += 1
        export_require(depth <= 32 and directories <= 65536, 'directory bound')
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path); info = path.lstat()
                export_require(not (getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)),
                               'reparse member forbidden')
                if stat.S_ISDIR(info.st_mode): work.append((path, depth + 1))
                else:
                    export_require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'symlink/special/hardlinked member')
                    found.add(export_path(path.relative_to(root).as_posix()))
                    export_require(len(found) <= 65536, 'file count bound')
    return found


def verify_export_manifest(root):
    """Validate the complete exported bytes before importing any replay module."""
    root = Path(root).absolute(); manifest_raw = export_read(root / EXPORT_MANIFEST, 16 * 1024 * 1024)
    manifest = export_json(manifest_raw)
    export_require(type(manifest) is dict and set(manifest) == {'schemaVersion', 'algorithm', 'files'}
                   and type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 1
                   and manifest['algorithm'] == 'sha256' and type(manifest['files']) is dict
                   and 1 <= len(manifest['files']) <= 65535, 'manifest schema/types')
    files = manifest['files']
    for name, digest in files.items():
        export_path(name); export_digest(digest)
        export_require(name != EXPORT_MANIFEST, 'manifest cannot reference itself')
    expected = set(files) | {EXPORT_MANIFEST}
    export_require(export_files(root) == expected, 'manifest file set differs')
    for name, digest in files.items():
        export_require(export_read(root / name, digest_only=True) == digest, 'file hash differs: ' + name)
    export_require(export_files(root) == expected and export_read(root / EXPORT_MANIFEST, 16 * 1024 * 1024) == manifest_raw,
                   'export changed during verification')
    required = {'freeze.json', 'checker.py', 'raw_boot_audit.py', 'input-boot1.json', 'input-boot2.json'}
    required.update('replay/scripts/boot/' + name for name in REPLAY_MODULES)
    export_require(required <= set(files), 'required replay source/reference missing')
    replay = {name for name in files if name.startswith('replay/')}
    export_require(replay == {'replay/scripts/boot/' + name for name in REPLAY_MODULES}, 'replay dependency set differs')
    freeze = export_json(export_read(root / 'freeze.json', 16 * 1024 * 1024))
    export_require(type(freeze) is dict and type(freeze.get('schemaVersion')) is int and freeze['schemaVersion'] == 1
                   and freeze.get('profile') == 'persistent-uninstall' and type(freeze.get('files')) is dict
                   and type(freeze.get('checker')) is dict and set(freeze['checker']) == {'path', 'sha256'}
                   and type(freeze.get('inputs')) is dict and set(freeze['inputs']) == {'boot1Sha256', 'boot2Sha256'},
                   'source freeze schema/types')
    frozen = freeze['files']
    for name, digest in frozen.items(): export_path(name); export_digest(digest)
    checker_path = export_path(freeze['checker']['path'])
    export_require(PurePosixPath(checker_path).name == 'checker.py'
                   and export_digest(freeze['checker']['sha256']) == files['checker.py']
                   == frozen.get(checker_path), 'checker differs from source freeze')
    raw_path = str(PurePosixPath(checker_path).parent / 'raw_boot_audit.py')
    export_require(files['raw_boot_audit.py'] == frozen.get(raw_path), 'raw auditor differs from source freeze')
    for name in REPLAY_MODULES:
        export_require(files['replay/scripts/boot/' + name] == frozen.get('scripts/boot/' + name),
                       'replay dependency differs from source freeze: ' + name)
    for number in (1, 2):
        export_require(files['input-boot%d.json' % number] == export_digest(freeze['inputs']['boot%dSha256' % number]),
                       'reference input differs from source freeze')
    return {'exportManifestSha256': hashlib.sha256(manifest_raw).hexdigest(), 'exportFileCount': len(files)}


PORTABLE = (HERE / EXPORT_MANIFEST).exists()
if PORTABLE:
    verify_export_manifest(HERE)
    sys.dont_write_bytecode = True
REPO = HERE / 'replay' if PORTABLE else Path('/repo') if HERE == Path('/prep') else HERE.parents[2]
sys.path.insert(0, str(REPO / 'scripts/boot'))
from bootlib import (PERSISTENT_UNINSTALL_ARGS, PERSISTENT_UNINSTALL_FIXTURE,
    persistent_uninstall_session_contract, checked_session, validate_bundle, validate_reviewed_input)
from infra import integer, validate_cold_pair
from persistent_uninstall_receipt import (PersistentUninstallReceipts, PACKAGE_ID, PACKAGE_VERSION,
    PRIVATE_CONTENT, bounded_json as relay_json, content_snapshot, private_content, removed_identity, exact,
    package_directory)

_spec = importlib.util.spec_from_file_location('uninstall_raw_audit', HERE / 'raw_boot_audit.py')
raw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(raw)
require = raw.require
PUBLIC_KEY = bytes.fromhex('ea4a6c63e29c520abef5507b132ec5f9954776aebebe7b92421eea691446d22c')
ACTIONS = ('initializedRoots', 'packagesInstalled', 'trustStoresProvisioned', 'catalogsCreated', 'sourcesCreated')
ROOT = '/data/mingos/install-root'
CATALOG = ROOT + '/catalog.json'
SOURCE = '/opt/mingos/install-source/app.json'


def bounded_json(data):
    value = relay_json(data)
    work = [value]
    while work:
        item = work.pop()
        require(type(item) is not float or math.isfinite(item), 'non-finite JSON number')
        if isinstance(item, dict): work.extend(item.values())
        elif isinstance(item, list): work.extend(item)
    return value


def read(path, limit=128 * 1024 * 1024):
    path = Path(path).absolute()
    for parent in (*reversed(path.parents), path.parent):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'unsafe evidence ancestor')
    return export_read(path, limit)


def json_file(path, limit=8 * 1024 * 1024):
    return bounded_json(read(path, limit))


def file_hash(path):
    return hashlib.sha256(read(path)).hexdigest()


def same_json(value, expected):
    return json.dumps(value, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)


def text(value):
    value = value.encode()
    return struct.pack('>Q', len(value)) + value


def openssl_verify(public, message, signature):
    binary = shutil.which('openssl')
    require(binary is not None, 'OpenSSL unavailable: actual Ed25519 verification required')
    binary = Path(binary).resolve()
    with tempfile.TemporaryDirectory(prefix='ming-uninstall-signature-') as directory:
        root = Path(directory)
        (root / 'public.der').write_bytes(bytes.fromhex('302a300506032b6570032100') + public)
        (root / 'signature').write_bytes(signature)
        (root / 'message').write_bytes(message)
        argv = [str(binary), 'pkeyutl', '-verify', '-pubin', '-inkey', str(root / 'public.der'),
                '-keyform', 'DER', '-rawin', '-in', str(root / 'message'), '-sigfile', str(root / 'signature')]
        valid = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        require(valid.returncode == 0, 'actual package signature verification failed')
        (root / 'message').write_bytes(message[:-1] + bytes([message[-1] ^ 1]))
        invalid = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        require(invalid.returncode == 1, 'tampered signature did not reject normally')
    return {'path': str(binary), 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}


def signed_package(manifest_path, package_id, version, expected_wasm):
    original = read(manifest_path, 16384)
    manifest = bounded_json(original)
    if type(manifest) is dict:
        manifest.setdefault('dataMigration', None)
    exact(manifest, ('schemaVersion', 'id', 'version', 'sdkVersion', 'entry', 'privateStorage', 'publicStorage',
                     'dataMigration', 'publisherKeyId', 'publisherSignature'))
    require(type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 1
            and manifest['id'] == package_id and manifest['version'] == version
            and type(manifest['sdkVersion']) is int and manifest['sdkVersion'] == 6
            and manifest['entry'] == 'main.wasm' and manifest['privateStorage'] is False
            and manifest['publicStorage'] is False and manifest['dataMigration'] is None
            and manifest['publisherKeyId'] == 'mingos-dev-2026', 'exact signed uninstall fixture manifest required')
    wasm = read(Path(manifest_path).parent / 'main.wasm', 4 * 1024 * 1024)
    require(wasm[:8] == b'\0asm\x01\0\0\0' and wasm == read(expected_wasm, 4 * 1024 * 1024),
            'signed fixture differs from packaged Wasm')
    message = (b'ming-package-signature-v1\0' + text('mingos-dev-2026') + struct.pack('>I', 1)
        + text(package_id) + text(version) + struct.pack('>I', 6) + text('main.wasm') + b'\0\0'
        + struct.pack('>Q', 4) + b'null' + struct.pack('>Q', len(wasm)) + wasm)
    require(isinstance(manifest['publisherSignature'], str), 'signature must be base64 string')
    try:
        signature = base64.b64decode(manifest['publisherSignature'], validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError('invalid signature base64') from error
    require(len(signature) == 64, 'Ed25519 signature length')
    executable = openssl_verify(PUBLIC_KEY, message, signature)
    fingerprint = (b'ming-private-storage-package-v1\0' + struct.pack('>I', 1) + text(package_id)
        + text(version) + text('main.wasm') + struct.pack('>I', 6) + struct.pack('>Q', len(wasm)) + wasm
        + b'\0publisher-key-id-v1' + text('mingos-dev-2026'))
    return {'packageId': package_id, 'version': version, 'fingerprint': hashlib.sha256(fingerprint).hexdigest(),
        'manifestSha256': hashlib.sha256(original).hexdigest(), 'wasmSha256': hashlib.sha256(wasm).hexdigest(),
        'signatureVerified': True, 'tamperedMessageRejected': True, 'openssl': executable}


def validate_input(spec, number):
    require(type(number) is int and number in (1, 2), 'fixed boot role required')
    from infra import InputPlan
    import io
    InputPlan(spec, io.StringIO(), 60, split_transactions=True, pointer_handshake=True)
    reference = json_file(HERE / f'input-boot{number}.json')
    require(same_json(spec, reference), 'input differs from fixed seven/one-step uninstall plan')
    return spec


def helper_argv(number, phase, review):
    require(type(number) is int and number in (1, 2) and phase in ('pre-desktop', 'post-desktop'), 'helper role')
    fixture = PERSISTENT_UNINSTALL_FIXTURE
    if number == 1 and phase == 'pre-desktop':
        return [fixture['helper'], 'initialize-first', ROOT, fixture['initialWasm'], fixture['sourceWasm'],
            SOURCE, '/opt/mingos/catalog.json', review['catalogSha256'], fixture['receipt']]
    return [fixture['helper'], 'open-uninstalled', ROOT, fixture['sourceWasm'], '/data/mingos', fixture['receipt']]


def validate_snapshot(value, boot_id, number, phase, session=None, pid=None):
    exact(value, ('envelope', 'content', 'removed', 'privateContent'))
    env = value['envelope']
    exact(env, ('schemaVersion', 'bootId', 'bootNumber', 'role', 'marker', 'previousMarker', 'diskUuid',
        'registryRoot', 'catalogPath', 'sessionId', 'desktopPid', 'phase', 'status', 'actions'))
    initial = number == 1 and phase == 'pre-desktop'
    require(type(env['schemaVersion']) is int and env['schemaVersion'] == 1
        and type(number) is int and number in (1, 2) and type(env['bootNumber']) is int and env['bootNumber'] == number
        and env['bootId'] == boot_id and env['phase'] == phase and phase in ('pre-desktop', 'post-desktop')
        and env['role'] == ('initialize-first' if number == 1 else 'open-uninstalled')
        and env['registryRoot'] == ROOT and env['catalogPath'] == CATALOG
        and isinstance(env['diskUuid'], str) and bool(env['diskUuid'])
        and env['status'] == ('initialized-first' if initial else 'opened-uninstalled'), 'snapshot envelope role/path/status')
    exact(env['marker'], ('bootId', 'counter'))
    require(env['marker']['bootId'] == boot_id and type(env['marker']['counter']) is int
        and env['marker']['counter'] == number, 'snapshot marker')
    if number == 1:
        require(env['previousMarker'] is None, 'first boot previous marker')
    else:
        exact(env['previousMarker'], ('bootId', 'counter'))
        require(isinstance(env['previousMarker']['bootId'], str) and env['previousMarker']['bootId']
            and env['previousMarker']['bootId'] != boot_id and type(env['previousMarker']['counter']) is int
            and env['previousMarker']['counter'] == 1, 'second boot previous marker')
    if phase == 'pre-desktop':
        require(env['sessionId'] is None and env['desktopPid'] is None, 'pre-desktop envelope has process')
    else:
        require(isinstance(session, str) and bool(session) and integer(pid, 2)
            and env['sessionId'] == session and type(env['desktopPid']) is int and env['desktopPid'] == pid,
            'post-desktop envelope process identity')
    exact(env['actions'], ACTIONS)
    require(all(type(count) is int and count == int(initial) for count in env['actions'].values()),
            'helper initialization actions differ by stage')
    if initial:
        require(value['removed'] is None and value['privateContent'] is None, 'initial removed/private evidence forbidden')
    else:
        removed_identity(value['removed'])
        private_content(value['privateContent'])
    content_snapshot(value['content'], removed=value['removed'])
    require(len(value['content']['files']) == (6 if initial else 8), 'snapshot complete file inventory')
    return value


def verify_fixture_directory(fixture, inputs, review):
    fixture, inputs = Path(fixture), Path(inputs)
    payload, install = inputs / 'payload', fixture / 'data/install-root'
    helper = payload / 'prepare-persistent-uninstall-fixture'
    command = json_file(fixture / 'command.json')
    original = PurePosixPath('/prep/build/fixture')
    staged = PurePosixPath('/prep/inputs/payload')
    expected = [str(staged / 'prepare-persistent-uninstall-fixture'), 'initialize-first', str(original / 'data/install-root'),
        str(staged / 'packages/initial/main.wasm'), str(staged / 'packages/new/main.wasm'), str(original / 'install-source/app.json'),
        str(staged / 'catalog.json'), review['catalogSha256'], str(original / 'fixture-receipt.json')]
    require(command.get('stage') == 'fixture' and command.get('commandRoot') == str(original)
        and command.get('inputsRoot') == '/prep/inputs' and command.get('argv') == expected
        and type(command.get('exitCode')) is int and command['exitCode'] == 0
        and command.get('executableSha256') == file_hash(helper) == review['persistentUninstallFixtureSha256']['helper'],
        'actual initialization helper command/hash/status differs')
    value = json_file(fixture / 'fixture-receipt.json', 262144)
    exact(value, ('schemaVersion', 'status', 'actions', 'content', 'removed', 'privateContent'))
    require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1 and value['status'] == 'initialized-first'
        and value['removed'] is None and value['privateContent'] is None, 'actual fixture initialization receipt')
    exact(value['actions'], ACTIONS)
    require(all(type(count) is int and count == 1 for count in value['actions'].values())
        and same_json(json_file(fixture / 'stdout.log', 262144), value), 'actual fixture stdout/actions differ')
    content_snapshot(value['content'])
    require(len(value['content']['files']) == 6, 'initial fixture contains history')
    found, directories = {}, set()
    for path in sorted(install.rglob('*')):
        info = path.lstat()
        name = path.relative_to(install).as_posix()
        if stat.S_ISDIR(info.st_mode):
            directories.add(name)
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'unsafe actual fixture member')
            if name in ('.ming-installed-packages.lock', '.ming-publisher-keyring.lock'):
                require(info.st_size == 0, 'nonempty fixture lock')
            else:
                found[name] = file_hash(path)
    require(all((install / name).is_file() for name in ('.ming-installed-packages.lock', '.ming-publisher-keyring.lock')),
            'fixture lock missing')
    require(found == {row['path']: row['sha256'] for row in value['content']['files']}, 'actual complete fixture inventory/hash differs')
    parents = {parent.as_posix() for name in found for parent in PurePosixPath(name).parents if str(parent) != '.'}
    require(directories == parents, 'actual extra/empty fixture directory')
    require({p.name for p in (fixture / 'data').iterdir()} == {'install-root'}, 'initial fixture reseeded private data')
    require(read(install / 'trusted-publishers-v1', 16384) == b'ming-trusted-publishers-v1\nmingos-dev-2026\tactive\t'
        + base64.b64encode(PUBLIC_KEY) + b'\n', 'actual fixed publisher keyring')
    app = value['content']['apps'][0]
    bootstrap = signed_package(install / app['manifestPath'], 'dev.ming.text-entry-demo', '0.1.0', payload / 'packages/initial/main.wasm')
    source = signed_package(fixture / 'install-source/app.json', PACKAGE_ID, PACKAGE_VERSION, payload / 'packages/new/main.wasm')
    require(all(app[key] == bootstrap[key] for key in ('packageId', 'version', 'fingerprint', 'manifestSha256', 'wasmSha256')),
        'bootstrap signature/content identity mismatch')
    require({p.name for p in (fixture / 'install-source').iterdir()} == {'app.json', 'main.wasm'}, 'source inventory')
    for label, package in (('initialWasm', bootstrap), ('sourceWasm', source)):
        require(package['wasmSha256'] == review['persistentUninstallFixtureSha256'][label], 'packaged fixture review hash differs')
    require(file_hash(payload / 'catalog.json') == review['catalogSha256'], 'reviewed catalog differs')
    return {'bootstrap': bootstrap, 'source': source, 'initialContent': value['content'],
        'helperSha256': file_hash(helper), 'receiptSha256': file_hash(fixture / 'fixture-receipt.json')}


def bind_signed_content(content, signed, installed=False, removed=None):
    content_snapshot(content, installed, removed)
    files = {row['path']: row['sha256'] for row in content['files']}
    for app in content['apps']:
        package = signed['bootstrap' if app['app'] == 1 else 'source']
        require(all(app[key] == package[key] for key in ('packageId', 'version', 'fingerprint', 'manifestSha256', 'wasmSha256')),
            'snapshot differs from independently verified signed fixture')
    if removed is not None:
        require(same_json(removed, {key: signed['source'][key] for key in ('packageId', 'version', 'fingerprint')}),
            'removed identity differs from signed fixture')
        directory = package_directory(removed)
        require(files.get(directory + '/app.json') == signed['source']['manifestSha256']
            and files.get(directory + '/main.wasm') == signed['source']['wasmSha256'], 'removed signed history changed')
    for key in ('bootstrapSha256', 'trustSha256'):
        require(content[key] == signed['initialContent'][key], 'static bootstrap/trust changed')


def pair(root):
    root = Path(root).absolute()
    export = verify_export_manifest(root)
    require(PORTABLE and root.resolve() == HERE, 'pair must replay its own sealed export')
    return _pair_verified(root, export)


def verify_terminal(outcome, parser, number, events):
    require(type(outcome.get('droppedInputs')) is int and outcome['droppedInputs'] == 0, 'dropped native inputs')
    trace, instances = outcome.get('trace'), outcome.get('instances')
    require(isinstance(trace, list) and len(trace) <= 512 and isinstance(instances, list)
        and 1 <= len(instances) <= 128, 'complete bounded outcome trace/history required')
    identities = set()
    for row in instances:
        parser.history(row)
        identity = parser.identity(row)
        require(identity not in identities and row['app'] in ({1, 2} if number == 1 else {1}),
                'duplicate/out-of-role full outcome worker')
        identities.add(identity)
        require(row['completed'] is True and row['status'] == 'closed' and row['error'] is None
            and integer(row['firstReadyMs']) and integer(row['framesSubmitted'], 1)
            and integer(row['presentedSourceSequence'], 1), 'worker lacks normal Ready/terminal/exit')
        frame = row.get('frameClock', {})
        require(set(frame) == {'source', 'closes', 'cancels', 'frames', 'inputs', 'pending', 'reads', 'timers', 'waits'}
            and frame['source'] == 'host-monotonic-opportunity'
            and all(type(v) is int and 0 <= v <= 2**64-1 for k, v in frame.items() if k != 'source')
            and frame['closes'] == 1 and frame['cancels'] == 0
            and frame['waits'] == sum(frame[k] for k in ('frames', 'timers', 'inputs', 'closes', 'cancels', 'pending')),
            'worker frame clock terminal/accounting differs')
        iso = row.get('isolation', {})
        require(iso.get('requested') == 'strict' and iso.get('error') is None
            and all(iso.get(k) is True for k in ('prepared', 'applied', 'cleaned')), 'strict worker isolation not completed')
    previous = {}
    for event in trace:
        exact(event, ('atMs', 'app', 'generation', 'window', 'kind', 'detail'))
        require(all(integer(event[k]) for k in ('atMs', 'app', 'generation', 'window'))
            and isinstance(event['kind'], str) and 1 <= len(event['kind']) <= 64
            and isinstance(event['detail'], str) and len(event['detail']) <= 262144, 'invalid full outcome trace')
        key = event['app'], event['generation'], event['window']
        require(event['atMs'] >= previous.get(key, 0), 'worker trace clock regressed')
        previous[key] = event['atMs']
        require(event['kind'] not in ('failed', 'close-deadline-cancel')
            and (number == 1 or (event['app'] != 2 and event['kind'] not in
                ('installed', 'uninstalled', 'persistent-install-commit', 'persistent-uninstall-commit'))),
            'failed/out-of-role full trace')
    for row in instances:
        matching = [(i, e) for i, e in enumerate(trace) if (e['app'], e['generation'], e['window'])
                    == (row['app'], row['generation'], row['window'])]
        kinds = ('ready', 'close-request', 'terminal-reply-written', 'result-accepted', 'worker-reaped', 'worker-result', 'closed')
        positions = []
        for kind in kinds:
            found = [(i, e) for i, e in matching if e['kind'] == kind]
            require(len(found) == 1, 'unique worker lifecycle trace missing: ' + kind)
            positions.append(found[0][0])
            detail = found[0][1]['detail']
            if kind == 'ready':
                source = re.fullmatch(r'source=([1-9][0-9]{0,19})', detail)
                require(source is not None and int(source[1]) <= row['presentedSourceSequence'],
                        'Ready source sequence differs from completed presentation')
            else:
                require(detail == ('close' if kind == 'terminal-reply-written' else ''), 'worker lifecycle failure detail')
        require(positions == sorted(positions), 'Ready/Close/Result/exit/reap lifecycle order')
        if number == 1 and parser.identity(row) == parser.launch[1]:
            require(positions[4] < parser.request['before']['traceCount'], 'uninstall requested before worker reap')
            require(positions[4] < parser.confirm['before']['traceCount'], 'uninstall confirmed before worker reap')
    if number == 1:
        matches = [row for row in instances if parser.identity(row) == parser.launch[1]]
        require(len(matches) == 1 and matches[0]['packageId'] == PACKAGE_ID, 'target native generation missing from outcome')
        private_content(parser.uninstall_commit['privateBefore'])
        private_content(parser.uninstall_commit['privateAfter'])
    samples = [event for event in events if event.get('type') == 'process-table']
    require(samples, 'raw worker process samples missing')
    allowed_pids = {row['workerPid'] for row in instances}
    for sample in samples:
        require(isinstance(sample.get('processes'), list), 'raw worker process table schema')
        for process in sample['processes']:
            require(isinstance(process, dict) and integer(process.get('pid'), 1) and isinstance(process.get('argv'), str),
                    'raw worker process identity')
            if '__worker' in process['argv']:
                require(process['pid'] in allowed_pids, 'unrecorded/app2 worker process in full boot')


def verify_boot(run, number, review, spec=None, signed=None, bundle_identity=None, repo=None):
    if spec is None:
        payload = review
        review, signed, bundle_identity = payload['review'], payload['signed'], payload['bundleIdentity']
        repo = payload['repo']
        spec = json_file(HERE / f'input-boot{number}.json')
    run, repo = Path(run), Path(repo)
    persistent_uninstall_session_contract(review)
    require(review.get('profile') == 'persistent-uninstall'
        and review.get('instrumentedProfile') == 'persistent-uninstall-pointer-ready-v1', 'independent uninstall profile required')
    validate_input(spec, number)
    report = json_file(run / 'result.json')
    require(type(report.get('bootNumber')) is int and report['bootNumber'] == number
        and report.get('bundleIdentity') == export_digest(bundle_identity)
        and report.get('inputScriptSha256') == review['inputScripts'][f'boot{number}Sha256'], 'boot role/bundle/input differs')
    result = raw.audit_raw_boot(run, spec, report, repo, expected_apps=(1, 2) if number == 1 else (1,))
    events, outcome, progress = result['events'], result['outcome'], result['progress']
    def one(kind, predicate=lambda event: True):
        rows = [(i, e) for i, e in enumerate(events) if e.get('type') == kind and predicate(e)]
        require(len(rows) == 1, 'unique raw event required: ' + kind)
        return rows[0]
    desktop_i, desktop = one('process-start', lambda e: e.get('name') == 'desktop')
    marker_i, marker = one('data-marker')
    workers_i, _ = one('desktop-workers-stopped')
    sync_i, _ = one('data-synced')
    exit_i, _ = one('desktop-exit')
    terminal_i, _ = one('guest-result')
    require(not any(e.get('type') in ('install-fixture', 'update-fixture', 'persistent-install-state', 'persistent-update-state') for e in events),
        'legacy fixture/profile evidence forbidden')
    snapshots = [(i, e) for i, e in enumerate(events) if e.get('type') == 'persistent-uninstall-state']
    require(len(snapshots) == 2, 'exactly two pre/post uninstall snapshots required')
    states = [e['state'] for e in events if e.get('type') == 'desktop-state']
    require(states, 'raw desktop states missing')
    session = states[0]['sessionId']
    before, after = [e.get('receipt') for _, e in snapshots]
    for (index, event), phase in zip(snapshots, ('pre-desktop', 'post-desktop')):
        require(type(event.get('code')) is int and event['code'] == 0
            and event.get('argv') == helper_argv(number, phase, review)
            and isinstance(event.get('stdout'), str) and isinstance(event.get('stderr'), str), 'raw helper argv/status differs')
        value = validate_snapshot(event['receipt'], report['bootId'], number, phase,
            session if phase == 'post-desktop' else None, desktop['pid'] if phase == 'post-desktop' else None)
        env = value['envelope']
        require(all(same_json(env[key], report[key]) for key in ('diskUuid', 'marker', 'previousMarker')), 'snapshot mounted disk differs')
        expected = {'schemaVersion': 1, 'status': env['status'], 'actions': env['actions'],
            **{key: value[key] for key in ('content', 'removed', 'privateContent')}}
        require(same_json(bounded_json(event['stdout']), expected), 'helper stdout differs from raw snapshot')
        require(marker_i < index < desktop_i if phase == 'pre-desktop' else workers_i < index < sync_i < terminal_i,
                'snapshot outside mounted/quiescent phase')
        bind_signed_content(value['content'], signed, removed=value['removed'])
    argv = ['/opt/mingos/ming-app-host', 'desktop', CATALOG, '--renderer', 'cpu'] + PERSISTENT_UNINSTALL_ARGS
    argv += ['--persistent-install-state', ROOT]
    if number == 1: argv += ['--install-source', SOURCE]
    require(desktop.get('argv') == argv, 'desktop source/events/no-reseed argv differs')
    stderr = raw.reconstruct_log(events, 'desktop-wayland.stderr.log')
    parser = PersistentUninstallReceipts(report['bootId'], desktop['pid'], number)
    rows = parser.feed(stderr)
    require(not parser.buffer, 'truncated native stderr')
    relays = [(i, e) for i, e in enumerate(events) if e.get('type') == 'desktop-persistent-receipt']
    require(len(rows) == len(relays), 'producer/guest relay coverage differs')
    for row, (index, relay) in zip(rows, relays):
        require(desktop_i < index < exit_i and all(same_json(relay.get(key), value) for key, value in row.items())
            and relay.get('bootId') == report['bootId'] and relay.get('sessionId') == session
            and type(relay.get('desktopPid')) is int and relay['desktopPid'] == desktop['pid'], 'producer bytes/offset/identity differs')
    for state in states: parser.bind_state(state)
    parser.complete(number)
    inputs = [row for row in rows if row['record']['kind'] == 'native-input']
    require(len(inputs) == len(spec['steps']) * 2, 'exact native input edge coverage differs')
    captures = {e['state']['revision']: e['diagnosticStderrOffset'] for e in events if e.get('type') == 'desktop-state'}
    for index, step in enumerate(spec['steps']):
        baseline, expected = progress[3 + index * 4]['state'], progress[4 + index * 4]['state']
        point = step['events'][0]
        for row, edge in zip(inputs[index * 2:index * 2 + 2], ('down', 'up')):
            value = row['record']['receipt']
            require(value['edge'] == edge and (value['x'], value['y']) == (point['x'], point['y'])
                and baseline['revision'] <= value['before']['state']['revision'] < value['after']['state']['revision'] <= expected['revision']
                and captures[baseline['revision']] <= row['logStartOffset'] < row['logEndOffset'] <= captures[expected['revision']],
                'native button not enclosed by reviewed raw input/state step')
    if number == 1:
        bind_signed_content(parser.installed_content, signed, installed=True)
        bind_signed_content(parser.uninstalled_content, signed, removed=parser.removed)
        require(same_json(before['content'], signed['initialContent'])
            and same_json(after['content'], parser.uninstalled_content)
            and same_json(after['removed'], parser.removed)
            and same_json(after['privateContent'], parser.private_content), 'pre/post differs from signed transaction')
        require(parser.app2_down == 5 and parser.closed_press == 7 and parser.request['pressSequence'] == 9
            and parser.confirm['pressSequence'] == 11, 'native install/launch/Close/U/confirm sequence differs')
        commit = next(row for row in rows if row['record']['kind'] == 'persistent-uninstall-commit')
        require(commit['logEndOffset'] <= captures[progress[-2]['state']['revision']], 'POWER preceded durable uninstall commit')
    else:
        require(all(same_json(before[key], after[key]) for key in ('content', 'removed', 'privateContent')), 'boot2 reseeded/mutated disk/private data')
    full_trace = outcome['trace']
    launch_input = next((row['record']['receipt'] for row in inputs
        if row['record']['receipt']['sequence'] == parser.app2_down), None)
    worker_started = next((row['record']['receipt'] for row in rows
        if row['record']['kind'] == 'native-launch' and row['record']['receipt']['phase'] == 'worker-started'), None)
    for row in rows:
        record = row['record']
        value = record['receipt']
        anchors = [value[k] for k in ('before', 'after', 'anchor') if k in value]
        for anchor in anchors:
            require(anchor['traceCount'] <= len(full_trace), 'native anchor exceeds full outcome trace')
            for historical in anchor['history']:
                identity = parser.identity(historical)
                matching = [item for item in outcome['instances'] if parser.identity(item) == identity]
                if historical['workerPid'] == 0:
                    # Rust records a starting instance before its supervisor publishes the PID.
                    # Bind only that native launch's pre-spawn down/up anchors to its later worker.
                    pending = [app for app in anchor['state']['apps'] if parser.identity(app) == identity]
                    launches = [index for index, event in enumerate(full_trace) if event['kind'] == 'launch'
                        and tuple(event[key] for key in ('app', 'generation', 'window')) == identity[:3]]
                    require(number == 1 and historical['status'] == 'starting' and historical['completed'] is False
                        and record['kind'] == 'native-input' and launch_input is not None and worker_started is not None
                        and value['pressSequence'] == parser.app2_down
                        and value['sequence'] in (parser.app2_down, parser.app2_down + 1)
                        and parser.launch is not None and parser.launch[1][:3] == identity[:3]
                        and len(pending) == 1 and pending[0]['state'] == 'starting'
                        and launch_input['after']['state']['revision'] <= anchor['state']['revision']
                            < worker_started['anchor']['state']['revision']
                        and len(launches) == 1
                        and launch_input['before']['traceCount'] <= launches[0] < launch_input['after']['traceCount']
                            <= anchor['traceCount'] <= worker_started['anchor']['traceCount'],
                        'pending native history lacks its pre-spawn launch binding')
                    matching = [item for item in outcome['instances'] if parser.identity(item) == parser.launch[1]]
                require(len(matching) == 1, 'native history missing from complete outcome')
                if historical['completed']:
                    require(all(same_json(matching[0].get(key), item) for key, item in historical.items()),
                            'terminal native history changed before outcome')
        if record['kind'] == 'native-input':
            require(same_json(value['trace'], full_trace[value['before']['traceCount']:value['after']['traceCount']]),
                    'native trace differs from full outcome slice')
    verify_terminal(outcome, parser, number, events)
    return {'bootNumber': number, 'bootId': report['bootId'], 'sessionId': session, 'desktopPid': desktop['pid'],
        'report': report, 'before': before, 'after': after,
        'nativeReceiptOffsets': [[row['logStartOffset'], row['logEndOffset']] for row in rows],
        'pointerHandshakes': result['pointerHandshakes'], 'qmpInputRequestsAcknowledged': result['qmpInputRequestsAcknowledged'],
        **{name + 'Sha256': file_hash(run / name) for name in ('guest.jsonl', 'inputs.jsonl', 'qmp.jsonl', 'qmp.json', 'result.json', 'kernel-serial.log')}}


def _pair_verified(root, export):
    inputs, bundle, runs = root / 'inputs', root / 'bundle', root / 'pair'
    review = json_file(inputs / 'session-review.json')
    checked_session(review, inputs / 'payload')
    persistent_uninstall_session_contract(review)
    manifest = json_file(bundle / 'bundle.json')
    validate_bundle(bundle, file_hash(inputs / 'session-review.json'))
    require(manifest.get('payloadProfile') == 'persistent-uninstall' and same_json(manifest.get('sessionReview'), review)
        and manifest.get('instrumentedQualification') is True and manifest.get('diagnosticOnly') is False
        and manifest.get('diagnosticProfile') == 'input-pointer-ready-v1', 'independent uninstall bundle required')
    from bundle import verify_payload
    verify_payload(inputs / 'payload', review)
    for name, expected in manifest['payloadSha256'].items():
        export_path(name)
        require(file_hash(inputs / 'payload' / name) == expected, 'reviewed payload bytes changed')
    signed = verify_fixture_directory(root / 'fixture', inputs, review)
    audited = []
    for number in (1, 2):
        frozen, _ = validate_reviewed_input(review, inputs / f'input-boot{number}.json', number)
        require(read(runs / f'boot-{number}.input.json') == frozen
            and read(HERE / f'input-boot{number}.json') == frozen, 'immutable fixed input copy differs')
        audited.append(verify_boot(runs / f'boot-{number}', number, review, bounded_json(frozen), signed,
            file_hash(bundle / 'bundle.json'), REPO))
    validate_cold_pair(audited[0]['report'], audited[1]['report'])
    one, two = audited
    require(one['sessionId'] != two['sessionId'] and one['bootId'] != two['bootId']
        and one['after']['envelope']['diskUuid'] == two['before']['envelope']['diskUuid']
        and same_json(two['before']['envelope']['previousMarker'], one['after']['envelope']['marker'])
        and all(same_json(one['after'][key], two['before'][key]) and same_json(two['before'][key], two['after'][key])
            for key in ('content', 'removed', 'privateContent')), 'reboot identity/marker/content/removal/private chain differs')
    require(json_file(runs / 'result.json').get('status') == 'same-bundle-disk-cold-boot-verified', 'cold pair runner success missing')
    return {'status': 'actual-persistent-uninstall-cold-pair-evidence-verified', 'boots': audited,
        'diskUuid': one['report']['diskUuid'], 'bundleIdentity': file_hash(bundle / 'bundle.json'),
        'persistentUninstallNormalRebootVerified': True, 'privateDataPreserved': True,
        'formalQualification': False, 'finalSystemAccepted': False,
        'checkerSha256': file_hash(Path(__file__)), 'rawAuditorSha256': file_hash(HERE / 'raw_boot_audit.py'), **export,
        'qualification': 'Instrumented Linux normal reboot; physical display/input and power failure unverified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    require(not output.resolve().is_relative_to(args.pair.resolve()), 'output must be outside sealed evidence')
    require(not output.exists(), 'new checker output required')
    try:
        if not PORTABLE:
            verify_export_manifest(args.pair)
            return subprocess.run([sys.executable, '-I', '-B', str(args.pair / 'checker.py'),
                '--pair', str(args.pair), '--output', str(output)], stdin=subprocess.DEVNULL, timeout=300).returncode
        result, code = pair(args.pair), 0
    except Exception as error:
        result, code = {'status': 'failed', 'error': repr(error), 'persistentUninstallNormalRebootVerified': False,
            'privateDataPreserved': False, 'formalQualification': False, 'finalSystemAccepted': False}, 1
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
