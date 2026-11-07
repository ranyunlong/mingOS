#!/usr/bin/env python3
"""Closed uninstall inputs, geometry and first-failure pair controls.

This independent Linux driver never launches a guest at import. The historical
APFS/update drivers remain unchanged. No resource removal operation is provided.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys

HEX = re.compile(r'[0-9a-f]{64}\Z')
HERE = Path(__file__).resolve().parent
REPO = Path('/repo') if HERE == Path('/prep') else HERE.parents[2]

def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for raw in iter(lambda: stream.read(1024 * 1024), b''): digest.update(raw)
    return digest.hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('duplicate JSON field: ' + key)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON: ' + value)))


def read(path): return strict_json(Path(path).read_bytes())


def write(path, value, *, exclusive=True):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == 'posix':
        fd = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def relative(root, name):
    if not isinstance(name, str) or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/')):
        raise ValueError('unsafe relative artifact path')
    path = root / name
    if path.resolve() != path or not path.resolve().is_relative_to(root):
        raise ValueError('symlink or escaping artifact path')
    return path


def run_two_boots(pair, run_one, gate):
    # Creating this latch before any child launch makes a failed generation non-retryable.
    latch = pair / 'attempt.json'
    write(latch, {'schemaVersion': 1, 'startedAt': now(), 'retryAllowed': False})
    reports, failure, number = [], None, None
    try:
        for number in (1, 2):
            report = run_one(number)
            checked = gate(number, report)
            write(pair / ('boot-%d-gate.json' % number), checked)
            reports.append(report)
        result = {'status': 'same-bundle-disk-cold-boot-verified', 'bootIds': [r['bootId'] for r in reports],
                  'bundleIdentity': reports[0]['bundleIdentity'], 'diskUuid': reports[0]['diskUuid'],
                  'markers': [r['marker'] for r in reports], 'firstFailure': None, 'finalSystemAccepted': False}
    except Exception as error:
        failure = {'bootNumber': number, 'reason': repr(error), 'at': now()}
        write(pair / 'first-failure.json', failure)
        result = {'status': 'failed', 'firstFailure': failure, 'finalSystemAccepted': False}
    write(pair / 'result.json', result)
    return result


class RawGuard:
    def __init__(self, path, number):
        self.path, self.number, self.offset, self.pending = path, number, 0, b''
        self.boot_id, self.session_id = None, None

    def poll(self, final=False):
        if not self.path.exists():
            if final: raise ValueError('missing raw guest stream')
            return
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            data = stream.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024: raise ValueError('raw guest incremental budget exceeded')
            self.offset = stream.tell()
        lines = (self.pending + data).split(b'\n')
        self.pending = lines.pop()
        # A valid 256 KiB stderr receipt expands through base64 in the guest
        # envelope. The relay enforces its own raw limit; bound this transport
        # independently without shrinking that accepted receipt size.
        if len(self.pending) > 1024 * 1024: raise ValueError('raw guest envelope line limit')
        for line in lines:
            event = strict_json(line)
            if not isinstance(event, dict): raise ValueError('invalid raw guest record')
            kind = event.get('type')
            if kind in ('guest-error', 'data-sync-error', 'forced-process-stop', 'desktop-error'):
                raise ValueError('raw guest failure: ' + kind)
            if kind == 'desktop-state':
                state = event.get('state') or {}
                if state.get('error') or state.get('phase') == 'failed': raise ValueError('raw desktop failure')
                for app in state.get('apps', []):
                    if app.get('state') == 'failed': raise ValueError('raw worker failure')
                    if self.number == 2 and app.get('app') != 1: raise ValueError('boot2 unexpected app/worker')
            if kind == 'guest-result' and (event.get('status') != 'normal' or event.get('desktopExitCode') != 0
                    or event.get('dataSynced') is not True or event.get('poweroffRequested') is not True):
                raise ValueError('abnormal raw terminal result')
        if final and self.pending: raise ValueError('truncated raw guest stream')


def validate_geometry(receipt, repo, stdout_path=None):
    if set(receipt) != {'schemaVersion','geometry','sourceSha256','test','stdoutSha256'} or type(receipt['schemaVersion']) is not int or receipt['schemaVersion'] != 1:
        raise ValueError('actual geometry evidence schema')
    test = receipt['test']
    if test != {'matched':1,'passed':1,'failed':0,'ignored':0}: raise ValueError('geometry test must match and pass exactly one test')
    sources = {'crates/ming-app-host/src/graphics/desktop_shell.rs','crates/ming-app-host/src/desktop_model.rs','crates/ming-app-host/src/desktop_native.rs'}
    if set(receipt['sourceSha256']) != sources: raise ValueError('geometry actual source set')
    for name, expected in receipt['sourceSha256'].items():
        if sha(relative(repo, name)) != expected: raise ValueError('geometry actual source changed')
    geometry = receipt['geometry']
    if geometry.get('schemaVersion') != 1 or geometry.get('width') != 1280 or geometry.get('height') != 800:
        raise ValueError('geometry viewport')
    points = [[1148,12],[880,478],[676,766],[96,104],[656,748],[880,478],[1238,12]]
    if geometry.get('boot1Points') != points or geometry.get('boot2Points') != [points[6]]:
        raise ValueError('geometry fixed actual pointers')
    if geometry.get('app2Dock',{}).get('index') != 1 or geometry['app2Dock'].get('count') != 2:
        raise ValueError('geometry actual two-app dock')
    if not isinstance(receipt['stdoutSha256'],str) or not HEX.fullmatch(receipt['stdoutSha256']): raise ValueError('geometry output hash')
    if stdout_path is not None:
        raw = Path(stdout_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt['stdoutSha256']: raise ValueError('geometry raw output changed')
        pattern = rb'(?:test desktop_native::input_tests::persistent_uninstall_two_app_input_geometry \.\.\. )?MING_PERSISTENT_UNINSTALL_GEOMETRY (.+)'
        lines = [matched.group(1) for line in raw.splitlines() if (matched := re.fullmatch(pattern,line))]
        if len(lines) != 1 or strict_json(lines[0]) != geometry or b'1 passed; 0 failed; 0 ignored' not in raw:
            raise ValueError('geometry raw test output mismatch')


def input_contracts():
    return ({'schemaVersion': 1, 'requireApps': [1], 'steps': [{'name': 'native-install', 'events': [{'kind': 'pointer', 'x': 1148, 'y': 12}, {'kind': 'button', 'button': 'left', 'down': True}, {'kind': 'button', 'button': 'left', 'down': False}], 'expect': {'storagePrompt': {'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True}, 'rootAdvance': True, 'storagePromptPresented': True}}, {'name': 'confirm-install', 'events': [{'kind': 'pointer', 'x': 880, 'y': 478}, {'kind': 'button', 'button': 'left', 'down': True}, {'kind': 'button', 'button': 'left', 'down': False}], 'expect': {'storagePrompt': None, 'rootAdvance': True}}, {'name': 'dock-launch-app2', 'events': [{'kind': 'pointer', 'x': 676, 'y': 766}, {'kind': 'button', 'button': 'left', 'down': True}, {'kind': 'button', 'button': 'left', 'down': False}], 'expect': {'apps': [{'app': 2, 'state': 'running', 'generation': 1}]}}, {'name': 'close-app2-after-ready', 'events': [{'kind': 'pointer', 'x': 96, 'y': 104}, {'kind': 'button', 'button': 'left', 'down': True}, {'kind': 'button', 'button': 'left', 'down': False}], 'expect': {'apps': [{'app': 2, 'state': 'closed', 'generation': 1}]}, 'requireApps': [1, 2]}, {'name': 'native-uninstall-app2', 'events': [{'kind': 'pointer', 'x': 656, 'y': 748}, {'kind': 'button', 'button': 'left', 'down': True}, {'kind': 'button', 'button': 'left', 'down': False}], 'expect': {'storagePrompt': {'mode': 10, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True}, 'rootAdvance': True, 'storagePromptPresented': True}}, {'name': 'confirm-uninstall', 'events': [{'kind': 'pointer', 'x': 880, 'y': 478}, {'kind': 'button', 'button': 'left', 'down': True}, {'kind': 'button', 'button': 'left', 'down': False}], 'expect': {'storagePrompt': None, 'rootAdvance': True}}, {'name': 'power-off-after-uninstall', 'events': [{'kind': 'pointer', 'x': 1238, 'y': 12}, {'kind': 'button', 'button': 'left', 'down': True}, {'kind': 'button', 'button': 'left', 'down': False}], 'expect': {'phase': 'stopped'}}]}, {'schemaVersion': 1, 'requireApps': [1], 'steps': [{'name': 'power-off-reopened', 'events': [{'kind': 'pointer', 'x': 1238, 'y': 12}, {'kind': 'button', 'button': 'left', 'down': True}, {'kind': 'button', 'button': 'left', 'down': False}], 'expect': {'phase': 'stopped'}}]})

def validate_inputs(first, second, output):
    directory = str(REPO / 'scripts/boot')
    if directory not in sys.path: sys.path.insert(0, directory)
    from infra import InputPlan
    if (first, second) != input_contracts():
        raise ValueError('changed qualification pointer plan')
    for number, plan in enumerate((first, second), 1):
        InputPlan(plan, Path(output) / ('input-%d.jsonl' % number), 900,
                  split_transactions=True, pointer_handshake=True)


def execution_budget(first, second):
    if (first, second) != input_contracts():
        raise ValueError('changed qualification pointer plan')
    return {'bootSteps': [7, 1], 'minimumBootSeconds': [770, 470],
            'startupSeconds': 300, 'uiStepSeconds': 45, 'normalShutdownSeconds': 120,
            'pointerWaitMs': 5000, 'timeoutSecondsEach': 900,
            'vmSupervisorSlackSeconds': 45, 'pairTimeoutSeconds': 1920,
            'containerTimeoutSeconds': 1950, 'guestMemoryMiB': 2048,
            'guestVcpus': 2, 'progressTimeoutMs': 300000,
            'stderrLimitBytes': 8388608, 'observerMaxEvents': 4096,
            'observerMaxSeconds': 900, 'retainedWorkerBudgetsChanged': False,
            'staticCalculationOnly': True, 'finalSystemAccepted': False}
