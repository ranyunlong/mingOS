"""Public S1 controls; statvfs adapters are controls, not real mount evidence."""
import copy
import importlib.util
import io
import os
import pathlib
import subprocess
import sys
import tarfile
import unittest
from unittest import mock

import current_source as c
import test_linux_handoff as fixtures


class PublicAdmission(unittest.TestCase):
    def test_public_module_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('import_handoff'))

    def test_invalid_pins_and_relative_paths(self):
        import import_handoff as api
        for function in (api.import_restore, api.reopen):
            with self.assertRaises(ValueError):
                function('/missing', 'invalid', '0' * 64, '0' * 64, '/missing')
            with self.assertRaises(ValueError):
                function('relative', '0' * 64, '0' * 64, '0' * 64, '/missing')
        with self.assertRaises(ValueError):
            api.restore('/missing', '0' * 64, 'invalid', '/missing')

    def test_cli_requires_explicit_arguments(self):
        result = subprocess.run([sys.executable, '-B', str(pathlib.Path(__file__).with_name('import_handoff.py')),
                                 'import-restore'], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'--archive', result.stderr)


@unittest.skipUnless(sys.platform == 'linux', 'Linux filesystem controls run in retained Tools container')
class ImportControls(fixtures.LinuxHandoff):
    def setUp(self):
        super().setUp()
        repo = self.base / 'repo'
        (repo / 'bin').mkdir()
        (repo / 'bin').chmod(0o755)
        self.write(repo / 'bin/run', b'#!/bin/sh\nexit 0\n')
        (repo / 'bin/run').chmod(0o755)
        index = {'LICENSE': {'mode': 0o100644, 'blob': '2' * 40},
                 'bin/run': {'mode': 0o100755, 'blob': '3' * 40}}
        raw = c.encoded(c.make_manifest(repo, 'a' * 40, index, []))
        c.export(repo, raw, c.sha(raw), self.base / 'source.tar', self.base / 'descriptor.json')
        incoming = self.root / 'current-source'
        self.write(incoming / 'source-inventory.json', raw)
        self.write(incoming / 'source.tar', (self.base / 'source.tar').read_bytes())
        self.write(incoming / 'source-descriptor.json', (self.base / 'descriptor.json').read_bytes())
        self.source_pin = c.sha((incoming / 'source-descriptor.json').read_bytes())
        self.raw = c.encoded(fixtures.h.make(self.root, self.runtime_pin, self.source_pin))
        self.write(self.root / 'handoff-manifest.json', self.raw)
        self.pin = c.sha(self.raw)
        import import_handoff
        self.api = import_handoff
        self.handoff_pin = self.pin
        self.archive = self.base / 'handoff.tar'
        with tarfile.open(self.archive, 'w', format=tarfile.PAX_FORMAT) as archive:
            archive.add(self.root, arcname='delivery')
        self.archive_pin = c.sha(self.archive.read_bytes())

    def rewrite(self, change):
        with tarfile.open(self.archive, 'r:') as archive:
            entries = [(copy.copy(m), archive.extractfile(m).read() if m.isreg() else None)
                       for m in archive]
        entries = change(entries)
        output = self.base / ('changed-' + str(len(list(self.base.glob('changed-*')))) + '.tar')
        with tarfile.open(output, 'w', format=tarfile.PAX_FORMAT) as archive:
            for member, raw in entries:
                archive.addfile(member, io.BytesIO(raw) if raw is not None else None)
        return output, c.sha(output.read_bytes())

    def imported(self):
        output = self.base / 'output'
        result = self.api.import_restore(self.archive, self.archive_pin, self.handoff_pin,
                                         self.source_pin, output)
        return output, result

    def reopen(self, output):
        # This adapter tests admission branches only. Real RO mount evidence is separate.
        with mock.patch.object(self.api, '_readonly', return_value=None):
            return self.api.reopen(self.archive, self.archive_pin, self.handoff_pin,
                                   self.source_pin, output)

    def test_fixture_is_genuine(self):
        self.assertEqual(self.api.h.verify(self.root, self.raw, self.pin)['status'],
                         'complete-workflow-sibling-integrity-verified')

    def test_standalone_inventory_and_descriptor_binding(self):
        for kind in ('standalone', 'descriptor'):
            with self.subTest(kind=kind):
                path = self.root / 'current-source' / ('source-inventory.json' if kind == 'standalone'
                                                      else 'source-descriptor.json')
                original = path.read_bytes()
                if kind == 'standalone':
                    path.write_bytes(b'{}\n')
                    source_pin = self.source_pin
                else:
                    descriptor = c.strict.strict_json(original)
                    descriptor['fileCount'] += 1
                    path.write_bytes(c.encoded(descriptor))
                    source_pin = c.sha(path.read_bytes())
                value = self.api.h.make(self.root, self.runtime_pin, source_pin)
                raw = c.encoded(value)
                (self.root / 'handoff-manifest.json').write_bytes(raw)
                archive = self.base / (kind + '.tar')
                with tarfile.open(archive, 'w', format=tarfile.PAX_FORMAT) as output:
                    output.add(self.root, arcname='delivery')
                destination = self.base / ('bad-' + kind)
                with self.assertRaises(ValueError):
                    self.api.import_restore(archive, c.sha(archive.read_bytes()), c.sha(raw), source_pin, destination)
                self.assertFalse((destination / 'import-restore-result.json').exists())
                path.write_bytes(original)
                (self.root / 'handoff-manifest.json').write_bytes(self.raw)

    def test_import_and_reopen_exact_receipt(self):
        output, result = self.imported()
        self.assertEqual(set(p.name for p in output.iterdir()),
                         {'delivery', 'source-repo', 'import-restore-result.json'})
        self.assertEqual(result['status'], 'handoff-imported-and-current-source-restored')
        self.assertEqual(set(result), {'status', 'archiveSha256', 'handoffSha256',
                                     'sourceDescriptorSha256', 'importVerification', 'sourceRestore', *c.FLAGS})
        self.assertEqual((output / 'import-restore-result.json').read_bytes(), c.encoded(result))
        self.assertEqual(result['importVerification'], self.api.h.verify(self.root, self.raw, self.pin))
        result = self.reopen(output)
        self.assertEqual(set(result), {'status', 'archiveSha256', 'handoffSha256', 'sourceDescriptorSha256', *c.FLAGS})
        self.assertEqual(result['status'], 'handoff-and-source-readonly-reopened')

    def test_restore_refuses_existing_destination(self):
        destination = self.base / 'existing'
        destination.mkdir()
        marker = destination / 'keep'
        marker.write_bytes(b'original')
        with self.assertRaises((ValueError, FileExistsError)):
            self.api.restore(self.archive, self.archive_pin, self.pin, destination)
        self.assertEqual(marker.read_bytes(), b'original')

    def test_three_wrong_pins(self):
        for index in range(3):
            pins = [self.archive_pin, self.pin, self.source_pin]
            pins[index] = '0' * 64
            output = self.base / ('wrong-' + str(index))
            with self.assertRaises(ValueError):
                self.api.import_restore(self.archive, *pins, output)
            self.assertFalse((output / 'import-restore-result.json').exists())

    def test_partial_failure_cannot_be_retried(self):
        def corrupt(entries):
            for index, (member, raw) in enumerate(entries):
                if member.name.endswith('/receipts/fixture.json'):
                    entries[index] = (member, b'!!!\n')
            return entries
        archive, pin = self.rewrite(corrupt)
        output = self.base / 'partial'
        with self.assertRaises(ValueError):
            self.api.import_restore(archive, pin, self.pin, self.source_pin, output)
        self.assertTrue(output.exists())
        self.assertFalse((output / 'import-restore-result.json').exists())
        with self.assertRaises((ValueError, FileExistsError)):
            self.api.import_restore(self.archive, self.archive_pin, self.pin, self.source_pin, output)

    def test_member_controls(self):
        cases = ['duplicate', 'missing', 'extra', 'traversal', 'symlink', 'hardlink',
                 'fifo', 'sparse', 'pax', 'size', 'mode', 'directory-size', 'root-file']
        for case in cases:
            def change(entries):
                index = next(i for i, (m, _) in enumerate(entries) if m.name.endswith('/receipts/fixture.json'))
                member, raw = entries[index]
                if case == 'duplicate': entries.append((copy.copy(member), raw))
                elif case == 'missing': entries.pop(index)
                elif case == 'extra':
                    member.name = 'delivery/extra'; entries.append((copy.copy(member), raw))
                elif case == 'traversal': member.name = 'delivery/../escape'
                elif case in ('symlink', 'hardlink', 'fifo', 'sparse'):
                    member.type = {'symlink': tarfile.SYMTYPE, 'hardlink': tarfile.LNKTYPE,
                                   'fifo': tarfile.FIFOTYPE, 'sparse': tarfile.GNUTYPE_SPARSE}[case]
                    member.linkname = 'delivery/handoff-manifest.json';member.size = 0;entries[index] = (member, None)
                elif case == 'pax': member.pax_headers['comment'] = 'unknown'
                elif case == 'size': member.size += 1;entries[index] = (member, raw + b'x')
                elif case == 'mode': member.mode = 0o777
                elif case == 'directory-size':
                    member, _ = entries[0];member.size = 1;entries[0] = (member, b'x')
                elif case == 'root-file':
                    member, _ = entries[0];member.type = tarfile.REGTYPE;member.size = 0;entries[0] = (member, b'')
                return entries
            with self.subTest(case=case):
                archive, pin = self.rewrite(change)
                with self.assertRaises((ValueError, tarfile.TarError)):
                    self.api.restore(archive, pin, self.pin, self.base / ('bad-' + case))

    def test_caps(self):
        for constant in ('MAX_MEMBERS', 'MAX_FILE', 'MAX_MANIFEST', 'MAX_TOTAL'):
            with self.subTest(constant=constant), mock.patch.object(self.api, constant, 1):
                with self.assertRaises(ValueError):
                    self.api.restore(self.archive, self.archive_pin, self.pin, self.base / constant)

    def test_symlink_parent_and_archive(self):
        link = self.base / 'link';link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.api.import_restore(self.archive, self.archive_pin, self.pin, self.source_pin, link / 'out')
        archive_link = self.base / 'link.tar';archive_link.symlink_to(self.archive)
        with self.assertRaises((ValueError, OSError)):
            self.api.restore(archive_link, self.archive_pin, self.pin, self.base / 'out')

    def test_archive_mutation(self):
        original = self.api.h.verify
        def mutate(*args):
            result = original(*args)
            with self.archive.open('r+b') as output:
                output.seek(-1, 2);output.write(b'x')
            return result
        with mock.patch.object(self.api.h, 'verify', side_effect=mutate):
            with self.assertRaises(ValueError):
                self.api.restore(self.archive, self.archive_pin, self.pin, self.base / 'out')

    def test_reopen_negative_controls(self):
        output, receipt = self.imported()
        for directory in (output, output / 'delivery', output / 'source-repo'):
            extra = directory / 'extra';extra.write_bytes(b'extra')
            with self.assertRaises(ValueError): self.reopen(output)
            extra.unlink()
        source = output / 'source-repo/LICENSE';raw = source.read_bytes()
        source.write_bytes(b'changed')
        with self.assertRaises(ValueError): self.reopen(output)
        source.write_bytes(raw);source.chmod(0o755)
        with self.assertRaises(ValueError): self.reopen(output)
        source.chmod(0o644)
        path = output / 'import-restore-result.json'
        for changed in ({**receipt, 'extra': False}, {**receipt, 'formalQualification': True},
                        {**receipt, 'sourceRestore': {**receipt['sourceRestore'], 'fileCount': 9}}):
            path.write_bytes(c.encoded(changed))
            with self.assertRaises(ValueError): self.reopen(output)
        path.write_bytes(c.encoded(receipt) + b' ')
        with self.assertRaises(ValueError): self.reopen(output)

    def test_source_symlink_and_directory_mode_controls(self):
        output, _ = self.imported()
        source = output / 'source-repo/LICENSE'
        raw = source.read_bytes()
        source.unlink();source.symlink_to(self.root / 'licenses/source/LICENSE')
        with self.assertRaises((ValueError, OSError)): self.reopen(output)
        source.unlink();source.write_bytes(raw);source.chmod(0o644)
        directory = output / 'source-repo/bin';directory.chmod(0o700)
        with self.assertRaises(ValueError): self.reopen(output)
        directory.chmod(0o755)
        directory = output / 'source-repo/extra-directory';directory.mkdir()
        with self.assertRaises(ValueError): self.reopen(output)

    def test_reopen_requires_readonly_filesystems(self):
        output, _ = self.imported()
        with self.assertRaises(ValueError):
            self.api.reopen(self.archive, self.archive_pin, self.pin, self.source_pin, output)
        for rejected in range(3):
            flags = [os.ST_RDONLY] * 3;flags[rejected] = 0
            with mock.patch.object(self.api.os, 'statvfs', side_effect=[mock.Mock(f_flag=f) for f in flags]):
                with self.assertRaises(ValueError):
                    self.api.reopen(self.archive, self.archive_pin, self.pin, self.source_pin, output)


if __name__ == '__main__':
    unittest.main()
