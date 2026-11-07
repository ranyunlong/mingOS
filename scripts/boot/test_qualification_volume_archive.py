"""Byte-only adversarial controls; never invokes Docker or extracts."""
import copy
import hashlib
import io
import json
import tarfile
import unittest
from unittest import mock

import qualification_volume_archive as archive


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, separators=(',', ':')).encode()


def member(name, data=b'', kind=tarfile.REGTYPE, mode=None, uid=65534, gid=65534):
    item = tarfile.TarInfo(name)
    item.type = kind
    item.mode = mode if mode is not None else (0o700 if kind == tarfile.DIRTYPE else 0o600)
    item.uid, item.gid, item.size, item.mtime = uid, gid, len(data), 0
    return item, data


def pack(items, format=tarfile.USTAR_FORMAT):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=format) as writer:
        for item, raw in items:
            writer.addfile(item, io.BytesIO(raw))
    return output.getvalue()


def patch_header(raw, field, value, offset=0, fix_checksum=True):
    result = bytearray(raw)
    result[offset + field:offset + field + len(value)] = value
    if fix_checksum:
        result[offset + 148:offset + 156] = b' ' * 8
        checksum = sum(result[offset:offset + 512])
        result[offset + 148:offset + 156] = ('%06o\0 ' % checksum).encode()
    return bytes(result)


class VolumeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'schemaVersion': 1, 'uid': 65534, 'gid': 65534, 'entries': [
            {'path': 'cache', 'type': 'directory', 'uid': 65534, 'gid': 65534, 'mode': 0o700},
            {'path': 'cache/data', 'type': 'file', 'uid': 65534, 'gid': 65534, 'mode': 0o600,
             'size': 5, 'sha256': digest(b'hello'), 'nlink': 1}]}
        self.payload = [member('cache/', kind=tarfile.DIRTYPE), member('cache/data', b'hello')]

    def raw(self, manifest=None, payload=None, manifest_bytes=None):
        raw_manifest = encoded(self.manifest if manifest is None else manifest) if manifest_bytes is None else manifest_bytes
        return pack([member(archive.MANIFEST_NAME, raw_manifest)] + (self.payload if payload is None else payload))

    def verify(self, raw=None):
        raw = self.raw() if raw is None else raw
        return archive.verify_volume_archive(io.BytesIO(raw), digest(raw))

    def reject_manifest(self, changed):
        with self.assertRaises(ValueError):
            self.verify(self.raw(manifest=changed))

    def test_valid_archive_binds_digest_inventory_metadata_and_offsets(self):
        raw = self.raw()
        plan = self.verify(raw)
        self.assertEqual(plan['archiveSha256'], digest(raw))
        self.assertEqual(plan['archiveBytes'], len(raw))
        self.assertEqual(plan['payloadBytes'], 5)
        self.assertEqual([item['path'] for item in plan['entries']], ['cache', 'cache/data'])
        data = plan['entries'][1]
        self.assertEqual(raw[data['offset']:data['offset'] + data['size']], b'hello')
        self.assertEqual(data['nlink'], 1)

    def test_0644_file_and_0755_executable_and_directory_preserved(self):
        value = copy.deepcopy(self.manifest)
        value['entries'][0]['mode'] = 0o755
        value['entries'][1]['mode'] = 0o644
        value['entries'].append({'path': 'cache/run', 'type': 'file', 'uid': 65534, 'gid': 65534,
            'mode': 0o755, 'size': 3, 'sha256': digest(b'run'), 'nlink': 1})
        raw = self.raw(value, [member('cache/', kind=tarfile.DIRTYPE, mode=0o755),
            member('cache/data', b'hello', mode=0o644), member('cache/run', b'run', mode=0o755)])
        self.assertEqual([item['mode'] for item in self.verify(raw)['entries']], [0o755, 0o644, 0o755])

    def test_unordered_archive_members_still_require_full_inventory(self):
        self.assertEqual(self.verify(self.raw(payload=list(reversed(self.payload))))['payloadBytes'], 5)

    def test_missing_manifest_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(pack(self.payload))

    def test_manifest_directory_rejected(self):
        with self.assertRaisesRegex(ValueError, 'manifest'):
            self.verify(pack([member(archive.MANIFEST_NAME, kind=tarfile.DIRTYPE)] + self.payload))

    def test_manifest_missing_unknown_and_wrong_schema_fields_rejected(self):
        for key, value in [('schemaVersion', True), ('schemaVersion', 2), ('uid', True), ('uid', 0), ('gid', 0), ('entries', {})]:
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(self.manifest)
                changed[key] = value
                self.reject_manifest(changed)
        changed = copy.deepcopy(self.manifest)
        changed['external'] = 'ignored'
        self.reject_manifest(changed)
        del changed['external']
        del changed['uid']
        self.reject_manifest(changed)

    def test_empty_manifest_inventory_rejected(self):
        self.reject_manifest(dict(self.manifest, entries=[]))

    def test_duplicate_json_keys_rejected(self):
        raw = encoded(self.manifest).replace(b'"schemaVersion":1', b'"schemaVersion":1,"schemaVersion":1')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            self.verify(self.raw(manifest_bytes=raw))

    def test_invalid_utf8_bom_and_nonfinite_manifest_rejected(self):
        for raw in (b'\xff', b'\xef\xbb\xbf' + encoded(self.manifest), b'{"schemaVersion":NaN}'):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                self.verify(self.raw(manifest_bytes=raw))

    def test_duplicate_manifest_and_archive_paths_rejected(self):
        changed = copy.deepcopy(self.manifest)
        changed['entries'].append(dict(changed['entries'][1]))
        self.reject_manifest(changed)
        with self.assertRaisesRegex(ValueError, 'duplicate archive'):
            self.verify(self.raw(payload=self.payload + [self.payload[1]]))

    def test_missing_extra_and_unlisted_payload_rejected(self):
        for payload in (self.payload[:1], self.payload + [member('unlisted', b'keep')]):
            with self.subTest(names=[item[0].name for item in payload]), self.assertRaisesRegex(ValueError, 'inventory'):
                self.verify(self.raw(payload=payload))

    def test_implicit_parent_and_file_parent_rejected(self):
        self.reject_manifest(dict(self.manifest, entries=self.manifest['entries'][1:]))
        changed = copy.deepcopy(self.manifest)
        changed['entries'][0] = dict(changed['entries'][1], path='cache')
        self.reject_manifest(changed)

    def test_reserved_manifest_path_not_payload(self):
        changed = copy.deepcopy(self.manifest)
        changed['entries'][1]['path'] = archive.MANIFEST_NAME
        self.reject_manifest(changed)

    def test_every_unsafe_manifest_path_rejected(self):
        for path in ('../escape', '/absolute', 'C:/windows', 'a\\b', 'a//b', './dot', 'a/../b', 'a/', 'a\0b', 'a\x7fb'):
            with self.subTest(path=path):
                changed = copy.deepcopy(self.manifest)
                changed['entries'][1]['path'] = path
                self.reject_manifest(changed)

    def test_manifest_entry_types_fields_and_bool_integers_rejected(self):
        for field, values in [('uid', [0, True]), ('gid', [0, True]), ('mode', [True, '0600']),
                ('size', [True, -1]), ('nlink', [True, 0, 2]), ('sha256', ['F' * 64, '0' * 63]),
                ('type', ['symlink', 'device', 'directory'])]:
            for value in values:
                with self.subTest(field=field, value=value):
                    changed = copy.deepcopy(self.manifest)
                    changed['entries'][1][field] = value
                    self.reject_manifest(changed)
        changed = copy.deepcopy(self.manifest)
        changed['entries'][1]['extra'] = 1
        self.reject_manifest(changed)

    def test_manifest_hash_size_owner_and_mode_mismatch_rejected(self):
        for field, value in [('sha256', '0' * 64), ('size', 4), ('mode', 0o700)]:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.manifest)
                changed['entries'][1][field] = value
                self.reject_manifest(changed)
        for uid, gid in ((0, 65534), (65534, 0)):
            with self.subTest(uid=uid, gid=gid), self.assertRaisesRegex(ValueError, 'owner'):
                self.verify(self.raw(payload=[self.payload[0], member('cache/data', b'hello', uid=uid, gid=gid)]))

    def test_group_world_write_and_setid_sticky_modes_rejected(self):
        for mode in (0o666, 0o777, 0o620, 0o602, 0o4600, 0o2600, 0o1700):
            with self.subTest(mode=oct(mode)):
                changed = copy.deepcopy(self.manifest)
                changed['entries'][1]['mode'] = mode
                self.reject_manifest(changed)
                with self.assertRaisesRegex(ValueError, 'permission'):
                    self.verify(self.raw(payload=[self.payload[0], member('cache/data', b'hello', mode=mode)]))

    def test_ustar_prefix_long_canonical_name_supported(self):
        parent = 'p' * 90
        name = parent + '/' + 'f' * 90
        value = copy.deepcopy(self.manifest)
        value['entries'][0]['path'] = parent
        value['entries'][1]['path'] = name
        result = self.verify(self.raw(value, [member(parent + '/', kind=tarfile.DIRTYPE), member(name, b'hello')]))
        self.assertEqual(result['entries'][1]['path'], name)

    def test_all_link_device_extension_and_legacy_types_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE,
                tarfile.FIFOTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK,
                tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_SPARSE, tarfile.AREGTYPE):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'forbidden'):
                self.verify(patch_header(self.raw(), 156, kind))

    def test_gnu_magic_and_other_tar_formats_rejected(self):
        for magic in (b'ustar ', b'\0' * 6):
            with self.subTest(magic=magic), self.assertRaisesRegex(ValueError, 'USTAR'):
                self.verify(patch_header(self.raw(), 257, magic))

    def test_empty_ustar_name_cannot_be_hidden_by_prefix(self):
        raw = patch_header(self.raw(), 0, b'\0' * 100)
        raw = patch_header(raw, 345, b'restore-manifest.json\0')
        with self.assertRaisesRegex(ValueError, 'name field'):
            self.verify(raw)

    def test_base256_and_bad_numeric_fields_rejected(self):
        for offset, data in ((108, b'\x80' + b'\0' * 7), (108, b'0000008\0'),
                             (124, b'-0000000001\0'), (136, b'99999999999\0')):
            with self.subTest(offset=offset, data=data), self.assertRaises(ValueError):
                self.verify(patch_header(self.raw(), offset, data))

    def test_bad_checksum_rejected(self):
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.verify(patch_header(self.raw(), 0, b'X', fix_checksum=False))

    def test_hidden_link_reserved_and_device_fields_rejected(self):
        for offset, raw in ((157, b'target'), (500, b'X'), (329, b'0000001\0'), (337, b'0000001\0'), (20, b'\0hidden')):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                self.verify(patch_header(self.raw(), offset, raw))

    def test_header_unsafe_paths_rejected_before_manifest_comparison(self):
        for name in (b'../escape\0', b'/absolute\0', b'C:/outside\0', b'a\\b\0'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unsafe'):
                self.verify(patch_header(self.raw(), 0, name.ljust(100, b'\0')))

    def test_gzip_and_concatenated_tar_rejected(self):
        import gzip
        for raw in (gzip.compress(self.raw()), self.raw() + self.raw()):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                self.verify(raw)

    def test_nonzero_padding_and_trailing_data_rejected(self):
        raw = self.raw()
        manifest_size = len(encoded(self.manifest))
        changed = bytearray(raw)
        changed[512 + manifest_size] = 1
        with self.assertRaisesRegex(ValueError, 'padding'):
            self.verify(bytes(changed))
        with self.assertRaisesRegex(ValueError, 'trailing'):
            self.verify(raw + b'X' + b'\0' * 511)

    def test_truncation_unaligned_and_missing_end_markers_rejected(self):
        raw = self.raw()
        # Last data block starts after manifest header + padded manifest +
        # directory header + file header. Compute from the accepted plan.
        data_end = self.verify(raw)['entries'][1]['offset'] + 512
        for invalid in (raw[:-1], raw[:data_end], raw[:data_end + 512], raw[:512], b''):
            with self.subTest(length=len(invalid)), self.assertRaises(ValueError):
                self.verify(invalid)

    def test_directory_with_payload_rejected(self):
        with self.assertRaisesRegex(ValueError, 'member size'):
            self.verify(self.raw(payload=[member('cache/', b'X', kind=tarfile.DIRTYPE), self.payload[1]]))

    def test_archive_manifest_member_and_file_limits_rejected(self):
        raw = self.raw()
        for name, limit in (('MAX_ARCHIVE', len(raw) - 1), ('MAX_MANIFEST', 10), ('MAX_ENTRIES', 1), ('MAX_FILE', 1)):
            with self.subTest(name=name), mock.patch.object(archive, name, limit), self.assertRaises(ValueError):
                self.verify(raw)

    def test_expected_digest_wrong_type_uppercase_and_mismatch_rejected(self):
        for expected in (None, 'F' * 64, '0' * 63, '0' * 64):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                archive.verify_volume_archive(io.BytesIO(self.raw()), expected)

    def test_changed_source_detected_after_full_validation(self):
        raw = self.raw()
        class Changed(io.BytesIO):
            resets = 0
            def seek(self, offset, whence=0):
                if offset == 0 and whence == 0:
                    self.resets += 1
                    if self.resets == 3:
                        mutable = bytearray(self.getvalue())
                        mutable[-1] = 1
                        self.getbuffer()[:] = mutable
                return super().seek(offset, whence)
        with self.assertRaisesRegex(ValueError, 'changed'):
            archive.verify_volume_archive(Changed(raw), digest(raw))

    def test_short_reads_supported_without_trusting_seek_past_eof(self):
        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 11))
        raw = self.raw()
        self.assertEqual(archive.verify_volume_archive(ShortReads(raw), digest(raw))['payloadBytes'], 5)

    def test_appended_zero_trailer_growth_is_bounded_and_rejected(self):
        raw = self.raw()
        class Growing(io.BytesIO):
            resets = 0
            def seek(self, offset, whence=0):
                if offset == 0 and whence == 0:
                    self.resets += 1
                    if self.resets == 2:
                        super().seek(0, 2)
                        self.write(b'\0' * 512)
                return super().seek(offset, whence)
        with self.assertRaisesRegex(ValueError, 'grew'):
            archive.verify_volume_archive(Growing(raw), digest(raw))


if __name__ == '__main__':
    unittest.main()
