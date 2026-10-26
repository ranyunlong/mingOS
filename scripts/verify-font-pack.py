#!/usr/bin/env python3
"""Offline qualification of MingOS's immutable builtin font (Python 3.11+).

No dependencies, downloads, system font discovery, or file writes. This is a
bounded metadata/cmap reader for the pinned OTTO face, not an outline sanitizer
or a shaping/rasterization test. Unicode scalar cmaps 4/12 are validated; auxiliary
Mac format 6 and variation format 14 are inventoried with bounded lengths only.
Run from any directory: python -B /path/to/MingOS/scripts/verify-font-pack.py
"""
from __future__ import annotations

from bisect import bisect_right
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FONT_PATH = "assets/fonts/NotoSansSC-Regular.otf"
LICENSE_PATH = "assets/fonts/OFL.txt"
NOTICE_PATH = "assets/fonts/NOTICE.txt"
MANIFEST_PATH = "assets/fonts/font-pack.json"
FONT_SIZE = 8_331_336
FONT_SHA256 = "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9"
LICENSE_SIZE = 4_301
LICENSE_SHA256 = "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"
MAX_FONT_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
CORPUS = "MingOS 中文，标点。AV fi é A\u0301"
UPSTREAM = {
    "repository": "https://github.com/notofonts/noto-cjk",
    "commit": "523d033d6cb47f4a80c58a35753646f5c3608a78",
    "tag": "Sans2.004",
    "font_path": "Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
    "license_path": "LICENSE",
}
NAME_LABELS = {
    0: "copyright", 1: "family", 2: "style", 3: "unique_id", 4: "full_name",
    5: "version", 6: "postscript_name", 7: "trademark", 8: "manufacturer",
    9: "designer", 10: "description", 11: "vendor_url", 12: "designer_url",
    13: "license_description", 14: "license_url",
}


class VerificationError(Exception):
    """A corrupt, unsupported, or unqualified asset; never a skipped pass."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def bounded(data, offset, length, label):
    require(0 <= offset <= len(data) and 0 <= length <= len(data) - offset,
            f"{label}: truncated or out-of-range bounds")
    return memoryview(data)[offset:offset + length]


def unpack(fmt, data, offset, label):
    return struct.unpack(fmt, bounded(data, offset, struct.calcsize(fmt), label))


def asset_bytes(root, relative, limit):
    # Deliberately narrower than general filesystem paths: a single ASCII basename
    # in assets/fonts. Reject traversal, drives, ADS, backslashes and symlink aliases.
    require(isinstance(relative, str) and re.fullmatch(
        r"assets/fonts/[A-Za-z0-9][A-Za-z0-9_.-]*", relative),
        f"unsafe asset path: {relative!r}")
    root = Path(root).resolve()
    directory = root / "assets" / "fonts"
    path = root / relative
    try:
        require(not any(part.is_symlink() for part in (root / "assets", directory, path)),
                f"symlink asset path rejected: {relative}")
        require(path.resolve().is_relative_to(directory), f"asset path escapes fonts: {relative}")
        require(path.is_file(), f"missing asset: {relative}")
        require(path.stat().st_size <= limit, f"{relative}: size exceeds {limit} byte limit")
        with path.open("rb") as source:
            raw = source.read(limit + 1)
        require(len(raw) <= limit, f"{relative}: read exceeds size limit")
        return raw
    except (OSError, ValueError, RuntimeError) as error:
        raise VerificationError(f"cannot read asset {relative}: {error}") from error


def file_record(path, raw):
    return {"path": path, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def read_names(data):
    fmt, count, storage = unpack(">HHH", data, 0, "name header")
    require(fmt == 0, f"unsupported name table format {fmt}")
    require(0 < count <= 4096, "name record count out of range")
    end = 6 + 12 * count
    bounded(data, 0, end, "name directory")
    require(end <= storage <= len(data), "name storage overlaps directory or is out of bounds")
    records, keys = [], set()
    for i in range(count):
        platform, encoding, language, name_id, length, offset = unpack(
            ">6H", data, 6 + 12 * i, "name record")
        key = (platform, encoding, language, name_id)
        require(key not in keys, "duplicate name record")
        keys.add(key)
        if platform == 0 or (platform == 3 and encoding in (0, 1, 10)):
            codec = "utf-16-be"
        elif platform == 1 and encoding == 0:
            codec = "mac_roman"
        else:
            raise VerificationError(f"unsupported name encoding {platform}/{encoding}")
        raw = bounded(data, storage + offset, length, "name string")
        try:
            value = bytes(raw).decode(codec, errors="strict")
        except UnicodeError as error:
            raise VerificationError(f"invalid name encoding: {error}") from error
        records.append({"platform_id": platform, "encoding_id": encoding,
                        "language_id": language, "name_id": name_id,
                        "label": NAME_LABELS.get(name_id, f"name_{name_id}"), "value": value})
    for name_id in (0, 1, 2, 5, 13, 14):
        require(any(r["name_id"] == name_id and r["value"] for r in records),
                f"missing nonempty name {NAME_LABELS[name_id]}")
    return records


def parse_format4(data, num_glyphs):
    _, length, language, twice_count = unpack(">4H", data, 0, "cmap format 4")
    require(length == len(data) and twice_count > 0 and twice_count % 2 == 0,
            "invalid cmap format 4 segment count/length")
    count = twice_count // 2
    bounded(data, 0, 16 + 8 * count, "cmap format 4 arrays")
    require(unpack(">H", data, 14 + 2 * count, "cmap reservedPad")[0] == 0,
            "cmap format 4 reservedPad must be zero")
    segments, previous = [], -1
    glyph_array = 16 + 8 * count
    for i in range(count):
        end = unpack(">H", data, 14 + 2 * i, "cmap endCode")[0]
        start = unpack(">H", data, 16 + 2 * count + 2 * i, "cmap startCode")[0]
        delta = unpack(">h", data, 16 + 4 * count + 2 * i, "cmap idDelta")[0]
        range_word = 16 + 6 * count + 2 * i
        relative = unpack(">H", data, range_word, "cmap idRangeOffset")[0]
        require(previous < start <= end, "cmap format 4 segments overlap or are out of order")
        require(end < 0xD800 or start > 0xDFFF, "cmap segment includes surrogate scalar")
        if relative:
            first = range_word + relative
            require(relative % 2 == 0 and first >= glyph_array,
                    "cmap format 4 invalid glyph range offset")
            bounded(data, first, 2 * (end - start + 1), "cmap glyph range")
        else:
            first = None
        segments.append((start, end, delta, first))
        previous = end
    require(segments[-1][0] == segments[-1][1] == 0xFFFF,
            "cmap format 4 missing terminal sentinel")

    starts = [segment[0] for segment in segments]

    def glyph(cp):
        index = bisect_right(starts, cp) - 1
        if index < 0:
            return 0
        start, end, delta, first = segments[index]
        if cp > end:
            return 0
        if first is None:
            return (cp + delta) & 0xFFFF
        raw = unpack(">H", data, first + 2 * (cp - start), "cmap glyph")[0]
        return (raw + delta) & 0xFFFF if raw else 0

    mapped = 0
    # Sorted disjoint BMP segments bound this loop to 65,536 iterations.
    for start, end, _, _ in segments:
        for cp in range(start, end + 1):
            gid = glyph(cp)
            require(gid < num_glyphs, "cmap format 4 glyph out of maxp range")
            mapped += bool(gid)
    return glyph, {"language": language, "segment_count": count,
                   "mapped_scalar_count": mapped, "supplementary_scalar_count": 0}


def parse_format12(data, num_glyphs):
    _, reserved, length, language, count = unpack(">HHIII", data, 0, "cmap format 12")
    require(reserved == 0 and length == len(data) == 16 + 12 * count and count > 0,
            "invalid cmap format 12 group count/length")
    groups, previous, mapped, supplementary = [], -1, 0, 0
    for i in range(count):
        start, end, gid = unpack(">III", data, 16 + 12 * i, "cmap format 12 group")
        require(previous < start <= end <= 0x10FFFF,
                "cmap format 12 group overlap or scalar out of range")
        require(end < 0xD800 or start > 0xDFFF, "cmap group includes surrogate scalar")
        require(gid + end - start < num_glyphs, "cmap format 12 glyph out of maxp range")
        groups.append((start, end, gid))
        first_mapped = start + (gid == 0)
        mapped += max(0, end - first_mapped + 1)
        supplementary += max(0, end - max(0x10000, first_mapped) + 1)
        previous = end

    starts = [group[0] for group in groups]

    def glyph(cp):
        index = bisect_right(starts, cp) - 1
        if index < 0:
            return 0
        start, end, gid = groups[index]
        return gid + cp - start if cp <= end else 0

    return glyph, {"language": language, "group_count": count,
                   "mapped_scalar_count": mapped, "supplementary_scalar_count": supplementary}


def read_cmap(data, num_glyphs, corpus):
    require(isinstance(corpus, str) and all(not 0xD800 <= ord(c) <= 0xDFFF for c in corpus),
            "coverage corpus must contain Unicode scalar values")
    version, count = unpack(">HH", data, 0, "cmap header")
    require(version == 0 and 0 < count <= 64, "invalid cmap version or record count")
    directory_end = 4 + 8 * count
    bounded(data, 0, directory_end, "cmap directory")
    records, ranges, parsed, usable = [], [], {}, []
    for i in range(count):
        platform, encoding, offset = unpack(">HHI", data, 4 + 8 * i, "cmap record")
        require(offset >= directory_end, "cmap subtable overlaps directory")
        fmt = unpack(">H", data, offset, "cmap format")[0]
        if fmt in (0, 2, 4, 6):
            length = unpack(">H", data, offset + 2, "cmap length")[0]
            minimum = {0: 262, 2: 518, 4: 16, 6: 10}[fmt]
        elif fmt in (8, 10, 12, 13):
            length = unpack(">I", data, offset + 4, "cmap length")[0]
            minimum = {8: 8208, 10: 20, 12: 16, 13: 16}[fmt]
        elif fmt == 14:
            length = unpack(">I", data, offset + 2, "cmap length")[0]
            minimum = 10
        else:
            raise VerificationError(f"unsupported cmap format {fmt}")
        require(length >= minimum, "cmap subtable length is too short")
        subtable = bounded(data, offset, length, "cmap subtable")
        span = (offset, offset + length)
        for other in ranges:
            require(span == other or span[1] <= other[0] or other[1] <= span[0],
                    "cmap subtable overlap")
        ranges.append(span)
        scalar_record = (platform == 0 and encoding in (0, 1, 2, 3, 4, 6)) or (
            platform == 3 and encoding in (1, 10))
        record = {"platform_id": platform, "encoding_id": encoding, "format": fmt,
                  "offset": offset, "length_bytes": length,
                  "validation": "scalar_mapping" if scalar_record else "inventory_only"}
        if scalar_record:
            require(fmt in (4, 12), f"unsupported required Unicode cmap format {fmt}")
            if span not in parsed:
                parser = parse_format4 if fmt == 4 else parse_format12
                parsed[span] = parser(subtable, num_glyphs)
            glyph, summary = parsed[span]
            record.update(summary)
            usable.append((len(records), glyph))
        records.append(record)
    require(usable, "no supported Unicode cmap format 4/12")
    # Select one full repertoire, never a union which could hide incompatible maps.
    selected_index, selected_glyph = max(
        usable, key=lambda item: (records[item[0]]["format"] == 12,
                                  records[item[0]]["platform_id"] == 3))
    scalars = []
    for char in dict.fromkeys(corpus):
        cp = ord(char)
        gid = selected_glyph(cp)
        for index, glyph in usable:
            if cp <= 0xFFFF or records[index]["format"] == 12:
                require(glyph(cp) == gid, f"inconsistent cmap mappings for U+{cp:04X}")
        scalars.append({"character": char, "codepoint": f"U+{cp:04X}", "glyph_id": gid})
    coverage = {"corpus": corpus, "normalization": "none; exact input scalars",
                "selected_cmap_record": selected_index, "scalars": scalars,
                "covered_scalar_count": sum(s["glyph_id"] != 0 for s in scalars),
                "required_scalar_count": len(scalars),
                "missing": [s["codepoint"] for s in scalars if s["glyph_id"] == 0],
                "mapped_scalar_count": records[selected_index]["mapped_scalar_count"],
                "supplementary_scalar_count": records[selected_index]["supplementary_scalar_count"]}
    return records, coverage


def inspect_font(raw, corpus=CORPUS):
    """Read bounded OTTO metadata and scalar mappings; missing glyphs are evidence.

    For synthetic fixtures this accepts any structurally valid relevant tables.
    verify_pack separately enforces the immutable production SHA/size and evidence.
    """
    require(len(raw) <= MAX_FONT_BYTES, "font size exceeds 16 MiB limit")
    signature, count, _, _, _ = unpack(">4s4H", raw, 0, "sfnt header")
    require(signature == b"OTTO", "expected OTTO OpenType/CFF sfnt signature")
    require(0 < count <= 256, "sfnt table count out of range")
    directory_end = 12 + 16 * count
    bounded(raw, 0, directory_end, "sfnt directory")
    tables, inventory, ranges = {}, [], []
    for i in range(count):
        tag_bytes, checksum, offset, length = unpack(">4sIII", raw, 12 + 16 * i, "sfnt table")
        require(all(32 <= c <= 126 for c in tag_bytes), "invalid sfnt table tag")
        tag = tag_bytes.decode("ascii")
        require(tag not in tables, f"duplicate sfnt table tag {tag!r}")
        require(offset >= directory_end, f"table {tag!r} overlaps sfnt directory")
        require(offset % 4 == 0, f"table {tag!r} offset is not 4-byte aligned")
        require(length > 0, f"table {tag!r} has empty range")
        tables[tag] = bounded(raw, offset, length, f"table {tag!r}")
        for start, end in ranges:
            require(offset + length <= start or end <= offset, f"sfnt table overlap: {tag!r}")
        ranges.append((offset, offset + length))
        inventory.append({"tag": tag, "offset": offset, "length_bytes": length,
                          "directory_checksum": f"{checksum:08x}"})
    for required in ("CFF ", "head", "maxp", "name", "cmap"):
        require(required in tables, f"missing required sfnt table {required}")
    head = tables["head"]
    bounded(head, 0, 54, "head")
    version, revision = unpack(">Ii", head, 0, "head version/revision")
    require(version == 0x10000 and unpack(">I", head, 12, "head magic")[0] == 0x5F0F3CF5,
            "invalid head version or magic")
    maxp_version, num_glyphs = unpack(">IH", tables["maxp"], 0, "maxp")
    require(maxp_version == 0x5000 and num_glyphs > 0, "invalid CFF maxp version/glyph count")
    names = read_names(tables["name"])
    cmaps, coverage = read_cmap(tables["cmap"], num_glyphs, corpus)
    notices = {label: list(dict.fromkeys(r["value"] for r in names if r["name_id"] == name_id))
               for name_id, label in ((0, "copyright"), (7, "trademark"),
                                      (13, "license_description"), (14, "license_url"))}
    return {"sfnt_version": "OTTO", "head_revision": {
                "raw_16_16": revision, "decimal": revision / 65536},
            "num_glyphs": num_glyphs, "tables": inventory, "names": names,
            "notices": notices, "cmaps": cmaps, "coverage": coverage}


def notice_text(report):
    """Preserve embedded text verbatim, clearly distinguishing provenance labels."""
    source = f"{UPSTREAM['repository']}/blob/{UPSTREAM['commit']}/"
    lines = ["MingOS builtin font pack revision 1", "",
             "Asset: NotoSansSC-Regular.otf (unmodified upstream bytes)",
             f"Upstream release tag: {UPSTREAM['tag']}",
             f"Upstream commit: {UPSTREAM['commit']}",
             f"Exact upstream font: {source}{UPSTREAM['font_path']}",
             f"Font size: {FONT_SIZE} bytes", f"Font SHA-256: {FONT_SHA256}", ""]
    for label, title in (("copyright", "Embedded copyright (name ID 0)"),
                         ("trademark", "Embedded trademark (name ID 7)"),
                         ("license_description", "Embedded license description (name ID 13)"),
                         ("license_url", "Embedded license pointer (name ID 14)")):
        lines.extend([title + ":", *report["notices"][label], ""])
    lines.extend(["Accompanying license: SIL Open Font License, Version 1.1 (OFL-1.1).",
                  "Local license: OFL.txt (unchanged copy of upstream LICENSE).",
                  f"Exact upstream license: {source}{UPSTREAM['license_path']}",
                  f"License size: {LICENSE_SIZE} bytes", f"License SHA-256: {LICENSE_SHA256}", "",
                  "Qualification evidence: font-pack.json; verify with scripts/verify-font-pack.py.",
                  "Scalar coverage is not evidence of shaping, rendering, or memory qualification.", ""])
    return "\n".join(lines)


def build_manifest(root=PROJECT_ROOT):
    """Derive auditable evidence only after checking immutable byte pins."""
    font = asset_bytes(root, FONT_PATH, MAX_FONT_BYTES)
    license_raw = asset_bytes(root, LICENSE_PATH, MAX_TEXT_BYTES)
    require(len(font) == FONT_SIZE, "font size differs from pinned upstream asset")
    require(hashlib.sha256(font).hexdigest() == FONT_SHA256, "font SHA-256 differs from pinned asset")
    require(len(license_raw) == LICENSE_SIZE, "OFL license size differs from pinned upstream LICENSE")
    require(hashlib.sha256(license_raw).hexdigest() == LICENSE_SHA256,
            "OFL license SHA-256 differs from pinned upstream LICENSE")
    require(b"SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007" in license_raw,
            "missing SIL OFL 1.1 license header")
    font_report = inspect_font(font)
    require(not font_report["coverage"]["missing"],
            f"pinned font repertoire gap: {font_report['coverage']['missing']}")
    notice = asset_bytes(root, NOTICE_PATH, MAX_TEXT_BYTES)
    require(notice == notice_text(font_report).encode("utf-8"),
            "NOTICE must preserve exact embedded notices and upstream pointers")
    return {"schema_version": 1, "font_pack_revision": 1, "builtin_font_id": 1,
            "upstream": dict(UPSTREAM), "font": {**file_record(FONT_PATH, font), **font_report},
            "license": {**file_record(LICENSE_PATH, license_raw), "spdx_id": "OFL-1.1"},
            "notice": file_record(NOTICE_PATH, notice),
            "qualification": {
                "scope": "immutable bytes, bounded sfnt/name/cmap metadata and corpus scalar coverage",
                "outline_tables": "inventory only; not an outline sanitizer",
                "auxiliary_cmaps": "bounded inventory only; no variation-sequence or Mac repertoire claim",
                "directory_checksums": "recorded; byte integrity enforced by pinned whole-file SHA-256",
                "runtime": "shaping, rasterization and memory qualification are separate"}}


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate manifest JSON key {key!r}")
        result[key] = value
    return result


def verify_pack(root=PROJECT_ROOT):
    raw = asset_bytes(root, MANIFEST_PATH, MAX_MANIFEST_BYTES)
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys,
                              parse_constant=invalid_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise VerificationError(f"invalid manifest JSON/UTF-8: {error}") from error
    require(isinstance(manifest, dict), "manifest JSON must be an object")
    # Compare pinned paths before reading any asset path supplied by a manifest.
    for key, path in (("font", FONT_PATH), ("license", LICENSE_PATH), ("notice", NOTICE_PATH)):
        require(isinstance(manifest.get(key), dict) and manifest[key].get("path") == path,
                f"manifest {key} path must be {path}")
    expected = build_manifest(root)
    # JSON canonicalization also distinguishes true/1 and 1/1.0, unlike dict equality.
    try:
        encoded_manifest = json.dumps(manifest, ensure_ascii=True, sort_keys=True, allow_nan=False)
    except (ValueError, RecursionError) as error:
        raise VerificationError(f"invalid manifest JSON values: {error}") from error
    encoded_expected = json.dumps(expected, ensure_ascii=True, sort_keys=True, allow_nan=False)
    require(encoded_manifest == encoded_expected, "manifest metadata differs from qualified asset evidence")
    return expected


def invalid_constant(value):
    raise VerificationError(f"invalid manifest JSON constant {value}")


def main():
    try:
        require(sys.version_info >= (3, 11), "Python 3.11+ is required")
        require(len(sys.argv) == 1, "usage: python -B scripts/verify-font-pack.py (no arguments)")
        report = verify_pack()
    except VerificationError as error:
        print(f"FAIL font pack: {error}", file=sys.stderr)
        return 1
    coverage = report["font"]["coverage"]
    print(f"Font pack revision 1 verified: OTTO, {len(report['font']['tables'])} tables; "
          f"{coverage['covered_scalar_count']}/{coverage['required_scalar_count']} corpus scalars; "
          "no corpus repertoire gaps; pinned font and OFL-1.1 SHA-256 match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
