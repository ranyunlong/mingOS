"""Synthetic raw controls: these tests do not start a worker or a VM."""
import base64
import copy
import hashlib
import json
import unittest

try:
    import persistent_update_receipt as receipt
except ModuleNotFoundError:
    receipt = None

BOOT, SESSION, PID = 'boot', 'session', 42
PACKAGE = 'dev.ming.persistent-update-demo'
PREFIX = b'MING_PERSISTENT_UPDATE '
ALPHA = hashlib.sha256(b'alpha').hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def private(initial=True):
    entries = [] if initial else [{'path': 'state', 'type': 'directory', 'length': 0, 'sha256': None}]
    entries += [{'path': 'note.txt' if initial else 'state/note.txt', 'type': 'file', 'length': 5, 'sha256': ALPHA}]
    return {'schemaVersion': 1, 'packageId': PACKAGE, 'namespaceExists': True,
            'files': {'entries': len(entries), 'bytes': 5, 'sha256': sha(b'ming-private-update-tree-v1\0' + encoded(entries))},
            'nonFilesSha256': sha(b'ming-private-update-non-files-v1\0[]')}


def app(number, version):
    package = 'dev.ming.text-entry-demo' if number == 1 else PACKAGE
    fingerprint = sha((package + version).encode())
    path = f'packages/{package.encode().hex()}/{version}-{fingerprint}'
    return {'app': number, 'packageId': package, 'version': version, 'fingerprint': fingerprint,
            'manifestPath': path + '/app.json', 'manifestSha256': sha((version + package + 'manifest').encode()),
            'wasmPath': path + '/main.wasm', 'wasmSha256': sha((version + package + 'wasm').encode())}


def content(initial=True):
    values = [app(2, '0.1.0' if initial else '0.2.0'), app(1, '0.1.0')]
    controls = {'catalog.json': sha(b'catalog'), 'installed-packages-v1': sha(b'old' if initial else b'new'),
                'trusted-publishers-v1': sha(b'trust'), 'desktop-install-state-v1.json': sha(b'bootstrap')}
    files = dict(controls)
    for item in values + ([] if initial else [app(2, '0.1.0')]):
        for kind in ('manifest', 'wasm'): files[item[kind + 'Path']] = item[kind + 'Sha256']
    return {'catalogSha256': controls['catalog.json'], 'registrySha256': controls['installed-packages-v1'],
            'trustSha256': controls['trusted-publishers-v1'], 'bootstrapSha256': controls['desktop-install-state-v1.json'],
            'apps': values, 'files': [{'path': path, 'sha256': value} for path, value in sorted(files.items())]}


def snapshot(number=1, phase='pre-desktop'):
    initial = number == 1 and phase == 'pre-desktop'
    counts = {'initializedRoots': 1, 'packagesInstalled': 2, 'publisherProvisioned': 1, 'bootstrapPublished': 1,
              'catalogWrites': 2, 'rootPublished': 1, 'sourceBundlesCreated': 1, 'privateSeedWrites': 1,
              'recoveryInvocations': 0, 'uiUpdatesCommitted': 0}
    envelope = {'schemaVersion': 1, 'bootId': BOOT, 'bootNumber': number,
                'role': 'initialize-first' if number == 1 else 'open-existing',
                'marker': {'bootId': BOOT, 'counter': number},
                'previousMarker': None if number == 1 else {'bootId': 'previous', 'counter': 1},
                'diskUuid': '11111111-1111-4111-8111-111111111111', 'registryRoot': '/data/mingos/install-root',
                'catalogPath': '/data/mingos/install-root/catalog.json', 'dataRoot': '/data/mingos',
                'sessionId': None if phase == 'pre-desktop' else SESSION,
                'desktopPid': None if phase == 'pre-desktop' else PID, 'phase': phase,
                'status': 'initialized-first' if initial else 'opened-existing',
                'actions': {key: value if initial else 0 for key, value in counts.items()}}
    return {'envelope': envelope, 'content': content(initial), 'privateContent': private(initial)}


def package_identity(number, version):
    value = app(number, version)
    return {key: value[key] for key in ('packageId', 'version', 'fingerprint', 'manifestSha256', 'wasmSha256')} | {
        'migrationSha256': sha(b'null') if version == '0.1.0' else sha(encoded({'fromVersion': '0.1.0',
            'steps': [{'op': 'ensureDir', 'path': 'state'}, {'op': 'renameFile', 'from': 'note.txt', 'to': 'state/note.txt'}]}))}


def update_record():
    return {'schemaVersion': 1, 'operation': 'update', 'transactionId': 'trial-123', 'app': 2,
            'old': package_identity(2, '0.1.0'), 'new': package_identity(2, '0.2.0'),
            'catalogSha256': content()['catalogSha256'], 'bootstrapSha256': content()['bootstrapSha256'],
            'trustSha256': content()['trustSha256'], 'siblingRecordsSha256': sha(b'sibling'),
            'preservedContentSha256': sha(b'preserved'), 'candidateContentExistedBefore': False,
            'dataRootBindingSha256': sha(b'authority'), 'publicPolicySlot': None, 'privateBefore': private(),
            'backupExpected': True, 'decision': 'prepared', 'confirmation': None, 'privateConfirmed': None}


def update_state(status='trial'):
    return {'transactionId': 'trial-123', 'app': 2, 'packageId': PACKAGE, 'newVersion': '0.2.0', 'status': status}


def history(status='ready'):
    ready = status in ('ready', 'closing', 'closed')
    return {'app': 2, 'generation': 1, 'window': 7, 'workerPid': 51, 'title': 'demo', 'status': status,
            'completed': status == 'closed', 'firstFrameMs': 10 if ready else None,
            'firstReadyMs': 11 if ready else None, 'framesSubmitted': 2 if ready else None,
            'lastSourceSequence': 2 if ready else 0, 'presentedSourceSequence': 2 if ready else 0,
            'presentedViewport': [720, 448] if ready else None, 'packageId': PACKAGE if ready else None,
            'requests': 18 if ready else None, 'inputCounts': [0] * 9 if ready else None,
            'events': [] if ready else None, 'gpuStats': None, 'error': None,
            **({'frameClock': {'source': 'host-monotonic-opportunity', 'closes': 1, 'cancels': 0,
                    'frames': 1, 'inputs': 1, 'pending': 0, 'reads': 1, 'timers': 0, 'waits': 3}}
               if status == 'closed' else {})}


def state(revision, status=None, *, prompt=False, updated=None, phase='ready'):
    apps = [] if status is None else [{'app': 2, 'generation': 1, 'window': 7, 'workerPid': 51,
             'state': status, 'sourceSequence': 2, 'presentedSourceSequence': 2,
             'firstReadyMs': 11 if status in ('ready', 'closing', 'closed') else None,
             'inputReady': status == 'ready', 'minimized': False, 'outer': None}]
    value = {'schemaVersion': 1, 'bootId': BOOT, 'sessionId': SESSION, 'revision': revision, 'phase': phase,
             'root': {'width': 1280, 'height': 800, 'presentedSequence': 2,
                      'storagePrompt': {'mode': 17, 'packageId': PACKAGE, 'version': '0.2.0', 'hasAllow': True} if prompt else None,
                      'storagePromptPresentedSequence': 2 if prompt else None}, 'apps': apps, 'error': None}
    if updated: value['persistentUpdate'] = update_state(updated)
    return value


def anchor(revision, status=None, *, prompt=False, updated=None, trace=0, phase='ready'):
    return {'state': state(revision, status, prompt=prompt, updated=updated, phase=phase),
            'history': [history(status)] if status else [], 'traceCount': trace,
            'droppedTrace': 0, 'droppedInputs': 0}


def record(kind, value):
    return {'kind': kind, 'bootId': BOOT, 'sessionId': SESSION, 'desktopPid': PID, 'receipt': value}


def line(value, prefix=PREFIX):
    return prefix + encoded(value) + b'\n'


def chain(number=1, early_commit=False):
    values = []
    def edge(n, before, after, action='other', target=None, trace=None):
        return record('native-input', {'schemaVersion': 1, 'sequence': n, 'pressSequence': n if n % 2 else n - 1,
            'edge': 'down' if n % 2 else 'up', 'x': 50, 'y': 60, 'action': action, 'targetApp': target,
            'before': before, 'after': after, 'trace': trace or []})
    def event(kind): return {'atMs': 12, 'app': 2, 'generation': 1, 'window': 7, 'kind': kind, 'detail': ''}
    updated = 'trial' if number == 1 else None
    rev = 1
    if number == 1:
        values += [edge(1, anchor(1), anchor(2)), edge(2, anchor(3), anchor(4, prompt=True)),
                   edge(3, anchor(5, prompt=True), anchor(6, prompt=True))]
        before = anchor(7, prompt=True)
        new = package_identity(2, '0.2.0')
        cause = {'app': 2, **{key: new[key] for key in ('packageId', 'version', 'fingerprint')},
                 'releaseSequence': 4, 'pressSequence': 3, 'presentedSequence': 2, 'promptRevision': 1,
                 'binding': {'mode': 17, 'packageId': PACKAGE, 'version': '0.2.0', 'hasAllow': True,
                     'promptRevision': 1, 'presentedSequence': 2, 'fingerprint': new['fingerprint'],
                     'manifestPath': '/opt/mingos/install-source/app.json'}, 'before': before}
        outer = update_record()
        inner = {'packageId': PACKAGE, 'oldVersion': '0.1.0', 'oldFingerprint': outer['old']['fingerprint'],
                 'newVersion': '0.2.0', 'newFingerprint': new['fingerprint'], 'backupName': '.ming-update-backup-trial-123',
                 'migrationDeclared': True, 'phase': 'recordPublished'}
        observation = {'schemaVersion': 1, 'transactionId': 'trial-123', 'outerRecordSha256': sha(encoded(outer)),
                       'innerJournalSha256': sha(encoded(inner)), 'innerPhase': 'recordPublished',
                       'outerRecordBase64': base64.b64encode(encoded(outer)).decode(),
                       'innerJournalBase64': base64.b64encode(encoded(inner)).decode(),
                       'content': content(False), 'privateContent': private(False)}
        values += [record('trial-published', {'record': outer, 'confirmation': cause,
                    'anchor': anchor(8, updated='trial'), 'trialObservation': observation}),
                   record('update-confirm', {**cause, 'allow': True, 'after': anchor(9, updated='trial')}),
                   edge(4, before, anchor(10, updated='trial'))]
        rev = 11
    down = 5 if number == 1 else 1
    values += [edge(down, anchor(rev, updated=updated), anchor(rev + 1, 'starting', updated=updated, trace=1),
                    'launch', 2, [event('launch')]),
               edge(down + 1, anchor(rev + 2, 'starting', updated=updated, trace=1),
                    anchor(rev + 3, 'starting', updated=updated, trace=1))]
    for phase, delta, status in (('worker-started', 4, 'running'), ('ready', 5, 'ready')):
        values.append(record('native-launch', {'schemaVersion': 1, 'downSequence': down, 'phase': phase,
            'app': 2, 'generation': 1, 'window': 7, 'workerPid': 51,
            'anchor': anchor(rev + delta, status, updated=updated, trace=2)}))
    close = down + 2
    values += [edge(close, anchor(rev + 6, 'ready', updated=updated, trace=2),
                    anchor(rev + 7, 'closing', updated=updated, trace=3), trace=[event('close-request')])]
    if not early_commit or number == 2:
        values += [edge(close + 1, anchor(rev + 8, 'closing', updated=updated, trace=3),
                        anchor(rev + 9, 'closing', updated=updated, trace=3))]
    if number == 1:
        proof = {'app': 2, 'package': package_identity(2, '0.2.0'), 'generation': 1, 'window': 7, 'workerPid': 51,
                 'inputReady': True, 'presentedSourceSequence': 2, 'completed': True,
                 'resultAccepted': True, 'nativeExitOk': True, 'reaped': True}
        terminal = anchor(rev + 10, 'closed', updated='committed', trace=4)
        values += [record('startup-confirmed', {'transactionId': 'trial-123', 'confirmation': cause,
                    'downSequence': down, 'proof': proof, 'anchor': terminal}),
                   record('persistent-update-commit', {'record': {**outer, 'decision': 'commit-decided',
                    'confirmation': proof, 'privateConfirmed': private(False)}, 'confirmation': cause,
                    'downSequence': down, 'content': content(False)})]
        if early_commit:
            values += [edge(close + 1, anchor(rev + 8, 'closing', updated='trial', trace=3),
                            anchor(rev + 11, 'closed', updated='committed', trace=4), trace=[event('startup-confirmed')])]
        values += [edge(9, anchor(rev + 12, 'closed', updated='committed', trace=4),
                            anchor(rev + 13, 'closed', updated='committed', trace=4)),
                   edge(10, anchor(rev + 14, 'closed', updated='committed', trace=4),
                            anchor(rev + 15, 'closed', updated='committed', trace=4))]
    return values


class PersistentUpdateReceiptTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(receipt, 'persistent update parser is not implemented')

    def replay(self, values=None, number=1):
        parser = receipt.PersistentUpdateReceipts(BOOT, PID, number, before=snapshot(number))
        raw = b'noise\n' + b''.join(line(value) for value in (chain(number) if values is None else values))
        rows = []
        for offset in range(0, len(raw), 79): rows.extend(parser.feed(raw[offset:offset + 79]))
        parser.bind_state(state(100, 'closed', updated='committed' if number == 1 else None, phase='stopped'))
        parser.complete(number)
        for row in rows:
            data = base64.b64decode(row['rawBase64'], validate=True)
            self.assertEqual(data, raw[row['logStartOffset']:row['logEndOffset']])
            self.assertEqual(row['record'], json.loads(data[len(PREFIX):]))
        return parser

    def test_full_native_chain_binds_real_trial_private_and_commit(self):
        parser = self.replay()
        self.assertEqual(parser.trial_record, update_record())
        self.assertEqual(parser.committed_content, content(False))
        self.assertEqual(parser.private_content, private(False))

    def test_commit_can_arrive_before_complete_close_release(self):
        self.replay(chain(early_commit=True))

    def test_boot2_only_reopens_and_runs_new_app(self):
        parser = self.replay(number=2)
        self.assertIsNone(parser.trial_record)
        self.assertIsNone(parser.committed_content)

    def test_trial_rejects_old_private_synthetic_phase_and_missing_observation(self):
        mutators = [lambda v: v.pop('trialObservation'), lambda v: v['trialObservation'].update(privateContent=private()),
                    lambda v: v['trialObservation'].update(innerPhase='startupConfirmed'),
                    lambda v: v['trialObservation'].update(transactionId='other'),
                    lambda v: v['trialObservation'].update(outerRecordSha256='a' * 64),
                    lambda v: v['trialObservation'].update(innerJournalSha256='a' * 64),
                    lambda v: v['trialObservation']['privateContent'].update(nonFilesSha256='a' * 64),
                    lambda v: v['record'].update(candidateContentExistedBefore=True),
                    lambda v: v['record'].update(privateBefore=private(False))]
        for mutate in mutators:
            with self.subTest(mutate=mutate):
                values = chain(); mutate(values[3]['receipt'])
                with self.assertRaises(ValueError): self.replay(values)

    def test_original_durable_bytes_are_decoded_hashed_and_parsed_together(self):
        for marker, mutation in [('outerRecord', 'identity'), ('innerJournal', 'phase'),
                                 ('innerJournal', 'backup'), ('innerJournal', 'duplicate-key'),
                                 ('outerRecord', 'encoding'), ('innerJournal', 'over-limit')]:
            values = chain(); observation = values[3]['receipt']['trialObservation']
            data = base64.b64decode(observation[marker + 'Base64'])
            if mutation == 'encoding':
                observation[marker + 'Base64'] += '\n'
            else:
                value = json.loads(data)
                if mutation == 'identity': value['transactionId'] = 'another'
                elif mutation == 'phase': value['phase'] = 'startupConfirmed'
                elif mutation == 'backup': value['backupName'] = '.ming-update-backup-another'
                data = (b'{"phase":"recordPublished","phase":"recordPublished"}' if mutation == 'duplicate-key'
                        else b' ' * 16385 if mutation == 'over-limit' else encoded(value))
                observation[marker + 'Base64'] = base64.b64encode(data).decode()
                observation[marker + 'Sha256'] = sha(data)
            with self.subTest(marker=marker, mutation=mutation), self.assertRaises(ValueError): self.replay(values)

    def test_trial_private_tree_rejects_empty_wrong_extra_data_and_directory_changes(self):
        for files in ({'entries': 0, 'bytes': 0, 'sha256': sha(b'')},
                      {'entries': 1, 'bytes': 5, 'sha256': private(False)['files']['sha256']},
                      {'entries': 3, 'bytes': 5, 'sha256': private(False)['files']['sha256']},
                      {'entries': 2, 'bytes': 6, 'sha256': private(False)['files']['sha256']},
                      {'entries': 2, 'bytes': 5, 'sha256': sha(b'wrong bytes or empty directory')}):
            values = chain(); values[3]['receipt']['trialObservation']['privateContent']['files'] = files
            with self.subTest(files=files), self.assertRaises(ValueError): self.replay(values)

    def test_missing_or_duplicate_lifecycle_receipts_fail_closed(self):
        for kind in ('trial-published', 'update-confirm', 'native-launch', 'startup-confirmed', 'persistent-update-commit'):
            values = chain(); index = next(i for i, value in enumerate(values) if value['kind'] == kind)
            with self.subTest(kind=kind, change='missing'), self.assertRaises(ValueError): self.replay(values[:index] + values[index + 1:])
            with self.subTest(kind=kind, change='duplicate'), self.assertRaises(ValueError): self.replay(values[:index] + [values[index]] + values[index:])

    def test_confirmation_binds_current_prompt_frame_press_and_new_signature_identity(self):
        mutations = [lambda c: c.update(pressSequence=1), lambda c: c.update(presentedSequence=1),
                     lambda c: c['binding'].update(mode=19), lambda c: c['binding'].update(manifestPath='/other'),
                     lambda c: c.update(version='0.1.0'), lambda c: c['before']['state']['root'].update(storagePromptPresentedSequence=None)]
        for mutate in mutations:
            values = chain(); mutate(values[3]['receipt']['confirmation'])
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.replay(values)

    def test_proof_requires_same_native_generation_pid_ready_close_result_and_reap(self):
        for key, bad in [('generation', 2), ('workerPid', PID), ('presentedSourceSequence', 3),
                         ('inputReady', False), ('resultAccepted', False), ('nativeExitOk', False), ('reaped', False)]:
            values = chain(); value = next(v['receipt'] for v in values if v['kind'] == 'startup-confirmed')
            value['proof'][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError): self.replay(values)
        for path, bad in [('completed', False), ('packageId', 'other'), ('firstReadyMs', None)]:
            values = chain(); value = next(v['receipt'] for v in values if v['kind'] == 'startup-confirmed')
            value['anchor']['history'][0][path] = bad
            with self.subTest(path=path), self.assertRaises(ValueError): self.replay(values)

    def test_unchanged_history_bootstrap_and_private_content_are_required(self):
        for mutate in (lambda v: v['content']['files'].pop(),
                       lambda v: v['content'].update(bootstrapSha256='a' * 64),
                       lambda v: v['record']['privateConfirmed']['files'].update(entries=3),
                       lambda v: v['record'].update(transactionId='different')):
            values = chain(); value = next(v['receipt'] for v in values if v['kind'] == 'persistent-update-commit'); mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError): self.replay(values)

    def test_native_launch_and_close_cannot_be_scripted_or_reordered(self):
        for mutate in (lambda v: v.update(downSequence=1), lambda v: v.update(generation=2),
                       lambda v: v.update(workerPid=PID), lambda v: v['anchor']['state'].update(error='failed')):
            values = chain(); mutate(next(v['receipt'] for v in values if v['kind'] == 'native-launch'))
            with self.assertRaises(ValueError): self.replay(values)
        values = chain(); values = [v for v in values if not (v['kind'] == 'native-input' and v['receipt']['sequence'] == 7)]
        with self.assertRaises(ValueError): self.replay(values)

    def test_old_prefixes_truncation_duplicate_keys_and_raw_bounds_are_rejected(self):
        for raw in (b'MING_PERSISTENT_INSTALL {}\n', b'MING_PERSISTENT_UNINSTALL {}\n',
                    PREFIX + b'{"kind":"x","kind":"x"}\n', PREFIX + b' ' * 262145 + b'\n'):
            parser = receipt.PersistentUpdateReceipts(BOOT, PID, 1, before=snapshot())
            with self.assertRaises(ValueError): parser.feed(raw)
        for raw in (PREFIX[:10], line(chain()[0])[:-1]):
            parser = receipt.PersistentUpdateReceipts(BOOT, PID, 1, before=snapshot())
            parser.feed(raw)
            with self.assertRaises(ValueError): parser.finish()

    def test_boot2_refuses_mutation_state_and_wrong_pre_generation(self):
        with self.assertRaises(ValueError): self.replay(chain(), number=2)
        parser = receipt.PersistentUpdateReceipts(BOOT, PID, 2, before=snapshot(2))
        with self.assertRaises(ValueError): parser.bind_state(state(1, updated='committed'))
        for mutate in (lambda v: v['envelope'].update(bootNumber=True),
                       lambda v: v['envelope']['actions'].update(recoveryInvocations=1),
                       lambda v: v.update(privateContent=private())):
            before = snapshot(2); mutate(before)
            with self.assertRaises(ValueError): receipt.PersistentUpdateReceipts(BOOT, PID, 2, before=before)

    def test_history_and_optional_worker_telemetry_are_strict(self):
        for key, bad in [('presentedViewport', [0, 448]), ('inputCounts', [False] * 9),
                         ('events', [{'code': True, 'value': 0}]), ('moduleSource', 'script'),
                         ('gpuStats', {'unknown': 1}), ('packageId', 1),
                         ('appData', {'writes': -1}), ('isolation', {'error': 'failed'})]:
            values = chain()
            value = next(v['receipt'] for v in values if v['kind'] == 'native-launch' and v['receipt']['phase'] == 'ready')
            value['anchor']['history'][0][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError): self.replay(values)
        for key, bad in [('firstReadyMs', False), ('outer', {'x': 1}), ('isolation', {'error': 'failed'}),
                         ('appData', {'writes': -1})]:
            values = chain()
            value = next(v['receipt'] for v in values if v['kind'] == 'native-launch' and v['receipt']['phase'] == 'ready')
            value['anchor']['state']['apps'][0][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError): self.replay(values)


if __name__ == '__main__':
    unittest.main()
