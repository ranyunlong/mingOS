"""Verify fixed historical Docker save archives and load private tagless copies.

Supports Docker-save manifest.json with legacy layer.tar or OCI blob paths.
OCI-only, multi-image, multi-platform and external layer references fail closed.
Importing this module never calls Docker. No resource deletion is implemented.
"""
import argparse
from contextlib import ExitStack
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile


EXPECTED_IMAGE_IDS = {
    'rust': 'sha256:868a411f3e53b95fcdfb0604031e0172e51cf7d1ae2bce8aa0ea1c0bb82f0130',
    'tools': 'sha256:2440560d810a834637cfeeb1057f7f471c40caa8e532645e00e063cbc8ec639a',
    'openssl': 'sha256:3646133f4b4d2e007dd3eee28d9db6336c29f6ccb31b9f8a49b88bc0a70051be',
}
SHA256 = re.compile(r'sha256:[0-9a-f]{64}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')
MAX_ARCHIVE = 64 * 1024 ** 3
MAX_LAYER = 32 * 1024 ** 3
MAX_JSON = 16 * 1024 ** 2
INDEX_TYPES = {'application/vnd.oci.image.index.v1+json', 'application/vnd.docker.distribution.manifest.list.v2+json'}
MANIFEST_TYPES = {'application/vnd.oci.image.manifest.v1+json', 'application/vnd.docker.distribution.manifest.v2+json'}
LAYER_TYPES = {'application/vnd.oci.image.layer.v1.tar', 'application/vnd.oci.image.layer.v1.tar+gzip',
               'application/vnd.docker.image.rootfs.diff.tar', 'application/vnd.docker.image.rootfs.diff.tar.gzip'}
CONFIG_TYPES = {'application/vnd.oci.image.config.v1+json', 'application/vnd.docker.container.image.v1+json'}


class RestoreError(ValueError):
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = receipt


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    if len(raw) > MAX_JSON:
        raise ValueError('JSON metadata exceeds limit')
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs,
                          parse_constant=lambda text: (_ for _ in ()).throw(ValueError('non-finite JSON value')))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid JSON metadata') from error


def safe_name(name):
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name
            or PurePosixPath(name).is_absolute() or any(ord(c) < 32 for c in name)
            or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('unsafe archive/asset path')
    return name


def stream_hash(source, limit=MAX_ARCHIVE):
    value, size = hashlib.sha256(), 0
    while True:
        chunk = source.read(1024 ** 2)
        if not chunk:
            return value.hexdigest(), size
        size += len(chunk)
        if size > limit:
            raise ValueError('archive/layer exceeds byte limit')
        value.update(chunk)


def archive_hash(source):
    source.seek(0)
    value = stream_hash(source)
    source.seek(0)
    return value


def _metadata(archive, member):
    if member.size > MAX_JSON:
        raise ValueError('JSON metadata exceeds limit')
    with archive.extractfile(member) as stream:
        raw = stream.read(MAX_JSON + 1)
    return raw, strict_json(raw)


def _validate_envelope(stream):
    # TarFile hides GNU/PAX control records while iterating logical members.
    # Inspect the decompressed outer envelope before trusting those members.
    position, count = 0, 0
    stream.seek(0)
    while True:
        header = stream.read(512)
        if len(header) != 512:
            raise ValueError('truncated tar envelope/end markers')
        position += 512
        if header == b'\0' * 512:
            if stream.read(512) != b'\0' * 512:
                raise ValueError('tar requires two zero end markers')
            position += 512
            while True:
                tail = stream.read(1024 ** 2)
                if not tail:
                    stream.seek(0)
                    return
                position += len(tail)
                if position > MAX_ARCHIVE:
                    raise ValueError('decompressed tar envelope exceeds byte limit')
                if tail.strip(b'\0'):
                    raise ValueError('nonzero trailing archive data')
        member = tarfile.TarInfo.frombuf(header, 'utf-8', 'surrogateescape')
        if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE):
            raise ValueError('archive links/devices/extensions are forbidden')
        if member.size < 0 or member.size > MAX_LAYER or (member.isdir() and member.size):
            raise ValueError('invalid tar envelope member size')
        padding = (-member.size) % 512
        position += member.size + padding
        count += 1
        if position > MAX_ARCHIVE or count > 4096:
            raise ValueError('tar envelope exceeds byte/member limit')
        stream.seek(position - padding)
        if stream.read(padding) != b'\0' * padding:
            raise ValueError('nonzero/truncated tar member padding')


def _descriptor(value, archive, members, allowed_types, used):
    if (not isinstance(value, dict) or set(value) - {'mediaType', 'digest', 'size', 'annotations', 'platform'}
            or value.get('mediaType') not in allowed_types
            or not isinstance(value.get('digest'), str) or not SHA256.fullmatch(value['digest'])
            or type(value.get('size')) is not int or value['size'] < 0):
        raise ValueError('unsupported or external OCI descriptor/reference')
    annotations = value.get('annotations', {})
    if not isinstance(annotations, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in annotations.items()):
        raise ValueError('invalid descriptor annotations')
    name = 'blobs/sha256/' + value['digest'][7:]
    member = members.get(name)
    if member is None or not member.isfile() or member.size != value['size']:
        raise ValueError('OCI descriptor size/blob differs')
    with archive.extractfile(member) as stream:
        observed, _ = stream_hash(stream)
    if 'sha256:' + observed != value['digest']:
        raise ValueError('OCI descriptor digest differs')
    used.add(name)
    return name


def _oci_references(archive, members, record, image_id, used):
    if 'index.json' not in members and 'oci-layout' not in members:
        return
    if 'index.json' not in members or 'oci-layout' not in members:
        raise ValueError('incomplete OCI index/layout')
    if _metadata(archive, members['oci-layout'])[1] != {'imageLayoutVersion': '1.0.0'}:
        raise ValueError('unsupported OCI layout')
    used.update(('index.json', 'oci-layout'))
    index = _metadata(archive, members['index.json'])[1]
    visited = set()
    for _ in range(4):
        if (not isinstance(index, dict) or set(index) - {'schemaVersion', 'mediaType', 'manifests', 'annotations'}
                or type(index.get('schemaVersion')) is not int or index['schemaVersion'] != 2
                or index.get('mediaType', 'application/vnd.oci.image.index.v1+json') not in INDEX_TYPES
                or not isinstance(index.get('manifests'), list) or len(index['manifests']) != 1):
            raise ValueError('ambiguous OCI image/platform references')
        descriptor = index['manifests'][0]
        name = _descriptor(descriptor, archive, members, INDEX_TYPES | MANIFEST_TYPES, used)
        if name in visited:
            raise ValueError('cyclic OCI references')
        visited.add(name)
        nested = _metadata(archive, members[name])[1]
        if descriptor['mediaType'] in INDEX_TYPES:
            index = nested
            continue
        if (not isinstance(nested, dict) or set(nested) - {'schemaVersion', 'mediaType', 'config', 'layers', 'annotations'}
                or type(nested.get('schemaVersion')) is not int or nested['schemaVersion'] != 2
                or nested.get('mediaType', descriptor['mediaType']) not in MANIFEST_TYPES
                or not isinstance(nested.get('layers'), list)):
            raise ValueError('unsupported OCI image manifest')
        config = _descriptor(nested.get('config'), archive, members, CONFIG_TYPES, used)
        layers = [_descriptor(item, archive, members, LAYER_TYPES, used) for item in nested['layers']]
        if config != record['Config'] or nested['config']['digest'] != image_id or layers != record['Layers']:
            raise ValueError('OCI and Docker save references disagree')
        return
    raise ValueError('too many OCI index reference hops')


def verify_archive(source, expected_id):
    """Verify bytes from an already opened source, without extraction or Docker."""
    if not isinstance(expected_id, str) or not SHA256.fullmatch(expected_id):
        raise ValueError('exact SHA-256 image ID required')
    before, source_bytes = archive_hash(source)
    try:
        with tarfile.open(fileobj=source, mode='r:*') as archive:
            _validate_envelope(archive.fileobj)
            members = {}
            for member in archive:
                name = safe_name(member.name.rstrip('/') if member.isdir() else member.name)
                if name in members:
                    raise ValueError('duplicate archive member: ' + name)
                if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
                        or member.pax_headers or member.sparse is not None
                        or member.size < 0 or member.size > MAX_LAYER):
                    raise ValueError('archive links/devices/extensions are forbidden')
                members[name] = member
                if len(members) > 4096:
                    raise ValueError('too many archive members')
            archive.fileobj.seek(archive.offset)
            while True:
                trailer = archive.fileobj.read(1024 ** 2)
                if not trailer:
                    break
                if trailer.strip(b'\0'):
                    raise ValueError('nonzero trailing archive data')
            if 'manifest.json' not in members or not members['manifest.json'].isfile():
                raise ValueError('Docker save manifest.json is required')
            _, manifests = _metadata(archive, members['manifest.json'])
            if not isinstance(manifests, list) or len(manifests) != 1 or not isinstance(manifests[0], dict):
                raise ValueError('exactly one Docker save image required')
            record = manifests[0]
            if (set(record) - {'Config', 'RepoTags', 'Layers', 'LayerSources'}
                    or not {'Config', 'Layers'} <= set(record)
                    or not isinstance(record['Layers'], list)):
                raise ValueError('unsupported Docker save reference fields')
            config_name = safe_name(record['Config'])
            if config_name not in (expected_id[7:] + '.json', 'blobs/sha256/' + expected_id[7:]):
                raise ValueError('config filename does not bind expected image ID')
            tags = record.get('RepoTags')
            if tags is None:
                tags = []
            if (not isinstance(tags, list) or any(not isinstance(tag, str) or not tag for tag in tags)
                    or len(tags) != len(set(tags))):
                raise ValueError('ambiguous repository tags')
            record = dict(record, RepoTags=tags)
            config_member = members.get(config_name)
            if config_member is None or not config_member.isfile():
                raise ValueError('config is missing')
            config_raw, config = _metadata(archive, config_member)
            if 'sha256:' + hashlib.sha256(config_raw).hexdigest() != expected_id:
                raise ValueError('config digest differs from expected image ID')
            rootfs = config.get('rootfs') if isinstance(config, dict) else None
            if (not isinstance(rootfs, dict) or rootfs.get('type') != 'layers'
                    or not isinstance(rootfs.get('diff_ids'), list)
                    or len(rootfs['diff_ids']) != len(record['Layers'])
                    or any(not isinstance(item, str) or not SHA256.fullmatch(item) for item in rootfs['diff_ids'])):
                raise ValueError('config rootfs diff_ids/layer count differs')
            used = {'manifest.json', config_name}
            raw_hashes = {config_name: hashlib.sha256(config_raw).hexdigest()}
            for name, diff_id in zip(record['Layers'], rootfs['diff_ids']):
                safe_name(name)
                member = members.get(name)
                if member is None or not member.isfile() or name in ('manifest.json', config_name):
                    raise ValueError('referenced layer is missing or ambiguous')
                with archive.extractfile(member) as stream:
                    raw_hashes[name] = stream_hash(stream, MAX_LAYER)[0]
                if name.startswith('blobs/sha256/') and name != 'blobs/sha256/' + raw_hashes[name]:
                    raise ValueError('layer blob digest differs')
                with archive.extractfile(member) as stream:
                    magic = stream.read(2)
                    stream.seek(0)
                    if magic == b'\x1f\x8b':
                        with gzip.GzipFile(fileobj=stream) as decompressed:
                            actual = stream_hash(decompressed, MAX_LAYER)[0]
                    else:
                        actual = stream_hash(stream, MAX_LAYER)[0]
                if 'sha256:' + actual != diff_id:
                    raise ValueError('layer uncompressed diff_id differs')
                used.add(name)
            if 'LayerSources' in record:
                sources = record['LayerSources']
                if not isinstance(sources, dict) or set(sources) - set(rootfs['diff_ids']):
                    raise ValueError('extra LayerSources references')
                for diff_id, descriptor in sources.items():
                    position = rootfs['diff_ids'].index(diff_id)
                    if _descriptor(descriptor, archive, members, LAYER_TYPES, used) != record['Layers'][position]:
                        raise ValueError('LayerSources references disagree')
            _oci_references(archive, members, record, expected_id, used)
            if 'repositories' in members:
                repos = _metadata(archive, members['repositories'])[1]
                expected = {}
                last = record['Layers'][-1].split('/')[0] if record['Layers'] else None
                for tag in tags:
                    repository, separator, label = tag.rpartition(':')
                    if not separator or '/' in label:
                        raise ValueError('invalid legacy repository tag')
                    expected.setdefault(repository, {})[label] = last
                if repos != expected:
                    raise ValueError('extra legacy repository references')
                used.add('repositories')
            for name in record['Layers']:
                if name.endswith('/layer.tar'):
                    for suffix in ('json', 'VERSION'):
                        metadata = name.rsplit('/', 1)[0] + '/' + suffix
                        if metadata in members:
                            member = members[metadata]
                            if not member.isfile() or member.size > MAX_JSON:
                                raise ValueError('invalid legacy layer metadata')
                            if suffix == 'json':
                                _metadata(archive, member)
                            else:
                                with archive.extractfile(member) as stream:
                                    if stream.read() != b'1.0':
                                        raise ValueError('unsupported legacy layer VERSION')
                            used.add(metadata)
            for name, member in members.items():
                if member.isdir():
                    if not any(item.startswith(name + '/') for item in used):
                        raise ValueError('unreferenced archive directory')
                elif name not in used:
                    raise ValueError('unreferenced archive payload or extra image references')
            verified = {'imageId': expected_id, 'archiveSha256': before, 'archiveBytes': source_bytes,
                        'config': config_name, 'layers': record['Layers'], 'diffIds': rootfs['diff_ids'],
                        'rawSha256': raw_hashes}
    except (tarfile.TarError, EOFError, OSError) as error:
        raise ValueError('invalid Docker save archive: ' + str(error)) from error
    if archive_hash(source) != (before, source_bytes):
        raise ValueError('archive changed during verification')
    return verified


class _HashingReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, count=-1):
        raw = self.source.read(count)
        self.digest.update(raw)
        return raw


def sanitize_archive(source, output, verified):
    """Copy only the verified config/layers and a new manifest with no tags."""
    if archive_hash(source) != (verified['archiveSha256'], verified['archiveBytes']):
        raise ValueError('archive changed before sanitization')
    output.seek(0)
    output.truncate()
    with tarfile.open(fileobj=source, mode='r:*') as original, tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as clean:
        record = [{'Config': verified['config'], 'RepoTags': [], 'Layers': verified['layers']}]
        raw = json.dumps(record, separators=(',', ':')).encode()
        info = tarfile.TarInfo('manifest.json')
        info.size, info.mode = len(raw), 0o600
        clean.addfile(info, io.BytesIO(raw))
        for name in dict.fromkeys([verified['config'], *verified['layers']]):
            member = original.getmember(name)
            info = tarfile.TarInfo(name)
            info.size, info.mode = member.size, 0o600
            with original.extractfile(member) as stream:
                observed = _HashingReader(stream)
                clean.addfile(info, observed)
                if observed.digest.hexdigest() != verified['rawSha256'][name]:
                    raise ValueError('archive member changed while sanitizing')
    output.flush()
    if archive_hash(source) != (verified['archiveSha256'], verified['archiveBytes']):
        raise ValueError('archive changed during sanitization')
    output.seek(0)


def safe_asset(root, relative):
    name = safe_name(relative)
    candidate = root.joinpath(*name.split('/'))
    for path in (*reversed(root.parents), root):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('asset path contains a link/reparse point')
    cursor = root
    for part in name.split('/'):
        cursor = cursor / part
        info = cursor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('asset path contains a link/reparse point')
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('asset must be a regular file')
    return candidate


def _docker_run(receipt, key, command, timeout, **kwargs):
    operation = {'key': key, 'command': command, 'exitCode': None, 'timedOut': False,
                 'stdout': '', 'stderr': ''}
    receipt['operations'].append(operation)
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, check=False, **kwargs)
    except subprocess.TimeoutExpired as error:
        operation.update(timedOut=True, stdout=(error.stdout or b'').decode('utf-8', 'replace'),
                         stderr=(error.stderr or b'').decode('utf-8', 'replace'))
        raise ValueError('Docker command timed out; partial output retained') from error
    except OSError as error:
        operation['error'] = str(error)
        raise
    operation.update(exitCode=result.returncode, stdout=result.stdout.decode('utf-8', 'replace'),
                     stderr=result.stderr.decode('utf-8', 'replace'))
    return result, operation


def load_images(root, manifest, docker='docker'):
    """Verify every fixed input before the first Docker subprocess, then load."""
    receipt = {'schemaVersion': 1, 'status': 'failed', 'formalQualification': False,
               'finalSystemAccepted': False, 'images': [], 'operations': []}
    phase, key = 'manifest', None
    try:
        prefix = [docker] if isinstance(docker, str) else list(docker)
        if not prefix or any(not isinstance(item, str) or not item for item in prefix):
            raise ValueError('Docker executable command required')
        root = Path(root).absolute()
        manifest = Path(manifest).absolute()
        try:
            relative = manifest.relative_to(root).as_posix()
        except ValueError as error:
            raise ValueError('manifest must be inside AssetRoot') from error
        manifest = safe_asset(root, relative)
        with manifest.open('rb') as stream:
            raw_manifest = stream.read(16385)
        if len(raw_manifest) > 16384:
            raise ValueError('manifest exceeds 16 KiB')
        value = strict_json(raw_manifest)
        if (not isinstance(value, dict) or set(value) - {'schemaVersion', 'images', 'volume'}
                or type(value.get('schemaVersion')) is not int or value['schemaVersion'] != 1
                or not isinstance(value.get('images'), list) or len(value['images']) != 3):
            raise ValueError('fixed restore manifest schema required')
        entries = {}
        for entry in value['images']:
            if (not isinstance(entry, dict) or set(entry) != {'key', 'archive', 'archiveSha256', 'imageId'}
                    or entry.get('key') not in EXPECTED_IMAGE_IDS or entry['key'] in entries
                    or entry['imageId'] != EXPECTED_IMAGE_IDS[entry['key']]
                    or not isinstance(entry['archiveSha256'], str) or not HEX.fullmatch(entry['archiveSha256'])):
                raise ValueError('image differs from fixed qualification contract')
            entries[entry['key']] = entry
        if set(entries) != set(EXPECTED_IMAGE_IDS):
            raise ValueError('exact rust/tools/openssl images required')
        receipt['manifestSha256'] = hashlib.sha256(raw_manifest).hexdigest()
        receipt.update(assetRoot=str(root), manifest=str(manifest), loadImages=True, restoreVolume=False)
        with ExitStack() as stack:
            prepared = []
            for key in EXPECTED_IMAGE_IDS:
                phase = 'archive-verification'
                entry = entries[key]
                path = safe_asset(root, entry['archive'])
                source = stack.enter_context(path.open('rb'))
                verified = verify_archive(source, entry['imageId'])
                if verified['archiveSha256'] != entry['archiveSha256']:
                    raise ValueError('archive SHA-256 differs from manifest: ' + key)
                phase = 'tagless-staging'
                clean = stack.enter_context(tempfile.TemporaryFile())
                sanitize_archive(source, clean, verified)
                clean_hash, clean_bytes = archive_hash(clean)
                record = {name: verified[name] for name in ('imageId', 'archiveSha256', 'archiveBytes', 'diffIds')}
                record.update(key=key, taglessSha256=clean_hash, taglessBytes=clean_bytes)
                receipt['images'].append(record)
                prepared.append((entry, source, clean, verified, clean_hash, clean_bytes))
            if manifest.read_bytes() != raw_manifest:
                raise ValueError('manifest changed before Docker admission')
            for entry, source, clean, verified, clean_hash, clean_bytes in prepared:
                if archive_hash(source) != (verified['archiveSha256'], verified['archiveBytes']):
                    raise ValueError('archive changed before Docker admission')
                if archive_hash(clean) != (clean_hash, clean_bytes):
                    raise ValueError('tagless staging changed before Docker admission')
            for entry, source, clean, verified, clean_hash, clean_bytes in prepared:
                key, phase = entry['key'], 'load'
                command = prefix + ['image', 'load', '--quiet']
                clean.seek(0)
                result, operation = _docker_run(receipt, key, command, 1800, stdin=clean)
                if result.returncode != 0:
                    raise ValueError('Docker load failed exit ' + str(result.returncode) + ': ' + operation['stdout'] + operation['stderr'])
                phase = 'inspect'
                command = prefix + ['image', 'inspect', entry['imageId']]
                result, _ = _docker_run(receipt, key, command, 60)
                if result.returncode != 0:
                    raise ValueError('Docker inspect failed: ' + key)
                inspection = strict_json(result.stdout)
                if not isinstance(inspection, list) or len(inspection) != 1 or inspection[0].get('Id') != entry['imageId']:
                    raise ValueError('Docker inspect returned a different image ID')
            receipt['status'] = 'image-load-verified'
            return receipt
    except Exception as error:
        receipt['failure'] = {'phase': phase, 'key': key, 'error': str(error)}
        raise RestoreError(str(error), receipt) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-root', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = load_images(args.asset_root, args.manifest)
    except RestoreError as error:
        print(json.dumps(error.receipt, separators=(',', ':')))
        print('QUALIFICATION_ASSET_RESTORE_BLOCKED: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
