#!/usr/bin/env python3
"""Recheck actual raw update boots, signatures, and snapshots without running a guest.

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
REPLAY_MODULES = ('bootlib.py', 'infra.py', 'persistent_update_receipt.py', 'wayland_receipt.py',
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
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid)


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
    path = Path(path); before = path.lstat()
    export_require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                   and (limit is None or before.st_size <= limit), 'unsafe/oversized file: ' + str(path))
    stamp = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
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
                path = Path(entry.path); info = entry.stat(follow_symlinks=False)
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
    required = {'freeze.json', 'verify-persistent-update-evidence.py', 'raw_boot_audit.py', 'input-boot1.json', 'input-boot2.json'}
    required.update('replay/scripts/boot/' + name for name in REPLAY_MODULES)
    export_require(required <= set(files), 'required replay source/reference missing')
    replay = {name for name in files if name.startswith('replay/')}
    export_require(replay == {'replay/scripts/boot/' + name for name in REPLAY_MODULES}, 'replay dependency set differs')
    freeze = export_json(export_read(root / 'freeze.json', 16 * 1024 * 1024))
    export_require(type(freeze) is dict and type(freeze.get('schemaVersion')) is int and freeze['schemaVersion'] == 1
                   and freeze.get('profile') == 'persistent-update' and type(freeze.get('files')) is dict
                   and type(freeze.get('checker')) is dict and set(freeze['checker']) == {'path', 'sha256'}
                   and type(freeze.get('inputs')) is dict and set(freeze['inputs']) == {'boot1Sha256', 'boot2Sha256'},
                   'source freeze schema/types')
    frozen = freeze['files']
    for name, digest in frozen.items(): export_path(name); export_digest(digest)
    checker_path = export_path(freeze['checker']['path'])
    export_require(PurePosixPath(checker_path).name == 'verify-persistent-update-evidence.py'
                   and export_digest(freeze['checker']['sha256']) == files['verify-persistent-update-evidence.py']
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


PORTABLE = any((HERE / name).exists() for name in (EXPORT_MANIFEST, 'replay', 'pair/verification-context.json', 'inputs/session-review.json'))
if PORTABLE:
    verify_export_manifest(HERE)
    sys.dont_write_bytecode = True
REPO = HERE / 'replay' if PORTABLE else Path('/repo') if HERE == Path('/prep') else HERE.parents[1]
sys.path.insert(0, str(REPO / 'scripts/boot'))
from bootlib import (PERSISTENT_UPDATE_FIXTURE, PERSISTENT_UPDATE_ARGS, checked_session,
    persistent_update_session_contract, sha256, strict_json, validate_bundle, validate_reviewed_input)
from infra import integer, validate_cold_pair
from persistent_update_receipt import (ACTION_COUNTS, PACKAGE_ID, PersistentUpdateReceipts,
    SOURCE_MANIFEST, bounded_json, compact, content_snapshot, digest, exact, private_content, validate_snapshot)

_spec = importlib.util.spec_from_file_location('persistent_update_raw_boot_audit', HERE / 'raw_boot_audit.py')
raw = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(raw)
require = raw.require
PUBLIC_KEY = bytes.fromhex('ea4a6c63e29c520abef5507b132ec5f9954776aebebe7b92421eea691446d22c')
MIGRATION = {'fromVersion': '0.1.0', 'steps': [{'op': 'ensureDir', 'path': 'state'},
             {'op': 'renameFile', 'from': 'note.txt', 'to': 'state/note.txt'}]}
INPUT_HASHES = {1: '823ccec8dd15a9b6a4e639f8448649af3b5d5251672970ba8c10aa4117562e10',
                2: '7bd20ab7f393496d5c21682070622dbbe5cc400aca35a64a0550b42c52f692ce'}


def read(path, limit=128 * 1024 * 1024):
    path = Path(path); info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
            'missing/unsafe/bounded evidence file: ' + str(path))
    data = path.read_bytes()
    again = path.lstat()
    require((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            == (again.st_dev, again.st_ino, again.st_size, again.st_mtime_ns, again.st_ctime_ns)
            and len(data) == info.st_size, 'evidence file changed while reading')
    return data


def json_file(path, limit=8 * 1024 * 1024):
    return bounded_json(read(path, limit))


def file_hash(path):
    return hashlib.sha256(read(path)).hexdigest()


def text(value):
    data = value.encode(); return struct.pack('>Q', len(data)) + data


def same_json(value, expected):
    """Compare JSON values without Python's bool/int/float equality aliases."""
    return json.dumps(value, sort_keys=True) == json.dumps(expected, sort_keys=True)


def openssl_verify(public, message, signature):
    """Use the actual OpenSSL binary for both a valid and tampered message."""
    binary = shutil.which('openssl')
    require(binary is not None, 'OpenSSL executable unavailable')
    binary = Path(binary).resolve()
    require(binary.is_file(), 'OpenSSL executable missing')
    with tempfile.TemporaryDirectory(prefix='ming-update-signature-') as directory:
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
        require(invalid.returncode == 1, 'tampered signature negative control did not reject normally')
    return {'path': str(binary), 'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}


def signed_package(manifest_path, expected_id, version, expected_wasm, public=PUBLIC_KEY):
    manifest_bytes = read(manifest_path, 16384); manifest = bounded_json(manifest_bytes)
    exact(manifest, ('schemaVersion', 'id', 'version', 'sdkVersion', 'entry', 'privateStorage', 'publicStorage',
                     'dataMigration', 'publisherKeyId', 'publisherSignature'))
    migration = MIGRATION if expected_id == PACKAGE_ID and version == '0.2.0' else None
    require(type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 1
            and manifest['id'] == expected_id and manifest['version'] == version
            and type(manifest['sdkVersion']) is int and manifest['sdkVersion'] == 6 and manifest['entry'] == 'main.wasm'
            and manifest['privateStorage'] is False and manifest['publicStorage'] is False
            and manifest['dataMigration'] == migration and manifest['publisherKeyId'] == 'mingos-dev-2026',
            'exact signed fixture manifest/SDK/migration required')
    if migration is not None:
        exact(manifest['dataMigration'], ('fromVersion', 'steps'))
        for item, expected in zip(manifest['dataMigration']['steps'], MIGRATION['steps']): exact(item, expected)
    wasm = read(Path(manifest_path).parent / 'main.wasm', 4 * 1024 * 1024)
    require(wasm[:8] == b'\0asm\x01\0\0\0' and wasm == read(expected_wasm, 4 * 1024 * 1024),
            'signed module differs from exact staged packaged bytes')
    # Match the Rust struct's field/step encoding, independently of input JSON order.
    migration_bytes = compact(migration)
    message = (b'ming-package-signature-v1\0' + text('mingos-dev-2026') + struct.pack('>I', 1)
        + text(expected_id) + text(version) + struct.pack('>I', 6) + text('main.wasm') + b'\0\0'
        + struct.pack('>Q', len(migration_bytes)) + migration_bytes + struct.pack('>Q', len(wasm)) + wasm)
    require(isinstance(manifest['publisherSignature'], str), 'signature string required')
    try: signature = base64.b64decode(manifest['publisherSignature'], validate=True)
    except (ValueError, UnicodeError) as error: raise ValueError('invalid signed fixture base64') from error
    require(len(signature) == 64, 'Ed25519 signature size')
    executable = openssl_verify(public, message, signature)
    fingerprint = (b'ming-private-storage-package-v1\0' + struct.pack('>I', 1) + text(expected_id)
        + text(version) + text('main.wasm') + struct.pack('>I', 6) + struct.pack('>Q', len(wasm)) + wasm)
    if migration is not None:
        fingerprint += b'\0data-migration-v1' + struct.pack('>Q', len(migration_bytes)) + migration_bytes
    fingerprint += b'\0publisher-key-id-v1' + text('mingos-dev-2026')
    return {'packageId': expected_id, 'version': version, 'fingerprint': hashlib.sha256(fingerprint).hexdigest(),
        'manifestSha256': hashlib.sha256(manifest_bytes).hexdigest(), 'wasmSha256': hashlib.sha256(wasm).hexdigest(),
        'migrationSha256': hashlib.sha256(migration_bytes).hexdigest(), 'signatureVerified': True,
        'tamperedMessageRejected': True, 'openssl': executable}


def verify_fixture_directory(fixture_dir, inputs, review):
    fixture, inputs = Path(fixture_dir).absolute(), Path(inputs).absolute()
    payload, data = inputs / 'payload', fixture / 'data'; install = data / 'install-root'
    require(not fixture.is_symlink() and not data.is_symlink() and not install.is_symlink(), 'unsafe actual fixture path')
    helper = payload / 'prepare-persistent-update-fixture'
    command = json_file(fixture / 'command.json')
    require(command.get('commandRoot') == '/prep/build/fixture' and command.get('inputsRoot') == '/prep/inputs',
            'original fixed container helper roots differ')
    original, staged = Path(command['commandRoot']), Path(command['inputsRoot']) / 'payload'
    expected = [str(staged / 'prepare-persistent-update-fixture'), 'initialize-first', str(original / 'data/install-root'), str(original / 'data'),
        str(staged / 'packages/bootstrap/main.wasm'), str(staged / 'packages/old/main.wasm'),
        str(staged / 'packages/new/main.wasm'), str(original / 'install-source/app.json'),
        str(staged / 'catalog.json'), review['catalogSha256'], 'seed', str(original / 'fixture-receipt.json')]
    require(command.get('stage') == 'fixture' and type(command.get('exitCode')) is int and command['exitCode'] == 0
            and command.get('argv') == expected and command.get('executableSha256') == file_hash(helper)
            == review['persistentUpdateFixtureSha256']['helper'], 'actual reviewed helper command differs/failed')
    value = json_file(fixture / 'fixture-receipt.json', 262144)
    exact(value, ('schemaVersion', 'status', 'actions', 'content', 'privateContent'))
    require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1 and value['status'] == 'initialized-first'
            and value['actions'] == ACTION_COUNTS and all(type(v) is int for v in value['actions'].values()),
            'actual fixture physical initialization actions differ')
    require(same_json(json_file(fixture / 'stdout.log', 262144), value), 'helper stdout differs from actual file receipt')
    content_snapshot(value['content'], True); private_content(value['privateContent'], True)
    actual, directories = {}, set()
    for path in sorted(install.rglob('*')):
        info = path.lstat(); relative = path.relative_to(install).as_posix()
        require(not stat.S_ISLNK(info.st_mode), 'actual install fixture symlink')
        if stat.S_ISDIR(info.st_mode): directories.add(relative); continue
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'actual fixture unsafe file')
        if relative not in ('.ming-installed-packages.lock', '.ming-publisher-keyring.lock'):
            actual[relative] = file_hash(path)
    require(actual == {item['path']: item['sha256'] for item in value['content']['files']},
            'actual installed tree differs from helper snapshot or contains journals')
    allowed_dirs = {parent.as_posix() for name in actual for parent in Path(name).parents if parent.as_posix() != '.'}
    require(directories == allowed_dirs, 'actual fixture has extra/empty package directory')
    namespace = data / ('app-' + base64.b32encode(PACKAGE_ID.encode()).decode().lower().rstrip('='))
    require({p.name for p in namespace.iterdir()} == {'files'}
            and {p.name for p in (namespace / 'files').iterdir()} == {'note.txt'}
            and read(namespace / 'files/note.txt', 5) == b'alpha', 'actual fixture private alpha tree differs')
    keyring = read(install / 'trusted-publishers-v1', 16384)
    require(keyring == b'ming-trusted-publishers-v1\nmingos-dev-2026\tactive\t' + base64.b64encode(PUBLIC_KEY) + b'\n',
            'actual fixture development publisher keyring differs')
    signed = {}
    for label, number, package, version in (('bootstrap', 1, 'dev.ming.text-entry-demo', '0.1.0'), ('old', 2, PACKAGE_ID, '0.1.0')):
        entry = next(app for app in value['content']['apps'] if app['app'] == number)
        signed[label] = signed_package(install / entry['manifestPath'], package, version, payload / f'packages/{label}/main.wasm')
        require(all(entry[key] == signed[label][key] for key in ('packageId', 'version', 'fingerprint', 'manifestSha256', 'wasmSha256')),
                'actual signed package differs from initial helper identity')
    signed['new'] = signed_package(fixture / 'install-source/app.json', PACKAGE_ID, '0.2.0', payload / 'packages/new/main.wasm')
    require(signed['old']['wasmSha256'] != signed['new']['wasmSha256'], 'old/new fixture cannot use the same module')
    for name in ('bootstrap', 'old', 'new'):
        require(signed[name]['wasmSha256'] == review['persistentUpdateFixtureSha256'][name + 'Wasm'], 'signed packaged module differs from review')
    require(file_hash(payload / 'catalog.json') == review['catalogSha256'], 'reviewed initial catalog bytes differ')
    return {'status': 'actual-persistent-update-fixture-signatures-verified', **signed,
        'initialContent': value['content'], 'initialPrivateContent': value['privateContent'],
        'fixtureDirectory': 'fixture', 'helperSha256': file_hash(helper),
        'receiptSha256': file_hash(fixture / 'fixture-receipt.json'), 'commandSha256': file_hash(fixture / 'command.json')}


def helper_argv(number, phase, review):
    fixture = review['persistentUpdateFixture']
    argv = [fixture['helper'], 'open-existing', fixture['installRoot'], fixture['dataRoot']]
    if number == 1 and phase == 'pre-desktop':
        argv[1] = 'initialize-first'
        argv += [fixture['bootstrapWasm'], fixture['oldWasm'], fixture['newWasm'], fixture['sourceManifest'],
                 '/opt/mingos/catalog.json', review['catalogSha256'], 'seed']
    return argv + [fixture['receipt']]


def bind_signed_content(value, signed, initial):
    content_snapshot(value, initial)
    for app in value['apps']:
        package = signed['bootstrap' if app['app'] == 1 else 'old' if initial else 'new']
        require(all(app[key] == package[key] for key in ('packageId', 'version', 'fingerprint', 'manifestSha256', 'wasmSha256')),
                'raw snapshot differs from independently verified signed package')
    require(all(value[key] == signed['initialContent'][key] for key in ('catalogSha256', 'bootstrapSha256', 'trustSha256')),
            'raw static catalog/bootstrap/trust differs from actual fixture')


def validate_input(spec, number):
    require(type(number) is int and number in (1, 2), 'fixed boot number required')
    source = HERE / f'input-boot{number}.json'
    require(file_hash(source) == INPUT_HASHES[number], 'checker reference native input changed')
    require(json.dumps(spec, sort_keys=True) == json.dumps(json_file(source), sort_keys=True),
            'input differs from exact role-specific native pointer plan')
    return spec


def verify_boot(run, number, review, spec, signed, bundle_identity, repo):
    run = Path(run); repo = Path(repo)
    persistent_update_session_contract(review)
    require(review.get('profile') == 'persistent-update' and review.get('instrumentedProfile') == 'persistent-update-pointer-ready-v1',
            'independent persistent update profile/qualification required')
    validate_input(spec, number)
    report = json_file(run / 'result.json')
    require(type(report.get('bootNumber')) is int and report['bootNumber'] == number
            and report.get('bundleIdentity') == digest(bundle_identity)
            and report.get('inputScriptSha256') == review['inputScripts'][f'boot{number}Sha256'], 'raw boot number/bundle/input differs')
    result = raw.audit_raw_boot(run, spec, report, repo)
    events, outcome, progress = result['events'], result['outcome'], result['progress']
    def one(kind, predicate=lambda e: True):
        values = [(i, e) for i, e in enumerate(events) if e.get('type') == kind and predicate(e)]
        require(len(values) == 1, 'unique raw event required: ' + kind); return values[0]
    desktop_i, desktop = one('process-start', lambda e: e.get('name') == 'desktop')
    marker_i, marker = one('data-marker'); workers_i, _ = one('desktop-workers-stopped'); sync_i, _ = one('data-synced')
    exit_i, _ = one('desktop-exit'); terminal_i, terminal = one('guest-result')
    require(not any(e.get('type') in ('install-fixture', 'update-fixture', 'persistent-install-state', 'persistent-uninstall-state') for e in events),
            'legacy fixture/receipt is forbidden in update qualification')
    snapshots = [(i, e) for i, e in enumerate(events) if e.get('type') == 'persistent-update-state']
    require(len(snapshots) == 2, 'exactly pre/post raw update snapshots required')
    states = [e['state'] for e in events if e.get('type') == 'desktop-state']
    require(states, 'raw desktop state missing'); session = states[0]['sessionId']
    before, after = [e.get('receipt') for _, e in snapshots]
    for (index, event), phase in zip(snapshots, ('pre-desktop', 'post-desktop')):
        require(type(event.get('code')) is int and event['code'] == 0 and event.get('argv') == helper_argv(number, phase, review)
                and isinstance(event.get('stdout'), str) and isinstance(event.get('stderr'), str), 'raw helper command/status differs')
        value = validate_snapshot(event['receipt'], report['bootId'], number, phase,
                                  session if phase == 'post-desktop' else None, desktop['pid'] if phase == 'post-desktop' else None)
        env = value['envelope']
        require(env['diskUuid'] == report['diskUuid'] and env['marker'] == report['marker']
                and env['previousMarker'] == report['previousMarker'], 'snapshot does not bind actual mounted disk marker')
        expected = {'schemaVersion': 1, 'status': env['status'], 'actions': env['actions'],
                    'content': value['content'], 'privateContent': value['privateContent']}
        require(same_json(bounded_json(event['stdout']), expected), 'raw helper stdout differs from receipt/envelope')
        require(marker_i < index < desktop_i if phase == 'pre-desktop' else workers_i < index < sync_i < terminal_i,
                'helper snapshot outside mounted/pre-desktop or reaped/post-desktop boundary')
        bind_signed_content(value['content'], signed, number == 1 and phase == 'pre-desktop')
    expected_argv = ['/opt/mingos/ming-app-host', 'desktop', '/data/mingos/install-root/catalog.json', '--renderer', 'cpu'] + PERSISTENT_UPDATE_ARGS
    expected_argv += ['--persistent-install-state', '/data/mingos/install-root']
    if number == 1: expected_argv += ['--install-source', SOURCE_MANIFEST]
    require(desktop.get('argv') == expected_argv, 'desktop exact update argv/source/events contract differs')
    stderr = raw.reconstruct_log(events, 'desktop-wayland.stderr.log')
    parser = PersistentUpdateReceipts(report['bootId'], desktop['pid'], number, before=before)
    rows = parser.feed(stderr)
    require(not parser.buffer, 'raw native stream is truncated')
    relays = [(i, e) for i, e in enumerate(events) if e.get('type') == 'desktop-persistent-receipt']
    require(len(rows) == len(relays), 'raw producer/guest relay coverage differs')
    for row, (index, relay) in zip(rows, relays):
        require(desktop_i < index < exit_i and all(same_json(relay.get(key), value) for key, value in row.items())
                and relay.get('bootId') == report['bootId'] and relay.get('sessionId') == session
                and relay.get('desktopPid') == desktop['pid'], 'raw producer bytes/offsets/relay identity differ')
    for state in states: parser.bind_state(state)
    parser.complete(number)
    inputs = [row for row in rows if row['record']['kind'] == 'native-input']
    captures = {e['state']['revision']: e['diagnosticStderrOffset'] for e in events if e.get('type') == 'desktop-state'}
    for index, step in enumerate(spec['steps']):
        baseline, expected = progress[3 + index * 4]['state'], progress[4 + index * 4]['state']
        point = step['events'][0]
        for row, edge in zip(inputs[index * 2:index * 2 + 2], ('down', 'up')):
            value = row['record']['receipt']
            require(value['edge'] == edge and (value['x'], value['y']) == (point['x'], point['y'])
                    and baseline['revision'] <= value['before']['state']['revision'] < value['after']['state']['revision'] <= expected['revision']
                    and captures[baseline['revision']] <= row['logStartOffset'] < row['logEndOffset'] <= captures[expected['revision']],
                    'native button cause not enclosed by reviewed raw input/state step')
        update = step.get('expect', {}).get('persistentUpdate')
        if update is not None:
            require(number == 1 and parser.trial_record is not None and update['transactionId'] == '$trial', 'unbound dynamic update expectation')
            require(expected.get('persistentUpdate') == dict(update, transactionId=parser.trial_record['transactionId']), 'input expectation did not reach exact verified commit')
    require(after['privateContent']['nonFilesSha256'] == before['privateContent']['nonFilesSha256'], 'non-files private data changed')
    if number == 1:
        require(before['content'] == signed['initialContent'] and before['privateContent'] == signed['initialPrivateContent'], 'boot1 pre differs from real initialized fixture')
        require(after['content'] == parser.committed_content and after['privateContent'] == parser.private_content, 'stable post snapshot differs from real committed transaction')
        require(all(parser.trial_record[label] == {key: signed[label][key] for key in parser.trial_record[label]} for label in ('old', 'new')), 'trial package identity/signature differs')
        power = progress[-2]['state']
        require(power.get('persistentUpdate', {}).get('status') == 'committed', 'POWER dispatched before durable commit')
    else:
        require(before['content'] == after['content'] and before['privateContent'] == after['privateContent'], 'boot2 mutated/recovered/reseeded installed/private data')
    target = [row for row in outcome['instances'] if row.get('app') == 2]
    require(len(target) == 1 and target[0].get('completed') is True and target[0].get('error') is None
            and all(integer(target[0].get(key), 1) for key in ('app', 'generation', 'window', 'workerPid'))
            and tuple(target[0].get(key) for key in ('app', 'generation', 'window', 'workerPid')) == parser.launch[1]
            and target[0].get('packageId') == PACKAGE_ID
            and integer(target[0].get('frameClock', {}).get('closes'), 1, 1)
            and integer(target[0].get('frameClock', {}).get('cancels'), 0, 0),
            'raw outcome lacks the exact normally closed native worker')
    return {'bootNumber': number, 'bootId': report['bootId'], 'sessionId': session, 'desktopPid': desktop['pid'],
        'report': report, 'before': before, 'after': after, 'trialRecord': parser.trial_record,
        'nativeReceiptOffsets': [[row['logStartOffset'], row['logEndOffset']] for row in rows],
        'pointerHandshakes': result['pointerHandshakes'], 'qmpInputRequestsAcknowledged': result['qmpInputRequestsAcknowledged'],
        **{name + 'Sha256': file_hash(run / name) for name in ('guest.jsonl', 'inputs.jsonl', 'qmp.jsonl', 'qmp.json', 'result.json', 'kernel-serial.log')}}


def pair(root):
    root = Path(root).absolute(); inputs, bundle, runs = root / 'inputs', root / 'bundle', root / 'pair'
    if PORTABLE:
        export_require(root.resolve() == HERE, 'checker must replay its own sealed export')
    export = verify_export_manifest(root)
    export_require(PORTABLE, 'sealed exports must be replayed by their own exported checker')
    review = json_file(inputs / 'session-review.json'); checked_session(review, inputs / 'payload')
    persistent_update_session_contract(review)
    manifest = validate_bundle(bundle, file_hash(inputs / 'session-review.json'))
    require(review.get('profile') == 'persistent-update' and manifest.get('payloadProfile') == 'persistent-update'
            and manifest.get('sessionReview') == review and manifest.get('instrumentedQualification') is True
            and manifest.get('diagnosticOnly') is False and manifest.get('diagnosticProfile') == 'input-pointer-ready-v1',
            'actual independent update pointer-ready bundle required')
    from bundle import verify_payload
    verify_payload(inputs / 'payload', review)
    for name, expected in manifest['payloadSha256'].items():
        require(file_hash(inputs / 'payload' / name) == expected, 'reviewed bundled payload changed: ' + name)
    signed = verify_fixture_directory(root / 'fixture', inputs, review)
    audited = []
    for number in (1, 2):
        frozen, _ = validate_reviewed_input(review, inputs / f'input-boot{number}.json', number)
        require(hashlib.sha256(frozen).hexdigest() == INPUT_HASHES[number], 'reviewed frozen native input differs')
        require(read(runs / f'boot-{number}.input.json', 1024 * 1024) == frozen, 'cold-pair immutable input copy differs')
        audited.append(verify_boot(runs / f'boot-{number}', number, review, bounded_json(frozen), signed,
                                   file_hash(bundle / 'bundle.json'), REPO))
    validate_cold_pair(audited[0]['report'], audited[1]['report'])
    one, two = audited
    require(one['bootId'] != two['bootId'] and one['sessionId'] != two['sessionId']
            and one['after']['envelope']['diskUuid'] == two['before']['envelope']['diskUuid']
            and two['before']['envelope']['previousMarker'] == one['after']['envelope']['marker']
            and one['after']['content'] == two['before']['content']
            and one['after']['privateContent'] == two['before']['privateContent'], 'reboot changed identity/marker/content/private data')
    require(json_file(runs / 'result.json').get('status') == 'same-bundle-disk-cold-boot-verified', 'actual cold-pair runner success missing')
    return {'status': 'actual-persistent-update-cold-pair-evidence-verified', 'boots': audited,
        'diskUuid': one['report']['diskUuid'], 'bundleIdentity': file_hash(bundle / 'bundle.json'),
        'persistentUpdateNormalRebootVerified': True, 'dockLaunchVerified': True, 'finalSystemAccepted': False,
        'checkerSha256': file_hash(Path(__file__)), 'rawAuditorSha256': file_hash(HERE / 'raw_boot_audit.py'), **export,
        'qualification': 'Raw QMP/Wayland/native receipts and durable snapshots; physical display/input is unverified.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    sealed_roots = [args.pair.resolve()] + ([HERE] if PORTABLE else [])
    export_require(not any(output.is_relative_to(root) for root in sealed_roots),
                   'checker output must be outside the sealed export')
    require(not args.output.exists(), 'new checker output required')
    if not PORTABLE and (args.pair / EXPORT_MANIFEST).exists():
        root = args.pair.resolve()
        verify_export_manifest(root)
        return subprocess.run([sys.executable, '-I', '-B', str(root / 'verify-persistent-update-evidence.py'),
                               '--pair', str(root), '--output', str(output)], stdin=subprocess.DEVNULL).returncode
    try: result, code = pair(args.pair), 0
    except Exception as error:
        result, code = {'status': 'failed', 'error': repr(error), 'persistentUpdateNormalRebootVerified': False,
                        'finalSystemAccepted': False}, 1
    with args.output.open('x') as output: json.dump(result, output, indent=2); output.write('\n')
    print(json.dumps(result, indent=2)); return code


if __name__ == '__main__':
    raise SystemExit(main())
