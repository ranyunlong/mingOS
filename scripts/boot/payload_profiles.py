"""Closed, main-reviewed payload profiles. No build or guest execution on import."""
import json
from pathlib import Path
from bootlib import INSTALL_LIFECYCLE_EVENTS, INSTALL_LIFECYCLE_INSTALL_ROOT, INSTALL_LIFECYCLE_SOURCE, PERSISTENT_INSTALL_ARGS, PERSISTENT_INSTALL_FIXTURE, PERSISTENT_INSTALL_PROFILE, PERSISTENT_UPDATE_INSTALL_ROOT, checked_path, persistent_input_hashes, sha256
from wayland_receipt import POINTER_SYNC_POLICY

DATA_TEXT_ARGS = ['--duration-ms', '0', '--isolation', 'strict', '--data-root', '/data/mingos',
                  '--state-file', '/run/evidence/desktop-state.json']

def require(condition, message):
    if not condition: raise ValueError('data-text: ' + message)

def read_object(path):
    value = json.loads(Path(path).read_text())
    require(isinstance(value, dict), 'object required: ' + str(path))
    return value

def verify_data_text(payload, review, catalog):
    require(review.get('sessionMode') == 'long-lived' and review.get('extraArgs') == DATA_TEXT_ARGS,
            'exact reviewed strict/data/state session required')
    require(isinstance(catalog, dict) and catalog.get('schemaVersion') == 1, 'catalog schema')
    apps, start = catalog.get('apps'), catalog.get('autostart')
    require(isinstance(apps, list) and len(apps) == 2, 'exactly two apps required')
    require(isinstance(start, list) and len(start) == 2 and all(type(x) is int for x in start)
            and set(start) == {1, 2}, 'autostart must be exactly apps 1/2')
    require(all(isinstance(app, dict) and type(app.get('id')) is int for app in apps), 'app identity')
    require({app['id'] for app in apps} == {1, 2}, 'app IDs must be 1/2')
    evidence = review.get('packageEvidenceSha256')
    require(isinstance(evidence, dict) and set(evidence) == {'1', '2'}, 'review package evidence required')
    for app in apps:
        number = app['id']
        require(set(app) <= {'id', 'title', 'manifest', 'dataKey', 'slots'}, 'unknown catalog app field')
        if number == 1:
            require(app.get('dataKey') == 'counter-main' and app.get('slots') == [1]
                    and type(app['slots'][0]) is int, 'app1 exact counter-main slot1 grant required')
        else:
            require('dataKey' not in app and 'slots' not in app, 'app2 must have no data grant')
        manifest = checked_path(payload, app.get('manifest'))
        require(manifest.name == 'app.json', 'manifest filename must be app.json')
        package = manifest.parent
        data = read_object(manifest)
        expected = 'dev.ming.' + ('app-data-desktop' if number == 1 else 'text-entry-demo')
        require(data.get('schemaVersion') == 1 and data.get('sdkVersion') == 6 and data.get('id') == expected
                and data.get('entry') == 'main.wasm', 'package identity/SDK6/entry mismatch')
        raw, entry, receipt_path = package/'compiler.wasm', package/'main.wasm', package/'build-receipt.json'
        for path in (raw, entry):
            with path.open('rb') as source:
                require(source.read(8) == b'\0asm\x01\0\0\0', 'real Wasm header required: ' + path.name)
        hashes = {'rawSha256': sha256(raw), 'packagedSha256': sha256(entry)}
        receipt = read_object(receipt_path)
        if number == 1:
            envelope_path = package/'boot-receipt.json'
            require(evidence['1'] == sha256(envelope_path), 'app1 envelope review hash mismatch')
            envelope = read_object(envelope_path)
            expected_envelope = dict(hashes, schemaVersion=1, kind='derived-evidence-envelope',
                provenance='original-receipt-no-recompile', packageId=expected,
                manifestSha256=sha256(manifest), originalReceiptSha256=sha256(receipt_path))
            require(all(envelope.get(key) == value for key, value in expected_envelope.items()),
                    'derived envelope does not bind original package/receipt')
            require(receipt.get('status') == 'passed' and receipt.get('mode') == 'appdata'
                    and receipt.get('stage') == 'package' and type(receipt.get('compilerExitCode')) is int
                    and receipt['compilerExitCode'] == 0, 'original appdata compilation did not pass')
            package_receipt = receipt.get('package')
            require(isinstance(package_receipt, dict) and all(package_receipt.get(k) == v for k, v in hashes.items()),
                    'original nested raw/packaged receipt mismatch')
        else:
            require(evidence['2'] == sha256(receipt_path), 'app2 receipt review hash mismatch')
            require(receipt.get('schemaVersion') == 1 and receipt.get('status') == 'passed'
                    and type(receipt.get('exitCode')) is int and receipt['exitCode'] == 0
                    and receipt.get('manifestSha256') == sha256(manifest)
                    and all(receipt.get(k) == v for k, v in hashes.items()), 'flat desktop receipt mismatch')
            info = read_object(package/'build-info.json')
            require(all(info.get(k) == v for k, v in hashes.items()), 'text build-info mismatch')
    return catalog

def verify_data_text_update(payload, review, catalog):
    """Validate the schema-3 catalog used by the persistent update fixture."""
    require(review.get('sessionMode') == 'long-lived' and review.get('extraArgs') == DATA_TEXT_ARGS,
            'exact reviewed strict/data/state session required')
    require(isinstance(catalog, dict) and catalog.get('schemaVersion') == 3, 'catalog schema')
    apps, start = catalog.get('apps'), catalog.get('autostart')
    require(isinstance(apps, list) and len(apps) == 2, 'exactly two apps required')
    require(isinstance(start, list) and len(start) == 2 and set(start) == {1, 2}, 'autostart must be exactly apps 1/2')
    by_id = {app.get('id'): app for app in apps if isinstance(app, dict)}
    require(set(by_id) == {1, 2}, 'app IDs must be 1/2')
    require(by_id[1].get('packageId') == 'dev.ming.app-data-desktop'
            and by_id[1].get('dataKey') == 'counter-main' and by_id[1].get('slots') == [1],
            'app1 package/data binding')
    require(by_id[2].get('packageId') == 'dev.ming.text-entry-demo'
            and 'dataKey' not in by_id[2] and 'slots' not in by_id[2],
            'app2 package/data binding')
    fixture = review.get('updateFixture')
    require(fixture == {
        'helper': '/opt/mingos/prepare-update-fixture',
        'installRoot': PERSISTENT_UPDATE_INSTALL_ROOT,
        'updateManifest': '/tmp/mingos-update-fixture/1/app.json',
        'receipt': '/run/evidence/update-fixture.json',
    }, 'update fixture contract')
    for number, package_id in ((1, 'dev.ming.app-data-desktop'), (2, 'dev.ming.text-entry-demo')):
        package = payload / 'packages' / str(number)
        manifest = package / 'app.json'
        data = read_object(manifest)
        require(data.get('schemaVersion') == 1 and data.get('id') == package_id
                and data.get('version') == '0.1.0' and data.get('sdkVersion') == 6
                and data.get('entry') == 'main.wasm',
                'initial package identity/private storage')
        for name in ('compiler.wasm', 'main.wasm'):
            with (package / name).open('rb') as source:
                require(source.read(8) == b'\0asm\x01\0\0\0', 'real Wasm header required: ' + name)
        receipt = read_object(package / 'build-receipt.json')
        require(receipt.get('status') == 'passed', 'initial package receipt')
    helper = payload / 'prepare-update-fixture'
    require(helper.is_file(), 'update fixture helper missing')
    with helper.open('rb') as source:
        require(source.read(4) == b'\x7fELF', 'update fixture helper must be Linux ELF')
    require(isinstance(review.get('updateMigration'), dict)
            and review['updateMigration'].get('fromVersion') == '0.1.0'
            and review['updateMigration'].get('toVersion') == '0.2.0'
            and review['updateMigration'].get('movedFile') == 'counter -> state/counter',
            'update migration receipt contract')
    return catalog

def verify_install_lifecycle(payload, review, catalog):
    """Validate a parent-triggered install flow with a signed source package."""
    require(review.get('sessionMode') == 'long-lived' and review.get('extraArgs') == DATA_TEXT_ARGS,
            'exact reviewed strict/data/state session required')
    require(review.get('installRoot') == INSTALL_LIFECYCLE_INSTALL_ROOT
            and review.get('installEvents') == INSTALL_LIFECYCLE_EVENTS
            and review.get('installSource') == INSTALL_LIFECYCLE_SOURCE,
            'install root/event path contract')
    require(isinstance(catalog, dict) and catalog == {
        'schemaVersion': 3,
        'apps': [{'id': 1, 'title': 'Text Lab', 'packageId': 'dev.ming.text-entry-demo'}],
        'autostart': [1],
    }, 'single initial installed app catalog required')
    events = payload / 'install-events.json'
    require(events.is_file() and review.get('installEventsSha256') == sha256(events),
            'reviewed install event file required')
    actions = read_object(events)
    require(actions.get('schemaVersion') == 1 and isinstance(actions.get('steps'), list),
            'install event schema')
    require(not any(step.get('action', {}).get('op') == 'install' for step in actions['steps'] if isinstance(step, dict)),
            'install must be activated through the native header')
    require(any(any(event.get('kind') == 'pointer' and event.get('x') == 1140 and event.get('y') == 12
                       for event in step.get('events', []) if isinstance(event, dict))
                for step in actions['steps'] if isinstance(step, dict)),
            'native header INSTALL pointer event required')
    require(any(step.get('action', {}).get('op') == 'launch' and step.get('action', {}).get('app') == 2
                for step in actions['steps'] if isinstance(step, dict)),
            'new app launch action required')
    fixture = review.get('installFixture')
    require(fixture == {
        'helper': '/opt/mingos/prepare-install-fixture',
        'installRoot': INSTALL_LIFECYCLE_INSTALL_ROOT,
        'initialWasm': '/opt/mingos/packages/initial/main.wasm',
        'sourceWasm': '/opt/mingos/packages/new/main.wasm',
        'sourceManifest': '/opt/mingos/install-source/app.json',
        'receipt': '/run/evidence/install-fixture.json',
    }, 'install fixture contract')
    for relative in ('packages/initial/main.wasm', 'packages/new/main.wasm'):
        wasm = checked_path(payload, relative)
        require(wasm.read_bytes()[:8] == b'\0asm\x01\0\0\0', 'real Wasm install fixture required')
    require(isinstance(review.get('installLifecycle'), dict)
            and review['installLifecycle'].get('initialPackageId') == 'dev.ming.text-entry-demo'
            and review['installLifecycle'].get('newPackageId') == 'dev.ming.install-demo'
            and review['installLifecycle'].get('newVersion') == '1.0.0',
            'install lifecycle identity contract')
    return catalog

def verify_persistent_install(payload, review, catalog):
    """Freeze the initialization inputs; the second boot must only reopen disk state."""
    require(review.get('sessionMode') == 'long-lived' and review.get('extraArgs') == PERSISTENT_INSTALL_ARGS,
            'exact reviewed strict/data/state/qualification session required')
    require(catalog == {'schemaVersion': 3, 'apps': [
        {'id': 1, 'title': 'Text Lab', 'packageId': 'dev.ming.text-entry-demo'}], 'autostart': [1]}
        and type(catalog['schemaVersion']) is int and type(catalog['apps'][0]['id']) is int
        and type(catalog['autostart'][0]) is int,
        'persistent install requires only initial app1 and autostart [1]')
    require(review.get('persistentInstallFixture') == PERSISTENT_INSTALL_FIXTURE,
            'persistent fixture contract')
    require(review.get('instrumentedQualification') is True
            and review.get('instrumentedProfile') == PERSISTENT_INSTALL_PROFILE
            and review.get('inputSynchronization') == POINTER_SYNC_POLICY,
            'persistent install pointer qualification required')
    persistent_input_hashes(review)
    require(not (payload / 'install-root').exists() and not (payload / 'install-source').exists()
            and not (payload / 'install-events.json').exists(), 'persistent payload must not seed a RAM registry/source/events')
    expected = review.get('persistentInstallFixtureSha256')
    require(isinstance(expected, dict) and set(expected) == {'helper', 'initialWasm', 'sourceWasm'},
            'reviewed persistent helper and Wasm hashes required')
    for field in ('helper', 'initialWasm', 'sourceWasm'):
        relative = PERSISTENT_INSTALL_FIXTURE[field].removeprefix('/opt/mingos/')
        path = checked_path(payload, relative)
        require(path.is_file() and expected[field] == sha256(path), 'persistent fixture hash mismatch: ' + field)
        with path.open('rb') as source:
            header = source.read(8)
        require(header[:4] == b'\x7fELF' if field == 'helper' else header == b'\0asm\x01\0\0\0',
                'persistent fixture executable format: ' + field)
    return catalog

def verify_data_text_private_probe(payload, review, catalog):
    require(isinstance(catalog, dict) and catalog.get('schemaVersion') == 1, 'catalog schema')
    apps, start = catalog.get('apps'), catalog.get('autostart')
    require(isinstance(apps, list) and len(apps) == 3 and all(isinstance(a, dict) for a in apps), 'three apps required')
    require(isinstance(start, list) and len(start) == 3 and all(type(x) is int for x in start)
            and set(start) == {1, 2, 3}, 'autostart must be apps 1/2/3')
    require(all(type(app.get('id')) is int for app in apps)
            and {app['id'] for app in apps} == {1, 2, 3}, 'app identities')
    existing = [app for app in apps if app['id'] in (1, 2)]
    verify_data_text(payload, review, {'schemaVersion': 1, 'apps': existing, 'autostart': [1, 2]})
    probe = next(app for app in apps if app['id'] == 3)
    require(probe == {'id': 3, 'title': 'Private Probe', 'manifest': 'packages/private/app.json'},
            'private probe catalog entry')
    manifest = checked_path(payload, probe['manifest'])
    package = read_object(manifest)
    require(package == {'schemaVersion': 1, 'id': 'dev.ming.private-kernel-probe',
                        'version': '0.1.0', 'sdkVersion': 6, 'entry': 'main.wasm'},
            'private probe identity and no permission declaration')
    wasm = checked_path(payload, 'packages/private/main.wasm')
    with wasm.open('rb') as source:
        require(source.read(8) == b'\0asm\x01\0\0\0', 'private probe Wasm header')
    require(review.get('privateProbeSha256') == {'manifest': sha256(manifest), 'wasm': sha256(wasm)},
            'private probe review hashes')
    return catalog

def verify_data_text_public_probe(payload, review, catalog):
    require(isinstance(catalog, dict) and catalog.get('schemaVersion') == 1, 'catalog schema')
    apps, start = catalog.get('apps'), catalog.get('autostart')
    require(isinstance(apps, list) and len(apps) == 3 and all(isinstance(a, dict) for a in apps), 'three apps required')
    require(isinstance(start, list) and len(start) == 3 and all(type(x) is int for x in start)
            and set(start) == {1, 2, 3}, 'autostart must be apps 1/2/3')
    require(all(type(app.get('id')) is int for app in apps) and {app['id'] for app in apps} == {1, 2, 3}, 'app identities')
    existing = [app for app in apps if app['id'] in (1, 2)]
    verify_data_text(payload, review, {'schemaVersion': 1, 'apps': existing, 'autostart': [1, 2]})
    probe = next(app for app in apps if app['id'] == 3)
    require(probe == {'id': 3, 'title': 'Public Probe', 'manifest': 'packages/public/app.json'},
            'public probe catalog entry')
    manifest = checked_path(payload, probe['manifest'])
    package = read_object(manifest)
    require(package == {'schemaVersion': 1, 'id': 'dev.ming.public-kernel-probe',
                        'version': '0.1.0', 'sdkVersion': 6, 'entry': 'main.wasm',
                        'publicStorage': True}, 'public probe identity and permission declaration')
    wasm = checked_path(payload, 'packages/public/main.wasm')
    with wasm.open('rb') as source:
        require(source.read(8) == b'\0asm\x01\0\0\0', 'public probe Wasm header')
    require(review.get('publicProbeSha256') == {'manifest': sha256(manifest), 'wasm': sha256(wasm)},
            'public probe review hashes')
    require(review.get('publicPreauthorization') == {
        'packageId': 'dev.ming.public-kernel-probe',
        'policy': 'guest-seeded-public-grant-v1',
    }, 'public preauthorization review')
    return catalog

def verify_public_probe(payload, review, catalog):
    require(review.get('sessionMode') == 'bounded' and review.get('extraArgs') == [
        '--duration-ms', '15000', '--isolation', 'strict', '--data-root', '/data/mingos',
        '--state-file', '/run/evidence/desktop-state.json'], 'bounded public probe session required')
    require(catalog == {
        'schemaVersion': 1,
        'apps': [{'id': 1, 'title': 'Public Probe', 'manifest': 'packages/public/app.json'}],
        'autostart': [1],
    }, 'single public probe catalog required')
    manifest = checked_path(payload, 'packages/public/app.json')
    package = read_object(manifest)
    require(package == {'schemaVersion': 1, 'id': 'dev.ming.public-kernel-probe',
                        'version': '0.1.0', 'sdkVersion': 6, 'entry': 'main.wasm',
                        'publicStorage': True}, 'public probe identity and permission declaration')
    wasm = checked_path(payload, 'packages/public/main.wasm')
    with wasm.open('rb') as source:
        require(source.read(8) == b'\0asm\x01\0\0\0', 'public probe Wasm header')
    require(review.get('publicProbeSha256') == {'manifest': sha256(manifest), 'wasm': sha256(wasm)},
            'public probe review hashes')
    require(review.get('publicPreauthorization') == {
        'packageId': 'dev.ming.public-kernel-probe',
        'policy': 'guest-seeded-public-grant-v1',
    }, 'public preauthorization review')
    return catalog
