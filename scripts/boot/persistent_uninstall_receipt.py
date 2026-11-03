"""Strict receipts for the independent persistent-uninstall qualification."""
import base64
import re

from bootlib import PERSISTENT_INSTALL_CATALOG, PERSISTENT_INSTALL_ROOT, strict_json
from infra import integer, state_ready, validate_state
from wayland_receipt import PERSISTENT_INSTALL_PREFIX, check_line_size

PERSISTENT_UNINSTALL_PREFIX = b'MING_PERSISTENT_UNINSTALL '
PACKAGE_ID = 'dev.ming.install-demo'
PACKAGE_VERSION = '1.0.0'
PRIVATE_CONTENT = {'packageId': PACKAGE_ID, 'files': [{'path': 'note.txt', 'size': 5,
    'sha256': '8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8'}]}


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError('persistent uninstall receipt fields mismatch')


def digest(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('persistent uninstall hash mismatch')


def private_content(value):
    exact(value, ('packageId', 'files'))
    if not isinstance(value['files'], list) or len(value['files']) != 1:
        raise ValueError('persistent uninstall requires unique nonempty private sentinel')
    exact(value['files'][0], ('path', 'size', 'sha256'))
    if type(value['files'][0]['size']) is not int or value != PRIVATE_CONTENT:
        raise ValueError('persistent uninstall private sentinel mismatch')
    return value


def removed_identity(value):
    exact(value, ('packageId', 'version', 'fingerprint'))
    if value['packageId'] != PACKAGE_ID or value['version'] != PACKAGE_VERSION:
        raise ValueError('persistent uninstall removed identity mismatch')
    digest(value['fingerprint'])
    return value


def package_directory(identity):
    return 'packages/' + identity['packageId'].encode().hex() + '/' + identity['version'] + '-' + identity['fingerprint']


def content_snapshot(value, installed=False, removed=None):
    exact(value, ('catalogSha256', 'registrySha256', 'trustSha256', 'bootstrapSha256', 'apps', 'files'))
    control = {'catalog.json': 'catalogSha256', 'installed-packages-v1': 'registrySha256',
               'trusted-publishers-v1': 'trustSha256', 'desktop-install-state-v1.json': 'bootstrapSha256'}
    for key in control.values(): digest(value[key])
    expected = {1: ('dev.ming.text-entry-demo', '0.1.0')}
    if installed: expected[2] = (PACKAGE_ID, PACKAGE_VERSION)
    if not isinstance(value['apps'], list) or len(value['apps']) != len(expected):
        raise ValueError('persistent uninstall active app count mismatch')
    identities = {}
    bindings = {path: value[key] for path, key in control.items()}
    for app in value['apps']:
        exact(app, ('app', 'packageId', 'version', 'fingerprint', 'manifestPath', 'manifestSha256', 'wasmPath', 'wasmSha256'))
        if type(app['app']) is not int or app['app'] in identities:
            raise ValueError('persistent uninstall duplicate app')
        identities[app['app']] = (app['packageId'], app['version'])
        for key in ('fingerprint', 'manifestSha256', 'wasmSha256'): digest(app[key])
        if not isinstance(app['packageId'], str) or not isinstance(app['version'], str):
            raise ValueError('persistent uninstall package identity type')
        directory = package_directory(app)
        for name, filename in (('manifest', 'app.json'), ('wasm', 'main.wasm')):
            if app[name + 'Path'] != directory + '/' + filename:
                raise ValueError('persistent uninstall noncanonical package path')
            bindings[app[name + 'Path']] = app[name + 'Sha256']
    if identities != expected or [a['packageId'] for a in value['apps']] != sorted(a['packageId'] for a in value['apps']):
        raise ValueError('persistent uninstall active identity/order mismatch')
    if not isinstance(value['files'], list) or not 1 <= len(value['files']) <= 256:
        raise ValueError('persistent uninstall content file bound')
    files, pairs = {}, {}
    for item in value['files']:
        exact(item, ('path', 'sha256')); digest(item['sha256'])
        path = item['path']
        if not isinstance(path, str) or len(path) > 768 or path in files:
            raise ValueError('persistent uninstall duplicate/unsafe content path')
        if path not in control:
            match = re.fullmatch(r'packages/([0-9a-f]+)/([0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{64})/(app\.json|main\.wasm)', path)
            if not match or len(match[1]) % 2 or len(match[1]) > 512 or len(match[2]) > 160:
                raise ValueError('persistent uninstall unknown content file')
            pairs.setdefault(path.rsplit('/', 1)[0], set()).add(match[3])
        files[path] = item['sha256']
    if list(files) != sorted(files) or any(names != {'app.json', 'main.wasm'} for names in pairs.values()):
        raise ValueError('persistent uninstall incomplete/unsorted history')
    if any(files.get(path) != sha for path, sha in bindings.items()):
        raise ValueError('persistent uninstall content hash binding mismatch')
    if removed is not None:
        removed_identity(removed)
        directory = package_directory(removed)
        if directory not in pairs:
            raise ValueError('persistent uninstall removed history is missing')
    return value


CAUSE_FIELDS = ('app', 'packageId', 'version', 'fingerprint', 'releaseSequence',
                'pressSequence', 'presentedSequence', 'promptRevision')
HISTORY_FIELDS = ('app', 'generation', 'window', 'title', 'workerPid', 'status', 'completed',
    'firstFrameMs', 'firstReadyMs', 'framesSubmitted', 'lastSourceSequence', 'presentedSourceSequence',
    'presentedViewport', 'packageId', 'requests', 'inputCounts', 'events', 'gpuStats', 'error')
HISTORY_OPTIONAL = ('moduleSource', 'fuelConsumed', 'frameClock', 'appData', 'isolation')


def bounded_json(raw):
    try:
        value = strict_json(raw)
    except (RecursionError, UnicodeError) as error:
        raise ValueError('invalid persistent uninstall JSON') from error
    work, nodes = [(value, 0)], 0
    while work:
        item, depth = work.pop(); nodes += 1
        if depth > 24 or nodes > 16384:
            raise ValueError('persistent uninstall JSON nesting/item bound')
        if isinstance(item, dict): work.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list): work.extend((child, depth + 1) for child in item)
    return value


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
            raise ValueError('invalid/failed persistent uninstall isolation')
        if isolation['limits'] is not None:
            limits = isolation['limits']; exact(limits, ('memoryBytes', 'pids', 'cpuMax'))
            if (not integer(limits['memoryBytes'], 1) or not integer(limits['pids'], 1, 2**32-1)
                    or not isinstance(limits['cpuMax'], str) or len(limits['cpuMax']) > 128
                    or re.fullmatch(r'(max|[0-9]+) [0-9]+', limits['cpuMax']) is None):
                raise ValueError('invalid persistent uninstall isolation limits')
        if isolation['namespaceIds'] is not None and (not isinstance(isolation['namespaceIds'], list)
                or len(isolation['namespaceIds']) != 4 or any(not integer(n, 1) for n in isolation['namespaceIds'])):
            raise ValueError('invalid persistent uninstall namespace identity')
        if isolation['metrics'] is not None:
            metrics = isolation['metrics']
            exact(metrics, ('memoryCurrent', 'memoryPeak', 'pidsCurrent', 'vmHwmBytes', 'cpuStat', 'memoryEvents', 'pidsEvents'))
            if (any(not integer(metrics[key]) for key in ('memoryCurrent', 'memoryPeak', 'pidsCurrent'))
                    or (metrics['vmHwmBytes'] is not None and not integer(metrics['vmHwmBytes']))
                    or any(not isinstance(metrics[key], str) or len(metrics[key]) > 16384
                           for key in ('cpuStat', 'memoryEvents', 'pidsEvents'))):
                raise ValueError('invalid persistent uninstall isolation metrics')
    if 'appData' in value:
        data = value['appData']
        exact(data, ('writes', 'deletes', 'bytesWritten', 'directorySyncConfirmed', 'directorySyncUnconfirmed', 'lastReceipt'))
        if any(not integer(item) for key, item in data.items() if key != 'lastReceipt'):
            raise ValueError('invalid persistent uninstall app data counters')
        if data['lastReceipt'] is not None:
            last = data['lastReceipt']; exact(last, ('slot', 'operation', 'length', 'directorySynced'))
            if (not integer(last['slot'], 1, 8) or last['operation'] not in ('write', 'delete')
                    or not integer(last['length']) or type(last['directorySynced']) is not bool):
                raise ValueError('invalid persistent uninstall app data receipt')


class PersistentUninstallReceipts:
    """Validate the new qualification stream and retain exact stderr provenance."""
    def __init__(self, boot_id, desktop_pid, boot_number):
        if not isinstance(boot_id, str) or not boot_id or not integer(desktop_pid, 2) or type(boot_number) is not int or boot_number not in (1, 2):
            raise ValueError('invalid persistent uninstall relay identity/role')
        self.boot_id, self.desktop_pid, self.boot_number = boot_id, desktop_pid, boot_number
        self.session_id = self.state = None
        self.buffer = b''
        self.offset = self.count = self.native_sequence = self.native_revision = self.launch_revision = 0
        self.press = self.app2_down = self.launch = self.closed_press = None
        self.installed_content = self.uninstalled_content = self.removed = self.private_content = None
        self.install_commit = self.request = self.confirm = self.uninstall_commit = None
        self.pending = {}

    def validate_state(self, state):
        exact(state, ('schemaVersion', 'bootId', 'sessionId', 'revision', 'phase', 'root', 'apps', 'error'))
        validate_state(state, self.boot_id, self.session_id)
        if type(state['schemaVersion']) is not int or state['error'] is not None or state['phase'] == 'failed':
            raise ValueError('failed/invalid persistent uninstall state')
        root = state['root']
        if root is not None:
            exact(root, ('width', 'height', 'presentedSequence', 'storagePrompt', 'storagePromptPresentedSequence'))
        for app in state['apps']:
            basic = ('app', 'generation', 'window', 'workerPid', 'state', 'sourceSequence',
                     'presentedSourceSequence', 'firstReadyMs', 'inputReady', 'minimized', 'outer')
            exact(app, (*basic, *(key for key in ('isolation', 'appData') if key in app)))
            worker_telemetry(app)
            if (app['state'] == 'failed' or not integer(app['window'], 1)
                    or type(app['inputReady']) is not bool or type(app['minimized']) is not bool
                    or (app['firstReadyMs'] is not None and not integer(app['firstReadyMs']))):
                raise ValueError('failed/invalid persistent uninstall app state')
            if app['outer'] is not None:
                exact(app['outer'], ('x', 'y', 'width', 'height'))
                if any(not integer(app['outer'][key], -(2**31), 2**31-1) for key in ('x', 'y', 'width', 'height')):
                    raise ValueError('invalid persistent uninstall app geometry')
        if self.boot_number == 2 and any(app['app'] == 2 for app in state['apps']):
            raise ValueError('app2 appeared during reopen-only boot')
        return state

    def history(self, row):
        if not isinstance(row, dict) or not set(HISTORY_FIELDS) <= set(row) or set(row) - set(HISTORY_FIELDS) - set(HISTORY_OPTIONAL):
            raise ValueError('persistent uninstall history schema')
        if (not integer(row['app'], 1, 65535) or not integer(row['generation'], 1)
                or not integer(row['window'], 1) or not integer(row['workerPid'], 0)
                or row['status'] not in ('starting', 'running', 'ready', 'closing', 'closed', 'minimized')
                or type(row['completed']) is not bool or row['error'] is not None
                or not isinstance(row['title'], str) or len(row['title']) > 16384
                or (row['packageId'] is not None and (not isinstance(row['packageId'], str) or len(row['packageId']) > 256))):
            raise ValueError('failed/invalid persistent uninstall history')
        for key in ('firstFrameMs', 'firstReadyMs', 'framesSubmitted', 'requests', 'fuelConsumed'):
            if row.get(key) is not None and not integer(row[key]):
                raise ValueError('persistent uninstall history numeric type')
        for key in ('lastSourceSequence', 'presentedSourceSequence'):
            if not integer(row[key]): raise ValueError('persistent uninstall history sequence')
        if row['presentedSourceSequence'] > row['lastSourceSequence']:
            raise ValueError('persistent uninstall unpresented history')
        if row['presentedViewport'] is not None and (not isinstance(row['presentedViewport'], list)
                or len(row['presentedViewport']) != 2 or any(not integer(n, 1, 4096) for n in row['presentedViewport'])):
            raise ValueError('persistent uninstall history viewport')
        if row['inputCounts'] is not None and (not isinstance(row['inputCounts'], list)
                or len(row['inputCounts']) != 9 or any(not integer(n) for n in row['inputCounts'])):
            raise ValueError('persistent uninstall input counters')
        if row['events'] is not None and (not isinstance(row['events'], list) or len(row['events']) > 512):
            raise ValueError('persistent uninstall history event bound')
        if 'frameClock' in row:
            frame = row['frameClock']
            exact(frame, ('source', 'closes', 'cancels', 'frames', 'inputs', 'pending', 'reads', 'timers', 'waits'))
            if frame['source'] != 'host-monotonic-opportunity' or any(not integer(v) for k, v in frame.items() if k != 'source'):
                raise ValueError('persistent uninstall frame clock schema')
        worker_telemetry(row)
        return row

    def anchor(self, value):
        exact(value, ('state', 'history', 'traceCount', 'droppedTrace', 'droppedInputs'))
        state = self.validate_state(value['state'])
        if (not isinstance(value['history'], list) or len(value['history']) > 128
                or not integer(value['traceCount'], 0, 512)
                or type(value['droppedTrace']) is not int or value['droppedTrace'] != 0
                or type(value['droppedInputs']) is not int or value['droppedInputs'] != 0):
            raise ValueError('dropped/unbounded persistent uninstall anchor')
        seen = set()
        for row in value['history']:
            self.history(row)
            identity = row['app'], row['generation']
            if identity in seen or (self.boot_number == 2 and row['app'] == 2):
                raise ValueError('duplicate/out-of-role persistent uninstall history')
            seen.add(identity)
        return state

    def bind_state(self, state):
        self.validate_state(state)
        self.session_id, self.state = state['sessionId'], state

    @staticmethod
    def identity(value):
        return tuple(value[key] for key in ('app', 'generation', 'window', 'workerPid'))

    def closed(self, value):
        state = self.anchor(value)
        if (not self.launch or self.launch[0] != 'ready' or self.closed_press is None
                or not state['root'] or not integer(state['root']['presentedSequence'], 1)):
            raise ValueError('uninstall before native Ready/Close')
        identity = self.launch[1]
        matches = [row for row in value['history'] if self.identity(row) == identity]
        current = [row for row in state['apps'] if row['app'] == 2]
        if (len(matches) != 1 or len(current) != 1 or self.identity(current[0]) != identity
                or current[0]['state'] != 'closed' or current[0]['inputReady'] is not False):
            raise ValueError('uninstall target is not closed/reaped')
        row = matches[0]
        if (row['status'] != 'closed' or row['completed'] is not True or row['error'] is not None
                or row['packageId'] != PACKAGE_ID or not integer(row['firstReadyMs'])
                or row.get('frameClock', {}).get('closes') != 1 or row.get('frameClock', {}).get('cancels') != 0
                or ('isolation' in row and any(row['isolation'][key] is not True for key in ('prepared', 'applied', 'cleaned')))):
            raise ValueError('uninstall target lacks normal terminal result')
        return state

    def cause(self, value):
        exact(value, CAUSE_FIELDS)
        if (type(value['app']) is not int or value['app'] != 2
                or self.installed_content is None or value['packageId'] != PACKAGE_ID or value['version'] != PACKAGE_VERSION
                or any(not integer(value[key], 1, 64) for key in ('pressSequence', 'releaseSequence'))
                or value['releaseSequence'] != value['pressSequence'] + 1
                or not integer(value['presentedSequence'], 1) or not integer(value['promptRevision'], 1)):
            raise ValueError('persistent uninstall cause schema')
        digest(value['fingerprint'])
        app = next(app for app in self.installed_content['apps'] if app['app'] == 2)
        if any(value[key] != app[key] for key in ('packageId', 'version', 'fingerprint')):
            raise ValueError('persistent uninstall cause package changed')
        if (self.press is None or value['pressSequence'] != self.press['sequence']
                or value['releaseSequence'] != self.native_sequence + 1
                or (self.press['before']['state']['root'] or {}).get('presentedSequence') != value['presentedSequence']):
            raise ValueError('persistent uninstall cause lacks matching native press')
        return value

    @staticmethod
    def prompt(state):
        root = state.get('root') or {}
        value = root.get('storagePrompt')
        return value == {'mode': 10, 'packageId': PACKAGE_ID, 'version': PACKAGE_VERSION, 'hasAllow': True}

    def validate_record(self, record):
        kind, value = record['kind'], record['receipt']
        if type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1:
            raise ValueError('persistent uninstall receipt schema version')
        if kind == 'native-input':
            self.native_input(value)
            return
        if self.boot_number == 2: raise ValueError('mutation/launch evidence during reopen-only boot')
        if kind == 'persistent-install-commit':
            exact(value, ('schemaVersion', 'status', 'packageId', 'version', 'fingerprint', 'registryRoot',
                          'catalogPath', 'content', 'persistentBlockConfirmed'))
            identity = {key: value[key] for key in ('packageId', 'version', 'fingerprint')}
            removed_identity(identity)
            if self.install_commit or self.installed_content or self.app2_down or value['status'] != 'committed' or value['persistentBlockConfirmed'] is not False:
                raise ValueError('duplicate/out-of-order persistent install commit')
            self.paths(value)
            content_snapshot(value['content'], installed=True)
            if any(value[key] != value['content']['apps'][0][key] for key in identity):
                raise ValueError('install commit content identity mismatch')
            self.install_commit = value
        elif kind == 'persistent-install-installed':
            exact(value, ('schemaVersion', 'registryRoot', 'catalogPath', 'stateRevision', 'content'))
            self.paths(value)
            if not self.install_commit or self.installed_content or self.app2_down or not integer(value['stateRevision'], 1) or value['content'] != self.install_commit['content']:
                raise ValueError('invalid/out-of-order persistent installed snapshot')
            self.installed_content = content_snapshot(value['content'], installed=True)
        elif kind == 'native-launch':
            exact(value, ('schemaVersion', 'downSequence', 'phase', 'app', 'generation', 'window', 'workerPid', 'anchor'))
            if (value['downSequence'] != self.app2_down or not integer(value['downSequence'], 1, 64)
                    or type(value['app']) is not int or value['app'] != 2 or not integer(value['generation'], 1)
                    or not integer(value['window'], 1) or not integer(value['workerPid'], 2)
                    or value['phase'] not in ('worker-started', 'ready')):
                raise ValueError('persistent native launch identity mismatch')
            state = self.anchor(value['anchor']); identity = self.identity(value)
            if state['revision'] < self.native_revision or state['revision'] <= self.launch_revision:
                raise ValueError('persistent native launch revision order')
            if (value['phase'] == 'worker-started' and self.launch is not None) or (value['phase'] == 'ready' and self.launch != ('worker-started', identity)):
                raise ValueError('persistent native launch phase/identity mismatch')
            for rows in (state['apps'], value['anchor']['history']):
                matches = [row for row in rows if row['app'] == 2 and row['generation'] == value['generation']]
                if len(matches) != 1 or self.identity(matches[0]) != identity:
                    raise ValueError('persistent native launch anchor identity mismatch')
            old = [row['generation'] for row in self.app2_before['history'] if row['app'] == 2]
            if old and value['generation'] <= max(old): raise ValueError('persistent native generation reused')
            if value['phase'] == 'ready' and not state_ready(state, [2]): raise ValueError('persistent native launch not ready')
            self.launch, self.launch_revision = (value['phase'], identity), state['revision']
        elif kind in ('uninstall-request', 'uninstall-confirm'):
            confirm = kind == 'uninstall-confirm'
            exact(value, ('schemaVersion', *CAUSE_FIELDS, 'before', 'after', *(('allow',) if confirm else ())))
            cause = self.cause({key: value[key] for key in CAUSE_FIELDS})
            before, after = self.closed(value['before']), self.anchor(value['after'])
            if (after['revision'] <= before['revision'] or not after['root']
                    or self.press['edge'] != 'down' or self.press['action'] != 'other'):
                raise ValueError('persistent uninstall cause anchor order')
            if confirm:
                if (self.request is None or self.confirm is not None or value['allow'] is not True
                        or cause['promptRevision'] != self.request['promptRevision'] or not self.prompt(before)
                        or before['root']['storagePromptPresentedSequence'] != cause['presentedSequence']
                        or after['root']['storagePrompt'] is not None or any(app['app'] == 2 for app in after['apps'])):
                    raise ValueError('invalid/out-of-order persistent uninstall confirmation')
                if self.uninstall_commit is not None and self.uninstall_commit['cause'] != cause:
                    raise ValueError('persistent uninstall commit/confirm cause mismatch')
                identity = self.launch[1]
                if not any(self.identity(row) == identity and row['completed'] is True and row['status'] == 'closed' for row in value['after']['history']):
                    raise ValueError('uninstall discarded terminal history')
                self.confirm = value
            else:
                if self.request is not None or before['root']['storagePrompt'] is not None or not self.prompt(after) or after['root']['storagePromptPresentedSequence'] is not None:
                    raise ValueError('invalid/out-of-order persistent uninstall request')
                self.request = value
            if cause['releaseSequence'] in self.pending: raise ValueError('duplicate persistent uninstall release cause')
            self.pending[cause['releaseSequence']] = value
        elif kind == 'persistent-uninstall-commit':
            exact(value, ('schemaVersion', 'status', 'removed', 'registryRoot', 'catalogPath', 'content', 'cause', 'privateBefore', 'privateAfter'))
            self.paths(value); cause = self.cause(value['cause'])
            if (not self.request or any(pending is self.request for pending in self.pending.values())
                    or self.uninstall_commit or value['status'] != 'committed'
                    or cause['promptRevision'] != self.request['promptRevision']
                    or (self.confirm is not None and cause != {key: self.confirm[key] for key in CAUSE_FIELDS})):
                raise ValueError('invalid/out-of-order persistent uninstall commit')
            removed_identity(value['removed'])
            if any(value['removed'][key] != cause[key] for key in value['removed']): raise ValueError('persistent uninstall removed/cause mismatch')
            private_content(value['privateBefore']); private_content(value['privateAfter'])
            self.uninstall_commit = value
            content = content_snapshot(value['content'], removed=value['removed'])
            retained_files = lambda snapshot: [row for row in snapshot['files']
                                               if row['path'] not in ('catalog.json', 'installed-packages-v1')]
            if (content['apps'] != [app for app in self.installed_content['apps'] if app['app'] == 1]
                    or content['trustSha256'] != self.installed_content['trustSha256']
                    or content['bootstrapSha256'] != self.installed_content['bootstrapSha256']
                    or retained_files(content) != retained_files(self.installed_content)):
                raise ValueError('persistent uninstall changed app1/trust/historical package content')
            self.uninstalled_content = content
            self.removed, self.private_content = value['removed'], value['privateAfter']
        else:
            raise ValueError('unexpected/failed persistent uninstall receipt kind')

    @staticmethod
    def paths(value):
        if value['registryRoot'] != PERSISTENT_INSTALL_ROOT or value['catalogPath'] != PERSISTENT_INSTALL_CATALOG:
            raise ValueError('persistent uninstall receipt path mismatch')

    def native_input(self, value):
        exact(value, ('schemaVersion', 'sequence', 'pressSequence', 'edge', 'x', 'y', 'action', 'targetApp', 'before', 'after', 'trace'))
        if (not integer(value['sequence'], 1, 64) or value['sequence'] != self.native_sequence + 1
                or not integer(value['pressSequence'], 1, 64) or value['edge'] not in ('down', 'up')
                or any(item is not None and not integer(item, -(2**31), 2**31-1) for item in (value['x'], value['y']))
                or ((value['x'] is None) != (value['y'] is None)) or value['action'] not in ('launch', 'other')
                or not isinstance(value['trace'], list) or len(value['trace']) > 512):
            raise ValueError('persistent native input schema/sequence mismatch')
        down = value['edge'] == 'down'
        if ((down and (self.press is not None or value['pressSequence'] != value['sequence']))
                or (not down and (self.press is None or value['pressSequence'] != self.press['sequence']))
                or (value['action'] == 'launch' and (not down or not integer(value['targetApp'], 1, 65535)))
                or (value['action'] == 'other' and value['targetApp'] is not None)):
            raise ValueError('persistent native press/action mismatch')
        before, after = self.anchor(value['before']), self.anchor(value['after'])
        if before['revision'] < max(self.native_revision, self.launch_revision) or after['revision'] <= before['revision']:
            raise ValueError('persistent native state revision order')
        if value['after']['traceCount'] - value['before']['traceCount'] != len(value['trace']):
            raise ValueError('persistent native input trace loss')
        for trace in value['trace']:
            exact(trace, ('atMs', 'app', 'generation', 'window', 'kind', 'detail'))
            if (any(not integer(trace[key]) for key in ('atMs', 'app', 'generation', 'window'))
                    or not isinstance(trace['kind'], str) or not 1 <= len(trace['kind']) <= 64
                    or not isinstance(trace['detail'], str) or len(trace['detail']) > 262144):
                raise ValueError('persistent native trace schema')
            if self.boot_number == 2 and (trace['app'] == 2 or trace['kind'] in ('installed', 'uninstalled', 'persistent-install-commit', 'persistent-uninstall-commit')):
                raise ValueError('boot2 contains app2/mutation trace')
            if trace['kind'] == 'close-request' and trace['app'] == 2:
                if (not down or not self.launch or self.launch[0] != 'ready'
                        or tuple(trace[key] for key in ('app', 'generation', 'window')) != self.launch[1][:3]
                        or self.closed_press is not None): raise ValueError('invalid native Close cause')
                self.closed_press = value['sequence']
        if value['action'] == 'launch' and value['targetApp'] == 2:
            if self.boot_number != 1 or self.installed_content is None or self.app2_down is not None:
                raise ValueError('out-of-role native app2 launch')
            if any(row['app'] == 2 and (row.get('state', row.get('status')) != 'closed' or row.get('completed', True) is not True) for row in before['apps'] + value['before']['history']):
                raise ValueError('app2 active before current native launch')
            self.app2_down, self.app2_before = value['sequence'], value['before']
        if not down:
            if (value['x'], value['y']) != (self.press['x'], self.press['y']): raise ValueError('persistent native release coordinates changed')
            pending = self.pending.pop(value['sequence'], None)
            if pending is not None:
                if (value['before'] != pending['before'] or pending['presentedSequence'] != before['root']['presentedSequence']
                        or not before['revision'] < pending['after']['state']['revision'] <= after['revision']):
                    raise ValueError('persistent uninstall release does not enclose cause')
                cause_after = pending['after']
                if (value['after']['history'] != cause_after['history']
                        or value['after']['traceCount'] != cause_after['traceCount']
                        or any(after[key] != cause_after['state'][key] for key in ('apps', 'phase', 'error'))
                        or any(after['root'][key] != cause_after['state']['root'][key]
                               for key in ('width', 'height', 'storagePrompt'))):
                    raise ValueError('persistent uninstall release changed cause result')
                if pending is self.confirm:
                    if self.uninstall_commit is None or after['root']['storagePrompt'] is not None or any(app['app'] == 2 for app in after['apps']):
                        raise ValueError('persistent uninstall confirmation lacks committed result')
                elif not self.prompt(after): raise ValueError('persistent uninstall request prompt lost')
        self.native_sequence, self.native_revision = value['sequence'], after['revision']
        self.press = value if down else None

    def feed(self, data):
        self.buffer += data
        rows = []
        while b'\n' in self.buffer:
            raw, self.buffer = self.buffer.split(b'\n', 1)
            check_line_size(raw)
            start = self.offset; self.offset += len(raw) + 1
            if raw.startswith(PERSISTENT_INSTALL_PREFIX): raise ValueError('old receipt prefix in persistent uninstall stream')
            if not raw.startswith(PERSISTENT_UNINSTALL_PREFIX): continue
            record = bounded_json(raw[len(PERSISTENT_UNINSTALL_PREFIX):])
            exact(record, ('kind', 'bootId', 'sessionId', 'desktopPid', 'receipt'))
            if (not isinstance(record['kind'], str) or not 1 <= len(record['kind']) <= 64
                    or record['bootId'] != self.boot_id or type(record['desktopPid']) is not int or record['desktopPid'] != self.desktop_pid
                    or not isinstance(record['sessionId'], str) or not 1 <= len(record['sessionId']) <= 256
                    or (self.session_id is not None and record['sessionId'] != self.session_id) or not isinstance(record['receipt'], dict)):
                raise ValueError('persistent uninstall raw receipt identity/schema mismatch')
            self.session_id = record['sessionId']; self.count += 1
            if self.count > 128: raise ValueError('persistent uninstall receipt count bound')
            self.validate_record(record)
            rows.append({'logStartOffset': start, 'logEndOffset': self.offset,
                         'rawBase64': base64.b64encode(raw + b'\n').decode('ascii'), 'record': record})
        check_line_size(self.buffer)
        return rows

    def finish(self):
        for prefix in (PERSISTENT_UNINSTALL_PREFIX, PERSISTENT_INSTALL_PREFIX):
            if self.buffer and (self.buffer.startswith(prefix) or prefix.startswith(self.buffer)):
                raise ValueError('truncated persistent uninstall receipt')

    def complete(self, boot_number):
        self.finish()
        if type(boot_number) is not int or boot_number != self.boot_number or self.press is not None or self.pending:
            raise ValueError('persistent uninstall incomplete/out-of-role native stream')
        if self.state is None or self.state['phase'] != 'stopped' or self.state['error'] is not None:
            raise ValueError('persistent uninstall lacks normal terminal state')
        if self.boot_number == 1 and (any(app['app'] == 2 for app in self.state['apps'])
                or self.install_commit is None or self.installed_content is None
                or self.app2_down is None or not self.launch or self.launch[0] != 'ready'
                or self.request is None or self.confirm is None or self.uninstall_commit is None):
            raise ValueError('missing persistent uninstall lifecycle evidence')
