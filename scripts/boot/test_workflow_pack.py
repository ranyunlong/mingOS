"""Linux real-file controls; fixture bytes are synthetic, never a VM package."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import workflow_pack as verifier
import developer_pack as safety_io

ROLES = ('host', 'applications', 'probe', 'guest', 'launcher', 'kernel', 'runtimeTools')


class PackTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='developer-pack-test-')
        self.addCleanup(self.scratch.cleanup)
        self.pack = Path(self.scratch.name) / 'pack'
        self.pack.mkdir(mode=0o700)
        for name in ('bin', 'meta'):
            (self.pack / name).mkdir(mode=0o755)
        for role in ROLES:
            suffix = '.py' if role == 'launcher' else '.bin'
            (self.pack / 'bin' / (role + suffix)).write_bytes((role + ' synthetic bytes\n').encode())
        (self.pack / 'bin/launcher.py').write_bytes(b'raise AssertionError("pack code executed")\n')
        for name in ('source.json', 'build.json', 'image.json'):
            (self.pack / 'meta' / name).write_text('{"synthetic":true}\n')
        files = {}
        for path in sorted(self.pack.rglob('*')):
            if path.is_file():
                path.chmod(0o644)
                name = path.relative_to(self.pack).as_posix()
                role = path.stem if path.parent.name == 'bin' else 'launcher'
                files[name] = dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                   bytes=path.stat().st_size, mode=0o644, origin=role)
        origins = {}
        for role in ROLES:
            origins[role] = ({'kind': 'tool-image', 'imageId': 'sha256:' + '1' * 64,
                              'receipt': 'meta/image.json'} if role in ('kernel', 'runtimeTools') else
                             {'kind': 'source-build', 'sourceManifest': 'meta/source.json',
                              'buildReceipt': 'meta/build.json'})
        self.value = {'schemaVersion': 1, 'profile': 'developer-workflow-v1',
                      'purpose': 'developer-workflow-input-only', 'algorithm': 'sha256',
                      'files': files, 'directories': {'bin': {'mode': 0o755}, 'meta': {'mode': 0o755}},
                      'origins': origins,
                      'trust': {'purpose': 'fixture-development-only', 'publisherKeyId': 'mingos-dev-2026',
                                'releaseSigning': False},
                      'formalQualification': False, 'finalSystemAccepted': False}
        self.pin = self.seal()

    def seal(self, value=None, raw=None):
        if raw is None:
            raw = (json.dumps(self.value if value is None else value, sort_keys=True) + '\n').encode()
        path = self.pack / 'workflow-manifest.json'
        path.write_bytes(raw)
        path.chmod(0o644)
        return hashlib.sha256(raw).hexdigest()

    def rejected(self, pattern, *, pin=None):
        with self.assertRaisesRegex(ValueError, pattern):
            verifier.verify_pack(self.pack, self.pin if pin is None else pin)

    def test_valid_complete_fixture(self):
        result = verifier.verify_pack(self.pack, self.pin)
        self.assertEqual(result['status'], 'workflow-pack-bytes-verified')
        self.assertEqual(result['manifestSha256'], self.pin)
        self.assertEqual(result['fileCount'], len(self.value['files']))
        self.assertEqual(result['directoryCount'], 2)
        for name in ('formalQualification', 'finalSystemAccepted', 'guestBooted',
                     'sourceBuildReplayed', 'publisherAuthenticated'):
            self.assertIs(result[name], False)
        self.assertEqual(result['trustPurpose'], 'fixture-development-only')
        self.assertEqual(set(result['origins']), set(ROLES))

    def test_no_writes_processes_or_pack_code_execution(self):
        before = {p.relative_to(self.pack).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns,
                  p.stat().st_ctime_ns, stat.S_IMODE(p.stat().st_mode))
                  for p in self.pack.rglob('*') if p.is_file()}
        original_open = os.open
        def guarded_open(path, flags, *args, **kwargs):
            self.assertEqual(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), 0)
            return original_open(path, flags, *args, **kwargs)
        with mock.patch.object(os, 'open', side_effect=guarded_open), \
             mock.patch.object(subprocess, 'Popen', side_effect=AssertionError('process started')), \
             mock.patch.object(os, 'system', side_effect=AssertionError('shell started')):
            result = verifier.verify_pack(self.pack, self.pin)
        self.assertEqual(result['status'], 'workflow-pack-bytes-verified')
        after = {p.relative_to(self.pack).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns,
                 p.stat().st_ctime_ns, stat.S_IMODE(p.stat().st_mode))
                 for p in self.pack.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_wrong_external_pin(self): self.rejected('manifest SHA256', pin='0' * 64)
    def test_uppercase_pin(self): self.rejected('lowercase.*SHA256', pin='A' * 64)
    def test_missing_pin(self): self.rejected('lowercase.*SHA256', pin='')
    def test_duplicate_json_key(self):
        self.pin = self.seal(raw=b'{"schemaVersion":1,"schemaVersion":1}')
        self.rejected('duplicate JSON key')
    def test_nonfinite_json(self):
        self.pin = self.seal(raw=b'{"value":NaN}')
        self.rejected('non-finite')

    def test_closed_schema_and_flags(self):
        for label, change, pattern in (
            ('unknown', lambda v: v.update(unrecognized=True), 'schema'),
            ('wrong profile', lambda v: v.update(profile='persistent-uninstall-linux-portable-export'), 'profile'),
            ('boolean version', lambda v: v.update(schemaVersion=True), 'schema'),
            ('formal', lambda v: v.update(formalQualification=True), 'flags'),
            ('final', lambda v: v.update(finalSystemAccepted=True), 'flags'),
            ('numeric false', lambda v: v.update(formalQualification=0), 'flags'),
            ('release trust', lambda v: v['trust'].update(purpose='production-release'), 'fixture'),
            ('release signing', lambda v: v['trust'].update(releaseSigning=True), 'fixture'),
            ('unknown trust', lambda v: v['trust'].update(privateKey='not allowed'), 'fixture')):
            with self.subTest(label=label):
                value = copy.deepcopy(self.value); change(value)
                self.rejected(pattern, pin=self.seal(value))

    def test_closed_origin_roles_and_reference_bindings(self):
        for label, change in (
            ('missing role', lambda v: v['origins'].pop('host')),
            ('extra role', lambda v: v['origins'].update(extra=v['origins']['host'])),
            ('image identity', lambda v: v['origins']['kernel'].update(imageId='mutable:latest')),
            ('kind', lambda v: v['origins']['host'].update(kind='production-authority')),
            ('unsealed reference', lambda v: v['origins']['host'].update(buildReceipt='absent.json')),
            ('traversal reference', lambda v: v['origins']['host'].update(buildReceipt='../receipt')),
            ('unknown origin field', lambda v: v['origins']['host'].update(approved=True)),
            ('unknown file origin', lambda v: v['files']['bin/host.bin'].update(origin='unknown')),
            ('unused role', lambda v: v['files']['bin/host.bin'].update(origin='launcher'))):
            with self.subTest(label=label):
                value=copy.deepcopy(self.value);change(value)
                self.rejected('origin|path', pin=self.seal(value))

    def test_noncanonical_member_names(self):
        for name in ('../escape', '/absolute', 'bin/../x', 'bin//x', './x', 'bin\\x', 'x:y', 'x\nname'):
            with self.subTest(name=name):
                value=copy.deepcopy(self.value)
                value['files'][name]=value['files'].pop('bin/host.bin')
                self.rejected('path', pin=self.seal(value))

    def test_record_types_and_modes(self):
        for field, value in (('bytes', True), ('bytes', -1), ('mode', True), ('mode', 0o666),
                             ('sha256', 'f'*63), ('origin', 5)):
            with self.subTest(field=field,value=value):
                changed=copy.deepcopy(self.value);changed['files']['bin/host.bin'][field]=value
                self.rejected('record|origin', pin=self.seal(changed))

    def test_payload_bytes_tampered(self):
        (self.pack/'bin/host.bin').write_bytes(b'wrong')
        self.rejected('file inventory')
    def test_payload_missing(self):
        (self.pack/'bin/host.bin').unlink();self.rejected('file inventory')
    def test_extra_regular_member(self):
        (self.pack/'extra').write_bytes(b'x');self.rejected('file inventory')
    def test_extra_directory(self):
        (self.pack/'extra-dir').mkdir();self.rejected('directory inventory')
    def test_unlisted_empty_directory_parent(self):
        (self.pack/'bin/empty').mkdir();self.rejected('directory inventory')
    def test_file_mode_differs(self):
        (self.pack/'bin/host.bin').chmod(0o600);self.rejected('file inventory')
    def test_directory_mode_differs(self):
        (self.pack/'bin').chmod(0o700);self.rejected('directory inventory')
    def test_symlink_file(self):
        target=self.pack/'bin/host.bin';target.unlink();target.symlink_to('../meta/source.json')
        self.rejected('regular|link')
    def test_symlink_directory(self):
        (self.pack/'linked').symlink_to(self.pack/'bin',target_is_directory=True)
        self.rejected('regular|link')
    def test_hardlink(self):
        os.link(self.pack/'bin/host.bin',Path(self.scratch.name)/'alias');self.rejected('single-link')
    def test_manifest_hardlink(self):
        os.link(self.pack/'workflow-manifest.json',Path(self.scratch.name)/'alias');self.rejected('single-link')
    def test_fifo_nonblocking_refusal(self):
        os.mkfifo(self.pack/'fifo');self.rejected('regular|link')
    def test_socket_refusal(self):
        stream=socket.socket(socket.AF_UNIX);self.addCleanup(stream.close)
        stream.bind(str(self.pack/'socket'));self.rejected('regular|link')
    def test_file_byte_limit(self):
        with mock.patch.object(safety_io, 'MAX_FILE', 4096):
            with (self.pack/'bin/host.bin').open('wb') as stream:stream.truncate(4097)
            self.rejected('byte limit')

    def test_total_byte_limit(self):
        with mock.patch.object(safety_io, 'MAX_TOTAL', 1):
            self.rejected('total byte limit')

    def sparse_member(self, name, size):
        path = self.pack / name
        with path.open('wb') as stream:
            stream.truncate(size)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        self.value['files'][name].update(sha256=digest, bytes=size)

    def verify_streaming(self):
        self.pin = self.seal()
        original = os.read
        def bounded(fd, size):
            self.assertLessEqual(size, 1024**2)
            return original(fd, size)
        with mock.patch.object(os, 'read', side_effect=bounded):
            self.assertEqual(verifier.verify_pack(self.pack, self.pin)['status'],
                             'workflow-pack-bytes-verified')

    def test_actual_sparse_file_larger_than_65_mib(self):
        self.sparse_member('bin/host.bin', 66 * 1024**2)
        self.verify_streaming()

    def test_actual_total_larger_than_512_mib(self):
        self.sparse_member('bin/host.bin', 257 * 1024**2)
        self.sparse_member('bin/guest.bin', 257 * 1024**2)
        self.verify_streaming()
    def test_manifest_byte_limit(self):
        raw=b' '* (1024**2+1);self.pin=self.seal(raw=raw);self.rejected('byte limit')
    def test_manifest_mode_is_closed(self):
        (self.pack/'workflow-manifest.json').chmod(0o600);self.rejected('manifest mode')
    def test_excessive_json_nesting_is_value_error(self):
        self.pin=self.seal(raw=b'['*1100+b'0'+b']'*1100)
        try:verifier.verify_pack(self.pack,self.pin)
        except Exception as error:
            self.assertIsInstance(error,ValueError)
            self.assertRegex(str(error),'JSON|nesting')
        else:self.fail('accepted excessive JSON nesting')
    def test_descriptor_count_preserved_on_success_and_failure(self):
        before=len(os.listdir('/proc/self/fd'))
        for _ in range(4):
            verifier.verify_pack(self.pack,self.pin)
            self.rejected('manifest SHA256',pin='0'*64)
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)
    def test_ancestor_symlink(self):
        alias=Path(self.scratch.name)/'alias';alias.symlink_to(self.pack,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'canonical|directory|link'):
            verifier.verify_pack(alias,self.pin)

    def test_file_mutation_during_read(self):
        target=self.pack/'bin/host.bin';inode=target.stat().st_ino;original=os.read;changed=False
        def mutate(fd,size):
            nonlocal changed
            data=original(fd,size)
            if not changed and os.fstat(fd).st_ino==inode:
                changed=True;target.write_bytes(b'changed while being read')
            return data
        with mock.patch.object(os,'read',side_effect=mutate):self.rejected('changed')
        self.assertTrue(changed)

    def test_directory_mutation_during_scan(self):
        original=os.listdir;changed=False;inode=self.pack.stat().st_ino
        def mutate(fd):
            nonlocal changed
            names=original(fd)
            if not changed and os.fstat(fd).st_ino==inode:
                changed=True;(self.pack/'late-member').write_bytes(b'x')
            return names
        with mock.patch.object(os,'listdir',side_effect=mutate):self.rejected('changed')
        self.assertTrue(changed)

    def test_hashed_payload_mutated_after_inventory(self):
        original = verifier.inventory
        target = self.pack / 'bin/host.bin'
        before = verifier.stamp(self.pack.stat())
        def mutate(fd):
            result = original(fd)
            target.write_bytes(b'x' * target.stat().st_size)
            self.assertEqual(verifier.stamp(self.pack.stat()), before)
            return result
        with mock.patch.object(verifier, 'inventory', side_effect=mutate):
            self.rejected('changed|stamp|identity')

    def test_hashed_payload_replaced_after_inventory(self):
        original = verifier.inventory
        target = self.pack / 'bin/host.bin'
        def mutate(fd):
            result = original(fd)
            replacement = self.pack / 'bin/replacement'
            replacement.write_bytes(target.read_bytes())
            replacement.chmod(0o644)
            replacement.replace(target)
            return result
        with mock.patch.object(verifier, 'inventory', side_effect=mutate):
            self.rejected('changed|stamp|identity')

    def test_scanned_nested_membership_mutated_after_inventory(self):
        original = verifier.inventory
        before = verifier.stamp(self.pack.stat())
        def mutate(fd):
            result = original(fd)
            (self.pack / 'bin/late-member').write_bytes(b'x')
            self.assertEqual(verifier.stamp(self.pack.stat()), before)
            return result
        with mock.patch.object(verifier, 'inventory', side_effect=mutate):
            self.rejected('changed|stamp|identity|member')

    def test_hashed_payload_mutated_while_later_file_reads(self):
        original = os.read
        later_inode = (self.pack / 'meta/source.json').stat().st_ino
        target = self.pack / 'bin/host.bin'
        changed = False
        def mutate(fd, size):
            nonlocal changed
            data = original(fd, size)
            if not changed and os.fstat(fd).st_ino == later_inode:
                changed = True
                target.write_bytes(b'x' * target.stat().st_size)
            return data
        with mock.patch.object(os, 'read', side_effect=mutate):
            self.rejected('changed|stamp|identity')
        self.assertTrue(changed)

    def test_cli_unrelated_cwd_and_no_boot_actions(self):
        cli=Path(verifier.__file__).resolve()
        command=[sys.executable,'-I','-B',str(cli),'verify','--pack',str(self.pack),'--manifest-sha256',self.pin]
        result=subprocess.run(command,cwd=self.scratch.name,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        observed=json.loads(result.stdout or '{}')
        self.assertEqual(observed.get('status'),'workflow-pack-bytes-verified')
        self.assertEqual(observed.get('manifestSha256'),self.pin)
        for action in ('init','start','boot','inspect'):
            bad=subprocess.run(command[:4]+[action]+command[5:],cwd=self.scratch.name,capture_output=True,timeout=5)
            self.assertNotEqual(bad.returncode,0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
