"""Pure subprocess test double. It never imports or invokes a Docker client."""
import hashlib
import io
import json
from pathlib import Path
import sys

import qualification_volume_archive as archive
import qualification_volume_plan as plan


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def identity(ino, mode=0o700):
    return {'dev': 1, 'ino': ino, 'uid': 65534, 'gid': 65534, 'mode': mode}


def main():
    root, scenario, *args = sys.argv[1:]
    root = Path(root)
    statefile, logfile = root / 'fake-state.json', root / 'events.jsonl'
    state = json.loads(statefile.read_text()) if statefile.exists() else {'containers': {}, 'volume': 'f' * 64}
    event = {'argv': args}
    def save():
        statefile.write_text(json.dumps(state))
        with logfile.open('a') as output:
            output.write(json.dumps(event) + '\n')
    if args[:2] == ['image', 'inspect']:
        save()
        print(json.dumps([{'Id': plan.HELPER_IMAGE, 'Os': 'linux', 'Architecture': 'amd64', 'Config': {'Volumes': None}}]))
        return 0
    if args[0] == 'create':
        cid = hashlib.sha256(('container-' + str(len(state['containers']))).encode()).hexdigest()
        labels, requests, mounts = {}, [], []
        for index, value in enumerate(args):
            if value == '--label':
                key, text = args[index + 1].split('=', 1)
                labels[key] = text
            if value != '--mount':
                continue
            fields = args[index + 1].split(',')
            parts = {k: v for k, v in (field.split('=', 1) for field in fields if '=' in field)}
            flags = {field for field in fields if '=' not in field}
            request = {'Type': parts['type'], 'Target': parts['target']}
            if 'source' in parts:
                request['Source'] = parts['source']
            if 'readonly' in flags:
                request['ReadOnly'] = True
            if 'volume-nocopy' in flags:
                request['VolumeOptions'] = {'NoCopy': True}
            requests.append(request)
            if parts['type'] == 'volume':
                name = parts.get('source', state['volume'])
                mount = {'Type': 'volume', 'Name': name, 'Destination': parts['target'],
                    'Source': '/var/lib/docker/volumes/' + name + '/_data', 'RW': 'readonly' not in flags, 'Driver': 'local'}
            else:
                mount = {'Type': 'bind', 'Source': parts['source'], 'Destination': parts['target'], 'RW': 'readonly' not in flags}
            mounts.append(mount)
        role = labels['org.mingos.restore.role']
        image_index = args.index(plan.HELPER_IMAGE)
        inspection = {'Id': cid, 'Image': plan.HELPER_IMAGE,
            'Config': {'Image': plan.HELPER_IMAGE, 'User': args[args.index('--user') + 1],
                'Entrypoint': ['python3'], 'Cmd': args[image_index + 1:], 'Labels': labels},
            'State': {'Status': 'created', 'Running': False},
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
                'AutoRemove': False, 'CapDrop': ['ALL'], 'CapAdd': ['CHOWN'] if role == 'creator' else None,
                'SecurityOpt': ['no-new-privileges'], 'NanoCpus': 2 * 10 ** 9, 'Memory': 4 * 1024 ** 3,
                'PidsLimit': 128, 'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0}, 'Mounts': requests},
            'Mounts': mounts}
        if role == 'creator' and scenario == 'named-creator':
            inspection['HostConfig']['Mounts'][0]['Source'] = 'caller-cache'
        if role == 'creator' and scenario == 'extra-creator-mount':
            inspection['Mounts'].append({'Type': 'bind', 'Source': '/', 'Destination': '/unsafe', 'RW': True})
        if role == 'verify' and scenario == 'verify-rw':
            inspection['Mounts'][0]['RW'] = True
        if role == 'restore' and scenario == 'wrong-worker-user':
            inspection['Config']['User'] = '0:0'
        state['containers'][cid] = inspection
        event.update(cid=cid, role=role)
        save()
        print(cid)
        return 0
    if args[:2] == ['container', 'inspect']:
        cid = args[-1]
        result = state['containers'][cid]
        if scenario == 'wrong-creator-cid' and result['Config']['Labels']['org.mingos.restore.role'] == 'creator':
            result = dict(result, Id='0' * 64)
        save()
        print(json.dumps([result]))
        return 0
    if args[0] == 'start':
        cid = args[-1]
        container = state['containers'][cid]
        role = container['Config']['Labels']['org.mingos.restore.role']
        event.update(cid=cid, role=role)
        if scenario == 'fail-' + role:
            save()
            print('first failure retained', file=sys.stderr)
            return 17
        container['State'] = {'Status': 'exited', 'Running': False, 'ExitCode': 0,
            'OOMKilled': False, 'Dead': False, 'Restarting': False}
        if scenario == 'oom-' + role:
            container['State']['OOMKilled'] = True
        if scenario == 'dead-' + role:
            container['State']['Dead'] = True
        if scenario == 'restarting-' + role:
            container['State']['Restarting'] = True
        if role == 'creator':
            save()
            return 0
        if role == 'restore':
            raw = sys.stdin.buffer.read()
            event['stdinSha256'], event['stdinBytes'] = hashlib.sha256(raw).hexdigest(), len(raw)
            prepared = archive.verify_volume_archive(io.BytesIO(raw), event['stdinSha256'])
            nonce = container['Config']['Labels']['org.mingos.restore.nonce']
            result = {'schemaVersion': 1, 'status': 'payload-restored', 'nonce': nonce,
                'formalQualification': False, 'finalSystemAccepted': False,
                'archiveSha256': prepared['archiveSha256'], 'archiveBytes': prepared['archiveBytes'],
                'embeddedManifestSha256': prepared['manifestSha256'], 'payloadBytes': prepared['payloadBytes'],
                'rootIdentity': identity(100), 'payloadIdentity': identity(101), 'controlIdentity': identity(102),
                'leaseIdentity': identity(103, 0o600), 'privateArchiveIdentity': identity(104, 0o600),
                'files': [dict(entry, identity=identity(200 + index, entry['mode'])) for index, entry in enumerate(prepared['entries'])]}
            if scenario == 'bad-worker-receipt':
                result['files'][1]['sha256'] = '0' * 64
            core = encoded(result)
            result['completionReceipt'] = {'sha256': hashlib.sha256(core).hexdigest(), 'bytes': len(core), 'identity': identity(105, 0o600)}
            state['restoreReceipt'] = result
            if scenario == 'running-writer':
                container['State'] = {'Status': 'running', 'Running': True}
        else:
            incoming = json.loads(sys.stdin.buffer.read())
            if incoming != state['restoreReceipt']:
                raise ValueError('coordinator must pass the external saved receipt')
            result = {key: incoming[key] for key in ('nonce', 'archiveSha256', 'archiveBytes', 'payloadBytes',
                'rootIdentity', 'payloadIdentity', 'controlIdentity', 'leaseIdentity', 'files')}
            result.update(schemaVersion=1, status='payload-readonly-verified', statvfsReadOnly=True,
                formalQualification=False, finalSystemAccepted=False)
            if scenario == 'bad-verify-receipt':
                result['archiveSha256'] = '0' * 64
            if scenario == 'false-readonly':
                result['statvfsReadOnly'] = False
        save()
        print(json.dumps(result))
        return 0
    raise ValueError('forbidden/unexpected fake command: ' + repr(args))


if __name__ == '__main__':
    raise SystemExit(main())
