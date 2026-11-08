"""Seal controls. Actual mount policy is a separate, externally mounted control."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

try:
    import source_build_seal as target
except ModuleNotFoundError:
    target = None


@unittest.skipUnless(os.name == 'posix', 'Linux descriptor/mode controls')
class SealTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(target, 'source_build_seal implementation missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'payload'
        self.root.mkdir(mode=0o755)
        (self.root / 'empty').mkdir(mode=0o750)
        (self.root / 'executable').write_bytes(b'#!/bin/sh\nexit 0\n')
        (self.root / 'executable').chmod(0o555)
        self.context = {'generation': 'control-generation', 'sourceSha256': 'a' * 64}
        self.output = self.base / 'export-seal.json'

    def make(self):
        result = target.seal(self.root, context=self.context, output=self.output)
        return result, hashlib.sha256(self.output.read_bytes()).hexdigest()

    def check(self, digest):
        # Content-only internal boundary: it must not claim effective RO replay.
        return target._verify_contents(self.root, self.output, digest)

    def test_complete_canonical_inventory_and_context(self):
        value, digest = self.make()
        self.assertEqual(value['schemaVersion'], 1)
        self.assertEqual(value['kind'], 'p01-s3-payload-seal')
        self.assertEqual(value['context'], self.context)
        self.assertEqual(value['rootMode'], 0o755)
        self.assertEqual(value['directories'], {'empty': {'mode': 0o750}})
        self.assertEqual(value['files'], {'executable': {'mode': 0o555, 'bytes': 17,
            'sha256': hashlib.sha256(b'#!/bin/sh\nexit 0\n').hexdigest()}})
        self.assertEqual(self.output.read_bytes(), (json.dumps(value, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True, allow_nan=False) + '\n').encode())
        self.assertEqual(self.check(digest), value)

    def test_changed_bytes_size_membership_modes_and_context_rejected(self):
        original = (self.root / 'executable').read_bytes()
        _, digest = self.make()
        for change, undo in [
            (lambda: (self.root / 'added').write_bytes(b'x'), lambda: (self.root / 'added').unlink()),
            (lambda: (self.root / 'added-dir').mkdir(), lambda: (self.root / 'added-dir').rmdir()),
            (lambda: (self.root / 'empty').chmod(0o700), lambda: (self.root / 'empty').chmod(0o750)),
            (lambda: (self.root / 'executable').chmod(0o444), lambda: (self.root / 'executable').chmod(0o555)),
            (lambda: self.root.chmod(0o700), lambda: self.root.chmod(0o755)),
        ]:
            with self.subTest(change=change):
                change()
                with self.assertRaises(ValueError): self.check(digest)
                undo()
        (self.root / 'executable').chmod(0o755)
        (self.root / 'executable').write_bytes(original.replace(b'0', b'1'))
        (self.root / 'executable').chmod(0o555)
        with self.assertRaises(ValueError): self.check(digest)
        (self.root / 'executable').chmod(0o755)
        (self.root / 'executable').write_bytes(original + b'x')
        (self.root / 'executable').chmod(0o555)
        with self.assertRaises(ValueError): self.check(digest)
        value = json.loads(self.output.read_bytes())
        value['context']['generation'] = 'other'
        self.output.chmod(0o644)
        self.output.write_text(json.dumps(value))
        with self.assertRaises(ValueError): self.check(digest)

    def test_links_special_and_unsafe_names_rejected(self):
        for name, create in [
            ('link', lambda p: p.symlink_to('executable')),
            ('hard', lambda p: os.link(self.root / 'executable', p)),
            ('fifo', lambda p: os.mkfifo(p)),
            ('unsafe:alias', lambda p: p.write_bytes(b'x')),
            ('back\\slash', lambda p: p.write_bytes(b'x')),
            ('trailing.', lambda p: p.write_bytes(b'x')),
            ('control\nname', lambda p: p.write_bytes(b'x')),
        ]:
            with self.subTest(name=name):
                path = self.root / name
                create(path)
                with self.assertRaises((ValueError, OSError)):
                    target.seal(self.root, context=self.context, output=self.output)
                path.unlink()
                self.assertFalse(self.output.exists())

    def test_symlink_root_and_ancestor_and_output_rejected(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            target.seal(alias, context=self.context, output=self.output)
        ancestor = self.base / 'ancestor'
        ancestor.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            target.seal(ancestor / 'payload', context=self.context, output=self.output)
        self.output.symlink_to(self.base / 'absent')
        with self.assertRaises((ValueError, OSError)):
            target.seal(self.root, context=self.context, output=self.output)

    def test_exclusive_sibling_and_retained_fsync_failure(self):
        with self.assertRaises(ValueError):
            target.seal(self.root, context=self.context, output=self.root / 'seal.json')
        self.make()
        before = self.output.read_bytes()
        with self.assertRaises(FileExistsError): self.make()
        self.assertEqual(self.output.read_bytes(), before)
        other = self.base / 'failed-seal.json'
        with mock.patch.object(target.os, 'fsync', side_effect=OSError('durability control')):
            with self.assertRaises(OSError):
                target.seal(self.root, context=self.context, output=other)
        self.assertTrue(other.is_file(), 'failed partial evidence retained')

    def test_mutating_file_descriptor_rejected(self):
        path = self.root / 'executable'
        path.chmod(0o755)
        real_read = os.read
        changed = False
        def mutating_read(fd, count):
            nonlocal changed
            value = real_read(fd, count)
            if not changed and value == b'#!/bin/sh\nexit 0\n':
                changed = True
                path.write_bytes(b'#!/bin/sh\nexit 1\n')
            return value
        with mock.patch.object(target.os, 'read', side_effect=mutating_read):
            with self.assertRaises(ValueError): self.make()
        self.assertTrue(changed)

    def test_same_byte_identity_mutation_while_publishing_rejected(self):
        real_write = target._write_exclusive
        path = self.root / 'executable'
        def replacing_write(*args):
            real_write(*args)
            path.unlink()
            path.write_bytes(b'#!/bin/sh\nexit 0\n')
            path.chmod(0o555)
        with mock.patch.object(target, '_write_exclusive', side_effect=replacing_write):
            with self.assertRaisesRegex(ValueError, 'changed|mutated'):
                self.make()
        self.assertTrue(self.output.is_file(), 'failure output retained')

    def test_schema_duplicate_keys_types_and_unsafe_inventory_rejected(self):
        value, _ = self.make()
        self.output.chmod(0o644)
        cases = []
        for key, bad in [('schemaVersion', True), ('schemaVersion', 2), ('rootMode', True),
                         ('context', []), ('kind', 'other')]:
            copy = json.loads(json.dumps(value)); copy[key] = bad; cases.append(json.dumps(copy).encode())
        for key, bad in [('mode', True), ('bytes', '17'), ('sha256', 'A' * 64)]:
            copy = json.loads(json.dumps(value)); copy['files']['executable'][key] = bad
            cases.append(json.dumps(copy).encode())
        copy = json.loads(json.dumps(value)); copy['files']['../escape'] = copy['files'].pop('executable')
        cases.append(json.dumps(copy).encode())
        cases.append(b'{"schemaVersion":1,"schemaVersion":1}')
        for raw in cases:
            with self.subTest(raw=raw):
                self.output.write_bytes(raw)
                with self.assertRaises(ValueError): self.check(hashlib.sha256(raw).hexdigest())

    def test_writable_replay_rejected_despite_exact_bytes(self):
        _, digest = self.make()
        self.assertEqual(self.check(digest)['context'], self.context)
        with self.assertRaisesRegex(ValueError, 'payload.*read.only'):
            target.verify(self.root, self.output, digest)


class ActualMountPolicyTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('SOURCE_BUILD_RO_FIXTURE_ROOT'), 'external actual mount fixture required')
    def test_actual_mount_policy(self):
        self.assertIsNotNone(target, 'source_build_seal implementation missing')
        base = Path(os.environ['SOURCE_BUILD_RO_FIXTURE_ROOT'])
        root, seal = base / 'payload', base / 'export-seal.json'
        # The independent fixture pin is supplied by the controller, not recomputed here.
        digest = os.environ['SOURCE_BUILD_RO_SEAL_SHA256']
        expected = os.environ['SOURCE_BUILD_EXPECT_REPLAY']
        flags = {'payload': bool(os.statvfs(root).f_flag & os.ST_RDONLY),
                 'seal': bool(os.statvfs(seal).f_flag & os.ST_RDONLY),
                 'verifier': bool(os.statvfs(Path(target.__file__).parent).f_flag & os.ST_RDONLY)}
        self.assertEqual(flags, {key: key != expected for key in flags})
        if expected == 'payload-member':
            self.assertFalse(os.statvfs(root / 'empty').f_flag & os.ST_RDONLY)
        if expected == 'verifier-file':
            self.assertFalse(os.statvfs(Path(target.__file__)).f_flag & os.ST_RDONLY)
        if expected == 'accepted':
            self.assertEqual(target.verify(root, seal, digest)['context'],
                             {'generation': 'task3-ro-control'})
        else:
            with self.assertRaisesRegex(ValueError, expected + '.*read.only'):
                target.verify(root, seal, digest)


if __name__ == '__main__': unittest.main()
