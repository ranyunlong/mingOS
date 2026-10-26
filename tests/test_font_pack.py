"""Font-pack qualification: real asset plus small independent binary fixtures."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-font-pack.py"
FONT = "assets/fonts/NotoSansSC-Regular.otf"
OFL = "assets/fonts/OFL.txt"
MANIFEST = "assets/fonts/font-pack.json"
NOTICE = "assets/fonts/NOTICE.txt"
CORPUS = "MingOS 中文，标点。AV fi é A\u0301"
SHA = "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9"
OFL_SHA = "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_font_pack", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def name_table(overrides=None):
    values = {0: "Fixture copyright", 1: "Fixture Sans", 2: "Regular",
              5: "Version 1.000", 13: "SIL Open Font License, Version 1.1.",
              14: "http://scripts.sil.org/OFL"}
    values.update(overrides or {})
    records, storage = bytearray(), bytearray()
    for name_id, value in sorted(values.items()):
        raw = value.encode("utf-16-be")
        records.extend(struct.pack(">6H", 3, 1, 0x409, name_id, len(raw), len(storage)))
        storage.extend(raw)
    return struct.pack(">3H", 0, len(values), 6 + len(records)) + records + storage


def format4(*, indirect=False, missing=False):
    # A/B -> glyphs 3/4, followed by mandatory U+FFFF sentinel -> glyph 0.
    # Indirect encoding includes a delta and a zero glyph to test .notdef handling.
    header = struct.pack(">7H", 4, 36 if indirect else 32, 0, 4, 4, 1, 0)
    arrays = struct.pack(">5H", 66, 0xFFFF, 0, 65, 0xFFFF)
    deltas = struct.pack(">2h", 2 if indirect else (-65 if missing else -62), 1)
    offsets = struct.pack(">2H", 4 if indirect else 0, 0)
    return header + arrays + deltas + offsets + (struct.pack(">2H", 1, 0) if indirect else b"")


def format12(groups=((65, 66, 3), (0x1F600, 0x1F600, 5))):
    return (struct.pack(">HHIII", 12, 0, 16 + 12 * len(groups), 0, len(groups))
            + b"".join(struct.pack(">III", *g) for g in groups))


def cmap_table(records=None):
    records = records or [(3, 1, format4()), (3, 10, format12())]
    directory, data = bytearray(), bytearray()
    for platform, encoding, subtable in records:
        directory.extend(struct.pack(">HHI", platform, encoding, 4 + 8 * len(records) + len(data)))
        data.extend(subtable)
    return struct.pack(">HH", 0, len(records)) + directory + data


def sfnt(overrides=None):
    head = bytearray(54)
    struct.pack_into(">II", head, 0, 0x10000, 0x10000)
    struct.pack_into(">I", head, 12, 0x5F0F3CF5)
    tables = {b"CFF ": b"fixture", b"head": bytes(head),
              b"maxp": struct.pack(">IH", 0x5000, 16),
              b"name": name_table(), b"cmap": cmap_table()}
    tables.update(overrides or {})
    directory, data = bytearray(), bytearray()
    base = 12 + 16 * len(tables)
    for tag, raw in sorted(tables.items()):
        directory.extend(struct.pack(">4sIII", tag, 0, base + len(data), len(raw)))
        data.extend(raw)
        data.extend(b"\0" * (-len(data) % 4))
    return struct.pack(">4s4H", b"OTTO", len(tables), 0, 0, 0) + directory + data


def table_entry(data, tag):
    for offset in range(12, 12 + 16 * struct.unpack_from(">H", data, 4)[0], 16):
        if data[offset:offset + 4] == tag:
            return offset
    raise AssertionError(tag)


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verify = load_verifier()

    def inspect(self, data=None, corpus="AB"):
        return self.verify.inspect_font(sfnt() if data is None else data, corpus)

    def rejects(self, data, message):
        with self.assertRaisesRegex(self.verify.VerificationError, message):
            self.inspect(data)

    def test_fixture_names_head_and_scalar_coverage(self):
        report = self.inspect()
        self.assertEqual(report["sfnt_version"], "OTTO")
        self.assertEqual(report["head_revision"]["raw_16_16"], 65536)
        self.assertEqual(report["num_glyphs"], 16)
        self.assertEqual(report["notices"]["copyright"], ["Fixture copyright"])
        self.assertEqual([s["glyph_id"] for s in report["coverage"]["scalars"]], [3, 4])
        self.assertEqual(report["coverage"]["missing"], [])

    def test_format4_indirect_delta_and_zero_notdef(self):
        report = self.inspect(sfnt({b"cmap": cmap_table([(3, 1, format4(indirect=True))])}))
        self.assertEqual([s["glyph_id"] for s in report["coverage"]["scalars"]], [3, 0])
        self.assertEqual(report["coverage"]["missing"], ["U+0042"])

    def test_bmp_and_supplementary_gaps_do_not_inherit_neighbor_glyphs(self):
        for records in ([(3, 1, format4())], [(3, 10, format12())]):
            report = self.inspect(sfnt({b"cmap": cmap_table(records)}), corpus="@AC\U0001f5ff\U0001f601")
            self.assertEqual([s["glyph_id"] for s in report["coverage"]["scalars"]], [0, 3, 0, 0, 0])

    def test_format12_supplementary_and_missing_scalars(self):
        report = self.inspect(corpus="A\U0001f600\U0010ffff")
        self.assertEqual([s["glyph_id"] for s in report["coverage"]["scalars"]], [3, 5, 0])
        self.assertEqual(report["coverage"]["missing"], ["U+10FFFF"])

    def test_prefers_one_full_repertoire_subtable_without_union(self):
        altered = format12(((65, 65, 3),))
        self.rejects(sfnt({b"cmap": cmap_table([(3, 1, format4()), (3, 10, altered)])}),
                     "inconsistent|disagree")

    def test_nonunicode_auxiliary_subtable_is_not_scalar_coverage(self):
        mac = struct.pack(">6H", 6, 14, 0, 65, 2, 3) + struct.pack(">H", 4)
        self.rejects(sfnt({b"cmap": cmap_table([(1, 0, mac)])}), "supported Unicode cmap")

    def test_shared_cmap_subtable_offsets_are_valid(self):
        raw = bytearray(cmap_table([(0, 3, format4()), (3, 1, format4())]))
        raw[16:20] = raw[8:12]
        report = self.inspect(sfnt({b"cmap": bytes(raw)}))
        self.assertEqual(report["coverage"]["missing"], [])

    def test_unsupported_required_cmap_fails_even_with_supported_fallback(self):
        unsupported = struct.pack(">HHIII", 13, 0, 28, 0, 1) + struct.pack(">III", 65, 66, 3)
        self.rejects(sfnt({b"cmap": cmap_table([(3, 1, format4()), (3, 10, unsupported)])}),
                     "unsupported.*cmap|cmap.*unsupported")

    def test_invalid_scalar_input_is_rejected(self):
        with self.assertRaisesRegex(self.verify.VerificationError, "scalar"):
            self.inspect(corpus="\ud800")

    def test_truncated_sfnt_headers_and_directory(self):
        for data in (b"", b"OTTO", sfnt()[:20]):
            with self.subTest(size=len(data)):
                self.rejects(data, "truncated|bounds|directory")

    def test_wrong_sfnt_signature(self):
        self.rejects(b"ttcf" + sfnt()[4:], "OTTO")

    def test_table_count_is_bounded(self):
        raw = bytearray(sfnt())
        struct.pack_into(">H", raw, 4, 0xFFFF)
        self.rejects(raw, "table count|directory")

    def test_duplicate_table_tags(self):
        raw = bytearray(sfnt())
        raw[28:32] = raw[12:16]
        self.rejects(raw, "duplicate")

    def test_table_overlaps_other_table_or_directory(self):
        for new_offset in (0, struct.unpack_from(">I", sfnt(), 20)[0]):
            raw = bytearray(sfnt())
            struct.pack_into(">I", raw, table_entry(raw, b"cmap") + 8, new_offset)
            with self.subTest(offset=new_offset):
                self.rejects(raw, "overlap|directory")

    def test_table_out_of_range_and_unaligned(self):
        for offset, length in ((len(sfnt()) + 4, 20), (0xFFFFFFFC, 0xFFFFFFFF), (93, 10)):
            raw = bytearray(sfnt())
            struct.pack_into(">II", raw, table_entry(raw, b"cmap") + 8, offset, length)
            with self.subTest(offset=offset):
                self.rejects(raw, "bounds|range|align|truncated")

    def test_missing_required_table(self):
        raw = bytearray(sfnt())
        i = table_entry(raw, b"name")
        raw[i:i + 4] = b"xxxx"
        self.rejects(raw, "missing.*name")

    def test_invalid_head_and_glyph_count(self):
        for tag, value in ((b"head", bytes(54)), (b"head", b"short"),
                           (b"maxp", struct.pack(">IH", 0x5000, 0))):
            with self.subTest(tag=tag, size=len(value)):
                self.rejects(sfnt({tag: value}), "head|maxp|glyph")

    def test_name_storage_cannot_escape_table(self):
        for position, value in ((4, 0), (4, 0xFFFF), (14, 0xFFFF)):
            names = bytearray(name_table())
            struct.pack_into(">H", names, position, value)
            with self.subTest(position=position, value=value):
                self.rejects(sfnt({b"name": names}), "name|bounds|truncated")

    def test_malformed_name_encoding_and_missing_license(self):
        names = bytearray(name_table())
        storage = struct.unpack_from(">H", names, 4)[0]
        names[storage:storage + 2] = b"\xd8\x00"
        self.rejects(sfnt({b"name": names}), "name|encoding|UTF")
        self.rejects(sfnt({b"name": name_table({13: ""})}), "license|name")

    def test_truncated_and_out_of_range_cmap_directory(self):
        for raw in (b"\0\0\0\1", struct.pack(">HHHHI", 0, 1, 3, 1, 0xFFFFFFFE)):
            with self.subTest(raw=raw):
                self.rejects(sfnt({b"cmap": raw}), "cmap|bounds|truncated")

    def test_cmap_declared_length_is_bounded(self):
        for subtable, position, code in ((format4(), 2, ">H"), (format12(), 4, ">I")):
            raw = bytearray(subtable)
            struct.pack_into(code, raw, position, 0xFFFF)
            with self.subTest(format=raw[:2]):
                self.rejects(sfnt({b"cmap": cmap_table([(3, 1, raw)])}), "cmap|bounds|truncated")

    def test_format4_segment_order_and_terminal_sentinel(self):
        for position, value in ((14, 64), (16, 66), (20, 67), (18, 1), (6, 3)):
            raw = bytearray(format4())
            struct.pack_into(">H", raw, position, value)
            with self.subTest(position=position):
                self.rejects(sfnt({b"cmap": cmap_table([(3, 1, raw)])}), "cmap|segment|sentinel")

    def test_format4_range_offset_cannot_read_arrays_or_outside_subtable(self):
        for value in (1, 2, 0xFFFE):
            raw = bytearray(format4(indirect=True))
            struct.pack_into(">H", raw, 28, value)
            with self.subTest(value=value):
                self.rejects(sfnt({b"cmap": cmap_table([(3, 1, raw)])}), "cmap|range|bounds")

    def test_format12_groups_reject_overlap_surrogates_and_invalid_glyphs(self):
        for groups in (((66, 65, 3),), ((65, 66, 3), (66, 67, 6)),
                       ((0xD800, 0xD800, 3),), ((0x10FFFF, 0x110000, 3),),
                       ((65, 66, 15),), ((65, 65, 0xFFFFFFFF),)):
            with self.subTest(groups=groups):
                self.rejects(sfnt({b"cmap": cmap_table([(3, 10, format12(groups))])}),
                             "cmap|scalar|glyph|group")

    def test_format4_glyph_outside_maxp_is_rejected(self):
        raw = bytearray(format4())
        struct.pack_into(">h", raw, 24, 0)
        self.rejects(sfnt({b"cmap": cmap_table([(3, 1, raw)])}), "glyph")


class PackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.verify = load_verifier()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "assets/fonts").mkdir(parents=True)
        for path in (FONT, OFL, MANIFEST, NOTICE):
            shutil.copyfile(ROOT / path, self.root / path)

    def edit_manifest(self, change):
        path = self.root / MANIFEST
        value = json.loads(path.read_text(encoding="utf-8"))
        change(value)
        path.write_text(json.dumps(value), encoding="utf-8")

    def rejected(self, message="."):
        with self.assertRaisesRegex(self.verify.VerificationError, message):
            self.verify.verify_pack(self.root)

    def test_actual_pack_pinned_bytes_and_license(self):
        report = self.verify.verify_pack(self.root)
        self.assertEqual(report["font_pack_revision"], 1)
        self.assertEqual(report["font"]["size_bytes"], 8331336)
        self.assertEqual(report["font"]["sha256"], SHA)
        self.assertEqual(report["license"]["sha256"], OFL_SHA)
        self.assertEqual(report["license"]["size_bytes"], 4301)
        self.assertEqual(report["upstream"]["commit"], "523d033d6cb47f4a80c58a35753646f5c3608a78")
        self.assertEqual(report["font"]["head_revision"]["raw_16_16"], 131334)
        self.assertEqual(report["font"]["num_glyphs"], 31036)
        self.assertEqual(len(report["font"]["tables"]), 17)
        self.assertEqual(len(report["font"]["names"]), 18)
        self.assertEqual(report["font"]["notices"]["copyright"],
                         ["© 2014-2021 Adobe (http://www.adobe.com/)."])
        self.assertEqual(report["font"]["coverage"]["corpus"], CORPUS)
        self.assertEqual(report["font"]["coverage"]["missing"], [])
        actual = {s["codepoint"]: s["glyph_id"] for s in report["font"]["coverage"]["scalars"]}
        self.assertEqual(actual, {"U+004D": 46, "U+0069": 74, "U+006E": 79, "U+0067": 72,
                                 "U+004F": 48, "U+0053": 52, "U+0020": 1, "U+4E2D": 8805,
                                 "U+6587": 14750, "U+FF0C": 29763, "U+6807": 15387,
                                 "U+70B9": 17602, "U+3002": 1142, "U+0041": 34,
                                 "U+0056": 55, "U+0066": 71, "U+00E9": 167, "U+0301": 253})

    def test_actual_names_license_and_full_repertoire_counts(self):
        report = self.verify.verify_pack(self.root)
        font = report["font"]
        values = {(n["language_id"], n["name_id"]): n["value"] for n in font["names"]}
        self.assertEqual(values[0x409, 1], "Noto Sans SC")
        self.assertEqual(values[0x804, 2], "Regular")
        self.assertEqual(values[0x409, 5], "Version 2.004;hotconv 1.0.118;makeotfexe 2.5.65603")
        self.assertEqual(font["notices"]["trademark"], ["Noto is a trademark of Google Inc."])
        self.assertEqual(font["notices"]["license_url"], ["http://scripts.sil.org/OFL"])
        self.assertEqual(font["coverage"]["mapped_scalar_count"], 30890)
        self.assertEqual(font["coverage"]["supplementary_scalar_count"], 445)
        self.assertEqual([r["format"] for r in font["cmaps"]], [4, 12, 14, 6, 4, 12])
        self.assertEqual(font["tables"][1]["tag"], "CFF ")

    def test_actual_font_records_missing_emoji_without_system_fallback(self):
        report = self.verify.inspect_font((self.root / FONT).read_bytes(), "A\U0001f600")
        self.assertEqual(report["coverage"]["missing"], ["U+1F600"])
        self.assertEqual([s["glyph_id"] for s in report["coverage"]["scalars"]], [34, 0])

    def test_manifest_numeric_types_and_nonfinite_values_are_not_accepted(self):
        original = (self.root / MANIFEST).read_bytes()
        for value in (True, 1.0, float("nan"), float("inf")):
            with self.subTest(value=value):
                (self.root / MANIFEST).write_bytes(original)
                self.edit_manifest(lambda m: m.update(font_pack_revision=value))
                self.rejected("manifest")

    def test_manifest_numeric_overflow_is_a_controlled_verification_failure(self):
        path = self.root / MANIFEST
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace('"font_pack_revision": 1,',
                                     '"font_pack_revision": 1e999,'), encoding="utf-8")
        self.rejected("manifest")

    def test_missing_each_pack_file(self):
        for path in (FONT, OFL, NOTICE, MANIFEST):
            with self.subTest(path=path):
                content = (self.root / path).read_bytes()
                (self.root / path).unlink()
                self.rejected("missing|read")
                (self.root / path).write_bytes(content)

    def test_font_corruption_and_truncation(self):
        path = self.root / FONT
        raw = path.read_bytes()
        for bad in (raw[:128], raw[:-1] + bytes([raw[-1] ^ 1])):
            with self.subTest(size=len(bad)):
                path.write_bytes(bad)
                self.rejected("size|SHA|sha256")

    def test_changed_ofl_cannot_be_resealed_in_manifest(self):
        path = self.root / OFL
        raw = path.read_bytes().replace(b"Version 1.1", b"Version 1.0")
        path.write_bytes(raw)
        self.edit_manifest(lambda m: m["license"].update(sha256=hashlib.sha256(raw).hexdigest()))
        self.rejected("license|OFL|sha256|SHA")

    def test_changed_font_cannot_be_resealed_in_manifest(self):
        path = self.root / FONT
        raw = path.read_bytes()[:-1] + b"X"
        path.write_bytes(raw)
        self.edit_manifest(lambda m: m["font"].update(sha256=hashlib.sha256(raw).hexdigest()))
        self.rejected("sha256|SHA")

    def test_notice_must_preserve_exact_embedded_notices(self):
        path = self.root / NOTICE
        path.write_bytes(path.read_bytes().replace(b"2014-2021", b"2014-2022"))
        self.rejected("NOTICE|notice")

    def test_manifest_metadata_coverage_and_revision_cannot_lie(self):
        original = (self.root / MANIFEST).read_bytes()
        changes = (lambda m: m.update(font_pack_revision=2),
                   lambda m: m["font"].update(size_bytes=1),
                   lambda m: m["font"]["coverage"].update(missing=["U+0041"]),
                   lambda m: m["font"]["coverage"]["scalars"][0].update(glyph_id=0),
                   lambda m: m["font"]["names"][0].update(value="invented"),
                   lambda m: m["upstream"].update(commit="0" * 40),
                   lambda m: m["upstream"].update(font_path="other.otf"),
                   lambda m: m.update(unreviewed_field=True))
        for change in changes:
            with self.subTest(change=change):
                (self.root / MANIFEST).write_bytes(original)
                self.edit_manifest(change)
                self.rejected("manifest|revision|metadata")

    def test_manifest_paths_reject_absolute_traversal_windows_and_aliases(self):
        original = (self.root / MANIFEST).read_bytes()
        for field in ("font", "license", "notice"):
            for path in ("../outside.otf", "/etc/passwd", "C:/Windows/Fonts/font.otf",
                         "assets/fonts/../OFL.txt", "assets\\fonts\\OFL.txt",
                         "assets/fonts/OFL.txt:stream", "assets/fonts/./OFL.txt"):
                with self.subTest(field=field, path=path):
                    (self.root / MANIFEST).write_bytes(original)
                    self.edit_manifest(lambda m: m[field].update(path=path))
                    self.rejected("path|manifest")

    def test_symlink_to_external_asset_is_rejected(self):
        target = self.root / "outside.txt"
        target.write_bytes((self.root / OFL).read_bytes())
        (self.root / OFL).unlink()
        try:
            (self.root / OFL).symlink_to(target)
        except OSError as error:
            self.skipTest(f"symlink creation unavailable: {error}")
        self.rejected("symlink|path|escape")

    def test_invalid_and_oversized_manifest_and_duplicate_keys(self):
        path = self.root / MANIFEST
        for raw in (b"{", b"[]", b"null", b'{"schema_version":1,"schema_version":2}',
                    b" " * (1024 * 1024 + 1), b"\xff"):
            with self.subTest(size=len(raw)):
                path.write_bytes(raw)
                self.rejected("manifest|JSON|UTF|size|limit|duplicate")

    def test_oversized_font_and_license_are_rejected(self):
        for name, limit in ((FONT, 16 * 1024 * 1024), (OFL, 64 * 1024)):
            with self.subTest(name=name):
                path = self.root / name
                original = path.read_bytes()
                with path.open("wb") as output:
                    output.truncate(limit + 1)
                self.rejected("size|limit")
                path.write_bytes(original)


class CliTests(unittest.TestCase):
    def test_cli_works_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-B", str(SCRIPT)], cwd=directory,
                                    capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verified", result.stdout.lower())
        self.assertIn("18/18", result.stdout)

    def test_cli_reports_missing_pack_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            shutil.copyfile(SCRIPT, root / "scripts/verify-font-pack.py")
            result = subprocess.run([sys.executable, "-B", str(root / "scripts/verify-font-pack.py")],
                                    cwd=root, capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
