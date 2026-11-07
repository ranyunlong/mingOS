"""Real tar and subprocess controls; no test calls a Docker daemon."""
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

try:
    import qualification_image_archive as archive
except ModuleNotFoundError:
    archive = None


def encoded(value):
    return json.dumps(value, separators=(',', ':')).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ArchiveControls(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(archive, 'fail-closed Docker save archive parser must exist')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode='w') as output:
            info = tarfile.TarInfo('hello.txt')
            info.size = 5
            output.addfile(info, io.BytesIO(b'hello'))
        self.layer = layer.getvalue()
        self.config = encoded({'architecture': 'amd64', 'os': 'linux',
            'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + digest(self.layer)]}})
        self.image_id = 'sha256:' + digest(self.config)
        self.config_name = digest(self.config) + '.json'
        self.layer_name = 'a' * 64 + '/layer.tar'
        self.record = {'Config': self.config_name, 'RepoTags': ['test.example/existing:stable'], 'Layers': [self.layer_name]}
        self.members = [('manifest.json', encoded([self.record])),
                        (self.config_name, self.config), (self.layer_name, self.layer)]

    def write_tar(self, members=None, name='image.tar', compression=''):
        path = self.root / name
        with tarfile.open(path, 'w' + compression) as output:
            for item in self.members if members is None else members:
                key, data, *kind = item
                info = tarfile.TarInfo(key)
                info.size = len(data)
                if kind:
                    info.type = kind[0]
                    info.linkname = '../outside'
                output.addfile(info, io.BytesIO(data))
        return path

    def inspect(self, path, expected=None):
        with path.open('rb') as source:
            return archive.verify_archive(source, expected or self.image_id)

    def test_legacy_archive_binds_config_and_uncompressed_layer(self):
        value = self.inspect(self.write_tar())
        self.assertEqual(value['imageId'], self.image_id)
        self.assertEqual(value['diffIds'], ['sha256:' + digest(self.layer)])

    def test_blob_paths_and_gzip_layer_bind_uncompressed_diff_id(self):
        compressed = gzip.compress(self.layer, mtime=0)
        config = 'blobs/sha256/' + digest(self.config)
        layer = 'blobs/sha256/' + digest(compressed)
        record = dict(self.record, Config=config, Layers=[layer])
        value = self.inspect(self.write_tar([('manifest.json', encoded([record])), (config, self.config), (layer, compressed)], compression=':gz'))
        self.assertEqual(value['diffIds'], ['sha256:' + digest(self.layer)])

    def test_oci_blob_index_binds_unique_manifest_config_and_layers(self):
        config = 'blobs/sha256/' + digest(self.config)
        layer = 'blobs/sha256/' + digest(self.layer)
        image_manifest = encoded({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json',
            'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': self.image_id, 'size': len(self.config)},
            'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar', 'digest': 'sha256:' + digest(self.layer), 'size': len(self.layer)}]})
        descriptor = {'mediaType': 'application/vnd.oci.image.manifest.v1+json', 'digest': 'sha256:' + digest(image_manifest), 'size': len(image_manifest)}
        members = [('manifest.json', encoded([dict(self.record, Config=config, Layers=[layer])])),
            (config, self.config), (layer, self.layer), ('oci-layout', encoded({'imageLayoutVersion': '1.0.0'})),
            ('index.json', encoded({'schemaVersion': 2, 'manifests': [descriptor]})), ('blobs/sha256/' + digest(image_manifest), image_manifest)]
        self.assertEqual(self.inspect(self.write_tar(members))['imageId'], self.image_id)
        members[-2] = ('index.json', encoded({'schemaVersion': 2, 'manifests': [descriptor, descriptor]}))
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.inspect(self.write_tar(members))

    def test_falsy_repository_tag_types_are_rejected(self):
        for tags in ('', 0, False, {}):
            with self.subTest(tags=tags), self.assertRaisesRegex(ValueError, 'tag'):
                self.inspect(self.write_tar([('manifest.json', encoded([dict(self.record, RepoTags=tags)]))] + self.members[1:]))

    def test_nonzero_trailing_archive_is_rejected(self):
        path = self.write_tar()
        with path.open('ab') as stream:
            stream.write(b'extra image data')
        with self.assertRaisesRegex(ValueError, 'trailing'):
            self.inspect(path)

    def test_hidden_gnu_name_and_link_extension_headers_rejected(self):
        for kind in (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'extension'):
                self.inspect(self.write_tar([('././@LongLink', b'manifest.json\0', kind)] + self.members))

    def test_repeated_expanded_name_and_concatenated_tar_rejected(self):
        members = self.members + [('././@LongLink', b'manifest.json\0', tarfile.GNUTYPE_LONGNAME), ('short', encoded([self.record]))]
        with self.assertRaises(ValueError):
            self.inspect(self.write_tar(members))
        path = self.write_tar()
        appended = self.write_tar(name='second.tar').read_bytes()
        with path.open('ab') as stream:
            stream.write(appended)
        with self.assertRaisesRegex(ValueError, 'trailing'):
            self.inspect(path)

    def test_sanitized_tar_has_no_repository_tags_or_extra_members(self):
        source = self.write_tar()
        with source.open('rb') as raw, tempfile.TemporaryFile() as clean:
            verified = archive.verify_archive(raw, self.image_id)
            archive.sanitize_archive(raw, clean, verified)
            clean.seek(0)
            with tarfile.open(fileobj=clean) as result:
                manifest = json.load(result.extractfile('manifest.json'))
                self.assertEqual(manifest[0]['RepoTags'], [])
                self.assertEqual(set(result.getnames()), {'manifest.json', self.config_name, self.layer_name})

    def test_config_digest_cannot_be_replaced_by_manifest_claim(self):
        with self.assertRaisesRegex(ValueError, 'config|image'):
            self.inspect(self.write_tar(), 'sha256:' + '0' * 64)

    def test_changed_layer_is_rejected(self):
        members = self.members[:-1] + [(self.layer_name, self.layer + b'changed')]
        with self.assertRaisesRegex(ValueError, 'diff|layer'):
            self.inspect(self.write_tar(members))

    def test_duplicate_manifest_and_duplicate_json_keys_rejected(self):
        variants = [self.members + [self.members[0]],
            [('manifest.json', b'[{"Config":"x","Config":"y","Layers":[],"RepoTags":[]}]')] + self.members[1:]]
        for members in variants:
            with self.subTest(members=members[0][0]), self.assertRaisesRegex(ValueError, 'duplicate'):
                self.inspect(self.write_tar(members))

    def test_multiple_images_and_unknown_reference_fields_rejected(self):
        for records in ([self.record, self.record], [dict(self.record, ExtraReference='other')]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                self.inspect(self.write_tar([('manifest.json', encoded(records))] + self.members[1:]))

    def test_archive_traversal_absolute_backslash_and_links_rejected(self):
        for name, kind in [('../evil', tarfile.REGTYPE), ('/evil', tarfile.REGTYPE),
                ('C:/evil', tarfile.REGTYPE), ('a\\evil', tarfile.REGTYPE),
                ('symlink', tarfile.SYMTYPE), ('hardlink', tarfile.LNKTYPE), ('device', tarfile.CHRTYPE)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.inspect(self.write_tar(self.members + [(name, b'', kind)]))

    def test_unreferenced_payload_and_repository_refs_rejected(self):
        for extra in [('other.json', b'{}'), ('repositories', encoded({'other': {'tag': 'evil'}}))]:
            with self.subTest(extra=extra[0]), self.assertRaises(ValueError):
                self.inspect(self.write_tar(self.members + [extra]))

    def test_blob_digest_mismatch_rejected_even_with_correct_diff_id(self):
        record = dict(self.record, Layers=['blobs/sha256/' + '0' * 64])
        with self.assertRaisesRegex(ValueError, 'blob|digest'):
            self.inspect(self.write_tar([('manifest.json', encoded([record])), self.members[1], (record['Layers'][0], self.layer)]))

    def test_sanitize_rechecks_source_bytes(self):
        path = self.write_tar()
        with path.open('r+b') as raw, tempfile.TemporaryFile() as clean:
            verified = archive.verify_archive(raw, self.image_id)
            raw.seek(2048)
            raw.write(b'changed')
            raw.flush()
            with self.assertRaisesRegex(ValueError, 'changed|archive'):
                archive.sanitize_archive(raw, clean, verified)

    def manifest(self, paths):
        entries = []
        for key, path in zip(('rust', 'tools', 'openssl'), paths):
            entries.append({'key': key, 'archive': path.name, 'archiveSha256': digest(path.read_bytes()), 'imageId': self.image_id})
        path = self.root / 'manifest.json'
        path.write_bytes(encoded({'schemaVersion': 1, 'images': entries}))
        return path

    def test_all_archives_verified_before_first_docker_call(self):
        paths = [self.write_tar(name=str(i) + '.tar') for i in range(3)]
        paths[-1].write_bytes(b'invalid tar')
        manifest = self.manifest(paths)
        with mock.patch.dict(archive.EXPECTED_IMAGE_IDS, {key: self.image_id for key in ('rust', 'tools', 'openssl')}, clear=True), mock.patch.object(subprocess, 'run') as docker:
            with self.assertRaises(ValueError):
                archive.load_images(self.root, manifest, 'docker')
            docker.assert_not_called()

    def test_load_passes_sanitized_stdin_then_inspects_exact_id(self):
        paths = [self.write_tar(name=str(i) + '.tar') for i in range(3)]
        manifest = self.manifest(paths)
        calls = []
        def docker(command, **kwargs):
            calls.append(command)
            if command[1:3] == ['image', 'load']:
                with tarfile.open(fileobj=kwargs['stdin'], mode='r:') as value:
                    self.assertEqual(json.load(value.extractfile('manifest.json'))[0]['RepoTags'], [])
                return subprocess.CompletedProcess(command, 0, b'Loaded image ID\n', b'')
            self.assertEqual(command, ['docker', 'image', 'inspect', self.image_id])
            return subprocess.CompletedProcess(command, 0, encoded([{'Id': self.image_id}]), b'')
        with mock.patch.dict(archive.EXPECTED_IMAGE_IDS, {key: self.image_id for key in ('rust', 'tools', 'openssl')}, clear=True), mock.patch.object(subprocess, 'run', side_effect=docker):
            value = archive.load_images(self.root, manifest, 'docker')
        self.assertEqual(value['status'], 'image-load-verified')
        self.assertFalse(value['finalSystemAccepted'])
        self.assertEqual(len(calls), 6)
        self.assertTrue(all(command[1:3] in (['image', 'load'], ['image', 'inspect']) for command in calls))

    def test_public_manifest_cannot_override_fixed_ids(self):
        path = self.write_tar()
        with mock.patch.object(subprocess, 'run') as docker:
            with self.assertRaisesRegex(ValueError, 'contract|fixed'):
                archive.load_images(self.root, self.manifest([path] * 3), 'docker')
            docker.assert_not_called()

    def test_load_failure_preserves_first_failure_and_stops(self):
        paths = [self.write_tar(name=str(i) + '.tar') for i in range(3)]
        with mock.patch.dict(archive.EXPECTED_IMAGE_IDS, {key: self.image_id for key in ('rust', 'tools', 'openssl')}, clear=True), mock.patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess(['docker'], 17, b'first failure', b'kept')) as docker:
            with self.assertRaisesRegex(ValueError, '17|first failure'):
                archive.load_images(self.root, self.manifest(paths), 'docker')
            self.assertEqual(docker.call_count, 1)

    def test_wrong_inspect_id_stops_without_cleanup_or_next_load(self):
        paths = [self.write_tar(name=str(i) + '.tar') for i in range(3)]
        responses = [subprocess.CompletedProcess([], 0, b'loaded', b''), subprocess.CompletedProcess([], 0, encoded([{'Id': 'sha256:' + '0' * 64}]), b'')]
        with mock.patch.dict(archive.EXPECTED_IMAGE_IDS, {key: self.image_id for key in ('rust', 'tools', 'openssl')}, clear=True), mock.patch.object(subprocess, 'run', side_effect=responses) as docker:
            with self.assertRaisesRegex(ValueError, 'inspect'):
                archive.load_images(self.root, self.manifest(paths), 'docker')
            self.assertEqual(docker.call_count, 2)

    def test_timeout_keeps_attempted_command_and_partial_output(self):
        paths = [self.write_tar(name=str(i) + '.tar') for i in range(3)]
        timeout = subprocess.TimeoutExpired(['docker', 'image', 'load'], 1800, output=b'partial stdout', stderr=b'partial stderr')
        with mock.patch.dict(archive.EXPECTED_IMAGE_IDS, {key: self.image_id for key in ('rust', 'tools', 'openssl')}, clear=True), mock.patch.object(subprocess, 'run', side_effect=timeout) as docker:
            with self.assertRaises(archive.RestoreError) as error:
                archive.load_images(self.root, self.manifest(paths), 'docker')
            self.assertEqual(docker.call_count, 1)
        self.assertEqual(len(error.exception.receipt['operations']), 1, 'timed-out Docker attempt must retain evidence')
        operation = error.exception.receipt['operations'][0]
        self.assertTrue(operation['timedOut'])
        self.assertEqual(operation['stdout'], 'partial stdout')
        self.assertEqual(operation['stderr'], 'partial stderr')

    def test_real_fake_docker_subprocess_receives_tagless_bytes_in_order(self):
        paths = [self.write_tar(name=str(i) + '.tar') for i in range(3)]
        log = self.root / 'events.jsonl'
        fake = self.root / 'fake_docker.py'
        fake.write_text('import io,json,pathlib,sys,tarfile\n'
            + 'args=sys.argv[1:]\n'
            + 'event={"argv":args}\n'
            + 'if args[:2]==["image","load"]:\n'
            + ' raw=sys.stdin.buffer.read()\n'
            + ' with tarfile.open(fileobj=io.BytesIO(raw)) as source:\n'
            + '  event["manifest"]=json.load(source.extractfile("manifest.json"))\n'
            + ' print("Loaded image ID")\n'
            + 'else: print(json.dumps([{"Id":args[-1]}]))\n'
            + 'with pathlib.Path(' + repr(str(log)) + ').open("a") as output: output.write(json.dumps(event)+"\\n")\n')
        with mock.patch.dict(archive.EXPECTED_IMAGE_IDS, {key: self.image_id for key in ('rust', 'tools', 'openssl')}, clear=True):
            result = archive.load_images(self.root, self.manifest(paths), [sys.executable, str(fake)])
        events = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(result['status'], 'image-load-verified')
        self.assertEqual([event['argv'][1] for event in events], ['load', 'inspect'] * 3)
        self.assertTrue(all(event['manifest'][0]['RepoTags'] == [] for event in events[::2]))


if __name__ == '__main__':
    unittest.main()
