"""Pure creator command and exact inspection admission.

No Docker invocation or target write occurs here. Integration must record a
command attempt before executing it and preserve every created resource.
"""
import re

HELPER_IMAGE = 'sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80'
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
HEX32 = re.compile(r'[0-9a-f]{32}\Z')

# Retain descriptors across ownership transfer. Root only has CHOWN and must
# not attempt to reopen a private UID65534 directory after fchown.
INITIALIZE = '''import os,stat,sys
if sys.platform != 'linux' or os.geteuid() != 0: raise ValueError('Linux root initializer required')
root=os.open('/restore',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
try:
 if os.listdir(root): raise ValueError('anonymous volume must be literal empty')
 for name in ('payload','control'):
  os.mkdir(name,0o700,dir_fd=root)
  child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=root)
  try:
   os.fchmod(child,0o700);os.fchown(child,65534,65534);os.fsync(child)
  finally: os.close(child)
 os.fchmod(root,0o700);os.fchown(root,65534,65534);os.fsync(root)
finally: os.close(root)
'''


def creator_command(nonce, docker='docker'):
    if not isinstance(nonce, str) or not HEX32.fullmatch(nonce):
        raise ValueError('32 lowercase hex nonce required')
    if not isinstance(docker, str) or not docker:
        raise ValueError('Docker executable required')
    return [docker, 'create', '--pull', 'never', '--network', 'none',
            '--read-only', '--user', '0:0', '--cpus', '2', '--memory', '4g',
            '--pids-limit', '128', '--cap-drop', 'ALL', '--cap-add', 'CHOWN',
            '--security-opt', 'no-new-privileges', '--label', 'org.mingos.restore.nonce=' + nonce,
            '--label', 'org.mingos.restore.role=creator',
            '--mount', 'type=volume,target=/restore,volume-nocopy',
            '--entrypoint', 'python3', HELPER_IMAGE, '-I', '-B', '-c', INITIALIZE]


def validate_creator_inspection(value, cid, nonce):
    if (not isinstance(cid, str) or not HEX64.fullmatch(cid)
            or not isinstance(nonce, str) or not HEX32.fullmatch(nonce)):
        raise ValueError('exact creator CID and nonce required')
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ValueError('exactly one creator inspection required')
    item = value[0]
    config, host, state = item.get('Config', {}), item.get('HostConfig', {}), item.get('State', {})
    if any(not isinstance(field, dict) for field in (config, host, state)):
        raise ValueError('creator inspection objects required')
    labels = config.get('Labels') or {}
    if not isinstance(labels, dict):
        raise ValueError('creator inspection labels required')
    if (item.get('Id') != cid or item.get('Image') != HELPER_IMAGE
            or config.get('Image') != HELPER_IMAGE or config.get('User') != '0:0'
            or labels.get('org.mingos.restore.nonce') != nonce
            or labels.get('org.mingos.restore.role') != 'creator'
            or config.get('Entrypoint') != ['python3']
            or config.get('Cmd') != ['-I', '-B', '-c', INITIALIZE]
            or state.get('Status') != 'created' or state.get('Running') is not False):
        raise ValueError('creator identity/configuration differs')
    if (host.get('NetworkMode') != 'none' or host.get('ReadonlyRootfs') is not True
            or host.get('Privileged') is not False or host.get('AutoRemove') is not False
            or host.get('CapDrop') != ['ALL'] or host.get('CapAdd') not in (['CHOWN'], ['CAP_CHOWN'])
            or host.get('SecurityOpt') not in (['no-new-privileges'], ['no-new-privileges:true'])
            or host.get('NanoCpus') != 2 * 10 ** 9 or host.get('Memory') != 4 * 1024 ** 3
            or host.get('PidsLimit') != 128
            or host.get('RestartPolicy') != {'Name': 'no', 'MaximumRetryCount': 0}):
        raise ValueError('creator confinement differs')
    for key in ('Binds', 'VolumesFrom', 'Links', 'Devices', 'DeviceRequests',
                'Tmpfs', 'PortBindings', 'ExtraHosts'):
        if host.get(key) not in (None, [], {}):
            raise ValueError('unexpected creator attachment: ' + key)
    requested = host.get('Mounts')
    if not isinstance(requested, list) or len(requested) != 1 or not isinstance(requested[0], dict):
        raise ValueError('single anonymous requested mount required')
    request = dict(requested[0])
    if request.pop('Source', '') != '':
        raise ValueError('caller-selected volume source forbidden')
    if request != {'Type': 'volume', 'Target': '/restore', 'VolumeOptions': {'NoCopy': True}}:
        raise ValueError('anonymous volume-nocopy request required')
    mounts = item.get('Mounts')
    if not isinstance(mounts, list) or len(mounts) != 1 or not isinstance(mounts[0], dict):
        raise ValueError('single effective volume mount required')
    mount = mounts[0]
    volume = mount.get('Name')
    if (mount.get('Type') != 'volume' or mount.get('Destination') != '/restore'
            or mount.get('RW') is not True or mount.get('Driver') != 'local'
            or not isinstance(volume, str) or not HEX64.fullmatch(volume)):
        raise ValueError('effective anonymous volume identity differs')
    return {'creatorId': cid, 'nonce': nonce, 'helperImageId': HELPER_IMAGE,
            'volumeName': volume, 'subpath': 'payload', 'controlSubpath': 'control'}
