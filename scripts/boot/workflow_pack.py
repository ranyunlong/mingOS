"""Independent workflow pack verifier; old preview packs are rejected."""
import argparse
import json
import os
import sys
from pathlib import Path
TRUSTED_BOOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TRUSTED_BOOT))
from developer_pack import (require, relative, stamp, strict_json, open_root,
    read_file, inventory, confirm_inventory, HEX, MAX_JSON, MAX_MEMBERS, MAX_FILE, ROLES)
MANIFEST = 'workflow-manifest.json'
PROFILE = 'developer-workflow-v1'

def validate_schema(value):
    require(type(value) is dict and set(value) == {'schemaVersion', 'profile', 'purpose', 'algorithm',
            'files', 'directories', 'origins', 'trust', 'formalQualification', 'finalSystemAccepted'}
            and type(value['schemaVersion']) is int and value['schemaVersion'] == 1,
            'closed manifest schema differs')
    require(value['profile'] == PROFILE and value['purpose'] == 'developer-workflow-input-only'
            and value['algorithm'] == 'sha256', 'closed dev-only profile differs')
    require(value['formalQualification'] is False and value['finalSystemAccepted'] is False,
            'acceptance flags must be false')
    require(type(value['trust']) is dict and value['trust'] == {
            'purpose': 'fixture-development-only', 'publisherKeyId': 'mingos-dev-2026', 'releaseSigning': False}
            and value['trust']['releaseSigning'] is False, 'fixture-only trust schema differs')
    files, directories, origins = value['files'], value['directories'], value['origins']
    require(type(files) is dict and 1 <= len(files) <= MAX_MEMBERS
            and type(directories) is dict and len(files) + len(directories) + 1 <= MAX_MEMBERS,
            'file/directory record count differs')
    for name, record in files.items():
        relative(name)
        require(name != MANIFEST and type(record) is dict
                and set(record) == {'sha256', 'bytes', 'mode', 'origin'}
                and isinstance(record['sha256'], str) and HEX.fullmatch(record['sha256'])
                and type(record['bytes']) is int and 0 <= record['bytes'] <= MAX_FILE
                and type(record['mode']) is int and record['mode'] in (0o600, 0o644, 0o700, 0o755),
                'file record differs')
        require(isinstance(record['origin'], str) and record['origin'] in ROLES, 'file origin differs')
    for name, record in directories.items():
        relative(name)
        require(name not in files and type(record) is dict and set(record) == {'mode'}
                and type(record['mode']) is int and record['mode'] in (0o700, 0o755), 'directory record differs')
    require(type(origins) is dict and set(origins) == ROLES
            and {record['origin'] for record in files.values()} == ROLES, 'closed origin roles differ')
    for role, record in origins.items():
        require(type(record) is dict, 'origin record differs')
        if role in ('kernel', 'runtimeTools'):
            require(set(record) == {'kind', 'imageId', 'receipt'} and record['kind'] == 'tool-image'
                    and isinstance(record['imageId'], str) and record['imageId'].startswith('sha256:')
                    and HEX.fullmatch(record['imageId'][7:]), 'tool image origin differs')
            references = (record['receipt'],)
        else:
            require(set(record) == {'kind', 'sourceManifest', 'buildReceipt'}
                    and record['kind'] == 'source-build', 'source origin differs')
            references = (record['sourceManifest'], record['buildReceipt'])
        for name in references:
            relative(name)
            require(name in files, 'origin reference is not a sealed file')
    return value


def verify_pack(pack, manifest_sha256):
    require(isinstance(manifest_sha256, str) and HEX.fullmatch(manifest_sha256),
            'exact lowercase manifest SHA256 required')
    require(sys.platform == 'linux', 'Linux descriptor/no-follow semantics required')
    fd = None
    try:
        fd = open_root(pack)
        root_stamp = stamp(os.fstat(fd))
        observed, raw = read_file(fd, MANIFEST, limit=MAX_JSON, data=True)
        require(observed['sha256'] == manifest_sha256, 'manifest SHA256 differs from external pin')
        require(observed['mode'] == 0o644, 'closed manifest mode must be 0644')
        value = validate_schema(strict_json(raw))
        files, directories, snapshots = inventory(fd)
        require(files.pop(MANIFEST, None) == observed, 'manifest changed during inventory')
        expected = {name: {key: record[key] for key in ('sha256', 'bytes', 'mode')}
                    for name, record in value['files'].items()}
        require(files == expected, 'complete file inventory differs')
        require(directories == value['directories'], 'complete directory inventory differs')
        final_record, final_raw = read_file(fd, MANIFEST, limit=MAX_JSON, data=True)
        require(final_record == observed and final_raw == raw and stamp(os.fstat(fd)) == root_stamp,
                'manifest/root changed during verification')
        reopened = open_root(pack)
        try:
            require(stamp(os.fstat(reopened)) == root_stamp, 'canonical root changed during verification')
            confirm_inventory(reopened, snapshots)
        finally:
            os.close(reopened)
        return {'schemaVersion': 1, 'status': 'workflow-pack-bytes-verified', 'profile': PROFILE,
                'manifestSha256': manifest_sha256, 'fileCount': len(files), 'directoryCount': len(directories),
                'origins': value['origins'], 'originClaimsByteBound': True,
                'trustPurpose': value['trust']['purpose'], 'publisherAuthenticated': False,
                'guestBooted': False, 'sourceBuildReplayed': False,
                'formalQualification': False, 'finalSystemAccepted': False}
    except OSError as error:
        raise ValueError('Developer pack: unsafe/inaccessible directory, link or file: ' + str(error)) from error
    finally:
        if fd is not None:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('verify',))
    parser.add_argument('--pack', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify_pack(args.pack, args.manifest_sha256), sort_keys=True, allow_nan=False))
    except (ValueError, RecursionError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
