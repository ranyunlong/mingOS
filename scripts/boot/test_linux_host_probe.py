"""Synthetic probe provenance controls; no compiler or guest is executed."""
import copy
from pathlib import Path
import tempfile
import unittest

import linux_host_qualification as q
try:
    from linux_host_probe import verify_probe_build
except ModuleNotFoundError:
    verify_probe_build = None


def write_probe_build_evidence(probe_dir, repo, cold_freeze_sha):
    """Write explicit synthetic provenance around existing harmless artifacts."""
    probe_dir, repo = Path(probe_dir), Path(repo)
    command = ['cargo', '+1.99.1', 'build', '--offline', '--locked', '--release',
               '-p', 'ming-app-host', '--example', 'isolation_probe_v2', '--no-default-features', '-j1']
    logs = probe_dir / 'command'
    logs.mkdir()
    (logs / 'stdout.log').write_bytes(b'synthetic compiler observation; never executed\n')
    (logs / 'stderr.log').write_bytes(b'synthetic compiler stderr\n')
    started = '2026-11-07T00:00:00+00:00'
    result = {'command': command, 'startedAt': started, 'finishedAt': '2026-11-07T00:00:01+00:00',
              'exitCode': 0, 'leaderExitCode': 0, 'timedOut': False, 'orphanedChildren': [],
              'cleanupComplete': True, 'error': None,
              'stdout': q.stable_hash(logs / 'stdout.log'), 'stderr': q.stable_hash(logs / 'stderr.log')}
    q.write_json(logs / 'command.json', {'command': command, 'cwd': '/repo', 'timeoutSeconds': 1800, 'startedAt': started})
    q.write_json(logs / 'result.json', result)
    receipt = {'schemaVersion': 1, 'profile': 'source-v2', 'command': command, 'result': result,
        'sourceFreezeSha256': cold_freeze_sha,
        'rustImage': 'sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80',
        'source': {name: q.stable_hash(repo / name) for name in (
            'probes/linux-isolation-source-v2/probe.rs', 'crates/ming-app-host/examples/isolation_probe_v2.rs',
            'Cargo.lock', 'rust-toolchain.toml')},
        'artifacts': {name: q.stable_hash(probe_dir / name) for name in ('mingos-isolation-probe', 'vm-prepare.sh')},
        'sourceBeforeAfterMatched': True, 'cargoRegistryBeforeAfterMatched': True,
        'formalQualification': False, 'finalSystemAccepted': False}
    q.write_json(probe_dir / 'build-receipt.json', receipt)
    return receipt


class ProbeBuildControls(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(verify_probe_build, 'shared probe provenance verifier required')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo, self.probes = self.root / 'relocated-source', self.root / 'assets/probes'
        self.probes.mkdir(parents=True)
        for name in ('probes/linux-isolation-source-v2/probe.rs',
                     'crates/ming-app-host/examples/isolation_probe_v2.rs', 'Cargo.lock', 'rust-toolchain.toml'):
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic frozen source ' + name.encode())
        (self.probes / 'mingos-isolation-probe').write_bytes(b'\x7fELF harmless never executed')
        (self.probes / 'vm-prepare.sh').write_bytes(b'#!/bin/sh\nexit 1\n')
        for name in ('mingos-isolation-probe', 'vm-prepare.sh'):
            (self.probes / name).chmod(0o755)
        self.freeze = 'a' * 64
        self.receipt = write_probe_build_evidence(self.probes, self.repo, self.freeze)

    def verify(self):
        return verify_probe_build(self.probes, self.repo, self.freeze)

    def rewrite_receipt(self, value):
        path = self.probes / 'build-receipt.json'
        path.unlink()
        q.write_json(path, value)

    def test_relocated_exact_build_and_raw_logs_verify(self):
        self.assertEqual(self.verify(), self.receipt)

    def test_missing_receipt_or_command_product_rejected(self):
        for name in ('build-receipt.json', 'command/command.json', 'command/result.json', 'command/stdout.log', 'command/stderr.log'):
            path = self.probes / name
            raw = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                self.verify()
            path.write_bytes(raw)

    def test_wrong_build_provenance_cannot_be_resealed(self):
        for key, value in (('schemaVersion', True), ('profile', 'legacy-v1'), ('sourceFreezeSha256', 'b' * 64),
                           ('rustImage', 'sha256:' + '0' * 64), ('sourceBeforeAfterMatched', False),
                           ('cargoRegistryBeforeAfterMatched', False), ('formalQualification', True),
                           ('finalSystemAccepted', True), ('command', ['cargo', 'build'])):
            receipt = copy.deepcopy(self.receipt)
            receipt[key] = value
            self.rewrite_receipt(receipt)
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify()

    def test_failed_or_uncontained_execution_rejected_even_with_matching_result_file(self):
        for key, value in (('exitCode', 1), ('exitCode', False), ('leaderExitCode', 1), ('timedOut', True),
                           ('cleanupComplete', False), ('orphanedChildren', [{'pid': 42}]), ('error', 'failed'),
                           ('command', ['cargo', 'build'])):
            receipt = copy.deepcopy(self.receipt)
            receipt['result'][key] = value
            self.rewrite_receipt(receipt)
            path = self.probes / 'command/result.json'
            path.unlink(); q.write_json(path, receipt['result'])
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify()

    def test_changed_sources_artifacts_and_raw_logs_rejected(self):
        for path in (self.repo / 'Cargo.lock', self.probes / 'mingos-isolation-probe',
                     self.probes / 'vm-prepare.sh', self.probes / 'command/stdout.log', self.probes / 'command/stderr.log'):
            raw = path.read_bytes()
            path.write_bytes(raw + b'changed')
            with self.subTest(path=path), self.assertRaises(ValueError): self.verify()
            path.write_bytes(raw)

    def test_wrong_cwd_timeout_or_started_binding_rejected(self):
        path = self.probes / 'command/command.json'
        original = q.read_json(path)
        for key, value in (('cwd', '/ambient'), ('timeoutSeconds', 1799), ('startedAt', 'different')):
            changed = dict(original, **{key: value})
            path.unlink(); q.write_json(path, changed)
            with self.subTest(key=key), self.assertRaises(ValueError): self.verify()

    def test_extra_files_empty_directories_and_links_rejected(self):
        extra = self.probes / 'extra'
        extra.write_bytes(b'extra')
        with self.assertRaises(ValueError): self.verify()
        extra.unlink(); extra.mkdir()
        with self.assertRaises(ValueError): self.verify()
        extra.rmdir(); extra.symlink_to(self.probes / 'mingos-isolation-probe')
        with self.assertRaises(ValueError): self.verify()

    def test_receipt_cannot_omit_source_or_add_artifact(self):
        receipt = copy.deepcopy(self.receipt)
        del receipt['source']['Cargo.lock']
        self.rewrite_receipt(receipt)
        with self.assertRaises(ValueError): self.verify()
        receipt = copy.deepcopy(self.receipt)
        receipt['artifacts']['other'] = receipt['artifacts']['vm-prepare.sh']
        self.rewrite_receipt(receipt)
        with self.assertRaises(ValueError): self.verify()


if __name__ == '__main__':
    unittest.main()
