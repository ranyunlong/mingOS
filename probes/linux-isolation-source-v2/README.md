# Isolation probe source-v2

This directory is a new probe generation enabled only by an explicitly reviewed
"strictProbeProfile": "source-v2" session. It does not restore the historical
probe bytes or the historical data-disk identity.

Before a bundle can use this profile, profile.json must contain the hashes of
the probe source, example wrapper, Cargo.lock, rust-toolchain.toml, the
compiled mingos-isolation-probe, and vm-prepare.sh. The build is offline only
and must be followed by independent source and input review.
