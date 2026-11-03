"""Independent raw relay fixtures; these tests never start a VM or worker."""
import base64
import copy
import json
import unittest

import persistent_uninstall_receipt as receipt
from test_persistent_uninstall_guest import helper_receipt
from wayland_receipt import PointerReceipt


BOOT, SESSION, PID = 'boot', 'session', 42


def history(generation=3, status='closed'):
    ready = status in ('ready', 'closed')
    return {'app': 2, 'generation': generation, 'window': 7, 'workerPid': 51,
        'title': 'demo', 'status': status, 'completed': status == 'closed',
        'firstFrameMs': 10 if ready else None, 'firstReadyMs': 11 if ready else None,
        'framesSubmitted': 2 if ready else None, 'lastSourceSequence': 2 if ready else 0,
        'presentedSourceSequence': 2 if ready else 0, 'presentedViewport': [720, 448] if ready else None,
        'packageId': receipt.PACKAGE_ID if ready else None, 'requests': 18 if ready else None,
        'inputCounts': [0] * 9 if ready else None, 'events': [] if ready else None,
        'gpuStats': None, 'error': None,
        **({'frameClock': {'source': 'host-monotonic-opportunity', 'closes': 1, 'cancels': 0,
            'frames': 1, 'inputs': 1, 'pending': 0, 'reads': 1, 'timers': 0, 'waits': 3}}
           if status == 'closed' else {})}


def state(revision=1, status=None, prompt=None, presented=1, phase='ready'):
    apps = [] if status is None else [{'app': 2, 'generation': 3, 'window': 7, 'workerPid': 51,
        'state': status, 'sourceSequence': 2, 'presentedSourceSequence': 2,
        'firstReadyMs': 11 if status in ('ready', 'closed') else None,
        'inputReady': status == 'ready', 'minimized': False, 'outer': None}]
    return {'schemaVersion': 1, 'bootId': BOOT, 'sessionId': SESSION, 'revision': revision,
        'phase': phase, 'root': {'width': 1280, 'height': 800, 'presentedSequence': presented,
            'storagePrompt': prompt, 'storagePromptPresentedSequence': presented if prompt else None},
        'apps': apps, 'error': None}


def anchor(revision, status=None, *, prompt=None, presented=1, trace=0, old=False):
    rows = [history(1)] if old else []
    if status: rows.append(history(status=status))
    return {'state': state(revision, status, prompt, presented), 'history': rows,
        'traceCount': trace, 'droppedTrace': 0, 'droppedInputs': 0}


def record(kind, value):
    return {'kind': kind, 'bootId': BOOT, 'sessionId': SESSION, 'desktopPid': PID, 'receipt': value}


def line(value, prefix=receipt.PERSISTENT_UNINSTALL_PREFIX):
    return prefix + json.dumps(value, separators=(',', ':')).encode() + b'\n'


def chain(old=False):
    installed = helper_receipt(False)['content']
    target = copy.deepcopy(installed)
    from test_infra import InfraTests
    installed = InfraTests().persistent_helper_receipt(False)['content']
    identity = {key: installed['apps'][0][key] for key in ('packageId', 'version', 'fingerprint')}
    values = [record('persistent-install-commit', {'schemaVersion': 1, 'status': 'committed', **identity,
        'registryRoot': '/data/mingos/install-root', 'catalogPath': '/data/mingos/install-root/catalog.json',
        'content': installed, 'persistentBlockConfirmed': False}),
        record('persistent-install-installed', {'schemaVersion': 1, 'registryRoot': '/data/mingos/install-root',
            'catalogPath': '/data/mingos/install-root/catalog.json', 'stateRevision': 1, 'content': installed})]
    def a(*args, **kwargs): return anchor(*args, old=old, **kwargs)
    def edge(n, before, after, action='other', target_app=None, trace=None):
        return record('native-input', {'schemaVersion': 1, 'sequence': n, 'pressSequence': n if n % 2 else n - 1,
            'edge': 'down' if n % 2 else 'up', 'x': 50 if n < 7 else 841, 'y': 60 if n < 7 else 465,
            'action': action, 'targetApp': target_app, 'before': before, 'after': after, 'trace': trace or []})
    def event(kind): return {'atMs': 12, 'app': 2, 'generation': 3, 'window': 7, 'kind': kind, 'detail': ''}
    values += [edge(1, a(2), a(3, 'starting', trace=1), 'launch', 2, [event('launch')]),
               edge(2, a(3, 'starting', trace=1), a(4, 'starting', trace=1))]
    for phase, rev, status in (('worker-started', 5, 'running'), ('ready', 6, 'ready')):
        values.append(record('native-launch', {'schemaVersion': 1, 'downSequence': 1, 'phase': phase,
            'app': 2, 'generation': 3, 'window': 7, 'workerPid': 51, 'anchor': a(rev, status, trace=2)}))
    values += [edge(3, a(7, 'ready', trace=2), a(8, 'closing', trace=3), trace=[event('close-request')]),
               edge(4, a(8, 'closing', trace=3), a(9, 'closing', trace=3)),
               edge(5, a(10, 'closed', trace=8), a(11, 'closed', trace=8))]
    cause = {'app': 2, **identity, 'pressSequence': 5, 'releaseSequence': 6,
        'presentedSequence': 1, 'promptRevision': 17}
    prompt = {'mode': 10, 'packageId': receipt.PACKAGE_ID, 'version': '1.0.0', 'hasAllow': True}
    request_before, request_after = a(11, 'closed', trace=8), a(12, 'closed', prompt=prompt, trace=8)
    request_after['state']['root']['storagePromptPresentedSequence'] = None
    values += [record('uninstall-request', {'schemaVersion': 1, **cause, 'before': request_before, 'after': request_after}),
               edge(6, request_before, a(13, 'closed', prompt=prompt, presented=2, trace=8)),
               edge(7, a(14, 'closed', prompt=prompt, presented=2, trace=8), a(15, 'closed', prompt=prompt, presented=2, trace=8))]
    cause = dict(cause, pressSequence=7, releaseSequence=8, presentedSequence=2)
    before, after = a(15, 'closed', prompt=prompt, presented=2, trace=8), a(16, presented=2, trace=10)
    after['history'].append(history())
    values += [record('persistent-uninstall-commit', {'schemaVersion': 1, 'status': 'committed', 'removed': identity,
        'registryRoot': '/data/mingos/install-root', 'catalogPath': '/data/mingos/install-root/catalog.json',
        'content': target, 'cause': cause, 'privateBefore': receipt.PRIVATE_CONTENT, 'privateAfter': receipt.PRIVATE_CONTENT}),
        record('uninstall-confirm', {'schemaVersion': 1, **cause, 'allow': True, 'before': before, 'after': after}),
        edge(8, before, dict(after, state=dict(after['state'], revision=17)), trace=[event('persistent-uninstall-commit'), event('uninstalled')])]
    return values


class PersistentUninstallReceiptTests(unittest.TestCase):
    def parser(self, number=1):
        return receipt.PersistentUninstallReceipts(BOOT, PID, number)

    def replay(self, values, *, old=False):
        parser = self.parser()
        raw = b'noise\n' + b''.join(map(line, values))
        rows = []
        for i in range(0, len(raw), 17): rows.extend(parser.feed(raw[i:i+17]))
        parser.bind_state(state(100, phase='stopped'))
        parser.complete(1)
        self.assertEqual(len(rows), len(values))
        for row in rows:
            data = base64.b64decode(row['rawBase64'], validate=True)
            self.assertEqual(data, raw[row['logStartOffset']:row['logEndOffset']])
            self.assertEqual(row['record'], json.loads(data[len(receipt.PERSISTENT_UNINSTALL_PREFIX):]))
        return parser

    def test_complete_chain_accepts_commit_before_confirm_and_full_up(self):
        parser = self.replay(chain())
        self.assertEqual(parser.private_content, receipt.PRIVATE_CONTENT)
        self.assertEqual(parser.uninstalled_content, helper_receipt()['content'])

    def test_confirm_record_can_precede_commit_while_release_is_still_pending(self):
        values = chain()
        values[-3], values[-2] = values[-2], values[-3]
        self.replay(values)

    def test_release_after_anchor_must_preserve_the_same_history_and_state(self):
        for mutate in (lambda v: v[-1]['receipt']['after']['history'][0].update(requests=19),
                       lambda v: v[-1]['receipt']['after']['state']['apps'].append(
                           state(1, 'closed')['apps'][0]),
                       lambda v: v[-1]['receipt']['after']['state']['root'].update(width=1200)):
            values = chain()
            values[-1]['receipt']['after'] = copy.deepcopy(values[-1]['receipt']['after'])
            mutate(values)
            with self.assertRaises(ValueError): self.replay(values)

    def test_prior_closed_generation_is_legal_and_new_generation_is_bound(self):
        self.replay(chain(old=True))
        values = chain(old=True)
        values[4]['receipt']['generation'] = 1
        with self.assertRaises(ValueError): self.replay(values)

    def test_strict_worker_state_and_history_optional_telemetry(self):
        isolation = {'requested': 'strict', 'prepared': True, 'applied': True, 'cleaned': True,
            'uid': 20001, 'gid': 20001, 'cgroup': '/sys/fs/cgroup/mingos/worker',
            'limits': {'memoryBytes': 536870912, 'pids': 64, 'cpuMax': 'max 100000'},
            'namespaceIds': [1, 2, 3, 4], 'metrics': {'memoryCurrent': 100, 'memoryPeak': 200,
                'pidsCurrent': 0, 'vmHwmBytes': 100, 'cpuStat': 'usage_usec 1',
                'memoryEvents': 'oom 0', 'pidsEvents': 'max 0'}, 'error': None}
        data = {'writes': 1, 'deletes': 0, 'bytesWritten': 5, 'directorySyncConfirmed': 1,
            'directorySyncUnconfirmed': 0, 'lastReceipt': {'slot': 1, 'operation': 'write', 'length': 5, 'directorySynced': True}}
        values = chain()
        def enrich(value):
            if isinstance(value, dict):
                if 'app' in value and ('inputReady' in value or 'completed' in value):
                    value.update(isolation=copy.deepcopy(isolation), appData=copy.deepcopy(data))
                for child in value.values(): enrich(child)
            elif isinstance(value, list):
                for child in value: enrich(child)
        enrich(values)
        self.replay(values)
        good = state(1, 'ready'); good['apps'][0].update(isolation=isolation, appData=data)
        self.parser().bind_state(good)
        for mutate in (lambda v: v['isolation'].update(extra=0),
                       lambda v: v['isolation'].update(applied=1),
                       lambda v: v['isolation'].update(error='failure'),
                       lambda v: v['isolation']['limits'].update(pids=True),
                       lambda v: v['isolation'].update(namespaceIds=[1, 2]),
                       lambda v: v['isolation']['metrics'].update(memoryCurrent=-1),
                       lambda v: v['appData'].update(writes=True),
                       lambda v: v['appData']['lastReceipt'].update(directorySynced=1)):
            value = copy.deepcopy(good); mutate(value['apps'][0])
            with self.assertRaises(ValueError): self.parser().bind_state(value)

    def test_boot2_requires_no_mutations_or_app2_and_normal_terminal(self):
        parser = self.parser(2)
        parser.bind_state(state(1))
        parser.bind_state(state(2, phase='stopped'))
        parser.complete(2)
        for value in chain():
            with self.assertRaises(ValueError): self.parser(2).feed(line(value))
        for bad in (state(1, 'closed'), dict(state(), phase='failed'), dict(state(), error='failure')):
            with self.assertRaises(ValueError): self.parser(2).bind_state(bad)
        with self.assertRaises(ValueError): self.parser(2).complete(2)

    def test_drop_reorder_identity_and_unknown_receipts_fail_closed(self):
        original = chain()
        mutations = [lambda v: v.pop(5), lambda v: v.reverse(), lambda v: v[2].update(desktopPid=43),
            lambda v: v[2].update(sessionId='other'), lambda v: v[2].update(kind='unknown'),
            lambda v: v[2]['receipt'].update(sequence=2),
            lambda v: v[2]['receipt']['after'].update(droppedInputs=1),
            lambda v: v[2]['receipt']['after'].update(droppedTrace=1),
            lambda v: v[4]['receipt'].update(workerPid=52),
            lambda v: v[-3]['receipt']['cause'].update(pressSequence=5),
            lambda v: v[-2]['receipt'].update(promptRevision=18),
            lambda v: v[-1]['receipt'].update(x=842),
            lambda v: v[-2]['receipt']['before']['state']['root'].update(storagePromptPresentedSequence=None),
            lambda v: v[-2]['receipt']['before']['history'][0].update(completed=False),
            lambda v: v[-3]['receipt'].update(privateAfter={'packageId': receipt.PACKAGE_ID, 'files': []})]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                values = copy.deepcopy(original); mutate(values)
                with self.assertRaises(ValueError): self.replay(values)

    def test_uninstall_preserves_app1_trust_and_all_historical_package_bytes(self):
        for kind in ('app1', 'trust', 'bootstrap', 'history'):
            values = chain(); content = values[-3]['receipt']['content']
            if kind == 'app1':
                content['apps'][0]['manifestSha256'] = '8' * 64
                path = content['apps'][0]['manifestPath']
            elif kind == 'trust':
                content['trustSha256'] = '8' * 64; path = 'trusted-publishers-v1'
            elif kind == 'bootstrap':
                content['bootstrapSha256'] = '8' * 64; path = 'desktop-install-state-v1.json'
            else:
                path = next(row['path'] for row in content['files'] if row['path'].endswith('app.json')
                            and receipt.PACKAGE_ID.encode().hex() in row['path'])
            next(row for row in content['files'] if row['path'] == path)['sha256'] = '8' * 64
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError): self.replay(values)

    def test_raw_strict_json_prefix_truncation_and_bounds(self):
        for raw in (line(chain()[0], b'MING_PERSISTENT_INSTALL '),
            receipt.PERSISTENT_UNINSTALL_PREFIX + b'{"kind":1,"kind":2}\n',
            receipt.PERSISTENT_UNINSTALL_PREFIX + b'{"a":NaN}\n',
            receipt.PERSISTENT_UNINSTALL_PREFIX + b'[' * 64 + b'0' + b']' * 64 + b'\n',
            receipt.PERSISTENT_UNINSTALL_PREFIX + b'x' * 262144 + b'\n', b'x' * 65537 + b'\n'):
            with self.subTest(raw=raw[:80]):
                with self.assertRaises(ValueError): self.parser().feed(raw)
        for raw in (b'MING_PERSISTENT_UN', receipt.PERSISTENT_UNINSTALL_PREFIX + b'{'):
            parser = self.parser(); parser.feed(raw)
            with self.assertRaises(ValueError): parser.finish()
        parser = self.parser(); parser.count = 128
        with self.assertRaisesRegex(ValueError, 'count bound'): parser.feed(line(chain()[0]))

    def test_new_prefix_uses_large_bound_and_never_creates_pointer_readiness(self):
        parser = PointerReceipt()
        prefix = receipt.PERSISTENT_UNINSTALL_PREFIX
        parser.feed(prefix + b'x' * (262144 - len(prefix)))
        self.assertEqual(parser.feed(b'\n'), [])
        self.assertEqual(parser.offset, 262145)
        with self.assertRaises(ValueError): parser.feed(prefix + b'x' * (262145 - len(prefix)))
        parser = PointerReceipt()
        self.assertEqual(parser.feed(prefix + b'-> wl_seat#8.get_pointer(new id wl_pointer#99)\n'), [])
        self.assertIsNone(parser.pointer)


if __name__ == '__main__': unittest.main()
