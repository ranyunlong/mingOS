#!/usr/bin/python3
"""Trusted guest PID1 supervisor; SDK-only apps run in separate host workers."""
import base64
import json
import hashlib
import os
import re
import stat
import subprocess
import time
import traceback
from pathlib import Path, PurePosixPath
from bootlib import PERSISTENT_INSTALL_CATALOG, PERSISTENT_INSTALL_ROOT, PERSISTENT_UPDATE_ROOT, PERSISTENT_UPDATE_CATALOG, persistent_boot_role, persistent_session_contract, persistent_update_boot_role, persistent_update_session_contract, strict_json
from infra import STATE_PATH, integer, state_ready, validate_fault_injection, validate_state
from guest_platform import Platform
from bounded_log import BoundedLog, LogLimitExceeded
from wayland_receipt import PERSISTENT_INSTALL_PREFIX, PointerReceipt, check_line_size
from worker_resources import process_table

LOG = Path("/run/evidence")
channel = None
log_offsets = {}
STARTUP_LOG_NAMES = ('weston.log', 'weston-stderr.log', 'seatd.log')
STARTUP_LOG_MAX_BYTES = 4096

class PersistentDesktopReceipts:
    """Relay bounded producer records without losing their raw stderr positions."""
    def __init__(self, boot_id, desktop_pid):
        self.boot_id, self.desktop_pid = boot_id, desktop_pid
        self.session_id = None
        self.buffer = b''
        self.offset = self.count = 0
        self.native_sequence = self.native_revision = self.launch_revision = self.commits = 0
        self.press = self.app2_down = self.installed_content = None
        self.launches = {}

    @staticmethod
    def fields(value, fields):
        if not isinstance(value, dict) or set(value) != set(fields):
            raise ValueError('persistent desktop receipt fields mismatch')

    def anchor(self, anchor):
        self.fields(anchor, ('state', 'history', 'traceCount', 'droppedTrace', 'droppedInputs'))
        validate_state(anchor['state'], self.boot_id, self.session_id)
        if (not isinstance(anchor['history'], list) or len(anchor['history']) > 128
                or any(not isinstance(row, dict) or not integer(row.get('app'), 1, 65535) for row in anchor['history'])
                or not integer(anchor['traceCount'], 0, 512)
                or type(anchor['droppedTrace']) is not int or anchor['droppedTrace'] != 0
                or type(anchor['droppedInputs']) is not int or anchor['droppedInputs'] != 0):
            raise ValueError('invalid persistent native anchor')
        return anchor['state']

    def validate_record(self, record):
        kind, receipt = record['kind'], record['receipt']
        if kind == 'persistent-install-commit':
            if self.commits or self.installed_content is not None or self.app2_down is not None:
                raise ValueError('duplicate/out-of-order persistent commit')
            self.commits += 1
        elif kind == 'persistent-install-installed':
            self.fields(receipt, ('schemaVersion', 'registryRoot', 'catalogPath', 'stateRevision', 'content'))
            if (type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1
                    or receipt['registryRoot'] != PERSISTENT_INSTALL_ROOT or receipt['catalogPath'] != PERSISTENT_INSTALL_CATALOG
                    or not integer(receipt['stateRevision'], 1) or self.commits != 1
                    or self.installed_content is not None or self.app2_down is not None):
                raise ValueError('invalid/out-of-order installed snapshot')
            self.installed_content = persistent_content(receipt['content'], False)
        elif kind == 'native-input':
            self.fields(receipt, ('schemaVersion', 'sequence', 'pressSequence', 'edge', 'x', 'y', 'action',
                                  'targetApp', 'before', 'after', 'trace'))
            if (type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1
                    or not integer(receipt['sequence'], 1, 64) or receipt['sequence'] != self.native_sequence + 1
                    or not integer(receipt['pressSequence'], 1, 64) or receipt['edge'] not in ('down', 'up')
                    or any(value is not None and not integer(value, -(2**31), 2**31-1) for value in (receipt['x'], receipt['y']))
                    or ((receipt['x'] is None) != (receipt['y'] is None))
                    or receipt['action'] not in ('launch', 'other')
                    or not isinstance(receipt['trace'], list) or len(receipt['trace']) > 512
                    or any(not isinstance(row, dict) for row in receipt['trace'])):
                raise ValueError('invalid native input sequence/schema')
            down = receipt['edge'] == 'down'
            if ((down and (self.press is not None or receipt['pressSequence'] != receipt['sequence']))
                    or (not down and (self.press is None or receipt['pressSequence'] != self.press))
                    or (receipt['action'] == 'launch' and (not down or not integer(receipt['targetApp'], 1, 65535)))
                    or (receipt['action'] == 'other' and receipt['targetApp'] is not None)):
                raise ValueError('native input press/action mismatch')
            before, after = self.anchor(receipt['before']), self.anchor(receipt['after'])
            if before['revision'] < max(self.native_revision, self.launch_revision) or after['revision'] <= before['revision']:
                raise ValueError('native state revision order')
            if self.app2_down is None and any(row.get('app') == 2 for row in before['apps'] + receipt['before']['history']):
                raise ValueError('app2 existed before native Dock launch')
            if receipt['action'] == 'launch' and receipt['targetApp'] == 2:
                if self.app2_down is not None: raise ValueError('duplicate app2 Dock launch')
                self.app2_down = receipt['sequence']
            self.native_sequence, self.native_revision = receipt['sequence'], after['revision']
            self.press = receipt['sequence'] if down else None
        elif kind == 'native-launch':
            self.fields(receipt, ('schemaVersion', 'downSequence', 'phase', 'app', 'generation', 'window', 'workerPid', 'anchor'))
            if (type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1
                    or not integer(receipt['downSequence'], 1, 64) or receipt['downSequence'] != self.app2_down
                    or type(receipt['app']) is not int or receipt['app'] != 2
                    or type(receipt['generation']) is not int or receipt['generation'] != 1
                    or not integer(receipt['window'], 1) or not integer(receipt['workerPid'], 2)
                    or receipt['phase'] not in ('worker-started', 'ready')):
                raise ValueError('native launch identity/cause mismatch')
            state = self.anchor(receipt['anchor'])
            if state['revision'] < self.native_revision or state['revision'] <= self.launch_revision:
                raise ValueError('native launch anchor revision order')
            identity = tuple(receipt[key] for key in ('app', 'generation', 'window', 'workerPid'))
            previous = self.launches.get(receipt['downSequence'])
            if ((receipt['phase'] == 'worker-started' and previous is not None)
                    or (receipt['phase'] == 'ready' and previous != ('worker-started', identity))):
                raise ValueError('native launch phase order/identity mismatch')
            for rows in (state['apps'], receipt['anchor']['history']):
                matches = [row for row in rows if row.get('app') == 2]
                if len(matches) != 1 or tuple(matches[0].get(key) for key in ('app', 'generation', 'window', 'workerPid')) != identity:
                    raise ValueError('native launch anchor identity mismatch')
            if receipt['phase'] == 'ready' and not state_ready(state, [2]):
                raise ValueError('native launch ready anchor is not ready')
            self.launches[receipt['downSequence']] = (receipt['phase'], identity)
            self.launch_revision = state['revision']
        else:
            raise ValueError('unexpected/failed persistent desktop receipt')

    def bind_state(self, state):
        validate_state(state, self.boot_id, self.session_id)
        self.session_id = state['sessionId']

    def feed(self, data):
        self.buffer += data
        rows = []
        while b'\n' in self.buffer:
            raw, self.buffer = self.buffer.split(b'\n', 1)
            persistent = check_line_size(raw)
            start = self.offset
            self.offset += len(raw) + 1
            if not persistent: continue
            record = parse_persistent_json(raw[len(PERSISTENT_INSTALL_PREFIX):])
            if (not isinstance(record, dict) or set(record) != {'kind', 'bootId', 'sessionId', 'desktopPid', 'receipt'}
                    or not isinstance(record['kind'], str) or not 1 <= len(record['kind']) <= 64
                    or record['bootId'] != self.boot_id or type(record['desktopPid']) is not int
                    or record['desktopPid'] != self.desktop_pid
                    or not isinstance(record['sessionId'], str) or not record['sessionId']
                    or (self.session_id is not None and record['sessionId'] != self.session_id)
                    or not isinstance(record['receipt'], dict)):
                raise ValueError('persistent desktop receipt schema/identity mismatch')
            self.session_id = record['sessionId']
            self.count += 1
            if self.count > 128: raise ValueError('persistent desktop receipt count exceeds bound')
            self.validate_record(record)
            rows.append({'logStartOffset': start, 'logEndOffset': self.offset, 'record': record})
        check_line_size(self.buffer)
        return rows

    def finish(self):
        if self.buffer and (self.buffer.startswith(PERSISTENT_INSTALL_PREFIX) or PERSISTENT_INSTALL_PREFIX.startswith(self.buffer)):
            raise ValueError('truncated persistent desktop receipt')

    def complete(self, boot_number):
        self.finish()
        if (type(boot_number) is not int or boot_number not in (1, 2) or self.commits != int(boot_number == 1)
                or (self.installed_content is not None) != (boot_number == 1)
                or self.press is not None or self.app2_down is None
                or self.launches.get(self.app2_down, (None,))[0] != 'ready'):
            raise ValueError('missing/out-of-role persistent native/install evidence')

def persistent_context(session, platform, command_line, *, uninstall=False, update=False):
    contract, boot_role, prefix = persistent_session_contract, persistent_boot_role, 'persistent_install'
    if uninstall:
        from bootlib import persistent_uninstall_boot_role, persistent_uninstall_session_contract
        contract, boot_role, prefix = persistent_uninstall_session_contract, persistent_uninstall_boot_role, 'persistent_uninstall'
    if update:
        contract, boot_role, prefix = persistent_update_session_contract, persistent_update_boot_role, 'persistent_update'
    contract(session)
    params = {}
    for part in command_line.split():
        key, _, value = part.partition("=")
        if update and key.startswith(('mingos.persistent_install_', 'mingos.persistent_uninstall_')):
            raise ValueError('foreign persistent boot parameter in update profile')
        if uninstall and key in ('mingos.persistent_install_boot', 'mingos.persistent_install_role'):
            raise ValueError('old persistent install boot parameter in uninstall profile')
        if key in (f"mingos.{prefix}_boot", f"mingos.{prefix}_role"):
            if key in params: raise ValueError("duplicate persistent boot parameter")
            params[key] = value
    value = params.get(f"mingos.{prefix}_boot")
    if value not in ("1", "2"): raise ValueError("missing/invalid persistent boot number")
    number = int(value)
    role = boot_role(number)
    if params.get(f"mingos.{prefix}_role") != role:
        raise ValueError("persistent boot role mismatch")
    marker, previous = platform.marker, platform.previous
    if (platform.mounted is not True or not isinstance(platform.disk_uuid, str) or not platform.disk_uuid
            or not isinstance(platform.boot_id, str) or not platform.boot_id
            or not isinstance(marker, dict) or set(marker) != {"bootId", "counter"}
            or marker["bootId"] != platform.boot_id or type(marker["counter"]) is not int or marker["counter"] != number):
        raise ValueError("persistent boot requires verified mounted disk and matching marker")
    if number == 1:
        if previous is not None: raise ValueError("persistent first boot requires fresh disk marker1")
    elif (not isinstance(previous, dict) or set(previous) != {"bootId", "counter"}
          or type(previous["counter"]) is not int or previous["counter"] != 1
          or not isinstance(previous["bootId"], str) or not previous["bootId"] or previous["bootId"] == platform.boot_id):
        raise ValueError("persistent second boot requires distinct previous marker1")
    context = {"schemaVersion": 1, "bootId": platform.boot_id, "bootNumber": number, "role": role,
            "marker": marker, "previousMarker": previous, "diskUuid": platform.disk_uuid,
            "registryRoot": PERSISTENT_INSTALL_ROOT, "catalogPath": PERSISTENT_INSTALL_CATALOG}
    if update:
        context.update(registryRoot=PERSISTENT_UPDATE_ROOT, catalogPath=PERSISTENT_UPDATE_CATALOG, dataRoot='/data/mingos')
    return context

def persistent_update_context(session, platform, command_line):
    return persistent_context(session, platform, command_line, update=True)

def persistent_update_helper_argv(session, context, phase):
    persistent_update_session_contract(session)
    if phase not in ('pre-desktop', 'post-desktop') or context.get('role') != persistent_update_boot_role(context.get('bootNumber')):
        raise ValueError('invalid persistent update snapshot phase/role')
    fixture = session['persistentUpdateFixture']
    argv = [fixture['helper'], 'open-existing', fixture['installRoot'], fixture['dataRoot']]
    if context['bootNumber'] == 1 and phase == 'pre-desktop':
        argv[1] = 'initialize-first'
        argv += [fixture['bootstrapWasm'], fixture['oldWasm'], fixture['newWasm'], fixture['sourceManifest'],
                 '/opt/mingos/catalog.json', session['catalogSha256'], 'seed']
    return argv + [fixture['receipt']]

def persistent_update_expected_private(initial, non_files_sha256):
    entries = [] if initial else [{'path': 'state', 'type': 'directory', 'length': 0, 'sha256': None}]
    entries.append({'path': 'note.txt' if initial else 'state/note.txt', 'type': 'file', 'length': 5,
                    'sha256': hashlib.sha256(b'alpha').hexdigest()})
    raw = json.dumps(entries, ensure_ascii=False, separators=(',', ':')).encode()
    return {'schemaVersion': 1, 'packageId': 'dev.ming.persistent-update-demo', 'namespaceExists': True,
            'files': {'entries': len(entries), 'bytes': 5,
                      'sha256': hashlib.sha256(b'ming-private-update-tree-v1\0' + raw).hexdigest()},
            'nonFilesSha256': non_files_sha256}

def persistent_update_private(value, initial):
    if (not isinstance(value, dict) or not isinstance(value.get('nonFilesSha256'), str)
        or re.fullmatch('[0-9a-f]{64}', value['nonFilesSha256']) is None
        or value != persistent_update_expected_private(initial, value['nonFilesSha256'])
        or type(value['schemaVersion']) is not int or type(value['namespaceExists']) is not bool
        or any(type(value['files'][key]) is not int for key in ('entries', 'bytes'))):
        raise ValueError('persistent update private tree mismatch')
    return value

def persistent_uninstall_context(session, platform, command_line):
    return persistent_context(session, platform, command_line, uninstall=True)

def persistent_uninstall_helper_argv(session, context, phase):
    from bootlib import persistent_uninstall_boot_role, persistent_uninstall_session_contract
    persistent_uninstall_session_contract(session)
    if phase not in ('pre-desktop', 'post-desktop') or context.get('role') != persistent_uninstall_boot_role(context.get('bootNumber')):
        raise ValueError('invalid persistent uninstall snapshot role/phase')
    fixture = session['persistentUninstallFixture']
    if context['bootNumber'] == 1 and phase == 'pre-desktop':
        return [fixture['helper'], 'initialize-first', fixture['installRoot'], fixture['initialWasm'],
                fixture['sourceWasm'], fixture['sourceManifest'], '/opt/mingos/catalog.json', session['catalogSha256'], fixture['receipt']]
    return [fixture['helper'], 'open-uninstalled', fixture['installRoot'], fixture['sourceWasm'], '/data/mingos', fixture['receipt']]

def persistent_helper_argv(session, context, phase):
    persistent_session_contract(session)
    if phase not in ("pre-desktop", "post-desktop"): raise ValueError("invalid persistent snapshot phase")
    if context.get("role") != persistent_boot_role(context.get("bootNumber")):
        raise ValueError("persistent snapshot role mismatch")
    fixture = session["persistentInstallFixture"]
    initialize = context["bootNumber"] == 1 and phase == "pre-desktop"
    argv = [fixture["helper"], "initialize-first" if initialize else "open-existing", fixture["installRoot"]]
    if initialize:
        argv += [fixture["initialWasm"], fixture["sourceWasm"], fixture["sourceManifest"],
                 "/opt/mingos/catalog.json", session["catalogSha256"]]
    return argv + [fixture["receipt"]]

def parse_persistent_json(raw):
    if len(raw) > 262144: raise ValueError("persistent receipt exceeds bound")
    return strict_json(raw)

def read_persistent_receipt(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 262144):
            raise ValueError("unsafe persistent receipt metadata/size")
        with os.fdopen(fd, "rb", closefd=False) as source: raw = source.read(262145)
        return parse_persistent_json(raw)
    finally:
        os.close(fd)

def persistent_content(content, initialize, update_version=None):
    def exact(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys): raise ValueError("persistent receipt schema mismatch")
    def digest(value):
        if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{64}", value): raise ValueError("invalid persistent hash")
    def relative(value):
        if (not isinstance(value, str) or not value or len(value) > 768 or "\\" in value or ":" in value or "\0" in value
                or PurePosixPath(value).is_absolute() or any(part in ("", ".", "..") for part in value.split("/"))):
            raise ValueError("unsafe persistent relative content path")
    exact(content, ("catalogSha256", "registrySha256", "trustSha256", "bootstrapSha256", "apps", "files"))
    for field in ("catalogSha256", "registrySha256", "trustSha256", "bootstrapSha256"): digest(content[field])
    apps, files = content["apps"], content["files"]
    if (not isinstance(apps, list) or len(apps) != (2 if update_version else 1 if initialize else 2)
            or not isinstance(files, list) or not 1 <= len(files) <= 256):
        raise ValueError("persistent content app/file list mismatch")
    identities = {}
    for app in apps:
        exact(app, ("app", "packageId", "version", "fingerprint", "manifestPath", "manifestSha256", "wasmPath", "wasmSha256"))
        if type(app["app"]) is not int or app["app"] in identities: raise ValueError("invalid/duplicate persistent app ID")
        identities[app["app"]] = (app["packageId"], app["version"])
        for field in ("fingerprint", "manifestSha256", "wasmSha256"): digest(app[field])
        for field in ("manifestPath", "wasmPath"): relative(app[field])
    expected = {1: ("dev.ming.text-entry-demo", "0.1.0")}
    if update_version: expected[2] = ("dev.ming.persistent-update-demo", update_version)
    elif not initialize: expected[2] = ("dev.ming.install-demo", "1.0.0")
    if identities != expected or [app["packageId"] for app in apps] != sorted(app["packageId"] for app in apps):
        raise ValueError("persistent app identity/order mismatch")
    for app in apps:
        directory = "packages/" + app["packageId"].encode().hex() + "/" + app["version"] + "-" + app["fingerprint"]
        if app["manifestPath"] != directory + "/app.json" or app["wasmPath"] != directory + "/main.wasm":
            raise ValueError("noncanonical persistent active package path")
    file_hashes = {}
    package_files = {}
    for item in files:
        exact(item, ("path", "sha256")); relative(item["path"]); digest(item["sha256"])
        if item["path"] in file_hashes or item["path"] in (
                "desktop-install-pending-v1.json", "update-journal-v1", ".ming-installed-packages.lock", ".ming-publisher-keyring.lock"):
            raise ValueError("duplicate or forbidden persistent content file")
        if item["path"] not in ("catalog.json", "installed-packages-v1", "trusted-publishers-v1", "desktop-install-state-v1.json"):
            match = re.fullmatch(r"packages/([0-9a-f]+)/([0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{64})/(app\.json|main\.wasm)", item["path"])
            if not match or len(match[1]) % 2 or len(match[1]) > 512 or len(match[2]) > 160:
                raise ValueError("unknown persistent content file")
            package_files.setdefault(item["path"].rsplit("/", 1)[0], set()).add(match[3])
        file_hashes[item["path"]] = item["sha256"]
    if any(names != {"app.json", "main.wasm"} for names in package_files.values()):
        raise ValueError("incomplete persistent package content")
    if list(file_hashes) != sorted(file_hashes): raise ValueError("persistent file list must be sorted")
    bindings = {"catalog.json": content["catalogSha256"], "installed-packages-v1": content["registrySha256"],
                "trusted-publishers-v1": content["trustSha256"], "desktop-install-state-v1.json": content["bootstrapSha256"]}
    for app in apps:
        for field in ("manifest", "wasm"):
            path, expected_hash = app[field + "Path"], app[field + "Sha256"]
            if path in bindings and bindings[path] != expected_hash: raise ValueError("persistent file binding conflict")
            bindings[path] = expected_hash
    if any(file_hashes.get(path) != expected_hash for path, expected_hash in bindings.items()):
        raise ValueError("persistent file hashes disagree with content identities")
    return content

def persistent_update_snapshot(receipt, context, phase, session_id, desktop_pid=None):
    if (not isinstance(receipt, dict) or set(receipt) != {'schemaVersion', 'status', 'actions', 'content', 'privateContent'}
        or type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1
        or phase not in ('pre-desktop', 'post-desktop')
        or context.get('role') != persistent_update_boot_role(context.get('bootNumber'))):
        raise ValueError('invalid persistent update helper receipt role/schema')
    initial = context['bootNumber'] == 1 and phase == 'pre-desktop'
    if ((phase == 'pre-desktop' and (session_id is not None or desktop_pid is not None))
        or (phase == 'post-desktop' and (not isinstance(session_id, str) or not session_id or not integer(desktop_pid, 2)))
        or receipt['status'] != ('initialized-first' if initial else 'opened-existing')):
        raise ValueError('invalid persistent update helper session/status')
    expected = {'initializedRoots': 1, 'packagesInstalled': 2, 'publisherProvisioned': 1, 'bootstrapPublished': 1,
                'catalogWrites': 2, 'rootPublished': 1, 'sourceBundlesCreated': 1, 'privateSeedWrites': 1,
                'recoveryInvocations': 0, 'uiUpdatesCommitted': 0}
    if (not isinstance(receipt['actions'], dict) or set(receipt['actions']) != set(expected)
        or any(type(value) is not int or value != (expected[key] if initial else 0)
               for key, value in receipt['actions'].items())):
        raise ValueError('persistent update helper physical action counts mismatch')
    content = persistent_content(receipt['content'], False, '0.1.0' if initial else '0.2.0')
    private = persistent_update_private(receipt['privateContent'], initial)
    if initial and len(content['files']) != 8:
        raise ValueError('persistent update initial content has unexpected history')
    return {'envelope': dict(context, sessionId=session_id, desktopPid=desktop_pid, phase=phase,
                            status=receipt['status'], actions=receipt['actions']),
            'content': content, 'privateContent': private}

def persistent_snapshot(receipt, context, phase, session_id):
    def exact(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys): raise ValueError("persistent receipt schema mismatch")
    exact(receipt, ("schemaVersion", "status", "actions", "content"))
    if type(receipt["schemaVersion"]) is not int or receipt["schemaVersion"] != 1:
        raise ValueError("unsupported persistent helper receipt version")
    if phase not in ("pre-desktop", "post-desktop") or context.get("role") != persistent_boot_role(context.get("bootNumber")):
        raise ValueError("invalid persistent snapshot phase/role")
    if (phase == "pre-desktop" and session_id is not None) or (phase == "post-desktop" and (not isinstance(session_id, str) or not session_id)):
        raise ValueError("persistent snapshot session mismatch")
    initialize = context["bootNumber"] == 1 and phase == "pre-desktop"
    if receipt["status"] != ("initialized-first" if initialize else "opened-existing"):
        raise ValueError("persistent helper status mismatch")
    actions = receipt["actions"]
    exact(actions, ("initializedRoots", "packagesInstalled", "trustStoresProvisioned", "catalogsCreated", "sourcesCreated"))
    if any(type(value) is not int or value != int(initialize) for value in actions.values()):
        raise ValueError("persistent helper writes do not match role")
    content = persistent_content(receipt['content'], initialize)
    envelope = dict(context, sessionId=session_id, phase=phase, status=receipt["status"], actions=actions)
    return {"envelope": envelope, "content": content}

def persistent_uninstall_snapshot(receipt, context, phase, session_id, desktop_pid=None):
    from bootlib import persistent_uninstall_boot_role
    from persistent_uninstall_receipt import exact, private_content, content_snapshot, removed_identity
    exact(receipt, ('schemaVersion', 'status', 'actions', 'content', 'removed', 'privateContent'))
    if type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1:
        raise ValueError('unsupported persistent uninstall helper version')
    if phase not in ('pre-desktop', 'post-desktop') or context.get('role') != persistent_uninstall_boot_role(context.get('bootNumber')):
        raise ValueError('invalid persistent uninstall snapshot role/phase')
    if ((phase == 'pre-desktop' and (session_id is not None or desktop_pid is not None))
            or (phase == 'post-desktop' and (not isinstance(session_id, str) or not session_id or not integer(desktop_pid, 2)))):
        raise ValueError('persistent uninstall snapshot desktop/session mismatch')
    initial = context['bootNumber'] == 1 and phase == 'pre-desktop'
    if receipt['status'] != ('initialized-first' if initial else 'opened-uninstalled'):
        raise ValueError('persistent uninstall helper status mismatch')
    exact(receipt['actions'], ('initializedRoots', 'packagesInstalled', 'trustStoresProvisioned', 'catalogsCreated', 'sourcesCreated'))
    if any(type(value) is not int or value != int(initial) for value in receipt['actions'].values()):
        raise ValueError('persistent uninstall helper initialization count mismatch')
    if initial:
        if receipt['removed'] is not None or receipt['privateContent'] is not None:
            raise ValueError('initial uninstall snapshot cannot contain removed/private evidence')
    else:
        removed_identity(receipt['removed']); private_content(receipt['privateContent'])
    content = content_snapshot(receipt['content'], removed=receipt['removed'])
    if initial and len(content['files']) != 6:
        raise ValueError('initial uninstall snapshot contains unexpected history')
    return {'envelope': dict(context, sessionId=session_id, desktopPid=desktop_pid, phase=phase,
                             status=receipt['status'], actions=receipt['actions']),
            'content': content, 'removed': receipt['removed'], 'privateContent': receipt['privateContent']}

def prepare_persistent_snapshot(session, context, phase, session_id=None, *, uninstall=False, update=False, desktop_pid=None):
    argv_of = persistent_update_helper_argv if update else persistent_uninstall_helper_argv if uninstall else persistent_helper_argv
    argv = argv_of(session, context, phase)
    event = 'persistent-update-state' if update else 'persistent-uninstall-state' if uninstall else 'persistent-install-state'
    snapshot_of = ((lambda value: persistent_update_snapshot(value, context, phase, session_id, desktop_pid)) if update else
                   (lambda value: persistent_uninstall_snapshot(value, context, phase, session_id, desktop_pid)) if uninstall else
                   (lambda value: persistent_snapshot(value, context, phase, session_id)))
    helper = Path(argv[0])
    expected = session.get("persistentUpdateFixtureSha256" if update else "persistentUninstallFixtureSha256" if uninstall else "persistentInstallFixtureSha256", {}).get("helper")
    if expected != hashlib.sha256(helper.read_bytes()).hexdigest(): raise ValueError("persistent helper hash drift")
    path = Path(argv[-1])
    path.unlink(missing_ok=True)
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    snapshot = None
    try:
        if result.returncode == 0:
            snapshot = snapshot_of(read_persistent_receipt(path))
            stdout_snapshot = snapshot_of(parse_persistent_json(result.stdout))
            if stdout_snapshot != snapshot: raise ValueError("persistent helper stdout/file receipt mismatch")
    except Exception:
        emit(event, argv=argv, code=result.returncode, stdout=result.stdout,
             stderr=result.stderr, receipt=None)
        raise
    emit(event, argv=argv, code=result.returncode, stdout=result.stdout,
         stderr=result.stderr, receipt=snapshot)
    if result.returncode:
        raise RuntimeError('persistent update helper failed' if update else
                           'persistent uninstall helper failed' if uninstall else 'persistent install helper failed')
    return snapshot

def verify_persistent_uninstall_after(before, after, parser, boot_number):
    fields = ('content', 'removed', 'privateContent')
    expected = ({'content': parser.uninstalled_content, 'removed': parser.removed,
                 'privateContent': parser.private_content} if boot_number == 1 else before)
    if any(after[key] != expected[key] for key in fields):
        raise RuntimeError('persistent uninstall post-desktop content/identity/private evidence mismatch')

def verify_persistent_update_after(before, after, parser, boot_number):
    expected = ({'content': parser.committed_content, 'privateContent': parser.private_content}
                if boot_number == 1 else before)
    if (any(after[key] != expected[key] for key in ('content', 'privateContent'))
        or after['privateContent']['nonFilesSha256'] != before['privateContent']['nonFilesSha256']):
        raise RuntimeError('persistent update post-desktop content/private evidence mismatch')

def desktop_argv(session, context=None):
    if session.get('profile') == 'persistent-update':
        persistent_update_session_contract(session)
        if not context or context.get('role') != persistent_update_boot_role(context.get('bootNumber')):
            raise ValueError('persistent update desktop requires verified context')
        argv = ['/opt/mingos/ming-app-host', 'desktop', PERSISTENT_UPDATE_CATALOG, '--renderer', 'cpu'] + session['extraArgs']
        argv += ['--persistent-install-state', PERSISTENT_UPDATE_ROOT]
        if context['bootNumber'] == 1: argv += ['--install-source', session['installSource']]
        return argv
    if session.get('profile') == 'persistent-uninstall':
        from bootlib import persistent_uninstall_boot_role, persistent_uninstall_session_contract
        persistent_uninstall_session_contract(session)
        if not context or context.get('role') != persistent_uninstall_boot_role(context.get('bootNumber')):
            raise ValueError('persistent uninstall desktop requires verified context')
        argv = ['/opt/mingos/ming-app-host', 'desktop', PERSISTENT_INSTALL_CATALOG, '--renderer', 'cpu'] + session['extraArgs']
        argv += ['--persistent-install-state', PERSISTENT_INSTALL_ROOT]
        if context['bootNumber'] == 1: argv += ['--install-source', session['installSource']]
        return argv
    if session.get("profile") == "persistent-install":
        persistent_session_contract(session)
        if not context or context.get("role") != persistent_boot_role(context.get("bootNumber")):
            raise ValueError("persistent desktop requires verified boot context")
        argv = ["/opt/mingos/ming-app-host", "desktop", PERSISTENT_INSTALL_CATALOG, "--renderer", "cpu"] + session["extraArgs"]
        argv += ["--persistent-install-state", PERSISTENT_INSTALL_ROOT]
        if context["bootNumber"] == 1: argv += ["--install-source", session["installSource"]]
        return argv
    argv = ["/opt/mingos/ming-app-host", "desktop", "/opt/mingos/catalog.json", "--renderer", "cpu"] + session["extraArgs"]
    if session.get("installRoot") is not None: argv += ["--install-root", session["installRoot"]]
    return argv

def emit(kind, **fields):
    record = dict(type=kind, uptime=round(time.monotonic(), 3), **fields)
    line = json.dumps(record, ensure_ascii=True) + "\n"
    print("MINGOS_EVIDENCE " + line, end="", flush=True)
    if channel:
        channel.write(line); channel.flush()

def startup_diagnostic_error(error):
    # Exception messages may be arbitrarily large or contain unrelated data.
    return {'error': type(error).__name__[:64],
            'errno': error.errno if type(getattr(error, 'errno', None)) is int else None}


def startup_diagnostics():
    """Read at most one 4KiB tail from each fixed local startup log.

    No glob, EOF loop, child process, or additional transport write is involved.
    FIFO/device/link paths cannot turn this failure path into a blocking stream.
    """
    result = {'maxBytesPerFile': STARTUP_LOG_MAX_BYTES,
              'maxTotalBytes': len(STARTUP_LOG_NAMES) * STARTUP_LOG_MAX_BYTES, 'files': {}}
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        directory = os.open(LOG, flags | os.O_DIRECTORY)
    except OSError as error:
        result['directoryError'] = startup_diagnostic_error(error)
        return result
    try:
        for name in STARTUP_LOG_NAMES:
            descriptor = None
            try:
                descriptor = os.open(name, flags, dir_fd=directory)
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    result['files'][name] = {'status': 'unavailable', 'error': 'single-link-regular-required'}
                    continue
                offset = max(0, info.st_size - STARTUP_LOG_MAX_BYTES)
                count = min(STARTUP_LOG_MAX_BYTES, info.st_size - offset)
                raw = os.pread(descriptor, count, offset)
                result['files'][name] = {'status': 'captured', 'offset': offset,
                    'observedBytes': info.st_size, 'bytes': len(raw), 'complete': len(raw) == count,
                    'truncated': offset > 0, 'encoding': 'base64', 'data': base64.b64encode(raw).decode('ascii')}
            except OSError as error:
                result['files'][name] = {'status': 'unavailable', **startup_diagnostic_error(error)}
            finally:
                if descriptor is not None: os.close(descriptor)
    finally:
        os.close(directory)
    return result


def emit_guest_error(error, traceback_text):
    try:
        diagnostics = startup_diagnostics()
    except Exception as caught:
        diagnostics = {'collectionError': startup_diagnostic_error(caught)}
    # The first fatal record carries the diagnostics itself. The host may kill
    # immediately after that record without racing later log-flush events.
    emit('guest-error', error=error, traceback=traceback_text, startupDiagnostics=diagnostics)


def flush_logs():
    for log in LOG.glob("*.log"):
        offset = log_offsets.get(log.name, 0)
        with log.open("rb") as source:
            source.seek(offset)
            while chunk := source.read(16384):
                emit("log", name=log.name, offset=offset, encoding="base64", data=base64.b64encode(chunk).decode("ascii"))
                offset += len(chunk)
        log_offsets[log.name] = offset

def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    emit("command", argv=argv, code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    if result.returncode: raise RuntimeError("guest command failed: " + repr(argv))

def main():
    global channel
    LOG.mkdir(parents=True, exist_ok=True)
    desktop = weston = seat = observer = fault_injector = platform = None
    streams = []
    error = None
    desktop_code = None
    data_synced = False
    state = None
    state_bytes = None
    stderr_source = stderr_capture = None
    stderr_limited = False
    diagnostic = {}
    persistent = persistent_before = None
    persistent_uninstall = persistent_update = False
    fault_config = fault_receipt = None
    fault_receipt_path = LOG / "fault-injection.json"
    ready_capture_deadline = None
    pointer_parser = persistent_parser = None
    def relay_state():
        nonlocal state, state_bytes
        path = Path(STATE_PATH)
        if not path.exists(): return
        if path.is_symlink() or path.stat().st_size > 262144: raise RuntimeError("unsafe state file")
        raw = path.read_bytes()
        if raw == state_bytes: return
        try:
            value = json.loads(raw)
            validate_state(value, platform.boot_id, state["sessionId"] if state else None, state["revision"] if state else None)
            if state and value["revision"] == state["revision"] and value != state: raise ValueError("same revision changed state")
            if persistent_parser: persistent_parser.bind_state(value)
        except (ValueError, TypeError, KeyError) as error:
            emit("desktop-state-rejected", error=repr(error), rawBase64=base64.b64encode(raw).decode("ascii"))
            raise
        state_bytes, state = raw, value
        emit("desktop-state", state=value, desktopPid=desktop.pid if desktop else None, diagnosticStderrOffset=stderr_capture.captured if stderr_capture else None)
    def drain_stderr():
        nonlocal stderr_limited
        if stderr_source is None or stderr_limited: return
        while True:
            try: data = os.read(stderr_source.fileno(), 65536)
            except BlockingIOError: return
            if not data: return
            try:
                stderr_capture.append(data)
                if persistent_parser:
                    for row in persistent_parser.feed(data):
                        record = row['record']
                        emit("desktop-persistent-receipt", bootId=platform.boot_id, sessionId=record['sessionId'],
                             desktopPid=desktop.pid, **row)
                if pointer_parser:
                    for receipt in pointer_parser.feed(data):
                        emit("desktop-pointer-receipt", bootId=platform.boot_id, sessionId=state["sessionId"] if state else None, desktopPid=desktop.pid, receipt=receipt)
            except LogLimitExceeded as error:
                stderr_limited = True
                emit("diagnostic-log-limit", limitBytes=error.limit, capturedBytes=error.captured, observedBytesAtLeast=error.observed)
                raise
    def stop(proc, label):
        if proc and proc.poll() is None:
            proc.terminate()
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=5)
                emit("forced-process-stop", name=label, pid=proc.pid)
                raise RuntimeError("forced backend stop: " + label)
    try:
        subprocess.run(["/usr/lib/systemd/systemd-udevd", "--daemon"], check=True, timeout=10)
        command(["udevadm", "trigger", "--action=add"])
        command(["udevadm", "settle", "--timeout=10"])
        candidates = list(Path("/dev/virtio-ports").glob("org.mingos.evidence"))
        if not candidates:
            for entry in Path("/sys/class/virtio-ports").glob("vport*"):
                if (entry / "name").read_text().strip() == "org.mingos.evidence": candidates.append(Path("/dev") / entry.name)
        if not candidates: raise RuntimeError("virtio evidence channel missing")
        channel = candidates[0].open("w", buffering=1)
        platform = Platform(emit, LOG)
        emit("guest-start", kernel=list(os.uname()), osRelease=Path("/etc/os-release").read_text(), pid=1,
             bootId=platform.boot_id, commandLine=Path("/proc/cmdline").read_text())
        # Keep full dmesg in streamed logs without serializing one enormous serial JSON line.
        with (LOG / "kernel-dmesg.log").open("wb") as log:
            subprocess.run(["dmesg"], stdout=log, check=True, timeout=15)
        platform.prepare()
        platform.strict_before()
        diag_path = Path("/etc/mingos/input-diagnostics.json")
        diagnostic = json.loads(diag_path.read_text()) if diag_path.exists() else {}
        fault_config_path = Path("/etc/mingos/fault-injection.json")
        if fault_config_path.exists():
            from fault_injector import completed_receipt, read_json
            fault_config = validate_fault_injection(read_json(fault_config_path, 16384))
        pointer_parser = PointerReceipt() if diagnostic.get("pointerHandshake") else None
        flush_logs()
        emit("drm-devices", cards=[str(p) for p in Path("/dev/dri").glob("*")])
        emit("input-devices", nodes=[str(p) for p in Path("/dev/input").glob("event*")],
             modules=Path("/proc/modules").read_text(), entropyAvail=Path("/proc/sys/kernel/random/entropy_avail").read_text().strip())
        if not list(Path("/dev/dri").glob("card*")): raise RuntimeError("virtio-gpu DRM card missing")
        runtime = Path("/run/mingos"); runtime.mkdir(mode=0o700)
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY="wayland-mingos",
                   LIBSEAT_BACKEND="seatd", SEATD_SOCK="/run/seatd.sock", XDG_SESSION_TYPE="wayland")
        def launch(argv, name, desktop_debug=False):
            nonlocal stderr_source, stderr_capture
            output = (LOG / (name + ".log")).open("wb"); streams.append(output)
            extra = diagnostic.get("desktopEnv", {}) if desktop_debug else {}
            if extra and extra != {"WAYLAND_DEBUG": "client"}: raise RuntimeError("unexpected diagnostic desktop env")
            if extra:
                env_file = Path("/etc/mingos/desktop-diagnostic-env.json")
                env_sha = hashlib.sha256(env_file.read_bytes()).hexdigest()
                if env_sha != diagnostic["desktopEnvSha256"]: raise RuntimeError("diagnostic env hash mismatch")
                emit("desktop-diagnostic-env", env=extra, sha256=env_sha, profile=diagnostic["profile"], stderrLimitBytes=diagnostic["stderrLimitBytes"])
            proc = subprocess.Popen(argv, env=dict(env, **extra), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.PIPE if extra else output)
            if extra:
                stderr_source = proc.stderr; os.set_blocking(stderr_source.fileno(), False)
                target = (LOG / "desktop-wayland.stderr.log").open("wb"); streams.append(target)
                stderr_capture = BoundedLog(target, diagnostic["stderrLimitBytes"])
            emit("process-start", name=name, pid=proc.pid, argv=argv)
            return proc
        seat = launch(["seatd", "-u", "root", "-g", "root"], "seatd")
        deadline = time.monotonic() + 15
        while not Path(env["SEATD_SOCK"]).exists():
            if seat.poll() is not None or time.monotonic() >= deadline: raise RuntimeError("seatd socket not ready")
            time.sleep(0.1)
        if Path("/etc/mingos/input-diagnostics.json").exists():
            observer = launch(["python3", "-B", "/etc/mingos/evdev_observer.py", "--boot-id", platform.boot_id, "--duration-seconds", "900"], "evdev-observer-stderr")
        weston = launch(["weston", "--backend=drm", "--renderer=pixman", "--shell=kiosk-shell.so",
                         "--config=/etc/mingos/weston.ini", "--socket=wayland-mingos",
                         "--log=/run/evidence/weston.log", "--idle-time=0"], "weston-stderr")
        deadline = time.monotonic() + 30
        while not (runtime / "wayland-mingos").exists():
            if weston.poll() is not None or time.monotonic() >= deadline: raise RuntimeError("Weston DRM socket not ready")
            time.sleep(0.1)
        session = json.loads(Path("/etc/mingos/session.json").read_text())
        if session.get("profile") in ("persistent-install", "persistent-uninstall", "persistent-update"):
            session = strict_json(Path("/etc/mingos/session.json").read_text())
            persistent_uninstall = session['profile'] == 'persistent-uninstall'
            persistent_update = session['profile'] == 'persistent-update'
            if persistent_update:
                from persistent_update_receipt import PersistentUpdateReceipts
            persistent = persistent_context(session, platform, Path("/proc/cmdline").read_text(),
                                            uninstall=persistent_uninstall, update=persistent_update)
            persistent_before = prepare_persistent_snapshot(session, persistent, "pre-desktop",
                uninstall=persistent_uninstall, update=persistent_update)
        fixture = session.get("updateFixture")
        if fixture is not None:
            expected = {
                "helper": "/opt/mingos/prepare-update-fixture",
                "installRoot": "/data/mingos/install-root",
                "updateManifest": "/tmp/mingos-update-fixture/1/app.json",
                "receipt": "/run/evidence/update-fixture.json",
            }
            if fixture != expected:
                raise RuntimeError("unexpected update fixture contract")
            result = subprocess.run([
                fixture["helper"], "/data/mingos", fixture["installRoot"],
                fixture["updateManifest"], fixture["receipt"],
            ], capture_output=True, text=True, timeout=60)
            receipt = None
            receipt_path = Path(fixture["receipt"])
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
            emit("update-fixture", code=result.returncode, stdout=result.stdout,
                 stderr=result.stderr, receipt=receipt)
            if result.returncode:
                raise RuntimeError("update fixture failed: " + result.stderr.strip())
        install_fixture = session.get("installFixture")
        if install_fixture is not None:
            expected = {
                "helper": "/opt/mingos/prepare-install-fixture",
                "installRoot": "/opt/mingos/install-root",
                "initialWasm": "/opt/mingos/packages/initial/main.wasm",
                "sourceWasm": "/opt/mingos/packages/new/main.wasm",
                "sourceManifest": "/opt/mingos/install-source/app.json",
                "receipt": "/run/evidence/install-fixture.json",
            }
            if install_fixture != expected:
                raise RuntimeError("unexpected install fixture contract")
            result = subprocess.run([
                install_fixture["helper"], install_fixture["installRoot"],
                install_fixture["initialWasm"], install_fixture["sourceWasm"],
                install_fixture["sourceManifest"], install_fixture["receipt"],
            ], capture_output=True, text=True, timeout=60)
            receipt = None
            receipt_path = Path(install_fixture["receipt"])
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
            emit("install-fixture", code=result.returncode, stdout=result.stdout,
                 stderr=result.stderr, receipt=receipt)
            if result.returncode:
                raise RuntimeError("install fixture failed: " + result.stderr.strip())
        argv = desktop_argv(session, persistent)
        if session.get("profile") == "install-lifecycle":
            source = session.get("installSource")
            if source != "/opt/mingos/install-source/app.json" or not Path(source).is_file():
                raise RuntimeError("reviewed install source missing")
            argv += ["--install-source", source]
            events = Path(session.get("installEvents", ""))
            if str(events) != "/opt/mingos/install-events.json" or not events.is_file():
                raise RuntimeError("reviewed install event file missing")
            expected_hash = session.get("installEventsSha256")
            actual_hash = hashlib.sha256(events.read_bytes()).hexdigest()
            if not isinstance(expected_hash, str) or actual_hash != expected_hash:
                raise RuntimeError("reviewed install event hash mismatch")
            argv += ["--events", str(events)]
        desktop = launch(argv, "desktop", desktop_debug=True)
        if persistent:
            if stderr_capture is None: raise RuntimeError("persistent desktop requires captured diagnostic stderr")
            if persistent_update:
                persistent_parser = PersistentUpdateReceipts(platform.boot_id, desktop.pid, persistent['bootNumber'],
                                                             before=persistent_before)
            elif persistent_uninstall:
                from persistent_uninstall_receipt import PersistentUninstallReceipts
                persistent_parser = PersistentUninstallReceipts(platform.boot_id, desktop.pid, persistent['bootNumber'])
            else:
                persistent_parser = PersistentDesktopReceipts(platform.boot_id, desktop.pid)
        if fault_config:
            fault_injector = launch(["python3", "-B", "/etc/mingos/fault_injector.py", "--config",
                                     str(fault_config_path), "--state-file", STATE_PATH, "--receipt",
                                     str(fault_receipt_path)], "fault-injector")
        last_process_sample = 0
        while desktop.poll() is None:
            drain_stderr(); relay_state(); flush_logs()
            if fault_injector:
                fault_receipt = completed_receipt(fault_injector, fault_config, fault_receipt_path)
                if fault_receipt is not None:
                    emit("fault-injection", receipt=fault_receipt)
                    fault_injector = None
            if observer and observer.poll() is not None: raise RuntimeError("virtual input observer exited during desktop")
            if diagnostic.get("readyCaptureSeconds") and state and state["phase"] == "ready" and ready_capture_deadline is None:
                ready_capture_deadline = time.monotonic() + diagnostic["readyCaptureSeconds"]
                emit("diagnostic-ready-capture-window", seconds=diagnostic["readyCaptureSeconds"], triggeredByRevision=state["revision"])
            if ready_capture_deadline is not None and time.monotonic() >= ready_capture_deadline:
                raise RuntimeError("diagnostic-only ready capture window completed without normal desktop exit")
            if time.monotonic() >= last_process_sample + 1:
                emit("process-table", processes=process_table(resources=bool(diagnostic))); last_process_sample = time.monotonic()
            if weston.poll() is not None: raise RuntimeError("Weston exited during desktop")
            time.sleep(0.1)
        drain_stderr()
        if persistent_parser: persistent_parser.finish()
        desktop_code = desktop.returncode
        relay_state()
        emit("desktop-exit", code=desktop_code)
        if desktop_code != 0: raise RuntimeError("desktop exit: " + str(desktop_code))
        if fault_config and fault_receipt is None: raise RuntimeError("fault injector did not complete")
        active = [p for p in process_table() if "__worker" in p["argv"]]
        emit("desktop-workers-stopped", active=active)
        if active: raise RuntimeError("Kotlin workers still active; resource probe forbidden")
        if persistent:
            if not state: raise RuntimeError("persistent desktop ended without session state")
            persistent_parser.complete(persistent['bootNumber'])
            after = prepare_persistent_snapshot(session, persistent, "post-desktop", state["sessionId"],
                uninstall=persistent_uninstall, update=persistent_update,
                desktop_pid=desktop.pid if persistent_uninstall or persistent_update else None)
            if persistent_update:
                verify_persistent_update_after(persistent_before, after, persistent_parser, persistent['bootNumber'])
            elif persistent_uninstall:
                verify_persistent_uninstall_after(persistent_before, after, persistent_parser, persistent['bootNumber'])
            else:
                if persistent_parser.installed_content is not None and after['content'] != persistent_parser.installed_content:
                    raise RuntimeError("persistent post-desktop content disagrees with installed snapshot")
                if persistent["bootNumber"] == 2 and after["content"] != persistent_before["content"]:
                    raise RuntimeError("persistent content changed during reopen-only second boot")
        stop(weston, "weston"); stop(seat, "seatd"); stop(observer, "input-observer")
        platform.strict_after()
    except Exception as caught:
        error = repr(caught)
        emit_guest_error(error, traceback.format_exc())
    finally:
        for proc, label in ((desktop, "desktop"), (weston, "weston"), (seat, "seatd"), (observer, "input-observer"), (fault_injector, "fault-injector")):
            try: stop(proc, label)
            except Exception as caught:
                error = error or repr(caught)
        if platform:
            try: platform.finish_data(); data_synced = True
            except Exception as caught:
                error = error or repr(caught); emit("data-sync-error", error=repr(caught))
        try: drain_stderr()
        except Exception as caught:
            error = error or repr(caught); emit("diagnostic-stderr-error", error=repr(caught))
        if stderr_source is not None: stderr_source.close()
        for stream in streams: stream.close()
        if stderr_capture:
            path = LOG / "desktop-wayland.stderr.log"
            emit("desktop-diagnostic-stderr", bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), complete=not stderr_limited, limitBytes=stderr_capture.limit)
        flush_logs()
        emit("guest-result", status="normal" if error is None and desktop_code == 0 else "failed",
             desktopExitCode=desktop_code, error=error, dataSynced=data_synced,
             poweroffRequested=True, bootId=platform.boot_id if platform else None,
             diskUuid=platform.disk_uuid if platform else None,
             previousMarker=platform.previous if platform else None, marker=platform.marker if platform else None)
        emit("guest-stop")
        if channel: channel.close()
        subprocess.run(["/bin/busybox", "poweroff", "-f"], timeout=5)

if __name__ == "__main__": main()
