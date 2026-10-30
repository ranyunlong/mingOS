import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import isolation_profiles


class IsolationProfileTests(unittest.TestCase):
    def test_source_v2_prepare_checks_shell_parent_status(self):
        prepare = (
            Path(__file__).resolve().parents[2]
            / "probes/linux-isolation-source-v2/vm-prepare.sh"
        ).read_text()
        self.assertIn("/proc/$$/status", prepare)
        self.assertNotIn("/proc/self/status", prepare)
        self.assertIn("grep -q '^PPid:[[:space:]]*1$'", prepare)

    def test_source_v2_probe_applies_before_steady_quota(self):
        probe = (
            Path(__file__).resolve().parents[2]
            / "probes/linux-isolation-source-v2/probe.rs"
        ).read_text()
        self.assertIn('println!("CHILD_APPLIED', probe)
        self.assertIn("read_marker(&mut child, \"CHILD_APPLIED", probe)
        self.assertLess(probe.index("read_marker(&mut child, \"CHILD_APPLIED"), probe.index("group.mark_ready()?"))

    def test_legacy_profile_keeps_original_hash_gate(self):
        profile = isolation_profiles.load_profile("legacy-v1")
        self.assertEqual(
            profile["artifactSha256"],
            {
                "mingos-isolation-probe": "d213382d37e1496c5d5eb8ba31081d712395eb7b4e7ae19c7a4d7fd8aa028ccd",
                "vm-prepare.sh": "511336a8661fc3eb991e6c8b168c5615c7420f22baf3134eba72f3a111e15ac4",
            },
        )

    def fixture(self, root):
        repo = root / "repo"
        profile_path = repo / "probes/linux-isolation-source-v2/profile.json"
        probe_dir = root / "probes"
        probe_dir.mkdir()
        for name, text in (("mingos-isolation-probe", "binary"), ("vm-prepare.sh", "prepare")):
            (probe_dir / name).write_text(text)
        files = {
            "probes/linux-isolation-source-v2/probe.rs": "probe source",
            "crates/ming-app-host/examples/isolation_probe_v2.rs": "example source",
            "Cargo.lock": "lock",
            "rust-toolchain.toml": "toolchain",
        }
        for name, text in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        hashes = {name: isolation_profiles.sha256(repo / name) for name in files}
        artifact_hashes = {name: isolation_profiles.sha256(probe_dir / name) for name in ("mingos-isolation-probe", "vm-prepare.sh")}
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps({
            "schemaVersion": 1,
            "name": "source-v2",
            "probeSource": "probes/linux-isolation-source-v2/probe.rs",
            "exampleSource": "crates/ming-app-host/examples/isolation_probe_v2.rs",
            "cargoLock": "Cargo.lock",
            "rustToolchain": "rust-toolchain.toml",
            "sourceSha256": hashes["probes/linux-isolation-source-v2/probe.rs"],
            "exampleSha256": hashes["crates/ming-app-host/examples/isolation_probe_v2.rs"],
            "cargoLockSha256": hashes["Cargo.lock"],
            "rustToolchainSha256": hashes["rust-toolchain.toml"],
            "artifacts": artifact_hashes,
        }))
        review = {"strictProbeProfile": "source-v2", "strictProbeSha256": artifact_hashes}
        return repo, profile_path, probe_dir, review

    def test_source_profile_rejects_arbitrary_review_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            _, profile_path, probe_dir, review = self.fixture(Path(temp))
            review["strictProbeSha256"] = {"mingos-isolation-probe": "0" * 64, "vm-prepare.sh": "1" * 64}
            with patch.object(isolation_profiles, "PROFILE_PATH", profile_path):
                with self.assertRaises(ValueError):
                    isolation_profiles.verify_review_and_files("source-v2", review, probe_dir)

    def test_checked_in_source_profile_has_frozen_binary_hashes(self):
        profile = isolation_profiles.load_profile("source-v2")
        self.assertEqual(
            set(profile["artifactSha256"]),
            {"mingos-isolation-probe", "vm-prepare.sh"},
        )

    def test_source_and_toolchain_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, profile_path, probe_dir, review = self.fixture(Path(temp))
            with patch.object(isolation_profiles, "PROFILE_PATH", profile_path):
                isolation_profiles.verify_review_and_files("source-v2", review, probe_dir)
                (repo / "Cargo.lock").write_text("changed")
                with self.assertRaises(ValueError):
                    isolation_profiles.verify_review_and_files("source-v2", review, probe_dir)
                (repo / "Cargo.lock").write_text("lock")
                (repo / "probes/linux-isolation-source-v2/probe.rs").write_text("changed")
                with self.assertRaises(ValueError):
                    isolation_profiles.verify_review_and_files("source-v2", review, probe_dir)

    def test_built_probe_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            _, profile_path, probe_dir, review = self.fixture(Path(temp))
            with patch.object(isolation_profiles, "PROFILE_PATH", profile_path):
                (probe_dir / "mingos-isolation-probe").write_text("tampered")
                with self.assertRaises(ValueError):
                    isolation_profiles.verify_review_and_files("source-v2", review, probe_dir)


if __name__ == "__main__":
    unittest.main()
