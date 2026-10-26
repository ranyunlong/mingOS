#!/usr/bin/env python3
"""Losslessly encode QMP P6 screendumps as PNG; never renders app content."""
import argparse
import binascii
import hashlib
import json
import re
import struct
import zlib
from pathlib import Path

def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)

def convert(source):
    data = source.read_bytes()
    header = re.match(rb"P6\s+(\d+)\s+(\d+)\s+255\s", data)
    if not header:
        raise ValueError("QMP P6/255 screenshot required")
    width, height = map(int, header.groups())
    pixels = data[header.end():]
    if len(pixels) != width * height * 3:
        raise ValueError("PPM pixel length mismatch")
    rows = b"".join(b"\0" + pixels[y * width * 3:(y + 1) * width * 3] for y in range(height))
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))
    target = source.with_suffix(".png")
    target.write_bytes(png)
    return {"source": str(source), "png": str(target), "width": width, "height": height,
            "sourceSha256": hashlib.sha256(data).hexdigest(), "pngSha256": hashlib.sha256(png).hexdigest()}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    report = [convert(source) for source in sorted(args.directory.glob("scanout-*.ppm"))]
    (args.directory / "scanout-pngs.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
