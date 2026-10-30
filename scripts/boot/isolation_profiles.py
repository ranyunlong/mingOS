"""Immutable legacy/source-bound strict isolation profile contracts."""
import json
import hashlib
import re
from pathlib import Path

LEGACY_PROBE_HASHES = {
    "mingos-isolation-probe": "d213382d37e1496c5d5eb8ba31081d712395eb7b4e7ae19c7a4d7fd8aa028ccd",
    "vm-prepare.sh": "511336a8661fc3eb991e6c8b168c5615c7420f22baf3134eba72f3a111e15ac4",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PROFILE_PATH = Path(__file__).resolve().parents[2] / "probes/linux-isolation-source-v2/profile.json"

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def load_profile(name, require_binary=True):
    if name == "legacy-v1":
        return {"name": name, "artifactSha256": dict(LEGACY_PROBE_HASHES)}
    if name != "source-v2":
        raise ValueError("unknown isolation profile")
    profile = json.loads(PROFILE_PATH.read_text())
    if profile.get("schemaVersion") != 1 or profile.get("name") != name:
        raise ValueError("source-v2 profile schema/name mismatch")
    for field in ("sourceSha256", "exampleSha256", "cargoLockSha256", "rustToolchainSha256"):
        if not HEX64.fullmatch(profile.get(field, "")):
            raise ValueError("source-v2 profile hash is not frozen: " + field)
    artifacts = profile.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != set(LEGACY_PROBE_HASHES):
        raise ValueError("source-v2 artifact contract mismatch")
    if require_binary and any(
        not isinstance(artifacts.get(name), str) or not HEX64.fullmatch(artifacts.get(name, ""))
        for name in artifacts
    ):
        raise ValueError("source-v2 binary hashes are not frozen")
    return {"name": name, "profile": profile, "artifactSha256": dict(artifacts)}

def verify_review_and_files(name, review, probe_dir):
    profile = load_profile(name)
    if not probe_dir:
        raise ValueError("strict isolation profile requires probe directory")
    probe_dir = Path(probe_dir)
    if name == "legacy-v1":
        expected = dict(LEGACY_PROBE_HASHES)
    else:
        if review.get("strictProbeProfile") != "source-v2":
            raise ValueError("source-v2 review must explicitly select strictProbeProfile")
        p = profile["profile"]
        repo = PROFILE_PATH.parents[2]
        bound = {
            p["probeSource"]: sha256(repo / p["probeSource"]),
            p["exampleSource"]: sha256(repo / p["exampleSource"]),
            p["cargoLock"]: sha256(repo / p["cargoLock"]),
            p["rustToolchain"]: sha256(repo / p["rustToolchain"]),
        }
        expected_bound = {
            p["probeSource"]: p["sourceSha256"],
            p["exampleSource"]: p["exampleSha256"],
            p["cargoLock"]: p["cargoLockSha256"],
            p["rustToolchain"]: p["rustToolchainSha256"],
        }
        if bound != expected_bound:
            raise ValueError("source-v2 source/toolchain contract changed")
        expected = profile["artifactSha256"]
    if review.get("strictProbeSha256") != expected:
        raise ValueError("explicit reviewed strict-probe hashes required")
    for filename, digest in expected.items():
        path = probe_dir / filename
        if path.is_symlink() or not path.is_file() or sha256(path) != digest:
            raise ValueError("frozen probe changed: " + filename)
    return {"name": name, "artifactSha256": expected}
