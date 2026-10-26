#!/usr/bin/env python3
"""Decode guest virtio-serial logs. Decoding never implies boot acceptance."""
import argparse
import base64
import json
from pathlib import Path
from bootlib import checked_path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    count = incomplete = 0
    with args.input.open("rb") as stream, (args.output / "events.jsonl").open("wb") as events:
        for line in stream:
            if not line.endswith(b"\n"):
                incomplete += 1
                continue
            entry = json.loads(line)
            if entry.get("type") == "log":
                name = entry["name"]
                if Path(name).name != name:
                    raise ValueError("invalid guest log filename")
                target = checked_path(args.output, name)
                offset = entry["offset"]
                size = target.stat().st_size if target.exists() else 0
                if offset != size or entry["encoding"] != "base64":
                    raise ValueError("guest log chunks out of sequence")
                with target.open("ab") as output:
                    output.write(base64.b64decode(entry["data"], validate=True))
            else:
                events.write(line)
            count += 1
    print(json.dumps({"eventsDecoded": count, "incompleteTrailingLines": incomplete,
                      "acceptanceStatus": "requires-independent-review"}, indent=2))

if __name__ == "__main__":
    main()
