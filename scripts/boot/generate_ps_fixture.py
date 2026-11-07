"""Write tiny public-entry fixtures into an explicitly provided test directory."""
import json
from pathlib import Path
import sys

import qualification_volume_restore as restore
import test_qualification_volume_archive as fixtures

root, mode = Path(sys.argv[1]), sys.argv[2]
assets = root / 'assets'
assets.mkdir()
fixture = fixtures.VolumeArchiveTests()
fixture.setUp()
volume = assets / 'cache.tar'
volume.write_bytes(fixture.raw() if mode != 'bad-inner' else b'not-a-tar')
value = {'schemaVersion': 1, 'images': [], 'volume': {'name': 'caller-existing-cache',
    'archive': 'cache.tar', 'archiveSha256': fixtures.digest(volume.read_bytes())}}
for key, image_id in restore.EXPECTED_IMAGE_IDS.items():
    path = assets / (key + '.tar')
    path.write_bytes(key.encode())
    value['images'].append({'key': key, 'archive': path.name, 'archiveSha256': fixtures.digest(path.read_bytes()), 'imageId': image_id})
(assets / 'manifest.json').write_text(json.dumps(value), encoding='utf-8')
