#!/bin/sh
set -eu

repo=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
out=${1:?output directory required}
mkdir -p "$out"
cargo build --offline --release -p ming-app-host --example isolation_probe_v2 --manifest-path "$repo/Cargo.toml"
install -m 755 "$repo/target/release/examples/isolation_probe_v2" "$out/mingos-isolation-probe"
install -m 755 "$(dirname "$0")/vm-prepare.sh" "$out/vm-prepare.sh"
sha256sum "$out/mingos-isolation-probe" "$out/vm-prepare.sh"
