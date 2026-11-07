"""Synthetic test data only; never used by the production checker."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import shutil

HERE = Path(__file__).resolve().parent
REPO = Path('/repo') if HERE == Path('/prep') else HERE.parents[2]
sys.path.insert(0, str(REPO / 'scripts/boot'))
from wayland_receipt import POINTER_SYNC_POLICY
import test_persistent_uninstall_receipt as controls
from test_persistent_uninstall_guest import helper_receipt
from bootlib import PERSISTENT_UNINSTALL_ARGS, PERSISTENT_UNINSTALL_FIXTURE


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, separators=(',', ':')).encode()


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encoded(value) + b'\n')


def load():
    import importlib.util
    spec = importlib.util.spec_from_file_location('uninstall_checker_controls', HERE / 'checker.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return checker


class Fixture:
    def __init__(self, root):
        self.root, self.inputs, self.fixture = root, root / 'inputs', root / 'fixture'
        payload = self.inputs / 'payload'
        install = self.fixture / 'data/install-root'
        install.mkdir(parents=True)
        helper = payload / 'prepare-persistent-uninstall-fixture'
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b'\x7fELFsynthetic helper never executed')
        key = root / 'seed.der'
        key.write_bytes(bytes.fromhex('302e020100300506032b657004220420') + bytes([7]) * 32)
        self.packages = {}
        for label, package, version in (('initial', 'dev.ming.text-entry-demo', '0.1.0'), ('new', 'dev.ming.install-demo', '1.0.0')):
            wasm = b'\0asm\x01\0\0\0' + label.encode()
            staged = payload / 'packages' / label
            staged.mkdir(parents=True)
            (staged / 'main.wasm').write_bytes(wasm)
            def text(value):
                value = value.encode()
                return struct.pack('>Q', len(value)) + value
            message = (b'ming-package-signature-v1\0' + text('mingos-dev-2026') + struct.pack('>I', 1)
                + text(package) + text(version) + struct.pack('>I', 6) + text('main.wasm') + b'\0\0'
                + struct.pack('>Q', 4) + b'null' + struct.pack('>Q', len(wasm)) + wasm)
            (root / 'message').write_bytes(message)
            subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(key), '-keyform', 'DER', '-rawin',
                '-in', str(root / 'message'), '-out', str(root / 'signature')], check=True, capture_output=True)
            manifest = {'schemaVersion': 1, 'id': package, 'version': version, 'sdkVersion': 6, 'entry': 'main.wasm',
                'privateStorage': False, 'publicStorage': False, 'dataMigration': None, 'publisherKeyId': 'mingos-dev-2026',
                'publisherSignature': base64.b64encode((root / 'signature').read_bytes()).decode()}
            fingerprint = digest(b'ming-private-storage-package-v1\0' + struct.pack('>I', 1) + text(package)
                + text(version) + text('main.wasm') + struct.pack('>I', 6) + struct.pack('>Q', len(wasm)) + wasm
                + b'\0publisher-key-id-v1' + text('mingos-dev-2026'))
            directory = f'packages/{package.encode().hex()}/{version}-{fingerprint}'
            destination = install / directory if label == 'initial' else self.fixture / 'install-source'
            destination.mkdir(parents=True)
            put(destination / 'app.json', manifest)
            (destination / 'main.wasm').write_bytes(wasm)
            self.packages[label] = {'app': 1 if label == 'initial' else 2, 'packageId': package,
                'version': version, 'fingerprint': fingerprint, 'manifestPath': directory + '/app.json',
                'manifestSha256': digest((destination / 'app.json').read_bytes()), 'wasmPath': directory + '/main.wasm',
                'wasmSha256': digest(wasm)}
        (install / 'trusted-publishers-v1').write_bytes(b'ming-trusted-publishers-v1\nmingos-dev-2026\tactive\t'
            + base64.b64encode(bytes.fromhex('ea4a6c63e29c520abef5507b132ec5f9954776aebebe7b92421eea691446d22c')) + b'\n')
        for name in ('catalog.json', 'installed-packages-v1', 'desktop-install-state-v1.json'):
            (install / name).write_bytes(name.encode())
        for name in ('.ming-installed-packages.lock', '.ming-publisher-keyring.lock'):
            (install / name).write_bytes(b'')
        put(payload / 'catalog.json', {'schemaVersion': 3, 'apps': [
            {'id': 1, 'title': 'Text Lab', 'packageId': 'dev.ming.text-entry-demo'}], 'autostart': [1]})
        self.content = {key: digest((install / name).read_bytes()) for key, name in (
            ('catalogSha256', 'catalog.json'), ('registrySha256', 'installed-packages-v1'),
            ('bootstrapSha256', 'desktop-install-state-v1.json'), ('trustSha256', 'trusted-publishers-v1'))}
        self.content.update(apps=[self.packages['initial']], files=[{'path': p.relative_to(install).as_posix(), 'sha256': digest(p.read_bytes())}
            for p in sorted(install.rglob('*')) if p.is_file() and not p.name.startswith('.')])
        self.installed = copy.deepcopy(self.content)
        self.installed['apps'] = [self.packages['new'], self.packages['initial']]
        for key, data in (('catalogSha256', b'installed catalog'), ('registrySha256', b'installed registry')):
            self.installed[key] = digest(data)
        self.installed['files'] += [{'path': self.packages['new'][kind + 'Path'], 'sha256': self.packages['new'][kind + 'Sha256']} for kind in ('manifest', 'wasm')]
        self.removed = {key: self.packages['new'][key] for key in ('packageId', 'version', 'fingerprint')}
        self.after = copy.deepcopy(self.installed)
        self.after['apps'] = [self.packages['initial']]
        self.after['catalogSha256'], self.after['registrySha256'] = self.content['catalogSha256'], digest(b'removed registry')
        for snapshot in (self.installed, self.after):
            hashes = {'catalog.json': snapshot['catalogSha256'], 'installed-packages-v1': snapshot['registrySha256']}
            for row in snapshot['files']:
                row['sha256'] = hashes.get(row['path'], row['sha256'])
            snapshot['files'].sort(key=lambda row: row['path'])
        self.value = {'schemaVersion': 1, 'status': 'initialized-first', 'actions': {key: 1 for key in load().ACTIONS},
            'content': self.content, 'removed': None, 'privateContent': None}
        put(self.fixture / 'fixture-receipt.json', self.value)
        put(self.fixture / 'stdout.log', self.value)
        self.review = {'profile': 'persistent-uninstall', 'sessionMode': 'long-lived',
            'instrumentedProfile': 'persistent-uninstall-pointer-ready-v1', 'persistentInstallState': '/data/mingos/install-root',
            'catalogPath': '/data/mingos/install-root/catalog.json', 'installSource': '/opt/mingos/install-source/app.json',
            'extraArgs': PERSISTENT_UNINSTALL_ARGS, 'persistentUninstallFixture': PERSISTENT_UNINSTALL_FIXTURE,
            'catalogSha256': digest((payload / 'catalog.json').read_bytes()),
            'persistentUninstallFixtureSha256': {'helper': digest(helper.read_bytes()),
                'initialWasm': self.packages['initial']['wasmSha256'], 'sourceWasm': self.packages['new']['wasmSha256']},
            'inputScripts': {f'boot{n}Sha256': digest((HERE / f'input-boot{n}.json').read_bytes()) for n in (1, 2)}}
        staged, original = '/prep/inputs/payload', '/prep/build/fixture'
        put(self.fixture / 'command.json', {'stage': 'fixture', 'exitCode': 0, 'commandRoot': original,
            'inputsRoot': '/prep/inputs', 'executableSha256': digest(helper.read_bytes()), 'argv': [
                staged + '/prepare-persistent-uninstall-fixture', 'initialize-first', original + '/data/install-root',
                staged + '/packages/initial/main.wasm', staged + '/packages/new/main.wasm', original + '/install-source/app.json',
                staged + '/catalog.json', self.review['catalogSha256'], original + '/fixture-receipt.json']})


def isolation():
    return {'requested': 'strict', 'prepared': True, 'applied': True, 'cleaned': True, 'uid': 20001, 'gid': 20001,
        'cgroup': '/sys/fs/cgroup/mingos/worker', 'limits': {'memoryBytes': 134217728, 'pids': 32, 'cpuMax': '25000 100000'},
        'namespaceIds': [1, 2, 3, 4], 'metrics': None, 'error': None}


def app1(stopped=False):
    return {'app': 1, 'generation': 1, 'window': 1, 'workerPid': 52, 'state': 'closed' if stopped else 'running',
        'sourceSequence': 2, 'presentedSourceSequence': 2, 'firstReadyMs': 11, 'inputReady': not stopped,
        'minimized': False, 'outer': None}


class RawBoot:
    def __init__(self, root, number, fixture, signed):
        self.run = root / f'boot-{number}'
        self.run.mkdir()
        self.number, self.fixture, self.signed = number, fixture, signed
        self.spec = json.loads((HERE / f'input-boot{number}.json').read_bytes())
        self.boot, self.session = f'boot-{number}', f'session-{number}'
        self.events, self.progress, self.qmp = [], [], []
        self.stderr = bytearray(b'client get_pointer(new id wl_pointer@36)\n')
        self.clock, self.request_id = 10000.0, 0
        self.values = controls.chain(old=False) if number == 1 else []
        seen = set()
        def replace(value):
            if isinstance(value, dict):
                if id(value) in seen: return
                seen.add(id(value))
                if 'catalogSha256' in value and 'apps' in value:
                    target = fixture.installed if len(value.get('apps', [])) == 2 else fixture.after
                    value.clear(); value.update(copy.deepcopy(target))
                    return
                if 'fingerprint' in value and value.get('packageId') == 'dev.ming.install-demo':
                    value['fingerprint'] = fixture.removed['fingerprint']
                for key in ('sequence', 'pressSequence', 'releaseSequence', 'downSequence'):
                    if key in value: value[key] += 4
                if value.get('app') == 2 and 'generation' in value: value['generation'] = 1
                if 'bootId' in value: value['bootId'] = self.boot
                if 'sessionId' in value: value['sessionId'] = self.session
                if 'revision' in value and 'apps' in value:
                    value['revision'] += 20

                    if not any(a['app'] == 1 for a in value['apps']): value['apps'].append(app1())
                    for a in value['apps']:
                        if a['app'] == 2:
                            a['generation'] = 1
                            if a['state'] == 'ready': a['state'] = 'running'
                for item in list(value.values()): replace(item)
            elif isinstance(value, list):
                for item in value: replace(item)
        # Content replacement must classify before clearing.
        def repair_contents(value):
            if isinstance(value, dict):
                if 'catalogSha256' in value:
                    target = fixture.installed if len(value['apps']) == 2 else fixture.after
                    value.clear(); value.update(copy.deepcopy(target)); return
                for item in value.values(): repair_contents(item)
            elif isinstance(value, list):
                for item in value: repair_contents(item)
        # Install direct records and post-uninstall content are classified here.
        repair_contents(self.values)
        # Protect exact replacement snapshots from generic recursion.
        def mark(value):
            if isinstance(value, dict):
                if 'catalogSha256' in value: seen.add(id(value)); return
                for item in value.values(): mark(item)
            elif isinstance(value, list):
                for item in value: mark(item)
        mark(self.values)
        replace(self.values)
        for value in self.values:
            if value['kind'] == 'native-input':
                index = (value['receipt']['sequence'] - 1) // 2
                value['receipt'].update({axis: self.spec['steps'][index]['events'][0][axis] for axis in ('x', 'y')})
        def anchor(rev, prompt=None, stopped=False):
            value = controls.anchor(rev, prompt=prompt, presented=(1 if rev == 1 else 2 if rev < 21 else 3), trace=10 if number == 1 and rev >= 60 else 0)
            value['state'].update(bootId=self.boot, sessionId=self.session, phase='stopped' if stopped else 'ready')
            value['state']['apps'] = [app1(stopped)]
            return value
        def edge(n, before, after):
            point = self.spec['steps'][(n-1)//2]['events'][0]
            return {'kind': 'native-input', 'bootId': self.boot, 'sessionId': self.session, 'desktopPid': controls.PID,
                'receipt': {'schemaVersion': 1, 'sequence': n, 'pressSequence': n if n % 2 else n-1,
                    'edge': 'down' if n % 2 else 'up', 'x': point['x'], 'y': point['y'], 'action': 'other',
                    'targetApp': None, 'before': before, 'after': after, 'trace': []}}
        if number == 1:
            prompt = {'mode': 16, 'packageId': 'dev.ming.install-demo', 'version': '1.0.0', 'hasAllow': True}
            install = [edge(1, anchor(1), anchor(2, prompt)), edge(2, anchor(2, prompt), anchor(3, prompt))]
            allow = [edge(3, anchor(4, prompt), anchor(5, prompt)), *self.values[:2], edge(4, anchor(5, prompt), anchor(21))]
            rows = self.values[2:]
            groups = [install, allow, rows[:4], rows[4:6], rows[6:9], rows[9:]]
            for v in groups[-1]:
                if v['kind'] == 'uninstall-confirm': v['receipt']['after']['state']['root']['presentedSequence'] = 3
                elif v['kind'] == 'native-input' and v['receipt']['sequence'] == 12: v['receipt']['after']['state']['root']['presentedSequence'] = 3
            # Reap can finish asynchronously after Close release and before its expectation.
            groups[3][-1]['receipt']['after']['state']['apps'][0]['state'] = 'closed'
            # U ends after native release, confirm ends after its release.
            power_before = copy.deepcopy(groups[-1][-1]['receipt']['after'])
            groups.append([edge(13, dict(power_before, state=dict(power_before['state'], revision=60)), anchor(61)),
                           edge(14, anchor(61), anchor(62, stopped=True))])
        else:
            groups = [[edge(1, anchor(1), anchor(2)), edge(2, anchor(2), anchor(3, stopped=True))]]
        self.groups = groups
        self.before, self.after = self.snapshot_value('pre-desktop'), self.snapshot_value('post-desktop')
        self.emit('guest-start', bootId=self.boot)
        env = self.before['envelope']
        self.emit('data-marker', uuid='disk', marker=env['marker'], previousMarker=env['previousMarker'], readbackVerified=True)
        self.helper(self.before, 'pre-desktop')
        argv = ['/opt/mingos/ming-app-host', 'desktop', '/data/mingos/install-root/catalog.json', '--renderer', 'cpu']
        argv += PERSISTENT_UNINSTALL_ARGS + ['--persistent-install-state', '/data/mingos/install-root']
        if number == 1: argv += ['--install-source', '/opt/mingos/install-source/app.json']
        self.emit('process-start', name='desktop', pid=controls.PID, argv=argv)
        self.emit('process-table', processes=[{'pid': 52, 'argv': 'ming-app-host __worker', 'status': ''}])
        self.qmp.append({'monotonic': self.tick(), 'direction': 'receive', 'message': {'QMP': {}}})
        self.command('qmp_capabilities')
        mice = [{'name': 'tablet', 'absolute': True, 'current': True}]
        self.command('query-mice', result=mice)
        current = copy.deepcopy(groups[0][0]['receipt']['before']['state'])
        self.snapshot(current)
        self.record('input-routing', mice=mice, state=current)
        for index, (step, group) in enumerate(zip(self.spec['steps'], groups)):
            from infra import input_events
            # Each baseline is observed before the next pointer transaction.
            current = copy.deepcopy(group[0]['receipt']['before']['state'])
            self.snapshot(current)
            events = input_events(step['events'], current)
            transactions = [events[:2]] + [[item] for item in events[2:]]
            self.command('input-send-event', {'events': transactions[0]})
            point, offset = step['events'][0], len(self.stderr)
            self.record('input-pointer-dispatched', step=step['name'], point=point, desktopPid=controls.PID,
                afterLogOffset=offset, waitSeconds=5, state=current)
            line = ('client wl_pointer@36.enter(1, wl_surface@15, %s, %s)\n' if index == 0 else 'client wl_pointer@36.motion(1, %s, %s)\n') % (point['x'], point['y'])
            self.stderr.extend((line + 'client wl_pointer@36.frame()\n').encode())
            pointer = {'pointer': 36, 'surface': 15, 'entered': True, 'x': float(point['x']), 'y': float(point['y']),
                'sequence': index + 1, 'logOffset': len(self.stderr)}
            self.emit('desktop-pointer-receipt', bootId=self.boot, sessionId=self.session, desktopPid=controls.PID, receipt=pointer)
            self.record('input-pointer-ready', step=step['name'], receipt=dict(pointer, bootId=self.boot, sessionId=self.session,
                desktopPid=controls.PID), afterLogOffset=offset, state=current)
            for transaction in transactions[1:]: self.command('input-send-event', {'events': transaction})
            self.record('input-dispatched', step=step['name'], events=events, transactions=transactions,
                diagnosticSplit=True, pointerHandshake=True, state=current)
            for value in group:
                data = controls.line(value)
                start = len(self.stderr); self.stderr.extend(data)
                self.emit('desktop-persistent-receipt', bootId=self.boot, sessionId=self.session, desktopPid=controls.PID,
                    logStartOffset=start, logEndOffset=len(self.stderr), rawBase64=base64.b64encode(data).decode(), record=value)
            current = copy.deepcopy(group[-1]['receipt']['after']['state'] if group[-1]['kind'] == 'native-input' else group[-1]['receipt']['anchor']['state'])
            self.snapshot(current)
            self.record('input-expectation', step=step['name'], state=current)
        self.final = current
        self.emit('desktop-exit', code=0)
        self.emit('desktop-workers-stopped', active=[])
        self.helper(self.after, 'post-desktop')
        self.emit('data-synced', uuid='disk', marker=env['marker'], unmounted=True)
        self.emit('desktop-diagnostic-stderr', bytes=len(self.stderr), sha256=digest(self.stderr), complete=True, limitBytes=8388608)
        target = controls.history(generation=1)
        initial = dict(target, app=1, generation=1, window=1, workerPid=52, packageId='dev.ming.text-entry-demo')
        instances = [initial, target] if number == 1 else [initial]
        for row in instances: row['isolation'] = isolation()
        trace = []
        for row in reversed(instances):
            kinds = ['ready', 'close-request', 'terminal-reply-written', 'result-accepted', 'worker-reaped', 'worker-result', 'closed']
            if row['app'] == 2: kinds = ['launch'] + kinds + ['persistent-uninstall-commit', 'uninstalled']
            for kind in kinds:
                trace.append({'atMs': 12, 'app': row['app'], 'generation': row['generation'], 'window': row['window'],
                    'kind': kind, 'detail': 'source=2' if kind == 'ready' else 'close' if kind == 'terminal-reply-written' else ''})
        self.outcome = {'activeWorkersAfterShutdown': 0, 'droppedTrace': 0, 'droppedInputs': 0,
            'trace': trace, 'instances': instances}
        for name, data in (('desktop-wayland.stderr.log', bytes(self.stderr)), ('desktop.log', encoded(self.outcome) + b'\n')):
            self.emit('log', name=name, offset=0, encoding='base64', data=base64.b64encode(data).decode())
        self.emit('guest-result', status='normal', desktopExitCode=0, error=None, dataSynced=True, poweroffRequested=True,
            bootId=self.boot, diskUuid='disk', marker=env['marker'], previousMarker=env['previousMarker'])
        self.emit('guest-stop')
        self.report = {'status': 'normal-desktop-poweroff', 'bootId': self.boot, 'diskUuid': 'disk', 'marker': env['marker'],
            'previousMarker': env['previousMarker'], 'lastState': self.final, 'inputComplete': True, 'kernelPowerdown': True,
            'guestExitCode': 0, 'bootNumber': number, 'bundleIdentity': 'b' * 64,
            'inputScriptSha256': fixture.review['inputScripts'][f'boot{number}Sha256']}
        self.flush()

    def snapshot_value(self, phase):
        initial = self.number == 1 and phase == 'pre-desktop'
        return {'envelope': {'schemaVersion': 1, 'bootId': self.boot, 'bootNumber': self.number,
            'role': 'initialize-first' if self.number == 1 else 'open-uninstalled',
            'marker': {'bootId': self.boot, 'counter': self.number},
            'previousMarker': None if self.number == 1 else {'bootId': 'boot-1', 'counter': 1},
            'diskUuid': 'disk', 'registryRoot': '/data/mingos/install-root', 'catalogPath': '/data/mingos/install-root/catalog.json',
            'sessionId': self.session if phase == 'post-desktop' else None, 'desktopPid': controls.PID if phase == 'post-desktop' else None,
            'phase': phase, 'status': 'initialized-first' if initial else 'opened-uninstalled',
            'actions': {key: int(initial) for key in load().ACTIONS}},
            'content': copy.deepcopy(self.fixture.content if initial else self.fixture.after),
            'removed': None if initial else copy.deepcopy(self.fixture.removed),
            'privateContent': None if initial else copy.deepcopy(controls.receipt.PRIVATE_CONTENT)}

    def helper(self, value, phase):
        env = value['envelope']
        helper = {'schemaVersion': 1, 'status': env['status'], 'actions': env['actions'],
            **{key: value[key] for key in ('content', 'removed', 'privateContent')}}
        self.emit('persistent-uninstall-state', code=0, argv=load().helper_argv(self.number, phase, self.fixture.review),
            stdout=json.dumps(helper), stderr='', receipt=value)

    def tick(self):
        self.clock += 0.05
        return self.clock

    def emit(self, kind, **fields):
        self.events.append(dict(type=kind, uptime=float(len(self.events)), **copy.deepcopy(fields)))

    def record(self, kind, **fields):
        self.progress.append(dict(type=kind, monotonic=self.tick(), **copy.deepcopy(fields)))

    def command(self, name, arguments=None, result=None):
        self.request_id += 1
        message = {'execute': name, 'id': self.request_id}
        if arguments is not None: message['arguments'] = arguments
        self.qmp += [{'monotonic': self.tick(), 'direction': 'send', 'message': message},
            {'monotonic': self.tick(), 'direction': 'receive', 'message': {'id': self.request_id, 'return': {} if result is None else result}}]

    def snapshot(self, state):
        self.emit('desktop-state', state=state, desktopPid=controls.PID, diagnosticStderrOffset=len(self.stderr))

    def flush(self):
        for name, rows in (('guest.jsonl', self.events), ('inputs.jsonl', self.progress), ('qmp.jsonl', self.qmp)):
            (self.run / name).write_bytes(b''.join(encoded(row) + b'\n' for row in rows))
        put(self.run / 'qmp.json', [row['message'] for row in self.qmp])
        put(self.run / 'result.json', self.report)
        (self.run / 'kernel-serial.log').write_bytes(b'reboot: Power down\n')


def seal(root):
    put(root / "file-sha256.json", {"schemaVersion": 1, "algorithm": "sha256",
        "files": {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in root.rglob("*")
                  if p.is_file() and p.name != "file-sha256.json"}})


def portable_pair(root):
 root.mkdir(); f=Fixture(root); c=load(); signed=c.verify_fixture_directory(f.fixture,f.inputs,f.review)
 payload=f.inputs/'payload'; (payload/'ming-app-host').write_bytes(b'\x7fELFsynthetic host never executed')
 f.review.update(reviewed=True,reviewer='synthetic-control',hostSha256=digest((payload/'ming-app-host').read_bytes()),budgetEvidence={'synthetic':True},instrumentedQualification=True,inputSynchronization=POINTER_SYNC_POLICY)
 put(f.inputs/'session-review.json',f.review)
 bundle=root/'bundle'; bundle.mkdir()
 for name in ('vmlinuz','initramfs.cpio.gz'): (bundle/name).write_bytes(b'synthetic-'+name.encode())
 manifest={'reviewSha256':digest((f.inputs/'session-review.json').read_bytes()),'sha256':{n:digest((bundle/n).read_bytes()) for n in ('vmlinuz','initramfs.cpio.gz')},'payloadProfile':'persistent-uninstall','sessionReview':f.review,'instrumentedQualification':True,'diagnosticOnly':False,'diagnosticProfile':'input-pointer-ready-v1','payloadSha256':{p.relative_to(payload).as_posix():digest(p.read_bytes()) for p in payload.rglob('*') if p.is_file()}}
 put(bundle/'bundle.json',manifest); identity=digest((bundle/'bundle.json').read_bytes())
 runs=root/'pair'; runs.mkdir()
 for number in (1,2):
  b=RawBoot(root,number,f,signed); b.report['bundleIdentity']=identity;b.flush();shutil.move(b.run,runs/b.run.name)
  for dest in (f.inputs/f'input-boot{number}.json',runs/f'boot-{number}.input.json',root/f'input-boot{number}.json'): shutil.copyfile(HERE/f'input-boot{number}.json',dest)
 put(runs/'result.json',{'status':'same-bundle-disk-cold-boot-verified'})
 frozen={}
 for name in ('checker.py','raw_boot_audit.py'):
  shutil.copyfile(HERE/name,root/name)
 for name in ('checker.py','raw_boot_audit.py'): frozen[f'scripts/boot/qualification_uninstall/{name}']=digest((root/name).read_bytes())
 for name in c.REPLAY_MODULES:
  dest=root/'replay/scripts/boot'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(c.REPO/'scripts/boot'/name,dest);frozen['scripts/boot/'+name]=digest(dest.read_bytes())
 put(root/'freeze.json',{'schemaVersion':1,'profile':'persistent-uninstall','files':frozen,'checker':{'path':'scripts/boot/qualification_uninstall/checker.py','sha256':digest((root/'checker.py').read_bytes())},'inputs':f.review['inputScripts']})
 for name in ('seed.der','message','signature'): (root/name).unlink()
 seal(root)
 return root
