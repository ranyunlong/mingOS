"""Freeze controls use synthetic inputs, never compiler or guest evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import linux_host_freeze as freeze


class ColdProfileFreezeControls(unittest.TestCase):
    def test_prepare_passes_explicit_profile_to_real_freezer(self):
        root = Path('/prep')
        args = (root, Path('/repo'), Path('/cargo'), Path('/packages'), Path('/image'), Path('/capacity'), Path('/node'), '0' * 64)
        with mock.patch.object(freeze.q, 'checked_directory', return_value=root), \
             mock.patch.object(freeze.q, 'write_json'), mock.patch.object(freeze.q, 'stable_hash', return_value={'sha256': 'f' * 64}), \
             mock.patch.object(freeze, '_prepare', return_value={'profile': 'persistent-uninstall-linux-host'}) as prepare:
            result = freeze.prepare(*args, profile='persistent-uninstall-linux-host')
        self.assertEqual(prepare.call_args.args[-1], 'persistent-uninstall-linux-host')
        self.assertEqual(result['profile'], 'persistent-uninstall-linux-host')

    def test_uninstall_package_paths_are_exact_and_distinct(self):
        profile = 'persistent-uninstall-linux-host'
        expected = {key: key for key in ('read-file', 'image-viewer', 'text-entry-demo', 'uninstall')}
        self.assertEqual(freeze.package_paths_for(profile), expected)
        for paths in (dict(expected, old='old'), {key: 'same' for key in expected}, {}):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                freeze.package_paths_for(profile, paths)
        with self.assertRaises(ValueError):
            freeze.package_paths_for('unknown')
        self.assertEqual(tuple(freeze.package_paths_for()), freeze.LABELS)


class FreezeControls(unittest.TestCase):
    def test_failed_prepare_attempt_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = (root, root, root, root, root / 'image.json', root / 'capacity.json', root / 'node', '0' * 64)
            with self.assertRaises(ValueError):
                freeze.prepare(*args)
            self.assertEqual(freeze.q.read_json(root / 'prepare-result.json')['status'], 'failed')
            with self.assertRaises(FileExistsError):
                freeze.prepare(*args)

    def test_capacity_budget_covers_reserve_and_both_storage_layers(self):
        budget = freeze.capacity_budget()
        self.assertEqual(budget['reserveBytes'], 2 * 1024 ** 3)
        required = sum(budget.values())
        observation = {'hostAvailableBytes': required, 'linuxAvailableBytes': required,
                       'backingDiskPath': 'C:\\Docker\\docker_data.vhdx', 'hostFilesystem': 'NTFS'}
        freeze.validate_capacity(observation)
        for key in ('hostAvailableBytes', 'linuxAvailableBytes'):
            changed = dict(observation, **{key: required - 1})
            with self.subTest(key=key), self.assertRaises(ValueError):
                freeze.validate_capacity(changed)
        with self.assertRaises(ValueError):
            freeze.validate_capacity(dict(observation, backingDiskPath='unknown'))

    def test_image_receipt_requires_one_exact_image(self):
        image = {'Id': freeze.RUST_IMAGE, 'RootFS': {'Layers': ['sha256:' + 'a' * 64]}}
        freeze.validate_image_receipt([image], freeze.RUST_IMAGE)
        for value in ([], [image, image], [{'Id': 'sha256:' + '0' * 64}], [{}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                freeze.validate_image_receipt(value, freeze.RUST_IMAGE)

    def test_package_path_is_relative_canonical_and_within_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'old').mkdir()
            self.assertEqual(freeze.package_path(root, 'old'), root / 'old')
            for name in ('/old', '../old', './old', 'old/../old', 'old//x', ''):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    freeze.package_path(root, name)
            (root / 'alias').symlink_to(root / 'old', target_is_directory=True)
            with self.assertRaises(ValueError):
                freeze.package_path(root, 'alias')

    def test_storage_reopen_must_bind_the_same_real_objects(self):
        original = {'schemaVersion': 1, 'root': {'ino': 1}, 'marker': {'sha256': 'a'},
                    'lease': {'ino': 2}, 'lockCompetitorBlocked': True, 'lockAvailableAfterRelease': True}
        freeze.validate_reopen(original, dict(original))
        for key in ('root', 'marker', 'lease'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                freeze.validate_reopen(original, dict(original, **{key: {}}))
        with self.assertRaises(ValueError):
            freeze.validate_reopen(original, dict(original, lockCompetitorBlocked=False))


if __name__ == '__main__':
    unittest.main()
