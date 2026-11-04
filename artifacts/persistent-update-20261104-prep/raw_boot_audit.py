#!/usr/bin/env python3
"""Shared raw QMP, Wayland, log, and poweroff checks; never run a guest.

Derived from the existing persistent-install raw auditor. Update-specific
transactions and snapshots are validated by verify-persistent-update-evidence.
"""
import argparse
import base64
import hashlib
import json
import math
import struct
import sys
from pathlib import Path


def require(value, message):
    if not value:
        raise ValueError(message)


def read_json(path):
    return strict_json(Path(path).read_text())


def strict_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            require(key not in result, 'duplicate JSON key: ' + key)
            result[key] = item
        return result
    def invalid(value):
        raise ValueError('non-finite JSON value: ' + value)
    def finite(value):
        result = float(value)
        require(math.isfinite(result), 'non-finite JSON number')
        return result
    return json.loads(value, object_pairs_hook=unique, parse_constant=invalid, parse_float=finite)


def integer(value, low=0):
    return type(value) is int and value >= low


def records(path, clock):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'raw evidence file missing/unsafe: ' + str(path))
    require(path.stat().st_size <= 128 * 1024 * 1024, 'raw evidence file exceeds bound')
    data = path.read_bytes()
    require(data and data.endswith(b'\n'), 'raw JSONL is empty or truncated: ' + path.name)
    result, previous = [], -1
    for line in data.splitlines():
        row = strict_json(line)
        require(isinstance(row, dict), 'raw JSONL object required')
        timestamp = row.get(clock)
        require(type(timestamp) in (int, float) and math.isfinite(timestamp)
                and timestamp >= previous, 'raw ' + clock + ' clock missing/regressed')
        previous = timestamp
        result.append(row)
    return result


def reconstruct_log(events, name):
    result = bytearray()
    for event in events:
        if event.get('type') != 'log' or event.get('name') != name:
            continue
        require(event.get('encoding') == 'base64' and integer(event.get('offset'))
                and event['offset'] == len(result), name + ' byte offsets are not contiguous')
        block = base64.b64decode(event['data'], validate=True)
        require(block, name + ' contains an empty log chunk')
        result.extend(block)
    require(result, 'raw log missing: ' + name)
    return bytes(result)


def audit_qmp(boot):
    transcript = records(boot / 'qmp.jsonl', 'monotonic')
    require(read_json(boot / 'qmp.json') == [row['message'] for row in transcript],
            'QMP array differs from raw transcript')
    require(transcript[0].get('direction') == 'receive'
            and 'QMP' in transcript[0].get('message', {}), 'raw QMP greeting missing')
    requests, ordered = {}, []
    for row in transcript[1:]:
        message = row.get('message')
        require(isinstance(message, dict) and row.get('direction') in ('send', 'receive'), 'raw QMP record shape')
        if row['direction'] == 'send':
            identity = message.get('id')
            require(integer(identity, 1) and identity not in requests and isinstance(message.get('execute'), str),
                    'QMP request identity missing/duplicated')
            require(all(request['reply'] is not None for request in ordered), 'QMP request preceded previous acknowledgement')
            request = {'message': message, 'sent': row['monotonic'], 'reply': None}
            requests[identity] = request
            ordered.append(request)
        elif 'id' in message:
            identity = message['id']
            require(integer(identity, 1) and identity in requests and requests[identity]['reply'] is None, 'QMP orphan/duplicate reply')
            require('return' in message and 'error' not in message, 'QMP command failed')
            requests[identity]['reply'] = message
            requests[identity]['acknowledged'] = row['monotonic']
        else:
            require(isinstance(message.get('event'), str), 'unrecognized raw QMP message')
    require(ordered and ordered[0]['message']['execute'] == 'qmp_capabilities'
            and all(request['reply'] is not None for request in ordered), 'QMP handshake/replies incomplete')
    return ordered


def audit_raw_boot(boot, spec, report, repo=None):
    """Fail closed on raw records; a synthetic unit control is not boot acceptance."""
    repo = Path(repo) if repo else Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / 'scripts/boot'))
    from infra import input_events, state_input_ready, validate_state
    from wayland_receipt import PointerReceipt
    boot = Path(boot)
    events = records(boot / 'guest.jsonl', 'uptime')
    forbidden = {'guest-error', 'data-sync-error', 'forced-process-stop', 'desktop-state-rejected',
                 'diagnostic-log-limit', 'diagnostic-stderr-error'}
    require(not any(event.get('type') in forbidden for event in events), 'raw guest failure record')
    def one(kind, predicate=lambda event: True):
        matches = [event for event in events if event.get('type') == kind and predicate(event)]
        require(len(matches) == 1, 'expected exactly one raw ' + kind)
        return matches[0]
    start = one('guest-start')
    desktop = one('process-start', lambda event: event.get('name') == 'desktop')
    terminal = one('guest-result')
    require(isinstance(start.get('bootId'), str) and start['bootId']
            and integer(desktop.get('pid'), 1), 'raw boot/desktop process identity missing')
    require(terminal.get('status') == 'normal' and integer(terminal.get('desktopExitCode'))
            and terminal['desktopExitCode'] == 0
            and terminal.get('error') is None and terminal.get('dataSynced') is True
            and terminal.get('poweroffRequested') is True and terminal.get('bootId') == start['bootId'],
            'raw guest terminal is not a normal poweroff')
    exited, workers = one('desktop-exit'), one('desktop-workers-stopped')
    require(integer(exited.get('code')) and exited['code'] == 0 and workers.get('active') == [],
            'raw desktop exit or worker cleanup failed')
    stopped = one('guest-stop')
    require(events.index(stopped) == len(events) - 1 and events.index(terminal) == len(events) - 2,
            'guest terminal/stop ordering incomplete')
    marker = one('data-marker')
    synced = one('data-synced')
    require(marker.get('readbackVerified') is True and synced.get('unmounted') is True
            and marker.get('uuid') == synced.get('uuid') == terminal.get('diskUuid')
            and marker.get('marker') == synced.get('marker') == terminal.get('marker')
            and marker.get('previousMarker') == terminal.get('previousMarker')
            and events.index(start) == 0
            and events.index(marker) < events.index(desktop) < events.index(exited)
            < events.index(workers) < events.index(synced) < events.index(terminal),
            'raw data marker/sync/terminal identity differs')
    for field in ('bootId', 'diskUuid', 'marker', 'previousMarker'):
        require(field in report and report[field] == terminal.get(field), 'result differs from raw guest: ' + field)
    require(report.get('status') == 'normal-desktop-poweroff' and report.get('inputComplete') is True
            and report.get('kernelPowerdown') is True and integer(report.get('guestExitCode'))
            and report['guestExitCode'] == 0,
            'result contradicts normal raw evidence')
    require('reboot: Power down' in (boot / 'kernel-serial.log').read_text(errors='strict'),
            'actual kernel Power down missing')
    stdout = reconstruct_log(events, 'desktop.log')
    stderr = reconstruct_log(events, 'desktop-wayland.stderr.log')
    capture = one('desktop-diagnostic-stderr')
    require(capture.get('complete') is True and integer(capture.get('bytes'), 1)
            and capture['bytes'] == len(stderr) and integer(capture.get('limitBytes'), len(stderr))
            and capture.get('sha256') == hashlib.sha256(stderr).hexdigest()
            and events.index(exited) < events.index(capture) < events.index(terminal),
            'diagnostic stderr missing, truncated, or digest/length differs')
    outcome = strict_json(stdout)
    require(isinstance(outcome, dict) and integer(outcome.get('activeWorkersAfterShutdown'))
            and outcome['activeWorkersAfterShutdown'] == 0 and integer(outcome.get('droppedTrace'))
            and outcome['droppedTrace'] == 0, 'complete desktop outcome or trace missing')
    states, session, previous_revision, previous_capture = {}, None, None, 0
    for event in events:
        if event.get('type') != 'desktop-state':
            continue
        state = event['state']
        validate_state(state, start['bootId'], session, previous_revision)
        require(event.get('desktopPid') == desktop['pid'] and state.get('error') is None
                and state.get('phase') != 'failed' and all(app['state'] != 'failed' for app in state['apps'])
                and events.index(desktop) < events.index(event) < events.index(exited),
                'raw state desktop identity/error/order differs')
        captured = event.get('diagnosticStderrOffset')
        require(integer(captured) and previous_capture <= captured <= len(stderr), 'state stderr offset missing/regressed')
        revision = state['revision']
        require(revision not in states or states[revision]['state'] == state, 'same revision changed raw state')
        states[revision] = event
        session, previous_revision, previous_capture = state['sessionId'], revision, captured
    require(states and report.get('lastState') == states[previous_revision]['state']
            and states[previous_revision]['state']['phase'] == 'stopped', 'result lastState differs from stopped raw state')
    parser = PointerReceipt()
    parsed = parser.feed(stderr)
    require(not parser.buffer, 'Wayland diagnostic log ends in a partial line')
    emitted = [event for event in events if event.get('type') == 'desktop-pointer-receipt']
    require(parsed and [event.get('receipt') for event in emitted] == parsed,
            'raw Wayland log does not reproduce all pointer receipts')
    bound_receipts = []
    for event in emitted:
        require(event.get('bootId') == start['bootId'] and event.get('sessionId') == session
                and event.get('desktopPid') == desktop['pid']
                and any(events.index(state) < events.index(event) for state in states.values()),
                'raw pointer identity or prior state differs')
        bound_receipts.append(dict(event['receipt'], bootId=event['bootId'], sessionId=session, desktopPid=desktop['pid']))
    instances = outcome.get('instances', [])
    require(isinstance(instances, list) and len(instances) == 2
            and {item.get('app') for item in instances} == {1, 2}, 'complete outcome worker identities missing')
    for item in instances:
        require(isinstance(item, dict) and all(integer(item.get(key), 1)
                for key in ('app', 'generation', 'window', 'workerPid')),
                'outcome worker identity requires integers')
        matching = [app for state in states.values() for app in state['state']['apps']
                    if app['app'] == item['app'] and app.get('generation') == item.get('generation')
                    and app.get('workerPid') == item.get('workerPid') and app.get('inputReady') is True]
        require(integer(item.get('workerPid'), 1) and matching
                and integer(item.get('presentedSourceSequence'), 1)
                and item['presentedSourceSequence'] >= max(app.get('presentedSourceSequence') or 0 for app in matching),
                'outcome worker generation/PID/presentation differs from raw ready state')
    progress = records(boot / 'inputs.jsonl', 'monotonic')
    steps = spec['steps']
    require(steps and len({step['name'] for step in steps}) == len(steps), 'reviewed step names missing/duplicated')
    expected_types = ['input-routing'] + [kind for step in steps for kind in (
        'input-pointer-dispatched', 'input-pointer-ready', 'input-dispatched', 'input-expectation')]
    require([row.get('type') for row in progress] == expected_types, 'raw pointer handshake order/coverage differs')
    def snapshot(row):
        state = row.get('state', {})
        require(state.get('revision') in states and states[state['revision']]['state'] == state,
                'input snapshot is absent from raw guest state')
        return state
    ordered = audit_qmp(boot)
    allowed_qmp = {'qmp_capabilities', 'query-version', 'query-status', 'query-commands', 'query-block',
                   'query-qmp-schema', 'query-mice', 'screendump', 'input-send-event'}
    require(all(request['message']['execute'] in allowed_qmp for request in ordered),
            'unreviewed mutating/unknown QMP command')
    routed = [request for request in ordered if request['message']['execute'] == 'query-mice']
    require(len(routed) == 1 and routed[0]['reply']['return'] == progress[0].get('mice')
            and routed[0]['acknowledged'] <= progress[0]['monotonic']
            and any(mouse.get('current') is True and mouse.get('absolute') is True
                    for mouse in progress[0]['mice']), 'raw absolute pointer routing missing')
    snapshot(progress[0])
    sends = [request for request in ordered if request['message']['execute'] == 'input-send-event']
    cursor, surface, last_expectation, last_receipt_offset = 0, None, progress[0]['monotonic'], -1
    last_revision = snapshot(progress[0])['revision']
    for index, planned in enumerate(steps):
        armed, ready, dispatched, confirmed = progress[1 + index * 4:5 + index * 4]
        require(all(row.get('step') == planned['name'] for row in (armed, ready, dispatched, confirmed)),
                'handshake step identity/order differs')
        before, current, baseline, after = [snapshot(row) for row in (armed, ready, dispatched, confirmed)]
        required = planned.get('requireApps', spec['requireApps'])
        require(state_input_ready(before, required) and state_input_ready(current, required)
                and current == baseline and last_revision <= before['revision'] <= current['revision']
                and baseline['revision'] <= after['revision'], 'input dispatched before app/root readiness')
        require(armed.get('desktopPid') == desktop['pid'] and integer(armed.get('afterLogOffset'))
                and armed['afterLogOffset'] >= states[before['revision']]['diagnosticStderrOffset']
                and armed['afterLogOffset'] >= last_receipt_offset
                and armed['afterLogOffset'] == ready.get('afterLogOffset') and armed.get('waitSeconds') == 5,
                'ABS baseline PID/log offset/deadline differs')
        points = [event for event in planned['events'] if event.get('kind') == 'pointer']
        require(len(points) == 1 and planned['events'][0] == points[0] and armed.get('point') == points[0],
                'reviewed leading ABS point differs')
        receipt = ready.get('receipt', {})
        require(receipt in bound_receipts and receipt.get('entered') is True
                and integer(receipt.get('surface'), 1) and integer(receipt.get('pointer'), 1)
                and receipt['logOffset'] > armed['afterLogOffset']
                and (surface is None or receipt['surface'] == surface)
                and all(type(receipt.get(axis)) in (int, float) and math.isfinite(receipt[axis])
                        and abs(receipt[axis] - points[0][axis]) <= 1 for axis in ('x', 'y')),
                'current-client pointer receipt/identity/offset/coordinates differ')
        receipt_index = bound_receipts.index(receipt)
        require(events.index(states[before['revision']]) < events.index(emitted[receipt_index])
                and receipt['logOffset'] > last_receipt_offset,
                'pointer receipt preceded ABS baseline state or was reused')
        visible = [candidate for candidate, event in zip(bound_receipts, emitted)
                   if events.index(event) <= max(events.index(emitted[receipt_index]), events.index(states[current['revision']]))]
        require(visible[-1] == receipt, 'pointer receipt was invalidated before ready state')
        last_receipt_offset = receipt['logOffset']
        surface = receipt['surface']
        expected = input_events(planned['events'], baseline)
        transactions = [expected[:2]] + [[event] for event in expected[2:]]
        require(dispatched.get('diagnosticSplit') is True and dispatched.get('pointerHandshake') is True
                and dispatched.get('events') == expected and dispatched.get('transactions') == transactions,
                'dispatched transactions differ from reviewed split input')
        requests = sends[cursor:cursor + len(transactions)]
        require(len(requests) == len(transactions) and all(request['message'].get('arguments') == {'events': transaction}
                for request, transaction in zip(requests, transactions)), 'raw QMP input differs from reviewed transactions')
        require(last_expectation <= requests[0]['sent'] <= requests[0]['acknowledged'] <= armed['monotonic']
                <= ready['monotonic'] <= dispatched['monotonic'] <= confirmed['monotonic'],
                'raw ABS/ready/dispatch/expectation timing differs')
        require(ready['monotonic'] - requests[0]['sent'] <= 5
                and all(ready['monotonic'] <= request['sent'] <= request['acknowledged'] <= dispatched['monotonic']
                        for request in requests[1:]), 'button/key QMP occurred before pointer-ready or lacked acknowledgement')
        expected_state = planned.get('expect', {})
        require(after['revision'] > baseline['revision'], 'expectation did not advance revision')
        if 'phase' in expected_state:
            require(after['phase'] == expected_state['phase'], 'expected phase absent')
        if 'storagePrompt' in expected_state:
            require(after['root'].get('storagePrompt') == expected_state['storagePrompt'], 'expected prompt differs')
        if expected_state.get('rootAdvance'):
            require(after['root']['presentedSequence'] > baseline['root']['presentedSequence'], 'expected root did not advance')
        if expected_state.get('storagePromptPresented'):
            prompt_sequence = after['root'].get('storagePromptPresentedSequence')
            require(integer(prompt_sequence, 1) and after['root'].get('storagePrompt') is not None
                    and prompt_sequence == after['root']['presentedSequence']
                    and prompt_sequence > baseline['root']['presentedSequence'], 'current prompt presentation absent')
        apps = {app['app']: app for app in after['apps']}
        for condition in expected_state.get('apps', []):
            require(condition['app'] in apps and all(apps[condition['app']][key] == value for key, value in condition.items()),
                    'expected application identity/state differs')
        old = {app['app']: app for app in baseline['apps']}
        for app in expected_state.get('sourceAdvance', []):
            require(app in apps and (apps[app].get('presentedSourceSequence') or 0)
                    > (old.get(app, {}).get('presentedSourceSequence') or 0), 'expected source did not advance')
        cursor += len(transactions)
        last_expectation = confirmed['monotonic']
        last_revision = after['revision']
    require(cursor == len(sends), 'unreviewed extra raw QMP input')
    return {'events': events, 'outcome': outcome, 'desktopLog': stdout, 'progress': progress,
            'pointerHandshakes': len(steps), 'qmpInputRequestsAcknowledged': len(sends),
            'diagnosticLogSha256': hashlib.sha256(stderr).hexdigest()}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
