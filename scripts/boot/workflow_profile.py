"""Independent diagnostic developer workflow contract. Reads evidence; never builds or boots.

Use immutable, read-only inputs and the complete outer developer pack verifier.
Receipt bindings here do not independently replay a compiler or qualify a system.
"""
import json
from pathlib import Path
import re
import stat

PROFILE = 'developer-workflow-v1'
BUDGETS = {'guestSeconds': 900, 'driverSeconds': 945, 'outerSeconds': 1950}
CONTROL_POLICY = 'private-unix-session-layout-inputplan-v1'
ARGS = ['--duration-ms', '0', '--isolation', 'strict', '--data-root', '/data/mingos',
        '--state-file', '/run/evidence/desktop-state.json']
CATALOG = {'schemaVersion': 1, 'apps': [
    {'id': 1, 'title': 'Notes', 'manifest': 'packages/notes-demo/app.json'},
    {'id': 2, 'title': 'Motion', 'manifest': 'packages/animated-demo/app.json'}], 'autostart': [1, 2]}
APPS = ((1, 'notes-demo', 6), (2, 'animated-demo', 5))
EVIDENCE = ('command.json', 'inputs-before.json', 'inputs-after.json', 'toolchain.json',
            'java.stdout.log', 'java.stderr.log', 'gradle-version.stdout.log',
            'gradle-version.stderr.log', 'build.stdout.log', 'build.stderr.log',
            'dependencies-before.json', 'dependencies-after.json', 'dependencies-comparison.json')
HOST_PROVENANCE = {'freezeSha256': 'provenance/host/freeze.json',
                   'resultSha256': 'provenance/host/build-result.json',
                   'artifactsSha256': 'provenance/host/artifacts.json'}
REVIEW_KEYS = {'reviewed', 'reviewer', 'profile', 'sessionMode', 'extraArgs', 'hostSha256',
               'catalogSha256', 'payloadFiles', 'packageEvidenceSha256', 'hostProvenance',
               'strictProbeProfile', 'strictProbeSha256', 'probeSourceFreezeSha256',
               'probeBuildReceiptSha256', 'diagnosticOnly', 'instrumentedQualification',
               'formalQualification', 'finalSystemAccepted', 'budgetEvidence', 'inputScriptSha256', 'controlPolicy'}
HEX = re.compile(r'[0-9a-f]{64}\Z')


def require(condition, message):
    if not condition:
        raise ValueError('developer-workflow-v1: ' + message)


def digest(value):
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def session_contract(review):
    from isolation_profiles import load_profile
    require(type(review) is dict and set(review) == REVIEW_KEYS, 'closed review fields differ')
    require(review['reviewed'] is True and isinstance(review['reviewer'], str)
            and bool(review['reviewer'].strip()), 'independent review required')
    require(review['profile'] == PROFILE and review['sessionMode'] == 'long-lived'
            and review['extraArgs'] == ARGS, 'exact long-lived strict duration/data-root/state session required')
    require(review['budgetEvidence'] == BUDGETS, 'fixed workflow supervision budgets required')
    require(review['inputScriptSha256'] is None or digest(review['inputScriptSha256']), 'reviewed replay hash or explicit interactive mode required')
    require(review['controlPolicy'] == CONTROL_POLICY, 'private single-owner control policy required')
    require(review['diagnosticOnly'] is True and review['instrumentedQualification'] is False
            and review['formalQualification'] is False and review['finalSystemAccepted'] is False,
            'diagnostic-only false qualification/acceptance flags required')
    require(review['strictProbeProfile'] == 'source-v2'
            and review['strictProbeSha256'] == load_profile('source-v2')['artifactSha256']
            and digest(review['probeSourceFreezeSha256']) and digest(review['probeBuildReceiptSha256']),
            'source-v2 source/build/artifact bindings required')
    for key in ('hostSha256', 'catalogSha256'):
        require(digest(review[key]), 'exact hash required: ' + key)
    require(type(review['packageEvidenceSha256']) is dict
            and set(review['packageEvidenceSha256']) == {'1', '2'}
            and all(digest(value) for value in review['packageEvidenceSha256'].values()),
            'two exact package receipt hashes required')
    require(type(review['hostProvenance']) is dict and set(review['hostProvenance']) == set(HOST_PROVENANCE)
            and all(digest(value) for value in review['hostProvenance'].values()), 'Host provenance hashes required')
    require(type(review['payloadFiles']) is dict
            and all(isinstance(name, str) and digest(value) for name, value in review['payloadFiles'].items()),
            'complete payload file hash map required')
    return review


def expected_files():
    names = {'ming-app-host', 'catalog.json', *HOST_PROVENANCE.values()}
    for _, name, _ in APPS:
        prefix = 'packages/' + name + '/'
        names.update(prefix + leaf for leaf in ('app.json', 'build-info.json', 'build-receipt.json',
                                                'compiler.wasm', 'main.wasm'))
        names.update(prefix + 'evidence/' + leaf for leaf in EVIDENCE)
    return names


def inventory(payload):
    from bootlib import sha256
    payload = Path(payload)
    require(payload.is_dir() and not payload.is_symlink(), 'regular payload directory required')
    files, directories, stamps = {}, set(), {}
    for path in (payload, *payload.rglob('*')):
        info = path.lstat()
        require(not path.is_symlink() and not getattr(info, 'st_file_attributes', 0) & 0x400,
                'payload links/reparse points forbidden')
        name = path.relative_to(payload).as_posix()
        stamps[name] = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                        info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if stat.S_ISDIR(info.st_mode):
            if path != payload: directories.add(name)
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'single-link regular payload required')
            files[name] = sha256(path)
    return files, directories, stamps


def read_json(path):
    from bootlib import strict_json
    require(path.stat().st_size <= 16 * 1024**2, 'JSON metadata limit exceeded')
    value = strict_json(path.read_bytes())
    require(type(value) is dict, 'JSON object required: ' + path.name)
    return value


def verify_host(payload, review):
    from bootlib import sha256
    for key, name in HOST_PROVENANCE.items():
        require(sha256(payload / name) == review['hostProvenance'][key], 'Host provenance hash differs')
    freeze, result, artifacts = (read_json(payload / name) for name in HOST_PROVENANCE.values())
    source = freeze.get('source')
    require(type(freeze.get('schemaVersion')) is int and freeze['schemaVersion'] == 1
            and freeze.get('finalSystemAccepted') is False and freeze.get('formalQualification', False) is False
            and type(source) is dict and type(source.get('files')) is dict and bool(source['files']),
            'Host source freeze inventory required')
    require(result.get('stage') == 'build' and result.get('status') == 'passed'
            and result.get('formalQualification') is False and result.get('finalSystemAccepted') is False
            and result.get('freezeSha256') == review['hostProvenance']['freezeSha256'],
            'Host build result/source binding differs')
    artifact_file = payload / HOST_PROVENANCE['artifactsSha256']
    require(type(result.get('products')) is dict and result['products'].get('artifacts.json') ==
            {'sha256': sha256(artifact_file), 'bytes': artifact_file.stat().st_size},
            'Host build result/artifact inventory binding differs')
    host = payload / 'ming-app-host'
    require(artifacts.get('release/ming-app-host') == {'sha256': sha256(host), 'bytes': host.stat().st_size},
            'Host binary differs from cold build artifact')


def verify_package(payload, review, number, name, sdk):
    from bootlib import sha256
    package = payload / 'packages' / name
    manifest, receipt, info = (read_json(package / leaf) for leaf in
                               ('app.json', 'build-receipt.json', 'build-info.json'))
    require(manifest == {'schemaVersion': 1, 'id': 'dev.ming.' + name, 'version': '0.1.0',
                         'sdkVersion': sdk, 'entry': 'main.wasm'}
            and type(manifest['schemaVersion']) is int and type(manifest['sdkVersion']) is int,
            'fixed package identity/SDK differs: ' + name)
    require(sha256(package / 'build-receipt.json') == review['packageEvidenceSha256'][str(number)],
            'package receipt review hash differs')
    require(type(receipt.get('schemaVersion')) is int and receipt['schemaVersion'] == 1
            and receipt.get('status') == 'passed' and receipt.get('sample') == name
            and receipt.get('fixture') is None and receipt.get('offline') is True
            and type(receipt.get('compilerExitCode')) is int and receipt['compilerExitCode'] == 0
            and receipt.get('compilerSignal') is None and receipt.get('formalQualification') is False
            and receipt.get('finalSystemAccepted') is False and receipt.get('kotlin') == '2.3.20'
            and all(receipt.get(key) is True for key in ('rawToPackageReplayVerified',
                                                        'sourceInputsStable', 'dependencyInputsStable')),
            'successful source compiler receipt required')
    require(receipt.get('package') == info and info.get('sdkVersion') == sdk
            and type(info.get('sdkVersion')) is int and info.get('adapterVersion') == 1
            and info.get('kotlin') == '2.3.20' and info.get('fixture') is None,
            'complete nested build-info differs')
    for leaf, key in (('compiler.wasm', 'rawSha256'), ('main.wasm', 'packagedSha256'),
                      ('app.json', 'manifestSha256')):
        path = package / leaf
        require(receipt.get(key) == sha256(path), 'package byte hash differs: ' + leaf)
        if leaf.endswith('.wasm'):
            with path.open('rb') as source: require(source.read(8) == b'\0asm\x01\0\0\0', 'Wasm header required')
            require(info.get(key) == sha256(path), 'build-info Wasm hash differs')
    evidence = {'evidence/' + leaf: sha256(package / 'evidence' / leaf) for leaf in EVIDENCE}
    require(receipt.get('evidenceSha256') == evidence, 'complete compiler evidence binding differs')
    before, after = (read_json(package / 'evidence' / leaf) for leaf in ('inputs-before.json', 'inputs-after.json'))
    require(before == after and type(before.get('files')) is dict and bool(before['files'])
            and before['files'] == receipt.get('inputSha256') == info.get('inputSha256')
            and before.get('directories') == receipt.get('inputDirectories')
            and before['files'].get('samples/' + name + '/app.json') == sha256(package / 'app.json'),
            'source input inventory binding differs')
    command = receipt.get('command')
    require(isinstance(command, list) and all(isinstance(arg, str) for arg in command)
            and all(flag in command for flag in ('--offline', '--rerun-tasks', '--no-build-cache',
                     '--no-configuration-cache', ':' + name + ':compileProductionExecutableKotlinWasmWasi'))
            and read_json(package / 'evidence/command.json').get('argv') == command,
            'actual compiler command binding differs')
    for label in ('stdout', 'stderr'):
        require(receipt.get(label + 'Sha256') == sha256(package / ('evidence/build.' + label + '.log')),
                'compiler log hash differs')


def verify_workflow(payload, review, catalog):
    from bootlib import sha256
    session_contract(review)
    payload = Path(payload)
    names = expected_files()
    directories = {str(Path(name).parent).replace('\\', '/') for name in names}
    directories.discard('.')
    directories |= {str(parent).replace('\\', '/') for name in names for parent in Path(name).parents if str(parent) != '.'}
    before = inventory(payload)
    require(set(review['payloadFiles']) == names and before[0] == review['payloadFiles']
            and before[1] == directories, 'complete frozen payload inventory differs')
    require(review['hostSha256'] == sha256(payload / 'ming-app-host')
            and review['catalogSha256'] == sha256(payload / 'catalog.json'), 'Host/catalog review hashes differ')
    with (payload / 'ming-app-host').open('rb') as source: require(source.read(4) == b'\x7fELF', 'Linux ELF host required')
    require(catalog == CATALOG and type(catalog['schemaVersion']) is int
            and all(type(app['id']) is int for app in catalog['apps'])
            and all(type(number) is int for number in catalog['autostart'])
            and read_json(payload / 'catalog.json') == catalog, 'fixed two-Compose catalog differs')
    verify_host(payload, review)
    for number, name, sdk in APPS: verify_package(payload, review, number, name, sdk)
    require(inventory(payload) == before, 'payload changed during verification')
    return catalog


def validate_bundle_options(review, isolation_profile, probe_dir, *, input_diagnostics=False,
                            wayland_client_diagnostics=False, pointer_handshake_diagnostics=False):
    from bootlib import sha256
    from isolation_profiles import verify_review_and_files
    session_contract(review)
    require(isolation_profile == 'source-v2' and probe_dir is not None, 'source-v2 probe directory mandatory')
    require(all((input_diagnostics, wayland_client_diagnostics, pointer_handshake_diagnostics)),
            'bounded diagnostic input/client/pointer handshake required')
    from linux_host_probe import verify_probe_build
    contract = verify_review_and_files('source-v2', review, probe_dir)
    require(sha256(Path(probe_dir) / 'build-receipt.json') == review['probeBuildReceiptSha256'],
            'probe build receipt review hash differs')
    verify_probe_build(probe_dir, Path(__file__).resolve().parents[2], review['probeSourceFreezeSha256'])
    return contract
