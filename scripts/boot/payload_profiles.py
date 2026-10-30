"""Closed, main-reviewed payload profiles. No build or guest execution on import."""
import json
from pathlib import Path
from bootlib import checked_path, sha256

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
