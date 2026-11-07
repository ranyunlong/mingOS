"""Fail-closed raw receipts for the independent persistent-update qualification.

The observer reports hashes of complete trees, including empty directories. A
trial is accepted only from the real native stream and its original durable
outer/inner bytes; an old baseline is never used as the new private observation.
"""
import base64
import copy
import hashlib
import json
import re

from bootlib import PERSISTENT_UPDATE_ROOT, PERSISTENT_UPDATE_CATALOG, strict_json
from infra import integer, state_ready, validate_state
from wayland_receipt import check_line_size

PERSISTENT_UPDATE_PREFIX = b'MING_PERSISTENT_UPDATE '
OLD_PREFIXES = (b'MING_PERSISTENT_INSTALL ', b'MING_PERSISTENT_UNINSTALL ')
PACKAGE_ID = 'dev.ming.persistent-update-demo'
SOURCE_MANIFEST = '/opt/mingos/install-source/app.json'
CONTROL = {'catalog.json': 'catalogSha256', 'installed-packages-v1': 'registrySha256',
           'trusted-publishers-v1': 'trustSha256', 'desktop-install-state-v1.json': 'bootstrapSha256'}
APP_FIELDS = ('app', 'packageId', 'version', 'fingerprint', 'manifestPath', 'manifestSha256', 'wasmPath', 'wasmSha256')
IDENTITY_FIELDS = ('packageId', 'version', 'fingerprint', 'manifestSha256', 'wasmSha256', 'migrationSha256')
RECORD_FIELDS = ('schemaVersion', 'operation', 'transactionId', 'app', 'old', 'new', 'catalogSha256',
    'bootstrapSha256', 'trustSha256', 'siblingRecordsSha256', 'preservedContentSha256',
    'candidateContentExistedBefore', 'dataRootBindingSha256', 'publicPolicySlot', 'privateBefore',
    'backupExpected', 'decision', 'confirmation', 'privateConfirmed')
CAUSE_FIELDS = ('app', 'packageId', 'version', 'fingerprint', 'releaseSequence', 'pressSequence',
                'presentedSequence', 'promptRevision', 'binding', 'before')
HISTORY_FIELDS = ('app', 'generation', 'window', 'title', 'workerPid', 'status', 'completed',
    'firstFrameMs', 'firstReadyMs', 'framesSubmitted', 'lastSourceSequence', 'presentedSourceSequence',
    'presentedViewport', 'packageId', 'requests', 'inputCounts', 'events', 'gpuStats', 'error')
HISTORY_OPTIONAL = ('moduleSource', 'fuelConsumed', 'frameClock', 'appData', 'isolation')
GPU_FIELDS = ('shadowCacheHits', 'shadowCacheMisses', 'shadowCacheEvictions', 'shadowGenerationWork',
    'shadowParameterUploadBytes', 'shadowResidentBytes', 'shadowEntries', 'imageUploads', 'glyphUploads',
    'glyphUploadBytes', 'glyphCacheEvictions', 'glyphCacheCompactions', 'glyphResidentBytes', 'glyphEntries',
    'imageCacheCompactions', 'imageUploadBytes', 'sceneUploadBytes', 'readbackBytes', 'renderSubmissions',
    'surfacePresentations', 'surfaceFormat', 'presentationUploadBytes', 'surfaceReconfigurations', 'cachedImages')
ACTION_COUNTS = {'initializedRoots': 1, 'packagesInstalled': 2, 'publisherProvisioned': 1,
    'bootstrapPublished': 1, 'catalogWrites': 2, 'rootPublished': 1, 'sourceBundlesCreated': 1,
    'privateSeedWrites': 1, 'recoveryInvocations': 0, 'uiUpdatesCommitted': 0}


def exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise ValueError('persistent update exact fields mismatch')


def digest(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('persistent update digest mismatch')
    return value


def bounded_json(raw):
    try:
        value = strict_json(raw)
    except (RecursionError, UnicodeError) as error:
        raise ValueError('invalid persistent update JSON') from error
    work, count = [(value, 0)], 0
    while work:
        item, depth = work.pop(); count += 1
        if depth > 24 or count > 16384:
            raise ValueError('persistent update JSON nesting/item bound')
        if type(item) is dict: work.extend((child, depth + 1) for child in item.values())
        elif type(item) is list: work.extend((child, depth + 1) for child in item)
    return value


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def worker_telemetry(value):
    if 'isolation' in value:
        isolation = value['isolation']
        exact(isolation, ('requested', 'prepared', 'applied', 'cleaned', 'uid', 'gid', 'cgroup',
                          'limits', 'namespaceIds', 'metrics', 'error'))
        if (isolation['requested'] != 'strict' or isolation['error'] is not None
                or any(type(isolation[key]) is not bool for key in ('prepared', 'applied', 'cleaned'))
                or any(isolation[key] is not None and not integer(isolation[key], 20000, 59999) for key in ('uid', 'gid'))
                or (isolation['cgroup'] is not None and (not isinstance(isolation['cgroup'], str)
                    or not 1 <= len(isolation['cgroup']) <= 4096 or '\0' in isolation['cgroup']))):
            raise ValueError('persistent update isolation identity/error')
        if isolation['limits'] is not None:
            limits = isolation['limits']; exact(limits, ('memoryBytes', 'pids', 'cpuMax'))
            if (not integer(limits['memoryBytes'], 1) or not integer(limits['pids'], 1, 2**32-1)
                    or not isinstance(limits['cpuMax'], str) or len(limits['cpuMax']) > 128
                    or re.fullmatch(r'(max|[0-9]+) [0-9]+', limits['cpuMax']) is None):
                raise ValueError('persistent update isolation limits')
        if isolation['namespaceIds'] is not None and (type(isolation['namespaceIds']) is not list
                or len(isolation['namespaceIds']) != 4 or any(not integer(n, 1) for n in isolation['namespaceIds'])):
            raise ValueError('persistent update namespace identities')
        if isolation['metrics'] is not None:
            metrics = isolation['metrics']
            exact(metrics, ('memoryCurrent', 'memoryPeak', 'pidsCurrent', 'vmHwmBytes', 'cpuStat', 'memoryEvents', 'pidsEvents'))
            if (any(not integer(metrics[key]) for key in ('memoryCurrent', 'memoryPeak', 'pidsCurrent'))
                    or (metrics['vmHwmBytes'] is not None and not integer(metrics['vmHwmBytes']))
                    or any(not isinstance(metrics[key], str) or len(metrics[key]) > 16384
                           for key in ('cpuStat', 'memoryEvents', 'pidsEvents'))):
                raise ValueError('persistent update isolation metrics')
    if 'appData' in value:
        data = value['appData']
        exact(data, ('writes', 'deletes', 'bytesWritten', 'directorySyncConfirmed', 'directorySyncUnconfirmed', 'lastReceipt'))
        if any(not integer(item) for key, item in data.items() if key != 'lastReceipt'):
            raise ValueError('persistent update app data counters')
        if data['lastReceipt'] is not None:
            last = data['lastReceipt']; exact(last, ('slot', 'operation', 'length', 'directorySynced'))
            if (not integer(last['slot'], 1, 8) or last['operation'] not in ('write', 'delete')
                    or not integer(last['length']) or type(last['directorySynced']) is not bool):
                raise ValueError('persistent update app data receipt')


def expected_private(initial, non_files):
    digest(non_files)
    entries = [] if initial else [{'path': 'state', 'type': 'directory', 'length': 0, 'sha256': None}]
    entries.append({'path': 'note.txt' if initial else 'state/note.txt', 'type': 'file', 'length': 5,
                    'sha256': hashlib.sha256(b'alpha').hexdigest()})
    return {'schemaVersion': 1, 'packageId': PACKAGE_ID, 'namespaceExists': True,
        'files': {'entries': len(entries), 'bytes': 5,
                  'sha256': hashlib.sha256(b'ming-private-update-tree-v1\0' + compact(entries)).hexdigest()},
        'nonFilesSha256': non_files}


def private_content(value, initial, non_files=None):
    exact(value, ('schemaVersion', 'packageId', 'namespaceExists', 'files', 'nonFilesSha256'))
    exact(value['files'], ('entries', 'bytes', 'sha256'))
    if (type(value['schemaVersion']) is not int or type(value['namespaceExists']) is not bool
            or not integer(value['files']['entries']) or not integer(value['files']['bytes'])
            or value != expected_private(initial, value['nonFilesSha256'])
            or (non_files is not None and value['nonFilesSha256'] != non_files)):
        raise ValueError('persistent update private tree mismatch')
    return value


def package_directory(value):
    return f"packages/{value['packageId'].encode().hex()}/{value['version']}-{value['fingerprint']}"


def content_snapshot(value, initial):
    exact(value, (*CONTROL.values(), 'apps', 'files'))
    for key in CONTROL.values(): digest(value[key])
    if type(value['apps']) is not list or len(value['apps']) != 2:
        raise ValueError('persistent update requires exactly two active packages')
    expected = {1: ('dev.ming.text-entry-demo', '0.1.0'), 2: (PACKAGE_ID, '0.1.0' if initial else '0.2.0')}
    found, bindings = {}, {path: value[key] for path, key in CONTROL.items()}
    for app in value['apps']:
        exact(app, APP_FIELDS)
        if type(app['app']) is not int or app['app'] in found:
            raise ValueError('persistent update duplicate/noninteger app')
        found[app['app']] = (app['packageId'], app['version'])
        for key in ('fingerprint', 'manifestSha256', 'wasmSha256'): digest(app[key])
        if found[app['app']] != expected.get(app['app']):
            raise ValueError('persistent update active package/version mismatch')
        directory = package_directory(app)
        for kind, filename in (('manifest', 'app.json'), ('wasm', 'main.wasm')):
            if app[kind + 'Path'] != directory + '/' + filename:
                raise ValueError('persistent update package path mismatch')
            bindings[app[kind + 'Path']] = app[kind + 'Sha256']
    if found != expected or [app['packageId'] for app in value['apps']] != sorted(app['packageId'] for app in value['apps']):
        raise ValueError('persistent update active ordering mismatch')
    if type(value['files']) is not list or not 1 <= len(value['files']) <= 256:
        raise ValueError('persistent update file bound')
    files, pairs = {}, {}
    for item in value['files']:
        exact(item, ('path', 'sha256')); digest(item['sha256'])
        path = item['path']
        if not isinstance(path, str) or len(path) > 768 or path in files:
            raise ValueError('persistent update duplicate/unsafe file')
        if path not in CONTROL:
            match = re.fullmatch(r'packages/([0-9a-f]+)/([0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{64})/(app\.json|main\.wasm)', path)
            if not match or len(match[1]) % 2 or len(match[1]) > 512 or len(match[2]) > 160:
                raise ValueError('persistent update unknown/pending content file')
            pairs.setdefault(path.rsplit('/', 1)[0], set()).add(match[3])
        files[path] = item['sha256']
    if list(files) != sorted(files) or any(names != {'app.json', 'main.wasm'} for names in pairs.values()):
        raise ValueError('persistent update unsorted/incomplete history')
    if any(files.get(path) != value for path, value in bindings.items()):
        raise ValueError('persistent update content hash binding mismatch')
    if len(files) != (8 if initial else 10):
        raise ValueError('persistent update unexpected historical content')
    return value


def validate_snapshot(value, boot_id, boot_number, phase, session_id=None, desktop_pid=None):
    exact(value, ('envelope', 'content', 'privateContent')); env = value['envelope']
    exact(env, ('schemaVersion', 'bootId', 'bootNumber', 'role', 'marker', 'previousMarker', 'diskUuid',
        'registryRoot', 'catalogPath', 'dataRoot', 'sessionId', 'desktopPid', 'phase', 'status', 'actions'))
    if (type(boot_number) is not int or boot_number not in (1, 2) or type(env['schemaVersion']) is not int
            or env['schemaVersion'] != 1 or type(env['bootNumber']) is not int or env['bootNumber'] != boot_number
            or env['bootId'] != boot_id or env['phase'] != phase or phase not in ('pre-desktop', 'post-desktop')
            or env['role'] != ('initialize-first' if boot_number == 1 else 'open-existing')
            or env['registryRoot'] != PERSISTENT_UPDATE_ROOT or env['catalogPath'] != PERSISTENT_UPDATE_CATALOG
            or env['dataRoot'] != '/data/mingos' or not isinstance(env['diskUuid'], str)
            or re.fullmatch('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', env['diskUuid']) is None):
        raise ValueError('persistent update snapshot identity/role/paths')
    exact(env['marker'], ('bootId', 'counter'))
    if env['marker'] != {'bootId': boot_id, 'counter': boot_number} or type(env['marker']['counter']) is not int:
        raise ValueError('persistent update marker generation')
    if boot_number == 1:
        if env['previousMarker'] is not None: raise ValueError('persistent update first marker is not fresh')
    else:
        exact(env['previousMarker'], ('bootId', 'counter'))
        previous = env['previousMarker']
        if (type(previous['counter']) is not int or previous['counter'] != 1 or not isinstance(previous['bootId'], str)
                or not previous['bootId'] or previous['bootId'] == boot_id):
            raise ValueError('persistent update previous marker chain')
    if phase == 'pre-desktop':
        if env['sessionId'] is not None or env['desktopPid'] is not None:
            raise ValueError('persistent update pre snapshot has desktop identity')
    elif env['sessionId'] != session_id or env['desktopPid'] != desktop_pid or not integer(env['desktopPid'], 2) or not session_id:
        raise ValueError('persistent update post snapshot lacks exact desktop identity')
    first = boot_number == 1 and phase == 'pre-desktop'
    if env['status'] != ('initialized-first' if first else 'opened-existing'):
        raise ValueError('persistent update snapshot status')
    exact(env['actions'], ACTION_COUNTS)
    if any(type(env['actions'][key]) is not int or env['actions'][key] != (count if first else 0)
           for key, count in ACTION_COUNTS.items()):
        raise ValueError('persistent update initialization/recovery counts')
    content_snapshot(value['content'], first); private_content(value['privateContent'], first)
    return value


def identity(value, version):
    exact(value, IDENTITY_FIELDS)
    if value['packageId'] != PACKAGE_ID or value['version'] != version:
        raise ValueError('persistent update identity transition')
    for key in ('fingerprint', 'manifestSha256', 'wasmSha256', 'migrationSha256'): digest(value[key])
    if (version == '0.1.0') != (value['migrationSha256'] == hashlib.sha256(b'null').hexdigest()):
        raise ValueError('persistent update migration declaration')


def same_app(package, content, number=2):
    app = next(app for app in content['apps'] if app['app'] == number)
    if any(package[key] != app[key] for key in ('packageId', 'version', 'fingerprint', 'manifestSha256', 'wasmSha256')):
        raise ValueError('persistent update record/content package mismatch')


def validate_update_record(value, committed=False):
    exact(value, RECORD_FIELDS)
    if (type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or value['operation'] != 'update'
            or type(value['app']) is not int or value['app'] != 2 or not isinstance(value['transactionId'], str)
            or re.fullmatch('[A-Za-z0-9_-]{1,96}', value['transactionId']) is None
            or value['candidateContentExistedBefore'] is not False or value['backupExpected'] is not True
            or value['publicPolicySlot'] is not None or value['decision'] != ('commit-decided' if committed else 'prepared')):
        raise ValueError('persistent update record decision/schema')
    identity(value['old'], '0.1.0'); identity(value['new'], '0.2.0')
    for key in ('catalogSha256', 'bootstrapSha256', 'trustSha256', 'siblingRecordsSha256',
                'preservedContentSha256', 'dataRootBindingSha256'): digest(value[key])
    private_content(value['privateBefore'], True)
    if not committed and (value['confirmation'] is not None or value['privateConfirmed'] is not None):
        raise ValueError('persistent update Prepared has future confirmation')
    if committed:
        private_content(value['privateConfirmed'], False, value['privateBefore']['nonFilesSha256'])
    return value


def raw_marker(value, name, limit):
    encoded = value[name + 'Base64']
    if not isinstance(encoded, str) or len(encoded) > ((limit + 2) // 3) * 4:
        raise ValueError('persistent update marker encoding bound')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, UnicodeError) as error:
        raise ValueError('persistent update marker encoding') from error
    if not raw or len(raw) > limit or base64.b64encode(raw).decode() != encoded:
        raise ValueError('persistent update marker raw bound/canonical encoding')
    if hashlib.sha256(raw).hexdigest() != digest(value[name + 'Sha256']):
        raise ValueError('persistent update marker raw hash mismatch')
    return bounded_json(raw)


class PersistentUpdateReceipts:
    def __init__(self, boot_id, desktop_pid, boot_number, *, before):
        if not isinstance(boot_id, str) or not 1 <= len(boot_id) <= 256 or not integer(desktop_pid, 2):
            raise ValueError('persistent update relay identity')
        validate_snapshot(before, boot_id, boot_number, 'pre-desktop')
        self.boot_id, self.desktop_pid, self.boot_number = boot_id, desktop_pid, boot_number
        self.before = copy.deepcopy(before)
        self.session_id = self.state = None
        self.buffer = b''
        self.offset = self.received = self.count = self.native_sequence = self.native_revision = self.launch_revision = 0
        self.press = self.launch = self.app2_down = self.closed_press = None
        self.trial_record = self.trial_observation = self.trial_confirmation = self.confirm = self.startup = self.commit = None
        self.committed_content = self.private_content = None

    @staticmethod
    def worker_identity(value):
        return tuple(value[key] for key in ('app', 'generation', 'window', 'workerPid'))

    def validate_state(self, value):
        exact(value, ('schemaVersion', 'bootId', 'sessionId', 'revision', 'phase', 'root', 'apps', 'error',
                      *(('persistentUpdate',) if 'persistentUpdate' in value else ())))
        validate_state(value, self.boot_id, self.session_id)
        if type(value['schemaVersion']) is not int or value['error'] is not None or value['phase'] == 'failed':
            raise ValueError('persistent update failed state')
        if value['root'] is not None:
            exact(value['root'], ('width', 'height', 'presentedSequence', 'storagePrompt', 'storagePromptPresentedSequence'))
        for app in value['apps']:
            exact(app, ('app', 'generation', 'window', 'workerPid', 'state', 'sourceSequence', 'presentedSourceSequence',
                        'firstReadyMs', 'inputReady', 'minimized', 'outer',
                        *(key for key in ('isolation', 'appData') if key in app)))
            worker_telemetry(app)
            if (app['state'] == 'failed' or not integer(app['window'], 1) or type(app['minimized']) is not bool
                    or (app['firstReadyMs'] is not None and not integer(app['firstReadyMs']))):
                raise ValueError('persistent update failed app')
            if app['outer'] is not None:
                exact(app['outer'], ('x', 'y', 'width', 'height'))
                if any(not integer(app['outer'][key], -(2**31), 2**31-1) for key in app['outer']):
                    raise ValueError('persistent update app geometry')
        update = value.get('persistentUpdate')
        if update is not None:
            if self.boot_number != 1 or update['status'] not in ('trial', 'committed'):
                raise ValueError('persistent update failed/out-of-role transaction state')
            if self.trial_record is not None:
                expected = {'transactionId': self.trial_record['transactionId'], 'app': 2, 'packageId': PACKAGE_ID,
                            'newVersion': '0.2.0', 'status': update['status']}
                if update != expected: raise ValueError('persistent update state transaction mismatch')
        return value

    def history(self, row):
        if type(row) is not dict or not set(HISTORY_FIELDS) <= set(row) or set(row) - set(HISTORY_FIELDS) - set(HISTORY_OPTIONAL):
            raise ValueError('persistent update history fields')
        if (not integer(row['app'], 1, 65535) or not integer(row['generation'], 1) or not integer(row['window'], 1)
                or not integer(row['workerPid']) or type(row['completed']) is not bool or row['error'] is not None
                or row['status'] not in ('starting', 'running', 'ready', 'closing', 'closed', 'minimized')
                or not isinstance(row['title'], str) or len(row['title']) > 16384
                or (row['packageId'] is not None and (not isinstance(row['packageId'], str) or len(row['packageId']) > 256))):
            raise ValueError('persistent update failed history')
        for key in ('firstFrameMs', 'firstReadyMs', 'framesSubmitted', 'requests', 'fuelConsumed'):
            if row.get(key) is not None and not integer(row[key]): raise ValueError('persistent update history number')
        if (not integer(row['lastSourceSequence']) or not integer(row['presentedSourceSequence'])
                or row['presentedSourceSequence'] > row['lastSourceSequence']):
            raise ValueError('persistent update unpresented history')
        if row['presentedViewport'] is not None and (type(row['presentedViewport']) is not list
                or len(row['presentedViewport']) != 2 or any(not integer(n, 1, 4096) for n in row['presentedViewport'])):
            raise ValueError('persistent update history viewport')
        if row['inputCounts'] is not None and (type(row['inputCounts']) is not list
                or len(row['inputCounts']) != 9 or any(not integer(n) for n in row['inputCounts'])):
            raise ValueError('persistent update input counters')
        if row['events'] is not None:
            if type(row['events']) is not list or len(row['events']) > 512:
                raise ValueError('persistent update history event bound')
            for event in row['events']:
                exact(event, ('code', 'value'))
                if any(not integer(event[key], -(2**31), 2**31-1) for key in event):
                    raise ValueError('persistent update history event')
        if 'moduleSource' in row and row['moduleSource'] not in ('system-image', 'jit'):
            raise ValueError('persistent update module source')
        if row['gpuStats'] is not None:
            gpu = row['gpuStats']; exact(gpu, GPU_FIELDS)
            if (any(not integer(gpu[key]) for key in GPU_FIELDS if key != 'surfaceFormat')
                    or (gpu['surfaceFormat'] is not None and (not isinstance(gpu['surfaceFormat'], str) or len(gpu['surfaceFormat']) > 256))):
                raise ValueError('persistent update GPU statistics')
        if 'frameClock' in row:
            frame = row['frameClock']
            exact(frame, ('source', 'closes', 'cancels', 'frames', 'inputs', 'pending', 'reads', 'timers', 'waits'))
            # FrameClockStats.pending counts completed waits returning no event;
            # it is cumulative, not a count of outstanding worker requests.
            if (frame['source'] != 'host-monotonic-opportunity'
                    or any(not integer(v, 0, 2**64 - 1) for k, v in frame.items() if k != 'source')
                    or frame['waits'] != sum(frame[key] for key in ('frames', 'timers', 'inputs', 'closes', 'cancels', 'pending'))):
                raise ValueError('persistent update frame clock')
        worker_telemetry(row)
        return row

    def anchor(self, value):
        exact(value, ('state', 'history', 'traceCount', 'droppedTrace', 'droppedInputs'))
        state = self.validate_state(value['state'])
        if (type(value['history']) is not list or len(value['history']) > 128 or not integer(value['traceCount'], 0, 512)
                or type(value['droppedTrace']) is not int or value['droppedTrace'] != 0
                or type(value['droppedInputs']) is not int or value['droppedInputs'] != 0):
            raise ValueError('persistent update dropped/unbounded anchor')
        seen = set()
        for row in value['history']:
            self.history(row)
            key = row['app'], row['generation']
            if key in seen or (row['app'] == 2 and (row['generation'] != 1 or self.app2_down is None)):
                raise ValueError('persistent update duplicate/scripted app history')
            seen.add(key)
        return state

    def bind_state(self, state):
        self.validate_state(state)
        if self.state is not None and state['revision'] < self.state['revision']:
            raise ValueError('persistent update state revision regressed')
        self.session_id, self.state = state['sessionId'], copy.deepcopy(state)

    def cause(self, value):
        exact(value, CAUSE_FIELDS)
        new = self.trial_record['new'] if self.trial_record else None
        if (value['app'] != 2 or type(value['app']) is not int or value['packageId'] != PACKAGE_ID
                or value['version'] != '0.2.0' or not integer(value['pressSequence'], 1, 64)
                or value['pressSequence'] != 3 or value['releaseSequence'] != 4
                or type(value['releaseSequence']) is not int or not integer(value['presentedSequence'], 1)
                or not integer(value['promptRevision'], 1) or self.press is None
                or self.press['sequence'] != value['pressSequence'] or self.press['action'] != 'other'
                or self.native_sequence != 3):
            raise ValueError('persistent update confirmation lacks matching native press')
        digest(value['fingerprint'])
        if new and any(value[key] != new[key] for key in ('packageId', 'version', 'fingerprint')):
            raise ValueError('persistent update confirmation new identity mismatch')
        before = self.anchor(value['before']); root = before['root'] or {}
        prompt = {'mode': 17, 'packageId': PACKAGE_ID, 'version': '0.2.0', 'hasAllow': True}
        binding = value['binding']
        exact(binding, (*prompt, 'promptRevision', 'presentedSequence', 'fingerprint', 'manifestPath'))
        if (root.get('storagePrompt') != prompt or root.get('storagePromptPresentedSequence') != value['presentedSequence']
                or root.get('presentedSequence') != value['presentedSequence']
                or any(binding[key] != item for key, item in prompt.items())
                or binding['promptRevision'] != value['promptRevision'] or type(binding['promptRevision']) is not int
                or binding['presentedSequence'] != value['presentedSequence'] or type(binding['presentedSequence']) is not int
                or binding['fingerprint'] != value['fingerprint'] or binding['manifestPath'] != SOURCE_MANIFEST
                or any(app['app'] == 2 for app in before['apps'])
                or before['revision'] < self.native_revision):
            raise ValueError('persistent update confirmation prompt/frame/identity mismatch')
        return before

    def validate_trial(self, value):
        exact(value, ('record', 'confirmation', 'anchor', 'trialObservation'))
        if self.boot_number != 1 or self.trial_record is not None or self.app2_down is not None:
            raise ValueError('persistent update duplicate/out-of-role trial')
        record = validate_update_record(value['record'])
        if record['privateBefore'] != self.before['privateContent']:
            raise ValueError('persistent update trial baseline differs from pre')
        same_app(record['old'], self.before['content'])
        if any(record[key] != self.before['content'][key] for key in ('catalogSha256', 'bootstrapSha256', 'trustSha256')):
            raise ValueError('persistent update trial changed static pre content')
        self.trial_record = copy.deepcopy(record)
        try:
            self.cause(value['confirmation'])
            observation = value['trialObservation']
            exact(observation, ('schemaVersion', 'transactionId', 'outerRecordSha256', 'innerJournalSha256',
                'outerRecordBase64', 'innerJournalBase64', 'innerPhase', 'content', 'privateContent'))
            if (type(observation['schemaVersion']) is not int or observation['schemaVersion'] != 1
                    or observation['transactionId'] != record['transactionId'] or observation['innerPhase'] != 'recordPublished'
                    or raw_marker(observation, 'outerRecord', 4096) != record):
                raise ValueError('persistent update trial durable outer mismatch')
            inner = raw_marker(observation, 'innerJournal', 16384)
            expected_inner = {'packageId': PACKAGE_ID, 'oldVersion': record['old']['version'],
                'oldFingerprint': record['old']['fingerprint'], 'newVersion': record['new']['version'],
                'newFingerprint': record['new']['fingerprint'], 'backupName': '.ming-update-backup-' + record['transactionId'],
                'migrationDeclared': True, 'phase': 'recordPublished'}
            if inner != expected_inner or type(inner.get('migrationDeclared')) is not bool:
                raise ValueError('persistent update trial durable inner mismatch')
            content_snapshot(observation['content'], False); same_app(record['new'], observation['content'])
            before_files = {row['path']: row['sha256'] for row in self.before['content']['files']}
            after_files = {row['path']: row['sha256'] for row in observation['content']['files']}
            if (any(after_files.get(path) != sha for path, sha in before_files.items() if path != 'installed-packages-v1')
                    or next(app for app in observation['content']['apps'] if app['app'] == 1)
                       != next(app for app in self.before['content']['apps'] if app['app'] == 1)):
                raise ValueError('persistent update trial changed preserved history/bootstrap')
            private_content(observation['privateContent'], False, self.before['privateContent']['nonFilesSha256'])
            state = self.anchor(value['anchor'])
            if (state.get('persistentUpdate', {}).get('status') != 'trial' or not state['root']
                    or state['root']['storagePrompt'] is not None or any(app['app'] == 2 for app in state['apps'])
                    or state['revision'] <= value['confirmation']['before']['state']['revision']):
                raise ValueError('persistent update trial anchor mismatch')
        except Exception:
            self.trial_record = None
            raise
        self.trial_observation, self.trial_confirmation = copy.deepcopy(observation), copy.deepcopy(value['confirmation'])

    def validate_terminal(self, value, proof=None):
        state = self.anchor(value)
        if self.launch is None or self.launch[0] != 'ready' or self.closed_press is None:
            raise ValueError('persistent update lacks native Ready/Close')
        identity = self.launch[1]
        rows = [row for row in value['history'] if self.worker_identity(row) == identity]
        apps = [app for app in state['apps'] if app['app'] == 2]
        if (len(rows) != 1 or len(apps) != 1 or self.worker_identity(apps[0]) != identity
                or apps[0]['state'] != 'closed' or apps[0]['inputReady'] is not False):
            raise ValueError('persistent update target not closed')
        row = rows[0]
        if (row['status'] != 'closed' or row['completed'] is not True or row['packageId'] != PACKAGE_ID
                or not integer(row['firstReadyMs']) or not integer(row['firstFrameMs'])
                or row['firstFrameMs'] > row['firstReadyMs'] or not integer(row['framesSubmitted'], 1)
                or not integer(row['presentedSourceSequence'], 1) or row.get('frameClock', {}).get('closes') != 1
                or row.get('frameClock', {}).get('cancels') != 0
                or ('isolation' in row and any(row['isolation'][key] is not True for key in ('prepared', 'applied', 'cleaned')))):
            raise ValueError('persistent update incomplete terminal worker lifecycle')
        if proof:
            exact(proof, ('app', 'package', 'generation', 'window', 'workerPid', 'inputReady',
                'presentedSourceSequence', 'completed', 'resultAccepted', 'nativeExitOk', 'reaped'))
            if (self.worker_identity(proof) != identity or proof['package'] != self.trial_record['new']
                    or type(proof['generation']) is not int or type(proof['window']) is not int
                    or type(proof['workerPid']) is not int
                    or proof['presentedSourceSequence'] != row['presentedSourceSequence']
                    or type(proof['presentedSourceSequence']) is not int
                    or any(proof[key] is not True for key in ('inputReady', 'completed', 'resultAccepted', 'nativeExitOk', 'reaped'))):
                raise ValueError('persistent update lifecycle proof mismatch')
        return state

    def native_input(self, value):
        exact(value, ('schemaVersion', 'sequence', 'pressSequence', 'edge', 'x', 'y', 'action', 'targetApp', 'before', 'after', 'trace'))
        n = value['sequence']; down = value['edge'] == 'down'
        if (type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or not integer(n, 1, 64)
                or n != self.native_sequence + 1 or value['edge'] not in ('down', 'up')
                or not integer(value['pressSequence'], 1, 64)
                or value['action'] not in ('launch', 'other') or type(value['trace']) is not list or len(value['trace']) > 512
                or any(not integer(value[k], -(2**31), 2**31-1) for k in ('x', 'y'))
                or (down and (self.press is not None or value['pressSequence'] != n))
                or (not down and (self.press is None or value['pressSequence'] != self.press['sequence']))
                or (value['action'] == 'other' and value['targetApp'] is not None)
                or (value['action'] == 'launch' and (not down or type(value['targetApp']) is not int or value['targetApp'] != 2))):
            raise ValueError('persistent update native edge/sequence/schema')
        launch_down = 5 if self.boot_number == 1 else 1
        if value['action'] == 'launch':
            if n != launch_down or self.app2_down is not None or (self.boot_number == 1 and self.confirm is None):
                raise ValueError('persistent update scripted/out-of-role native launch')
            self.app2_down = n
        before, after = self.anchor(value['before']), self.anchor(value['after'])
        if (before['revision'] < max(self.native_revision, self.launch_revision) or after['revision'] <= before['revision']
                or value['after']['traceCount'] - value['before']['traceCount'] != len(value['trace'])):
            raise ValueError('persistent update native revision/trace order')
        if value['action'] == 'launch' and any(app['app'] == 2 for app in before['apps']):
            raise ValueError('persistent update app2 active before native Dock')
        for trace in value['trace']:
            exact(trace, ('atMs', 'app', 'generation', 'window', 'kind', 'detail'))
            if (any(not integer(trace[key]) for key in ('atMs', 'app', 'generation', 'window'))
                    or not isinstance(trace['kind'], str) or not 1 <= len(trace['kind']) <= 64
                    or not isinstance(trace['detail'], str) or len(trace['detail']) > 262144):
                raise ValueError('persistent update invalid trace')
            if self.boot_number == 2 and trace['kind'] in ('updated', 'installed', 'startup-confirmed', 'persistent-update-commit'):
                raise ValueError('persistent update boot2 mutation trace')
            if trace['kind'] == 'close-request' and trace['app'] == 2:
                if (not down or n != launch_down + 2 or not self.launch or self.launch[0] != 'ready'
                        or self.closed_press is not None or tuple(trace[k] for k in ('app', 'generation', 'window')) != self.launch[1][:3]):
                    raise ValueError('persistent update Close cause mismatch')
                self.closed_press = n
        if not down:
            if (value['x'], value['y']) != (self.press['x'], self.press['y']):
                raise ValueError('persistent update release coordinates changed')
            if n == 4 and self.boot_number == 1:
                if self.confirm is None or value['before'] != self.confirm['before']:
                    raise ValueError('persistent update confirmation release mismatch')
                confirmed = self.confirm['after']
                if (not before['revision'] < confirmed['state']['revision'] <= after['revision']
                        or value['after']['history'] != confirmed['history']
                        or value['after']['traceCount'] != confirmed['traceCount']
                        or any(after[k] != confirmed['state'][k] for k in ('apps', 'phase', 'error', 'persistentUpdate'))
                        or after['root']['storagePrompt'] is not None):
                    raise ValueError('persistent update full confirmation release changed result')
        if self.boot_number == 1 and n >= 9 and self.commit is None:
            raise ValueError('persistent update POWER before durable commit')
        self.native_sequence, self.native_revision = n, after['revision']
        self.press = value if down else None

    def validate_record(self, record):
        kind, value = record['kind'], record['receipt']
        if kind == 'native-input': return self.native_input(value)
        if kind == 'native-launch':
            exact(value, ('schemaVersion', 'downSequence', 'phase', 'app', 'generation', 'window', 'workerPid', 'anchor'))
            identity = self.worker_identity(value)
            if (type(value['schemaVersion']) is not int or value['schemaVersion'] != 1 or value['downSequence'] != self.app2_down
                    or not integer(value['downSequence'], 1, 64) or type(value['app']) is not int or value['app'] != 2
                    or type(value['generation']) is not int or value['generation'] != 1 or not integer(value['window'], 1)
                    or not integer(value['workerPid'], 2) or value['workerPid'] == self.desktop_pid
                    or value['phase'] not in ('worker-started', 'ready')):
                raise ValueError('persistent update native launch identity')
            state = self.anchor(value['anchor'])
            if state['revision'] < self.native_revision or state['revision'] <= self.launch_revision:
                raise ValueError('persistent update native launch revision')
            if ((value['phase'] == 'worker-started' and self.launch is not None)
                    or (value['phase'] == 'ready' and self.launch != ('worker-started', identity))):
                raise ValueError('persistent update native launch phase')
            for rows in (state['apps'], value['anchor']['history']):
                matches = [row for row in rows if row['app'] == 2]
                if len(matches) != 1 or self.worker_identity(matches[0]) != identity:
                    raise ValueError('persistent update native launch anchor identity')
            if value['phase'] == 'ready' and not state_ready(state, [2]):
                raise ValueError('persistent update launch not input Ready/presented')
            self.launch, self.launch_revision = (value['phase'], identity), state['revision']
        elif kind == 'trial-published':
            self.validate_trial(value)
        elif kind == 'update-confirm':
            exact(value, (*CAUSE_FIELDS, 'allow', 'after'))
            if self.boot_number != 1 or self.trial_record is None or self.confirm is not None or value['allow'] is not True:
                raise ValueError('persistent update missing/duplicate/native confirmation')
            self.cause({key: value[key] for key in CAUSE_FIELDS})
            if {key: value[key] for key in CAUSE_FIELDS} != self.trial_confirmation:
                raise ValueError('persistent update trial/confirmation differ')
            after = self.anchor(value['after'])
            if (after.get('persistentUpdate', {}).get('status') != 'trial' or not after['root']
                    or after['root']['storagePrompt'] is not None or after['revision'] <= value['before']['state']['revision']):
                raise ValueError('persistent update confirmation anchor')
            self.confirm = copy.deepcopy(value)
        elif kind == 'startup-confirmed':
            exact(value, ('transactionId', 'confirmation', 'downSequence', 'proof', 'anchor'))
            if (self.boot_number != 1 or self.trial_record is None or self.confirm is None or self.startup is not None
                    or value['transactionId'] != self.trial_record['transactionId']
                    or value['confirmation'] != self.trial_confirmation or value['downSequence'] != self.app2_down):
                raise ValueError('persistent update startup transaction/cause mismatch')
            state = self.validate_terminal(value['anchor'], value['proof'])
            if state.get('persistentUpdate', {}).get('status') != 'committed':
                raise ValueError('persistent update startup not committed')
            self.startup = copy.deepcopy(value)
        elif kind == 'persistent-update-commit':
            exact(value, ('record', 'confirmation', 'downSequence', 'content'))
            if (self.boot_number != 1 or self.startup is None or self.commit is not None
                    or value['confirmation'] != self.trial_confirmation or value['downSequence'] != self.app2_down):
                raise ValueError('persistent update commit lifecycle order')
            committed = validate_update_record(value['record'], True)
            expected = dict(self.trial_record, decision='commit-decided', confirmation=self.startup['proof'],
                            privateConfirmed=self.trial_observation['privateContent'])
            if committed != expected or value['content'] != self.trial_observation['content']:
                raise ValueError('persistent update commit changed transaction/private/content')
            content_snapshot(value['content'], False)
            self.commit = copy.deepcopy(value)
            self.committed_content, self.private_content = copy.deepcopy(value['content']), copy.deepcopy(committed['privateConfirmed'])
        else:
            raise ValueError('unexpected/failed persistent update receipt kind')

    def feed(self, data):
        if not isinstance(data, bytes): raise ValueError('persistent update byte stream required')
        self.received += len(data)
        if self.received > 8 * 1024 * 1024: raise ValueError('persistent update raw log bound')
        self.buffer += data
        rows = []
        while b'\n' in self.buffer:
            raw, self.buffer = self.buffer.split(b'\n', 1)
            check_line_size(raw)
            start = self.offset; self.offset += len(raw) + 1
            if raw.startswith(OLD_PREFIXES): raise ValueError('old receipt prefix in persistent update stream')
            if not raw.startswith(PERSISTENT_UPDATE_PREFIX): continue
            record = bounded_json(raw[len(PERSISTENT_UPDATE_PREFIX):])
            exact(record, ('kind', 'bootId', 'sessionId', 'desktopPid', 'receipt'))
            if (not isinstance(record['kind'], str) or not 1 <= len(record['kind']) <= 64 or record['bootId'] != self.boot_id
                    or type(record['desktopPid']) is not int or record['desktopPid'] != self.desktop_pid
                    or not isinstance(record['sessionId'], str) or not 1 <= len(record['sessionId']) <= 256
                    or (self.session_id is not None and record['sessionId'] != self.session_id) or type(record['receipt']) is not dict):
                raise ValueError('persistent update raw identity/schema mismatch')
            self.session_id = record['sessionId']; self.count += 1
            if self.count > 128: raise ValueError('persistent update raw receipt count bound')
            self.validate_record(record)
            rows.append({'logStartOffset': start, 'logEndOffset': self.offset,
                         'rawBase64': base64.b64encode(raw + b'\n').decode(), 'record': record})
        check_line_size(self.buffer)
        return rows

    def finish(self):
        for prefix in (PERSISTENT_UPDATE_PREFIX, *OLD_PREFIXES):
            if self.buffer and (self.buffer.startswith(prefix) or prefix.startswith(self.buffer)):
                raise ValueError('truncated persistent update raw receipt')

    def complete(self, boot_number):
        self.finish()
        expected_edges = 10 if self.boot_number == 1 else 4
        if (type(boot_number) is not int or boot_number != self.boot_number or self.press is not None
                or self.native_sequence != expected_edges or self.state is None or self.state['phase'] != 'stopped'
                or self.state['error'] is not None or not self.launch or self.launch[0] != 'ready' or self.closed_press is None):
            raise ValueError('incomplete persistent update native/terminal stream')
        if self.boot_number == 1 and (self.trial_record is None or self.confirm is None or self.startup is None or self.commit is None
                or self.state.get('persistentUpdate', {}).get('status') != 'committed'):
            raise ValueError('missing persistent update confirmation/lifecycle/commit')
        apps = [app for app in self.state['apps'] if app['app'] == 2]
        if (len(apps) != 1 or self.worker_identity(apps[0]) != self.launch[1]
                or apps[0]['state'] != 'closed' or apps[0]['inputReady'] is not False):
            raise ValueError('persistent update stopped state lost terminal worker identity')
