"""Portable Linux export controls. Synthetic receipts never imply qualification."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import linux_host_qualification as q
import linux_host_build as builder
import test_linux_host_build as build_controls
try:
    import linux_host_export as export
except ModuleNotFoundError:
    export = None


class PortableManifestControls(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(export, 'independent Linux portable exporter must exist')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'export'
        self.root.mkdir()
        (self.root / 'source/scripts/boot').mkdir(parents=True)
        (self.root / 'source/scripts/boot/linux_host_export.py').write_text('raise RuntimeError("unverified import")\n')
        (self.root / 'evidence.json').write_text('{"synthetic":true}\n')
        self.seal()

    def seal(self):
        path = self.root / export.MANIFEST
        if path.exists():
            path.unlink()
        files, directories = export.file_inventory(self.root)
        q.write_json(path, {'schemaVersion': 1, 'profile': export.PROFILE, 'algorithm': 'sha256',
            'files': files, 'directories': directories, 'bindings': {'unitOnly': True},
            'replayDriver': 'source/scripts/boot/linux_host_export.py',
            'formalQualification': False, 'finalSystemAccepted': False})

    def test_complete_regular_manifest_verifies_and_reports_own_digest(self):
        value = export.verify_manifest(self.root)
        self.assertEqual(value['manifestSha256'], q.stable_hash(self.root / export.MANIFEST)['sha256'])

    def test_changed_missing_added_files_and_empty_directories_rejected(self):
        for change in ('changed', 'missing', 'added', 'directory'):
            self.setUp()
            if change == 'changed': (self.root / 'evidence.json').write_text('changed')
            elif change == 'missing': (self.root / 'evidence.json').unlink()
            elif change == 'added': (self.root / 'extra').write_bytes(b'added')
            else: (self.root / 'empty-added').mkdir()
            with self.subTest(change=change), self.assertRaises(ValueError):
                export.verify_manifest(self.root)

    def test_links_special_and_unsafe_manifest_paths_rejected(self):
        (self.root / 'linked').symlink_to(self.root / 'evidence.json')
        with self.assertRaises(ValueError): export.verify_manifest(self.root)
        (self.root / 'linked').unlink()
        value = q.read_json(self.root / export.MANIFEST)
        value['files']['../escape'] = value['files']['evidence.json']
        (self.root / export.MANIFEST).unlink()
        q.write_json(self.root / export.MANIFEST, value)
        with self.assertRaises(ValueError): export.verify_manifest(self.root)

    def test_duplicate_json_bool_schema_and_self_reference_rejected(self):
        path = self.root / export.MANIFEST
        value = q.read_json(path)
        for raw in ('{"schemaVersion":1,"schemaVersion":1}',
                json.dumps(dict(value, schemaVersion=True)),
                json.dumps(dict(value, files=dict(value['files'], **{export.MANIFEST: value['files']['evidence.json']})))):
            path.write_text(raw)
            with self.subTest(raw=raw[:35]), self.assertRaises(ValueError): export.verify_manifest(self.root)

    def test_manifest_rejects_permission_change_on_driver(self):
        path = self.root / 'source/scripts/boot/linux_host_export.py'
        path.chmod(0o755)
        with self.assertRaises(ValueError): export.verify_manifest(self.root)

    def test_outputs_inside_export_and_symlinked_parent_rejected(self):
        alias = self.base / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        for root in (self.root, alias):
            with self.assertRaisesRegex(ValueError, 'outside'):
                export.check_output(self.root, root / 'result.json')
        export.check_output(self.root, self.base / 'result.json')

    def test_original_absolute_paths_map_by_longest_frozen_root(self):
        mappings = {'/prep': 'runtime', '/target': 'target', '/assets': 'assets', '/assets/qualification': 'checker-assets'}
        self.assertEqual(export.map_original(self.root, mappings, '/assets/qualification/input.json'), self.root / 'checker-assets/input.json')
        with self.assertRaises(ValueError): export.map_original(self.root, mappings, '/prep/../evil')
        with self.assertRaises(ValueError): export.map_original(self.root, mappings, '/unknown/file')

    def test_failed_bootstrap_saves_external_receipt_before_export_driver_executes(self):
        output = self.base / 'rejected.json'
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(export.__file__)),
            'replay', '--export', str(self.root), '--output', str(output)], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('unverified import', result.stderr)
        receipt = q.read_json(output)
        self.assertEqual(receipt['status'], 'failed')
        self.assertFalse(receipt['formalQualification']); self.assertFalse(receipt['finalSystemAccepted'])


class CopyAdmissionControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_copy_preserves_regular_bytes_modes_and_empty_directories(self):
        source = self.base / 'source'; source.mkdir()
        (source / 'empty/nested').mkdir(parents=True)
        (source / 'script').write_bytes(b'#!/bin/sh\nexit 1\n')
        (source / 'script').chmod(0o755)
        destination = self.base / 'copy'
        export.copy_tree(source, destination)
        self.assertEqual(export.file_inventory(source), export.file_inventory(destination))
        with self.assertRaises(FileExistsError): export.copy_tree(source, destination)

    def test_copy_rejects_frozen_hash_mismatch_and_hardlinks(self):
        source = self.base / 'source'; source.write_bytes(b'original')
        with self.assertRaises(ValueError):
            export.copy_file(source, self.base / 'output', {'sha256': '0' * 64, 'bytes': 8})
        self.assertFalse((self.base / 'output').exists())
        os.link(source, self.base / 'alias')
        with self.assertRaises(ValueError): export.copy_file(source, self.base / 'output')
        self.assertFalse((self.base / 'output').exists())

    def test_create_rejects_non_runtime_root_before_output_is_created(self):
        output = self.base / 'must-not-exist'
        with self.assertRaisesRegex(ValueError, 'runtime root'):
            export.create_export(root=self.base, repo=Path('/repo'), cold_repo=self.base,
                assets_root=self.base, tools_root=self.base, output=output)
        self.assertFalse(output.exists())


class ColdEvidenceControls(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(export, 'independent Linux portable exporter must exist')
        self.control = build_controls.ColdBuildControls('test_live_fixed_linux_isolation')
        self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.c = self.control
        self.unit_elf = self.c.target / 'release/deps/ming_app_host-aabbccdd'
        for path in (self.c.target / 'release/ming-app-host', self.c.target / 'release/examples/prepare_persistent_update_fixture', self.unit_elf):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\x7fELFsynthetic never executed')
            path.chmod(0o755)
        self.finish_build()

    def finish_build(self, mutate=None, *, relink_host=False, mutate_artifacts=None):
        def stage(directory):
            q.write_json(directory / 'environment.json', self.c.observed)
            q.write_json(directory / 'cold-target.json', {'path': str(self.c.target), 'literalEmpty': True, 'entries': []})
            env = builder.build_environment(self.c.cargo, self.c.target, directory, self.c.packages,
                {label: record['path'] for label, record in self.c.value['packages'].items()})
            q.write_json(directory / 'build-environment.json', {'initial': {key: value for key, value in env.items() if not key.startswith('MING_')}, 'explicit': env})
            commands = directory / 'commands'
            commands.mkdir()
            for number, entry in enumerate(q.build_commands(), 1):
                path = commands / ('%02d' % number)
                path.mkdir()
                counts = entry['expectedSummary'] or (2, 0, 0, 0)
                text = 'test result: ok. %d passed; %d failed; %d ignored; %d measured; 0 filtered out; finished in 0.01s\n' % counts if number == 1 or number > 5 else 'synthetic command\n'
                (path / 'stdout.log').write_text(text)
                (path / 'stderr.log').write_text('Running unittests src/lib.rs (' + str(self.unit_elf) + ')\n' if number == 1 else '')
                command = {'command': entry['command'], 'cwd': str(self.c.repo), 'timeoutSeconds': entry['timeoutSeconds']}
                result = {'command': entry['command'], 'exitCode': 0, 'leaderExitCode': 0, 'timedOut': False,
                    'cleanupComplete': True, 'orphanedChildren': [], 'error': None,
                    'stdout': q.stable_hash(path / 'stdout.log'), 'stderr': q.stable_hash(path / 'stderr.log')}
                if mutate: mutate(number, command, result, path)
                q.write_json(path / 'command.json', command)
                q.write_json(path / 'result.json', result)
                if number == 1 or number > 5:
                    q.write_json(path / 'summary.json', builder.summarize(text, entry['expectedSummary']))
            pre_seals = builder.seal_artifacts(self.c.target)
            pre_artifacts = builder.built_artifacts(self.c.target)
            if relink_host:
                host = self.c.target / 'release/ming-app-host'
                host.write_bytes(host.read_bytes() + b' synthetic explicit integration relink')
            final_seals = builder.seal_artifacts(self.c.target)
            artifacts = builder.built_artifacts(self.c.target)
            if mutate_artifacts:
                mutate_artifacts(pre_artifacts, artifacts, pre_seals, final_seals)
            q.write_json(directory / 'pre-explicit-artifact-seal.json', pre_seals)
            q.write_json(directory / 'final-artifact-seal.json', final_seals)
            q.write_json(directory / 'pre-explicit-artifacts.json', pre_artifacts)
            q.write_json(directory / 'artifacts.json', artifacts)
            return {'coldBuildCommands': 10, 'target': str(self.c.target), 'formalQualification': False, 'finalSystemAccepted': False}
        q.run_stage(self.c.root, 'build', self.c.freeze_sha, stage)

    def verify(self):
        return export.verify_cold_build(self.c.root, self.c.freeze, self.c.target, self.c.packages, self.c.repo)

    def test_ten_exact_cold_commands_summaries_and_elf_bind(self):
        value = self.verify()
        self.assertEqual(value['commands'], 10)
        self.assertFalse(value['formalQualification'])

    def test_distinct_pre_explicit_and_final_host_each_bind_their_own_seal(self):
        shutil.rmtree(self.c.root / 'stages/build')
        self.finish_build(relink_host=True)
        directory = self.c.root / 'stages/build'
        pre = q.read_json(directory / 'pre-explicit-artifacts.json')
        final = q.read_json(directory / 'artifacts.json')
        self.assertNotEqual(pre['release/ming-app-host'], final['release/ming-app-host'])
        self.assertEqual(pre['release/examples/prepare_persistent_update_fixture'],
                         final['release/examples/prepare_persistent_update_fixture'])
        self.assertEqual(self.verify()['artifacts'], final)

    def test_pre_and_final_seals_cannot_be_cross_bound_even_when_stage_is_resealed(self):
        host = 'release/ming-app-host'
        for variant in ('pre-seal-final', 'final-seal-pre', 'pre-inventory-final', 'final-inventory-pre'):
            shutil.rmtree(self.c.root / 'stages/build')
            def mutate(pre, final, pre_seals, final_seals):
                if variant == 'pre-seal-final':
                    for key in ('sourceBefore', 'sourceAfter', 'artifact'):
                        pre_seals[host][key] = final[host]
                elif variant == 'final-seal-pre':
                    for key in ('sourceBefore', 'sourceAfter', 'artifact'):
                        final_seals[host][key] = pre[host]
                elif variant == 'pre-inventory-final': pre[host] = final[host]
                else: final[host] = pre[host]
            self.finish_build(relink_host=True, mutate_artifacts=mutate)
            with self.subTest(variant=variant), self.assertRaises(ValueError): self.verify()

    def test_zero_summary_cannot_be_resealed_into_acceptance(self):
        shutil.rmtree(self.c.root / 'stages/build')
        def mutate(number, command, result, path):
            if number == 6:
                (path / 'stdout.log').write_text('test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n')
                result['stdout'] = q.stable_hash(path / 'stdout.log')
        self.finish_build(mutate)
        with self.assertRaisesRegex(ValueError, 'summary'): self.verify()

    def test_wrong_command_or_timeout_even_with_zero_exit_is_rejected(self):
        for variant in ('argv', 'timeout', 'cleanup', 'failed', 'extra'):
            shutil.rmtree(self.c.root / 'stages/build')
            def mutate(number, command, result, path):
                if number == 2:
                    if variant == 'argv': command['command'] = ['true']
                    elif variant == 'timeout': result['timedOut'] = True
                    elif variant == 'cleanup': result['cleanupComplete'] = False
                    elif variant == 'failed': result['exitCode'] = 1
                    else: (path.parent / '11').mkdir()
            self.finish_build(mutate)
            with self.subTest(variant=variant), self.assertRaises(ValueError): self.verify()

    def test_original_source_package_and_current_artifact_changes_rejected(self):
        for path in (self.c.repo / 'Cargo.toml', self.c.packages / 'old/main.wasm', self.c.target / 'release/ming-app-host'):
            original = path.read_bytes()
            path.write_bytes(b'changed')
            with self.subTest(path=path), self.assertRaises(ValueError): self.verify()
            path.write_bytes(original)


class RelocatedRawControls(unittest.TestCase):
    """Actual signature/raw audit on synthetic bytes; never a guest qualification."""
    def setUp(self):
        legacy_path = Path(__file__).resolve().parents[2] / 'artifacts/persistent-update-20261104-prep/test_persistent_update_evidence.py'
        spec = importlib.util.spec_from_file_location('linux_export_legacy_controls', legacy_path)
        self.legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.legacy)
        self.factory = self.legacy.PortableCheckerTests('test_help_from_unrelated_cwd_uses_only_sealed_export')
        self.factory.setUp()
        self.addCleanup(self.factory.doCleanups)
        self.factory.add_pair()
        self.root = self.factory.root / 'relocated'
        self.root.mkdir()
        scripts = self.root / 'source/scripts/boot'
        scripts.mkdir(parents=True)
        for path in Path(__file__).parent.glob('*.py'):
            shutil.copyfile(path, scripts / path.name)
        checker = self.root / export.CHECKER_BASE
        checker.mkdir(parents=True)
        for name in ('verify-persistent-update-evidence.py', 'raw_boot_audit.py', 'input-boot1.json', 'input-boot2.json'):
            shutil.copyfile(self.factory.export / name, checker / name)
        runtime = self.root / 'runtime'
        (runtime / 'build').mkdir(parents=True)
        for name in ('inputs', 'bundle'):
            shutil.copytree(self.factory.export / name, runtime / name)
        shutil.copytree(self.factory.export / 'fixture', runtime / 'build/fixture')
        shutil.copyfile(runtime / 'inputs/session-review.json', runtime / 'review.json')
        checker_module = self.legacy.load_checker()
        signed = checker_module.verify_fixture_directory(runtime / 'build/fixture', runtime / 'inputs', q.read_json(runtime / 'review.json'))
        q.write_json(runtime / 'fixture-verification.json', signed)
        (self.root / 'pair-work').mkdir()
        shutil.copytree(self.factory.export / 'pair', self.root / 'pair-work/cold-pair')
        self.seal()

    def seal(self):
        path = self.root / export.MANIFEST
        if path.exists(): path.unlink()
        files, directories = export.file_inventory(self.root)
        q.write_json(path, {'schemaVersion': 1, 'profile': export.PROFILE, 'algorithm': 'sha256',
            'files': files, 'directories': directories, 'bindings': {'syntheticRawOnly': True},
            'replayDriver': export.DRIVER, 'formalQualification': False, 'finalSystemAccepted': False})

    def run_raw(self):
        # An isolated interpreter has no original checkout imports. Production
        # public replay additionally requires the separate semantic binding gate.
        code = ('import sys,json; from pathlib import Path; root=Path(sys.argv[1]); '
                'sys.path.insert(0,str(root/"source/scripts/boot")); import linux_host_export as e; '
                'e.verify_manifest(root); e._modules(root/"source"); print(json.dumps(e.replay_raw(root)))')
        return subprocess.run([sys.executable, '-I', '-B', '-c', code, str(self.root)],
            cwd=self.factory.other, capture_output=True, text=True, timeout=60)

    def test_relocated_raw_positive_uses_unchanged_checker_and_five_two_native_steps(self):
        before = export.verify_manifest(self.root)
        result = self.run_raw()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value['status'], 'linux-portable-raw-normal-reboot-verified')
        self.assertEqual([item['pointerHandshakes'] for item in value['boots']], [5, 2])
        self.assertFalse(value['formalQualification'])
        self.assertFalse(value['finalSystemAccepted'])
        self.assertEqual(export.verify_manifest(self.root), before)
        self.assertFalse(list(self.root.rglob('__pycache__')))

    def test_resealed_raw_qmp_removal_cannot_pass(self):
        (self.root / 'pair-work/cold-pair/boot-2/qmp.jsonl').write_bytes(b'')
        self.seal()
        result = self.run_raw()
        self.assertNotEqual(result.returncode, 0)

    def test_manifest_change_rejected_before_malicious_module_import(self):
        (self.root / 'source/scripts/boot/linux_host_runtime.py').write_text('raise RuntimeError("UNVERIFIED_IMPORT_EXECUTED")\n')
        result = self.run_raw()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('UNVERIFIED_IMPORT_EXECUTED', result.stderr)

    def test_public_replay_rejects_synthetic_raw_without_cold_runtime_bindings(self):
        output = self.factory.other / 'reject.json'
        result = subprocess.run([sys.executable, '-I', '-B', str(self.root / export.DRIVER),
            'replay', '--export', str(self.root), '--output', str(output)],
            cwd=self.factory.other, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        value = q.read_json(output)
        self.assertEqual(value['status'], 'failed')
        self.assertFalse(value['formalQualification'])
        self.assertFalse(value['finalSystemAccepted'])


class FullBindingControls(unittest.TestCase):
    """Full public relocated metadata gate on explicitly synthetic evidence."""
    def setUp(self):
        import linux_host_runtime as runtime
        import linux_host_freeze as freezer
        import test_linux_host_probe as probe_controls
        self.raw = RelocatedRawControls('test_relocated_raw_positive_uses_unchanged_checker_and_five_two_native_steps')
        self.raw.setUp()
        self.addCleanup(self.raw.doCleanups)
        self.cold = ColdEvidenceControls('test_ten_exact_cold_commands_summaries_and_elf_bind')
        self.cold.setUp()
        self.addCleanup(self.cold.doCleanups)
        self.root, c = self.raw.root, self.cold.c
        self.c = c
        source, base = self.root / 'source', self.root / 'runtime'
        assets, tools, receipts = self.root / 'assets', self.root / 'tools', self.root / 'receipts/0'
        assets.mkdir(); tools.mkdir(); receipts.mkdir(parents=True)
        # Bind the synthetic signed payload to the synthetic cold outputs.
        shutil.copyfile(base / 'inputs/payload/prepare-persistent-update-fixture',
            c.target / 'release/examples/prepare_persistent_update_fixture')
        for label, package in (('bootstrap', 'text-entry-demo'), ('old', 'old'), ('new', 'new')):
            directory = c.packages / package
            shutil.copyfile(base / 'inputs/payload/packages' / label / 'main.wasm', directory / 'main.wasm')
            info = q.read_json(directory / 'build-info.json')
            info['packagedSha256'] = q.stable_hash(directory / 'main.wasm')['sha256']
            (directory / 'build-info.json').unlink(); q.write_json(directory / 'build-info.json', info)
            receipt = q.read_json(directory / 'build-receipt.json')
            receipt.update(packagedSha256=info['packagedSha256'], package=info)
            (directory / 'build-receipt.json').unlink(); q.write_json(directory / 'build-receipt.json', receipt)
            c.value['packages'][package]['files'] = q.tree_inventory(directory)
        # Same complete synthetic source for cold/current snapshots; package
        # build-info still binds its exact unchanged Cargo inputs.
        shutil.copytree(source / 'scripts', c.repo / 'scripts')
        for name in ('Cargo.lock', 'Cargo.toml'):
            shutil.copyfile(c.repo / name, source / name)
        (source / 'rust-toolchain.toml').write_text('[toolchain]\nchannel="1.99.1"\n')
        shutil.copyfile(source / 'rust-toolchain.toml', c.repo / 'rust-toolchain.toml')
        source_names = ('crates/ming-app-host/src/graphics/desktop_shell.rs',
                        'crates/ming-app-host/src/desktop_model.rs', 'crates/ming-app-host/src/desktop_native.rs')
        probe_source = 'probes/linux-isolation-source-v2/probe.rs'
        probe_example = 'crates/ming-app-host/examples/isolation_probe_v2.rs'
        for name in (*source_names, probe_source, probe_example):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic source never compiled\n')
            (c.repo / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, c.repo / name)
        probe = assets / 'probes'; probe.mkdir()
        for name in ('mingos-isolation-probe', 'vm-prepare.sh'):
            (probe / name).write_bytes(b'\x7fELF synthetic probe never executed' if '.' not in name else b'#!/bin/sh\nexit 1\n')
            (probe / name).chmod(0o755)
        for directory in (source, c.repo):
            shutil.copyfile(probe / 'vm-prepare.sh', directory / 'probes/linux-isolation-source-v2/vm-prepare.sh')
        probe_hashes = {p.name: q.stable_hash(p)['sha256'] for p in probe.iterdir()}
        profile = {'schemaVersion': 1, 'name': 'source-v2', 'probeSource': probe_source,
            'exampleSource': probe_example, 'cargoLock': 'Cargo.lock', 'rustToolchain': 'rust-toolchain.toml',
            'sourceSha256': q.stable_hash(source / probe_source)['sha256'],
            'exampleSha256': q.stable_hash(source / probe_example)['sha256'],
            'cargoLockSha256': q.stable_hash(source / 'Cargo.lock')['sha256'],
            'rustToolchainSha256': q.stable_hash(source / 'rust-toolchain.toml')['sha256'], 'artifacts': probe_hashes}
        for repo in (source, c.repo):
            (repo / 'probes/linux-isolation-source-v2').mkdir(parents=True, exist_ok=True)
            q.write_json(repo / 'probes/linux-isolation-source-v2/profile.json', profile)
        (receipts / 'node').write_bytes(b'synthetic Node never executed')
        c.value['environment']['replayNode'] = {'path': str(c.base / 'node'), 'sha256': q.stable_hash(receipts / 'node')['sha256']}
        c.inspection.unlink()
        q.write_json(c.inspection, [{'Id': freezer.RUST_IMAGE, 'RootFS': {'Layers': ['sha256:' + 'a' * 64]}}])
        c.value['environment']['rustImage']['inspectionReceiptSha256'] = q.stable_hash(c.inspection)['sha256']
        for name, path in (('image.json', c.inspection), ('capacity.json', c.capacity_receipt)):
            shutil.copyfile(path, receipts / name)
        c.value['source']['files'] = q.source_inventory(c.repo)
        c.freeze.unlink()
        c.freeze = c.root / 'freeze.json'
        q.write_json(c.freeze, c.value)
        c.freeze_sha = q.stable_hash(c.freeze)['sha256']
        probe_controls.write_probe_build_evidence(probe, source, c.freeze_sha)
        probe_build = q.read_json(probe / 'build-receipt.json')
        shutil.rmtree(c.root / 'stages/prepare'); shutil.rmtree(c.root / 'stages/build')
        def prepared(stage):
            q.write_json(stage / 'admission.json', {'freezeSha256': c.freeze_sha, 'packageReplayFiles': c.value['packageReplayFiles'],
                'storage': c.value['storage'], 'formalQualification': False})
            return {'syntheticOnly': True}
        q.run_stage(c.root, 'prepare', c.freeze_sha, prepared)
        (c.root / 'prepare-result.json').unlink()
        q.write_json(c.root / 'prepare-result.json', {'status': 'passed', 'freezeSha256': c.freeze_sha,
            'formalQualification': False, 'finalSystemAccepted': False})
        self.cold.finish_build()
        export.copy_tree(c.root, self.root / 'cold-build')
        export.copy_tree(c.repo, self.root / 'cold-source')
        export.copy_tree(c.target, self.root / 'target')
        export.copy_tree(c.packages, self.root / 'packages')
        checker = assets / 'checker'; checker.mkdir()
        historical = Path(__file__).resolve().parents[2] / 'artifacts/persistent-update-20261104-prep'
        for name in runtime.CHECKER_FILES:
            if name not in ('input-geometry.json', 'geometry.stdout.log', 'geometry-execution.json'):
                shutil.copyfile(historical / name, checker / name)
        points = [[1148,12],[880,478],[676,766],[96,104],[1238,12]]
        geometry = {'schemaVersion': 1, 'width': 1280, 'height': 800, 'boot1Points': points,
                    'boot2Points': [points[2], points[4]], 'app2Dock': {'index': 1, 'count': 2}}
        (checker / 'geometry.stdout.log').write_text('MING_PERSISTENT_UPDATE_GEOMETRY ' + json.dumps(geometry) + '\n'
            'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n')
        q.write_json(checker / 'input-geometry.json', {'schemaVersion': 1, 'geometry': geometry,
            'sourceSha256': {name: q.stable_hash(source / name)['sha256'] for name in source_names},
            'test': {'matched': 1, 'passed': 1, 'failed': 0, 'ignored': 0}, 'stdoutSha256': q.stable_hash(checker / 'geometry.stdout.log')['sha256']})
        first = c.root / 'stages/build/commands/01'
        execution = {'schemaVersion': 1, 'binaryPath': str(self.cold.unit_elf), 'binarySha256': q.stable_hash(self.cold.unit_elf)['sha256'],
            'argv': [str(self.cold.unit_elf), '--exact', 'desktop_native::input_tests::persistent_update_two_app_input_geometry', '--nocapture'],
            'exitCode': 0, 'stdoutSha256': q.stable_hash(checker / 'geometry.stdout.log')['sha256'],
            'coldBuildResultSha256': q.stable_hash(c.root / 'stages/build/result.json')['sha256'],
            'coldBuildFreezeSha256': c.freeze_sha, 'firstCommandResult': q.stable_hash(first / 'result.json'),
            'firstCommandStderr': q.stable_hash(first / 'stderr.log'), 'cargoLock': q.stable_hash(c.repo / 'Cargo.lock'),
            'rustToolchain': q.stable_hash(c.repo / 'rust-toolchain.toml'), 'formalQualification': False, 'finalSystemAccepted': False}
        q.write_json(checker / 'geometry-execution.json', execution)
        for name in runtime.CHECKER_FILES:
            if (base / name).exists(): (base / name).unlink()
            shutil.copyfile(checker / name, base / name)
            destination = self.root / export.CHECKER_BASE / name
            if destination.exists(): destination.unlink()
            shutil.copyfile(checker / name, destination)
        for number in (1, 2):
            name = 'input-boot%d.json' % number
            shutil.copyfile(historical / name, checker / name)
            shutil.copyfile(checker / name, base / name)
        q.write_json(tools / 'tools-image.json', [{'Id': runtime.TOOLS_IMAGE, 'RootFS': {'Layers': ['sha256:' + 'b' * 64]}}])
        tool_files = {'/usr/bin/synthetic': {'sha256': '1' * 64, 'bytes': 1}}
        q.write_json(tools / 'tools-manifest.json', {'schemaVersion': 1, 'files': tool_files})
        paths = {'repo': '/repo', 'build_root': str(c.root), 'build_freeze': str(c.freeze), 'target': str(c.target),
            'packages_root': str(c.packages), 'checker_dir': '/assets/checker', 'input1': '/assets/checker/input-boot1.json',
            'input2': '/assets/checker/input-boot2.json', 'tools_receipt': '/runtime/tools-image.json',
            'tools_manifest': '/runtime/tools-manifest.json', 'probe_dir': '/assets/probes'}
        frozen = {'schemaVersion': 1, 'profile': 'persistent-update-linux-runtime', 'paths': paths,
            'runtimeSource': q.source_inventory(source), 'coldBuild': {'root': str(c.root), 'freeze': str(c.freeze),
                'freezeSha256': c.freeze_sha, 'buildResult': q.stable_hash(c.root / 'stages/build/result.json'),
                'target': str(c.target), 'artifacts': builder.built_artifacts(c.target), 'sourceExtension': {}},
            'geometryExecution': execution, 'packages': {label: {'path': str(c.packages / label), 'files': row['files']} for label, row in c.value['packages'].items()},
            'checker': {name: q.stable_hash(checker / name) for name in runtime.CHECKER_FILES},
            'inputs': {str(n): {'path': paths['input%d' % n], **q.stable_hash(checker / ('input-boot%d.json' % n)),
                'steps': 5 if n == 1 else 2, 'minimumSeconds': 670 if n == 1 else 520} for n in (1, 2)},
            'tools': {'image': runtime.TOOLS_IMAGE, 'files': tool_files,
                'aptClosureQualification': 'development image observations only; no full offline APT closure claim'},
            'toolReceipts': {paths['tools_receipt']: q.stable_hash(tools / 'tools-image.json'), paths['tools_manifest']: q.stable_hash(tools / 'tools-manifest.json')},
            'probes': {'contract': {'name': 'source-v2', 'artifactSha256': probe_hashes},
                       'files': q.tree_inventory(probe), 'build': probe_build},
            'storage': {'probe': c.storage_receipt, 'reopen': c.storage_receipt}, 'formalQualification': False, 'finalSystemAccepted': False}
        q.write_json(base / 'runtime-freeze.json', frozen)
        self.runtime_sha = q.stable_hash(base / 'runtime-freeze.json')['sha256']
        q.write_json(base / 'execution-budget.json', {'syntheticOnly': True, 'formalQualification': False, 'finalSystemAccepted': False})
        review_base = runtime.review_base(base, dict(frozen, paths=dict(paths, repo=str(source))))
        q.write_json(base / 'build/review-base.json', review_base)
        (base / 'review.json').unlink(); q.write_json(base / 'review.json', dict(review_base, reviewed=True, reviewer='synthetic metadata control'))
        bundle = q.read_json(base / 'bundle/bundle.json')
        bundle.update(sessionReview=q.read_json(base / 'review.json'), reviewSha256=q.stable_hash(base / 'review.json')['sha256'])
        (base / 'bundle/bundle.json').unlink(); q.write_json(base / 'bundle/bundle.json', bundle)
        for number in (1, 2):
            report_path = self.root / ('pair-work/cold-pair/boot-%d/result.json' % number)
            report = q.read_json(report_path)
            report['bundleIdentity'] = q.stable_hash(base / 'bundle/bundle.json')['sha256']
            report_path.unlink(); q.write_json(report_path, report)
        q.write_json(base / 'runtime-prepare-result.json', {'status': 'passed', 'runtimeFreezeSha256': self.runtime_sha,
            'formalQualification': False, 'finalSystemAccepted': False})
        def admission(stage):
            q.write_json(stage / 'admission.json', {'runtimeFreezeSha256': self.runtime_sha, 'coldBuild': frozen['coldBuild'], 'guestStarted': False})
            return {'syntheticOnly': True}
        q.run_stage(base, 'prepare', self.runtime_sha, admission)
        def products(stage, value):
            q.write_json(stage / 'external-products.json', value)
            return {'syntheticOnly': True}
        q.run_stage(base, 'build', self.runtime_sha, lambda stage: products(stage, runtime._fixture_products(base)))
        q.run_stage(base, 'bundle', self.runtime_sha, lambda stage: products(stage,
            {'bundle': q.tree_inventory(base / 'bundle'), 'review': q.stable_hash(base / 'review.json')}))
        q.run_stage(base, 'admission', self.runtime_sha, lambda stage: products(stage,
            {'bundle': q.tree_inventory(base / 'bundle'), 'review': q.stable_hash(base / 'review.json'), 'runtimeFreeze': q.stable_hash(base / 'runtime-freeze.json')}))
        work = self.root / 'pair-work'; (work / 'disks').mkdir()
        image = work / 'disks/linux-runtime-128.raw'
        with image.open('wb') as stream: stream.truncate(128 * 1024 ** 2)
        disk = {'schemaVersion': 1, 'task': 'boot-infra-20261025', 'format': 'raw', 'filesystem': 'ext4',
            'sizeMiB': 128, 'image': 'disks/linux-runtime-128.raw',
            'uuid': q.read_json(work / 'cold-pair/boot-1/result.json')['diskUuid'], 'serial': 'synthetic',
            'creationSha256': q.stable_hash(image)['sha256']}
        q.write_json(work / 'disks/linux-runtime-128.json', disk)
        disk.update(imagePath='/work/synthetic/disks/linux-runtime-128.raw', manifestPath='/work/synthetic/disks/linux-runtime-128.json')
        def pair(stage):
            q.write_json(stage / 'external-products.json', {'pairRoot': '/work/synthetic/cold-pair', 'pairFiles': q.tree_inventory(work / 'cold-pair'),
                'workspaceRoot': '/work/synthetic', 'workspaceFiles': q.tree_inventory(work), 'disk': disk, 'runtimeFreezeSha256': self.runtime_sha})
            return {'twoBootsStrongRawVerified': True, 'bundleIdentity': q.stable_hash(base / 'bundle/bundle.json')['sha256'],
                'diskUuid': disk['uuid'], 'bootIds': ['boot', 'cold'], 'formalQualification': False, 'finalSystemAccepted': False}
        q.run_stage(base, 'pair', self.runtime_sha, pair)
        manifest = self.root / export.MANIFEST
        manifest.unlink()
        roots = {'runtime': '/prep', 'source': '/repo', 'coldSource': str(c.repo), 'coldBuild': str(c.root),
                 'target': str(c.target), 'packages': str(c.packages), 'assets': '/assets', 'tools': '/runtime', 'workspace': '/work/synthetic'}
        self.binding = {'roots': roots, 'receiptRoots': {str(c.base): 'receipts/0'}, 'runtimeFreezeSha256': self.runtime_sha,
            'coldFreezeSha256': c.freeze_sha, 'coldOriginalRepo': str(c.repo), 'geometryBinary': 'target/' + self.cold.unit_elf.relative_to(c.target).as_posix(),
            'targetFiles': q.tree_inventory(self.root / 'target')}
        self.seal()

    def seal(self):
        manifest = self.root / export.MANIFEST
        if manifest.exists(): manifest.unlink()
        files, directories = export.file_inventory(self.root)
        q.write_json(manifest, {'schemaVersion': 1, 'profile': export.PROFILE, 'algorithm': 'sha256', 'files': files,
            'directories': directories, 'bindings': self.binding, 'replayDriver': export.DRIVER,
            'formalQualification': False, 'finalSystemAccepted': False})

    def command(self, action='verify'):
        output = self.raw.factory.other / 'binding-result.json'
        return subprocess.run([sys.executable, '-I', '-B', str(self.root / export.DRIVER), action,
            '--export', str(self.root), '--output', str(output)], cwd=self.raw.factory.other,
            capture_output=True, text=True, timeout=90), output

    def test_public_verify_relocates_complete_synthetic_freeze_stage_tool_geometry_disk_chain(self):
        result, output = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        value = q.read_json(output)
        self.assertEqual(value['status'], 'linux-portable-bindings-verified')
        self.assertFalse(value['formalQualification']); self.assertFalse(value['finalSystemAccepted'])

    def test_resealed_cold_log_change_rejected_by_stage_binding(self):
        (self.root / 'cold-build/stages/build/commands/06/stdout.log').write_text('forged summary')
        self.seal()
        result, output = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(q.read_json(output)['status'], 'failed')

    def test_resealed_extra_importable_source_rejected_before_any_import(self):
        (self.root / 'source/scripts/boot/fcntl.py').write_text('raise RuntimeError("UNFROZEN_IMPORT_EXECUTED")\n')
        self.seal()
        result, output = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('UNFROZEN_IMPORT_EXECUTED', result.stderr)
        self.assertEqual(q.read_json(output)['status'], 'failed')

    def test_resealed_independent_probe_command_log_tamper_rejected(self):
        (self.root / 'assets/probes/command/stdout.log').write_text('forged probe build log')
        self.seal()
        result, output = self.command()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(q.read_json(output)['status'], 'failed')

    def test_public_replay_checks_complete_synthetic_binding_and_raw_chain(self):
        result, output = self.command('replay')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = q.read_json(output)
        self.assertEqual(value['status'], 'linux-portable-raw-normal-reboot-verified')
        self.assertFalse(value['formalQualification']); self.assertFalse(value['finalSystemAccepted'])


if __name__ == '__main__':
    unittest.main()
