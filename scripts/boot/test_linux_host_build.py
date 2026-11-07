"""Cold-build controls. Synthetic commands never invoke Cargo or a guest."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import linux_host_qualification as q
import linux_host_freeze as freezer
import linux_host_storage as storage
try:
    import linux_host_build as b
except ModuleNotFoundError:
    b = None


class ColdProfileBuildControls(unittest.TestCase):
    def test_uninstall_manifest_is_install_demo_sdk6_version_one(self):
        profile = 'persistent-uninstall-linux-host'
        files = {'compiler.wasm': 'a', 'main.wasm': 'b', 'app.json': 'c'}
        inputs = {'samples/persistent-uninstall-demo/app.json': 'source'}
        info = {'inputSha256': inputs, 'rawSha256': 'a', 'packagedSha256': 'b'}
        manifest = {'schemaVersion': 1, 'id': 'dev.ming.install-demo', 'version': '1.0.0', 'sdkVersion': 6, 'entry': 'main.wasm'}
        receipt = {'schemaVersion': 1, 'status': 'passed', 'sample': 'persistent-uninstall-demo', 'fixture': None,
            'offline': True, 'compilerExitCode': 0, 'compilerSignal': None, 'kotlin': '2.3.20',
            'formalQualification': False, 'finalSystemAccepted': False, 'rawToPackageReplayVerified': True,
            'sourceInputsStable': True, 'dependencyInputsStable': True, 'inputSha256': inputs,
            'rawSha256': 'a', 'packagedSha256': 'b', 'manifestSha256': 'c', 'package': info,
            'command': ['--offline', '--rerun-tasks', '--no-build-cache', '--no-configuration-cache',
                        ':persistent-uninstall-demo:compileProductionExecutableKotlinWasmWasi']}
        data = {'build-receipt.json': receipt, 'build-info.json': info, 'app.json': manifest}
        with mock.patch.object(q, 'read_json', side_effect=lambda path: data[path.name]), \
             mock.patch.object(q, 'stable_hash', side_effect=lambda path: {'sha256': files[path.name]}):
            b.verify_package(Path('/packages/uninstall'), 'uninstall', {name: {'sha256': sha} for name, sha in inputs.items()}, profile)
            for key, value in (('id', 'dev.ming.persistent-uninstall-demo'), ('version', '0.1.0'), ('sdkVersion', 5)):
                original = manifest[key]; manifest[key] = value
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identity'):
                    b.verify_package(Path('/packages/uninstall'), 'uninstall', {name: {'sha256': sha} for name, sha in inputs.items()}, profile)
                manifest[key] = original
            with self.assertRaises(ValueError):
                b.verify_package(Path('/packages/uninstall'), 'uninstall', {}, q.DEFAULT_COLD_PROFILE)

    def test_incomplete_uninstall_input_inventory_is_not_accepted(self):
        packages = {key: {'files': {}} for key in q.package_contract('persistent-uninstall-linux-host')}
        value = {'profile': 'persistent-uninstall-linux-host', 'packages': packages,
                 'source': {'files': {}}, 'cargoLock': {}, 'cargo': {'files': {'crate': {}}}}
        snapshot = {'packages': {key: {} for key in packages}, 'source': {}, 'cargoLock': {}, 'registry': {'crate': {}}}
        b.verify_inputs(snapshot, value)
        del snapshot['packages']['uninstall']
        with self.assertRaisesRegex(ValueError, 'package'):
            b.verify_inputs(snapshot, value)

    def test_uninstall_environment_and_two_fixed_outputs_are_profile_specific(self):
        profile = 'persistent-uninstall-linux-host'
        paths = {key: key for key in ('read-file', 'image-viewer', 'text-entry-demo', 'uninstall')}
        env = b.build_environment(Path('/cargo'), Path('/target'), Path('/stage'), Path('/packages'), paths, profile)
        self.assertEqual(env['MING_UNINSTALL_FIXTURE_WASM'], str(Path('/packages/uninstall/main.wasm')))
        self.assertEqual(env['MING_TEST_PRIVATE_WORKER'], str(Path('/target/release/ming-app-host')))
        self.assertEqual({key for key in env if key.startswith('MING_')}, {'MING_UNINSTALL_FIXTURE_WASM', 'MING_TEST_PRIVATE_WORKER'})
        self.assertEqual(b.artifact_paths(profile), ('release/ming-app-host', 'release/examples/prepare_persistent_uninstall_fixture'))
        self.assertEqual(b.artifact_paths(), b.ARTIFACT_PATHS)
        with self.assertRaises(ValueError):
            b.artifact_paths('unknown')
        with self.assertRaises(ValueError):
            b.build_environment(Path('/cargo'), Path('/target'), Path('/stage'), Path('/packages'), dict(paths, old='old'), profile)


class ColdBuildControls(unittest.TestCase):
    profile = q.DEFAULT_COLD_PROFILE

    def setUp(self):
        self.assertIsNotNone(b, 'independent cold-build controller must exist')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / 'repo'
        self.root = self.base / 'qualification'
        self.cargo = self.base / 'cargo'
        self.target = self.base / 'target'
        self.packages = self.base / 'packages'
        for directory in (self.repo, self.root, self.cargo, self.target, self.packages):
            directory.mkdir(mode=0o700)
        (self.repo / 'Cargo.lock').write_text('version = 4\n')
        (self.repo / 'Cargo.toml').write_text('[workspace]\nmembers=[]\n')
        (self.cargo / 'registry').mkdir()
        (self.cargo / 'registry/crate').write_bytes(b'frozen crate')
        self.inspection = self.base / 'image.json'
        q.write_json(self.inspection, [{'Id': b.RUST_IMAGE}])
        (self.root / 'replay').mkdir()
        (self.root / 'replay/control-evidence').write_bytes(b'unit-only replay evidence')
        (self.root / 'storage-control').mkdir(mode=0o700)
        with mock.patch.object(storage, '_space', return_value={'unitOnly': True}):
            self.storage_receipt = storage.probe(self.root / 'storage-control', 0)
        self.capacity_receipt = self.base / 'capacity.json'
        capacity = {'hostAvailableBytes': 1024 ** 4, 'linuxAvailableBytes': 1024 ** 4,
                    'backingDiskPath': 'C:\\Docker\\docker_data.vhdx', 'hostFilesystem': 'NTFS'}
        q.write_json(self.capacity_receipt, capacity)
        self.contract = q.package_contract(self.profile)
        self.paths = {label: label for label in self.contract}
        for label in self.contract:
            self.make_package(label)
        for label in ('read-file', 'image-viewer'):
            import shutil
            shutil.copytree(self.packages / label, self.repo / 'artifacts' / label)
        self.freeze = self.base / 'freeze.json'
        self.value = {'schemaVersion': 1, 'profile': self.profile,
            'source': {'files': q.source_inventory(self.repo)},
            'cargoLock': q.stable_hash(self.repo / 'Cargo.lock'),
            'cargo': {'files': q.tree_inventory(self.cargo / 'registry')},
            'packages': {label: {'path': label, 'files': q.tree_inventory(self.packages / label)} for label in self.contract},
            'packageReplayFiles': q.tree_inventory(self.root / 'replay'),
            'storage': {'probe': self.storage_receipt, 'reopen': self.storage_receipt,
                'capacityReceipt': str(self.capacity_receipt), 'capacityReceiptSha256': q.stable_hash(self.capacity_receipt)['sha256'],
                'capacityObserved': capacity, 'budget': freezer.capacity_budget()},
            'environment': {'rustImage': {'id': b.RUST_IMAGE,
                'inspectionReceipt': str(self.inspection), 'inspectionReceiptSha256': q.stable_hash(self.inspection)['sha256']}}}
        q.write_json(self.freeze, self.value)
        self.freeze_sha = q.stable_hash(self.freeze)['sha256']
        def prepared(directory):
            q.write_json(directory / 'admission.json', {'freezeSha256': self.freeze_sha,
                'packageReplayFiles': self.value['packageReplayFiles'], 'storage': self.value['storage'], 'formalQualification': False})
            return {'admitted': True}
        q.run_stage(self.root, 'prepare', self.freeze_sha, prepared)
        q.write_json(self.root / 'prepare-result.json', {'status': 'passed', 'freezeSha256': self.freeze_sha,
            'formalQualification': False, 'finalSystemAccepted': False})
        self.observed = {'uid': 65534, 'gid': 65534, 'interfaces': ['lo'], 'capEff': 0,
            'capPrm': 0, 'noNewPrivs': 1, 'seccomp': 2, 'rootReadOnly': True,
            'sourceReadOnly': True, 'cpuMax': [200000, 100000], 'memoryMax': 4294967296, 'pidsMax': 256}

    def make_package(self, label):
        directory = self.packages / label
        directory.mkdir()
        sample, fixture = self.contract[label]
        raw, main = b'raw wasm ' + label.encode(), b'packaged wasm ' + label.encode()
        manifest = {'schemaVersion': 1, 'id': 'dev.ming.install-demo' if label == 'uninstall' else 'dev.ming.' + sample,
            'version': '1.0.0' if label == 'uninstall' else '0.2.0' if fixture in ('new', 'absent-write') else '0.1.0',
            'sdkVersion': 1 if sample == 'read-file' else 2 if sample == 'image-viewer' else 6,
            'entry': 'main.wasm'}
        for name, data in [('compiler.wasm', raw), ('main.wasm', main)]:
            (directory / name).write_bytes(data)
        q.write_json(directory / 'app.json', manifest)
        files = {name: q.stable_hash(self.repo / name)['sha256'] for name in ('Cargo.toml', 'Cargo.lock')}
        info = {'rawSha256': hashlib.sha256(raw).hexdigest(), 'packagedSha256': hashlib.sha256(main).hexdigest(), 'inputSha256': files}
        q.write_json(directory / 'build-info.json', info)
        receipt = {'schemaVersion': 1, 'status': 'passed', 'sample': sample, 'fixture': fixture,
            'offline': True, 'compilerExitCode': 0, 'compilerSignal': None, 'formalQualification': False,
            'finalSystemAccepted': False, 'rawToPackageReplayVerified': True,
            'sourceInputsStable': True, 'dependencyInputsStable': True, 'kotlin': '2.3.20',
            'inputSha256': files, 'manifestSha256': q.stable_hash(directory / 'app.json')['sha256'],
            **{key: info[key] for key in ('rawSha256', 'packagedSha256')}, 'package': info,
            'command': ['java', '--offline', '--rerun-tasks', '--no-build-cache', '--no-configuration-cache',
                *(['-PpersistentUpdateFixture=' + fixture] if fixture else []), ':' + sample + ':compileProductionExecutableKotlinWasmWasi']}
        q.write_json(directory / 'build-receipt.json', receipt)

    def run_build(self):
        # Filesystem/lease operations are real; cold-build capacity is synthetic
        # because controls run on a 512MiB tmpfs and never invoke the compiler.
        def control_capacity(fd, required):
            self.assertEqual(required, sum(freezer.capacity_budget().values()))
            return {'unitOnly': True}
        self.readonly_inputs = []
        with mock.patch.object(q, 'observe_environment', return_value=self.observed), mock.patch.object(b, 'require_readonly', side_effect=lambda p: self.readonly_inputs.append(Path(p))), mock.patch.object(storage, '_space', side_effect=control_capacity):
            return b.run_build(self.root, self.repo, self.freeze, self.cargo, self.target, self.packages, self.profile)

    def replace_freeze(self, mutate):
        value = copy.deepcopy(self.value)
        mutate(value)
        self.freeze.unlink()
        q.write_json(self.freeze, value)

    def simulated_execute(self, directory, command, timeout, **kwargs):
        index = len(self.calls)
        self.calls.append((command, timeout, kwargs))
        if index == 4:
            for file in (self.target / name for name in b.artifact_paths(self.profile)):
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(b'\x7fELF' + b'control executable')
                file.chmod(0o755)
        expected = q.build_commands(self.profile)[index]['expectedSummary']
        text = ('test result: ok. %d passed; %d failed; %d ignored; %d measured; 9 filtered out; finished in 0.01s\n' % expected) if expected else 'control command passed\n'
        if index == 0:
            text = 'test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n'
        return self.original_execute(directory, [sys.executable, '-c', 'print(' + repr(text) + ',end="")'], 5,
            cwd=kwargs['cwd'], env=kwargs['env'])

    def test_live_fixed_linux_isolation(self):
        q.validate_environment(q.observe_environment(Path('/repo')))
        self.assertEqual(os.geteuid(), 65534)
        b.require_readonly(Path('/repo'))
        with self.assertRaisesRegex(ValueError, 'read-only'):
            b.require_readonly(self.inspection)

    def test_target_rejects_any_existing_metadata(self):
        (self.target / '.rustc_info.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.run_build()

    def test_target_rejects_wrong_owner_mode_and_symlink(self):
        self.target.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'mode|owner'):
            self.run_build()
        self.target.chmod(0o700)
        self.target.rmdir()
        self.target.symlink_to(self.cargo, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_build()

    def test_wrong_freeze_and_absent_prepare_never_execute(self):
        self.replace_freeze(lambda value: value.update(profile='old-apfs'))
        with mock.patch.object(q, 'execute') as execute:
            with self.assertRaises(ValueError):
                self.run_build()
            execute.assert_not_called()

    def test_network_or_resource_mismatch_never_executes(self):
        self.observed['interfaces'] = ['eth0', 'lo']
        with mock.patch.object(q, 'execute') as execute:
            with self.assertRaises(ValueError):
                self.run_build()
            execute.assert_not_called()

    def test_environment_removes_profile_flags_wrappers_and_loader_injection(self):
        hostile = {'RUSTFLAGS': '-Copt-level=0', 'CARGO_ENCODED_RUSTFLAGS': 'bad',
            'RUSTC_WRAPPER': '/bad', 'RUSTC_WORKSPACE_WRAPPER': '/bad',
            'CARGO_PROFILE_RELEASE_OPT_LEVEL': '0', 'CARGO_PROFILE_RELEASE_DEBUG': 'true',
            'CARGO_BUILD_RUSTFLAGS': 'bad', 'CC': '/bad', 'LD_PRELOAD': '/bad', 'PATH': '/bad'}
        with mock.patch.dict(os.environ, hostile):
            env = b.build_environment(self.cargo, self.target, self.root, self.packages, self.paths)
        for key in hostile:
            self.assertNotEqual(env.get(key), hostile[key])
        self.assertEqual(env['CARGO_TARGET_DIR'], str(self.target))
        self.assertEqual(env['MING_UPDATE_BOOTSTRAP_WASM'], str(self.packages / 'text-entry-demo/main.wasm'))

    def test_cargo_home_config_injection_is_rejected(self):
        (self.cargo / 'config.toml').write_text('[build]\nrustflags=["-Copt-level=0"]\n')
        with self.assertRaisesRegex(ValueError, 'config'):
            self.run_build()

    def test_package_changed_source_and_receipt_flags_are_rejected(self):
        (self.packages / 'old/main.wasm').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'package'):
            self.run_build()

    def test_package_path_escape_is_rejected(self):
        self.replace_freeze(lambda value: value['packages']['old'].update(path='../repo'))
        with self.assertRaises(ValueError):
            self.run_build()

    def test_registry_or_image_inspection_changes_are_rejected(self):
        (self.cargo / 'registry/crate').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'registry'):
            self.run_build()

    def test_first_failure_stops_and_is_durable(self):
        calls = []
        original = q.execute
        def fail(directory, command, timeout, **kwargs):
            calls.append(command)
            return original(directory, [sys.executable, '-c', 'print("kept");raise SystemExit(7)'], 5, **kwargs)
        with mock.patch.object(q, 'execute', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Cargo'):
                self.run_build()
        self.assertEqual(len(calls), 1)
        result = q.read_json(self.root / 'stages/build/result.json')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['finalSystemAccepted'])
        self.assertTrue((self.root / 'stages/build/commands/01/stdout.log').is_file())
        with self.assertRaises(FileExistsError):
            self.run_build()

    def test_zero_all_target_summaries_fail_closed(self):
        original = q.execute
        def no_summary(directory, command, timeout, **kwargs):
            return original(directory, [sys.executable, '-c', 'print("nothing tested")'], 5, **kwargs)
        with mock.patch.object(q, 'execute', side_effect=no_summary):
            with self.assertRaisesRegex(ValueError, 'summary'):
                self.run_build()

    def test_success_serializes_ten_commands_with_exact_limits_and_explicit_summaries(self):
        self.calls, self.original_execute = [], q.execute
        with mock.patch.object(q, 'execute', side_effect=self.simulated_execute):
            self.run_build()
        self.assertEqual([item[0] for item in self.calls], [item['command'] for item in q.build_commands()])
        self.assertEqual([item[1] for item in self.calls], [1800] * 5 + [900] * 5)
        self.assertNotIn('MING_TEST_PRIVATE_WORKER', self.calls[0][2]['env'])
        self.assertEqual(self.calls[5][2]['env']['MING_TEST_PRIVATE_WORKER'], str(self.target / 'release/ming-app-host'))
        self.assertIn(self.cargo / 'registry', self.readonly_inputs)
        self.assertIn(self.packages, self.readonly_inputs)
        record = q.verify_stage(self.root, 'build', self.freeze_sha)
        self.assertFalse(record['finalSystemAccepted'])
        self.assertTrue((self.root / 'stages/build/artifacts.json').is_file())

    def test_explicit_zero_match_stops_remaining_commands(self):
        self.calls, self.original_execute = [], q.execute
        def zero(directory, command, timeout, **kwargs):
            if len(self.calls) == 5:
                self.calls.append((command, timeout, kwargs))
                return self.original_execute(directory, [sys.executable, '-c', 'print("test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s")'], 5, **kwargs)
            return self.simulated_execute(directory, command, timeout, **kwargs)
        with mock.patch.object(q, 'execute', side_effect=zero):
            with self.assertRaisesRegex(ValueError, 'summary'):
                self.run_build()
        self.assertEqual(len(self.calls), 6)

    def test_cargo_hardlinked_outputs_are_sealed_before_explicit_tests_and_after_relink(self):
        self.calls, self.original_execute = [], q.execute
        def cargo_links(directory, command, timeout, **kwargs):
            result = self.simulated_execute(directory, command, timeout, **kwargs)
            if len(self.calls) == 5:
                for name in ('release/ming-app-host', 'release/examples/prepare_persistent_update_fixture'):
                    artifact = self.target / name
                    os.link(artifact, artifact.with_name(artifact.name + '-cargo-alias'))
            if len(self.calls) == 6:
                self.assertEqual((self.target / 'release/ming-app-host').stat().st_nlink, 1)
                self.assertTrue((self.root / 'stages/build/pre-explicit-artifact-seal.json').exists())
            if len(self.calls) == 9:
                for name in ('release/ming-app-host', 'release/examples/prepare_persistent_update_fixture'):
                    artifact = self.target / name
                    artifact.unlink()
                    os.link(artifact.with_name(artifact.name + '-cargo-alias'), artifact)
            return result
        with mock.patch.object(q, 'execute', side_effect=cargo_links):
            self.run_build()
        self.assertEqual(q.read_json(self.root / 'stages/build/result.json')['status'], 'passed')
        self.assertTrue((self.root / 'stages/build/final-artifact-seal.json').exists())
        self.assertEqual((self.target / 'release/ming-app-host').stat().st_nlink, 1)

    def test_failed_prepare_result_cannot_run_cargo(self):
        (self.root / 'prepare-result.json').unlink()
        q.write_json(self.root / 'prepare-result.json', {'status': 'failed', 'freezeSha256': self.freeze_sha})
        self.calls, self.original_execute = [], q.execute
        with mock.patch.object(q, 'execute', side_effect=self.simulated_execute):
            with self.assertRaisesRegex(ValueError, 'prepare'):
                self.run_build()
        self.assertEqual(self.calls, [])

    def test_package_replay_tree_change_cannot_run_cargo(self):
        (self.root / 'replay/changed').write_bytes(b'unbound evidence')
        self.calls, self.original_execute = [], q.execute
        with mock.patch.object(q, 'execute', side_effect=self.simulated_execute):
            with self.assertRaisesRegex(ValueError, 'replay'):
                self.run_build()
        self.assertEqual(self.calls, [])

    def test_package_receipt_false_flags_are_rejected_independently_of_tree_freeze(self):
        path = self.packages / 'old/build-receipt.json'
        original = q.read_json(path)
        for flag in ('offline', 'sourceInputsStable', 'dependencyInputsStable', 'rawToPackageReplayVerified'):
            changed = dict(original, **{flag: False})
            path.unlink()
            q.write_json(path, changed)
            with self.subTest(flag=flag), self.assertRaisesRegex(ValueError, 'receipt'):
                b.verify_package(path.parent, 'old', self.value['source']['files'])

    def test_source_change_during_command_retains_failure_evidence(self):
        original = q.execute
        def changed(directory, command, timeout, **kwargs):
            result = original(directory, [sys.executable, '-c', 'print("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s")'], 5, **kwargs)
            (self.repo / 'Cargo.toml').write_bytes(b'changed during command')
            return result
        with mock.patch.object(q, 'execute', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'source'):
                self.run_build()
        self.assertEqual(q.read_json(self.root / 'stages/build/result.json')['status'], 'failed')
        self.assertTrue((self.root / 'stages/build/inputs-after.json').is_file())

    def test_cli_help_is_read_only_and_lists_required_freeze(self):
        import subprocess
        result = subprocess.run([sys.executable, '-B', str(Path(b.__file__)), '--help'],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertIn('--freeze', result.stdout)
        self.assertIn('--target', result.stdout)

    def test_missing_prepare_result_is_rejected_before_command(self):
        (self.root / 'prepare-result.json').unlink()
        with mock.patch.object(q, 'execute') as execute:
            with self.assertRaisesRegex(ValueError, 'prepare'):
                self.run_build()
            execute.assert_not_called()

    def test_storage_extra_writer_blocks_build(self):
        with mock.patch.object(storage, '_space', return_value={'unitOnly': True}):
            with storage.storage_session(self.root / 'storage-control', self.storage_receipt, 0):
                with mock.patch.object(q, 'execute') as execute:
                    with self.assertRaisesRegex(storage.StorageError, 'writer'):
                        self.run_build()
                    execute.assert_not_called()

    def test_target_contaminated_during_admission_never_runs_cargo(self):
        self.calls, self.original_execute = [], q.execute
        original = b.validate_cargo_config
        def contaminate(repo, home):
            original(repo, home)
            (self.target / '.rustc_info.json').write_bytes(b'not cold')
        with mock.patch.object(b, 'validate_cargo_config', side_effect=contaminate), mock.patch.object(q, 'execute', side_effect=self.simulated_execute):
            with self.assertRaisesRegex(ValueError, 'empty'):
                self.run_build()
        self.assertEqual(self.calls, [])

    def test_writable_packages_root_never_runs_cargo(self):
        def readonly(path):
            if Path(path) == self.packages:
                raise ValueError('package inputs require read-only mount')
        with mock.patch.object(q, 'observe_environment', return_value=self.observed), mock.patch.object(b, 'require_readonly', side_effect=readonly), mock.patch.object(storage, '_space', return_value={'unitOnly': True}), mock.patch.object(q, 'execute', side_effect=AssertionError('writable packages reached Cargo')) as execute:
            with self.assertRaisesRegex(ValueError, 'read-only'):
                b.run_build(self.root, self.repo, self.freeze, self.cargo, self.target, self.packages)
            execute.assert_not_called()


class UninstallColdBuildControls(unittest.TestCase):
    """Real POSIX controls, synthetic compiler only; run in admitted Linux."""
    profile = 'persistent-uninstall-linux-host'
    setUp = ColdBuildControls.setUp
    make_package = ColdBuildControls.make_package
    run_build = ColdBuildControls.run_build
    simulated_execute = ColdBuildControls.simulated_execute

    def test_seven_commands_bind_uninstall_environment_and_both_artifact_seals(self):
        self.calls, self.original_execute = [], q.execute
        def execute(directory, command, timeout, **kwargs):
            result = self.simulated_execute(directory, command, timeout, **kwargs)
            if len(self.calls) == 5:
                for name in b.artifact_paths(self.profile):
                    path = self.target / name
                    os.link(path, path.with_name(path.name + '-cargo-alias'))
            if len(self.calls) == 6:
                for name in b.artifact_paths(self.profile):
                    path = self.target / name
                    self.assertEqual(path.stat().st_nlink, 1)
                    path.unlink()
                    os.link(path.with_name(path.name + '-cargo-alias'), path)
            return result
        with mock.patch.object(q, 'execute', side_effect=execute):
            record = self.run_build()
        self.assertEqual(record['detail']['coldBuildCommands'], 7)
        self.assertEqual([row[0] for row in self.calls], [row['command'] for row in q.build_commands(self.profile)])
        self.assertEqual([row[1] for row in self.calls], [1800] * 5 + [900] * 2)
        self.assertFalse(any(key.startswith('MING_') for key in self.calls[0][2]['env']))
        self.assertEqual(self.calls[5][2]['env']['MING_UNINSTALL_FIXTURE_WASM'], str(self.packages / 'uninstall/main.wasm'))
        directory = self.root / 'stages/build'
        for name in ('pre-explicit-artifact-seal.json', 'final-artifact-seal.json'):
            self.assertEqual(set(q.read_json(directory / name)), set(b.artifact_paths(self.profile)))
        for name in b.artifact_paths(self.profile):
            self.assertEqual((self.target / name).stat().st_nlink, 1)
        self.assertFalse((self.target / 'release/examples/prepare_persistent_update_fixture').exists())

    def test_default_update_request_rejects_uninstall_freeze_without_execution(self):
        with mock.patch.object(q, 'observe_environment', return_value=self.observed), mock.patch.object(q, 'execute') as execute:
            with self.assertRaisesRegex(ValueError, 'profile'):
                b.run_build(self.root, self.repo, self.freeze, self.cargo, self.target, self.packages)
            execute.assert_not_called()

    def test_zero_match_stops_second_uninstall_test_and_preserves_failed_generation(self):
        self.calls, self.original_execute = [], q.execute
        def execute(directory, command, timeout, **kwargs):
            if len(self.calls) == 5:
                self.calls.append((command, timeout, kwargs))
                return self.original_execute(directory, [sys.executable, '-c',
                    'print("test result: ok. 0 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s")'],
                    5, **kwargs)
            return self.simulated_execute(directory, command, timeout, **kwargs)
        with mock.patch.object(q, 'execute', side_effect=execute):
            with self.assertRaisesRegex(ValueError, 'summary'):
                self.run_build()
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(q.read_json(self.root / 'stages/build/result.json')['status'], 'failed')
        self.assertTrue((self.root / 'stages/build/inputs-after.json').exists())
        with self.assertRaises(FileExistsError):
            self.run_build()


class ArtifactSealControls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.target = Path(self.temp.name)
        self.names = ('release/ming-app-host', 'release/examples/prepare_persistent_update_fixture')
        for name in self.names:
            artifact = self.target / name
            artifact.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile('/usr/bin/true', artifact)
            artifact.chmod(0o755)
            os.link(artifact, artifact.with_name(artifact.name + '-cargo-alias'))

    def test_real_hardlinked_elf_detaches_only_fixed_outputs_with_identical_bytes(self):
        before = {name: (self.target / name).stat() for name in self.names}
        expected = hashlib.sha256(Path('/usr/bin/true').read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'single-link'):
            q.stable_hash(self.target / self.names[0])
        receipt = b.seal_artifacts(self.target)
        self.assertEqual(set(receipt), set(self.names))
        for name in self.names:
            artifact = self.target / name
            alias = artifact.with_name(artifact.name + '-cargo-alias')
            self.assertEqual(alias.stat().st_ino, before[name].st_ino)
            self.assertNotEqual(artifact.stat().st_ino, before[name].st_ino)
            self.assertEqual(artifact.stat().st_nlink, 1)
            self.assertEqual(q.stable_hash(artifact)['sha256'], expected)
            self.assertEqual(hashlib.sha256(alias.read_bytes()).hexdigest(), expected)
            self.assertEqual(receipt[name]['sourceBefore'], receipt[name]['sourceAfter'])
            self.assertEqual(receipt[name]['artifact']['sha256'], expected)
            self.assertTrue(receipt[name]['detached'])
        # The generic hash contract stays strict for every other hardlinked file.
        os.link(alias, alias.with_name('another-alias'))
        with self.assertRaisesRegex(ValueError, 'single-link'):
            q.stable_hash(alias)
        second = b.seal_artifacts(self.target)
        self.assertTrue(all(value['detached'] is False for value in second.values()))

    def test_symlink_leaf_and_parent_are_rejected(self):
        artifact = self.target / self.names[0]
        artifact.unlink()
        artifact.symlink_to(artifact.with_name(artifact.name + '-cargo-alias'))
        with self.assertRaises((OSError, ValueError)):
            b.seal_artifacts(self.target)
        artifact.unlink()
        (self.target / 'release').rename(self.target / 'actual-release')
        (self.target / 'release').symlink_to(self.target / 'actual-release', target_is_directory=True)
        with self.assertRaises(ValueError):
            b.seal_artifacts(self.target)

    def test_non_elf_non_executable_and_oversize_are_rejected(self):
        artifact = self.target / self.names[0]
        artifact.write_bytes(b'not an ELF')
        with self.assertRaises(ValueError):
            b.seal_artifacts(self.target)
        shutil.copyfile('/usr/bin/true', artifact)
        artifact.chmod(0o644)
        with self.assertRaises(ValueError):
            b.seal_artifacts(self.target)
        artifact.chmod(0o755)
        with artifact.open('r+b') as stream:
            stream.truncate(512 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            b.seal_artifacts(self.target)

    def test_source_mutation_during_hash_is_rejected(self):
        artifact = self.target / self.names[0]
        original_read = os.read
        changed = False
        def mutate(fd, count):
            nonlocal changed
            result = original_read(fd, count)
            if not changed:
                changed = True
                with artifact.open('r+b') as stream:
                    stream.seek(8); stream.write(b'changed')
            return result
        with mock.patch.object(os, 'read', side_effect=mutate):
            with self.assertRaises(ValueError):
                b.seal_artifacts(self.target)

    def test_source_replaced_during_copy_is_rejected_and_owned_temp_removed(self):
        artifact = self.target / self.names[0]
        replacement = self.target / 'replacement'
        shutil.copyfile('/usr/bin/true', replacement)
        replacement.chmod(0o755)
        replacement_inode = replacement.stat().st_ino
        original_write = os.write
        changed = False
        def replace(fd, raw):
            nonlocal changed
            if not changed:
                changed = True
                os.replace(replacement, artifact)
            return original_write(fd, raw)
        with mock.patch.object(os, 'write', side_effect=replace):
            with self.assertRaises(ValueError):
                b.seal_artifacts(self.target)
        self.assertEqual(artifact.stat().st_ino, replacement_inode)
        self.assertFalse(list(artifact.parent.glob('.linux-host-seal-*')))

    def test_replaced_temporary_is_not_deleted_as_owned_cleanup(self):
        artifact = self.target / self.names[0]
        replacement = self.target / 'foreign-temporary'
        replacement.write_bytes(b'preserve unrelated replacement')
        original_write = os.write
        moved_to = []
        def replace(fd, raw):
            result = original_write(fd, raw)
            if not moved_to:
                temporary = next(artifact.parent.glob('.linux-host-seal-*'))
                os.replace(replacement, temporary)
                moved_to.append(temporary)
            return result
        with mock.patch.object(os, 'write', side_effect=replace):
            with self.assertRaises(ValueError):
                b.seal_artifacts(self.target)
        self.assertEqual(moved_to[0].read_bytes(), b'preserve unrelated replacement')


if __name__ == '__main__':
    unittest.main()
