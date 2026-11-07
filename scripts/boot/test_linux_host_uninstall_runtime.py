"""Independent uninstall runtime controls; no guest or disk is created."""
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path, PurePosixPath
import unittest
from unittest import mock

try:
    import linux_host_uninstall_runtime as runtime
except ModuleNotFoundError:
    runtime = None


class UninstallRuntimeControls(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runtime, 'independent uninstall runtime required')

    def test_source_binding_rejects_every_delta(self):
        original = {'scripts/boot/linux_host_uninstall_runtime.py': {'sha256': 'a'}}
        self.assertEqual(runtime.source_extension(original, dict(original)), {})
        for after in ({}, dict(original, **{'docs/new.md': {}}),
                      {'scripts/boot/linux_host_uninstall_runtime.py': {'sha256': 'b'}}):
            with self.assertRaises(ValueError):
                runtime.source_extension(original, after)

    def test_fixed_inputs_have_seven_and_one_steps_and_full_budgets(self):
        base = Path(__file__).parent / 'qualification_uninstall'
        def digest(path):
            raw = Path(path).read_bytes()
            return {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
        with mock.patch.object(runtime.q, 'stable_hash', side_effect=digest), \
             mock.patch.object(runtime.q, 'read_json', side_effect=lambda p: json.loads(Path(p).read_text())):
            inputs = runtime.native_inputs(base / 'input-boot1.json', base / 'input-boot2.json')
            self.assertEqual([(inputs[str(n)]['steps'], inputs[str(n)]['minimumSeconds']) for n in (1, 2)],
                             [(7, 770), (1, 470)])
        with mock.patch.object(runtime.q, 'stable_hash', return_value={'sha256': '0' * 64, 'bytes': 1}):
            with self.assertRaises(ValueError):
                runtime.native_inputs(base / 'input-boot1.json', base / 'input-boot2.json')

    def test_fixture_command_uses_only_initial_and_source_packages(self):
        with mock.patch.object(runtime, 'Path', PurePosixPath):
            command = runtime.fixture_command('a' * 64)
        self.assertEqual(command, ['/prep/inputs/payload/prepare-persistent-uninstall-fixture', 'initialize-first',
            '/prep/build/fixture/data/install-root', '/prep/inputs/payload/packages/initial/main.wasm',
            '/prep/inputs/payload/packages/new/main.wasm', '/prep/build/fixture/install-source/app.json',
            '/prep/inputs/payload/catalog.json', 'a' * 64, '/prep/build/fixture/fixture-receipt.json'])

    def test_checker_and_driver_are_independent(self):
        self.assertEqual(runtime.COLD_PROFILE, 'persistent-uninstall-linux-host')
        self.assertEqual(runtime.CHECKER_FILES, ('checker.py', 'raw_boot_audit.py', 'driver.py',
            'input-geometry.json', 'geometry.stdout.log', 'geometry-execution.json', 'test_checker.py', 'test_driver.py',
            'fixtures.py'))

    def test_checker_source_bytes_are_bound_before_any_asset_module_is_loaded(self):
        sources = {('scripts/boot/qualification_uninstall/' + name): {'sha256': name, 'bytes': 12}
                   for name in runtime.CHECKER_FILES if name.endswith('.py')}
        def hashes(path):
            return {'sha256': Path(path).name, 'bytes': 12}
        with mock.patch.object(runtime.q, 'source_inventory', return_value=sources), \
             mock.patch.object(runtime.q, 'stable_hash', side_effect=hashes):
            records = runtime.checker_binding(Path('/assets/checker'), Path('/repo'))
            self.assertEqual(set(records), set(runtime.CHECKER_FILES))
            for name in tuple(sources):
                original = sources[name]
                sources[name] = {'sha256': 'substituted', 'bytes': 12}
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'checker source'):
                    runtime.checker_binding(Path('/assets/checker'), Path('/repo'))
                sources[name] = original

    def test_snapshot_rejects_checker_substitution_before_geometry_import(self):
        paths = {name: '/inputs/' + name for name in ('repo', 'build_root', 'build_freeze', 'target',
                 'packages_root', 'checker_dir', 'input1', 'input2', 'tools_receipt', 'tools_manifest', 'probe_dir')}
        with mock.patch.object(runtime.q, 'checked_directory', side_effect=lambda p: p), \
             mock.patch.object(runtime.q, 'observe_environment'), \
             mock.patch.object(runtime.q, 'validate_environment'), \
             mock.patch.object(runtime.builder, 'require_readonly'), \
             mock.patch.object(runtime.freezer, 'readonly_input'), \
             mock.patch.object(runtime, 'cold_build_binding'), \
             mock.patch.object(runtime, 'checker_binding', side_effect=ValueError('checker source differs')), \
             mock.patch.object(runtime, 'geometry_binding') as geometry, \
             mock.patch.object(runtime, '_load_driver') as driver:
            with self.assertRaisesRegex(ValueError, 'checker source'):
                runtime.runtime_snapshot(**paths)
            geometry.assert_not_called()
            driver.assert_not_called()

    def test_runtime_copy_imports_uninstall_receipts_from_actual_staged_copy(self):
        spec = importlib.util.spec_from_file_location('check_runtime_uninstall', Path(__file__).with_name('check-runtime.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with mock.patch.object(module, 'copy_guest_scripts', return_value={'sealed': 'hash'}) as copy, \
             mock.patch.object(module.subprocess, 'run') as execute:
            self.assertEqual(module.check_guest_python(Path('/staged'), Path('/scripts'), 'persistent-uninstall'),
                             {'sealed': 'hash'})
            copy.assert_called_once_with(Path('/staged'), Path('/scripts'), 'persistent-uninstall')
            self.assertIn('from persistent_uninstall_receipt import PersistentUninstallReceipts', execute.call_args.args[0][-1])

    def test_cold_binding_requires_uninstall_profile_seven_commands_and_explicit_artifacts(self):
        frozen = {'sha256': 'a' * 64, 'bytes': 10}
        source = {'Cargo.lock': {'sha256': 'b'}}
        value = {'profile': runtime.COLD_PROFILE, 'source': {'files': source}}
        artifacts = {'release/ming-app-host': {'sha256': 'c'}}
        def read(path):
            return artifacts if str(path).endswith('artifacts.json') else value
        with mock.patch.object(runtime.q, 'stable_hash', return_value=frozen), \
             mock.patch.object(runtime.q, 'read_json', side_effect=read), \
             mock.patch.object(runtime.q, 'verify_stage', return_value={'detail': {'coldBuildCommands': 7}}) as stage, \
             mock.patch.object(runtime.q, 'source_inventory', return_value=source), \
             mock.patch.object(runtime.builder, 'built_artifacts', return_value=artifacts) as built:
            runtime.cold_build_binding('/cold', '/freeze', '/target', '/repo')
            built.assert_called_once_with(Path('/target'), profile=runtime.COLD_PROFILE)
            value['profile'] = 'persistent-update-linux-host'
            with self.assertRaises(ValueError):
                runtime.cold_build_binding('/cold', '/freeze', '/target', '/repo')
            value['profile'] = runtime.COLD_PROFILE
            stage.return_value = {'detail': {'coldBuildCommands': 10}}
            with self.assertRaises(ValueError):
                runtime.cold_build_binding('/cold', '/freeze', '/target', '/repo')

    def test_review_uses_uninstall_contract_and_exact_payload_hashes(self):
        from bootlib import PERSISTENT_UNINSTALL_ARGS, PERSISTENT_UNINSTALL_FIXTURE, PERSISTENT_UNINSTALL_PROFILE
        value = {'inputs': {'1': {'sha256': 'a'}, '2': {'sha256': 'b'}},
                 'probes': {'contract': {'artifactSha256': {'probe': 'c'}}},
                 'paths': {'repo': '/repo'}, 'coldBuild': {'freezeSha256': 'd'}}
        with mock.patch.object(runtime.q, 'stable_hash', return_value={'sha256': 'e'}):
            review = runtime.review_base(Path('/prep'), value)
        self.assertEqual(review['profile'], 'persistent-uninstall')
        self.assertEqual(review['extraArgs'], PERSISTENT_UNINSTALL_ARGS)
        self.assertEqual(review['persistentUninstallFixture'], PERSISTENT_UNINSTALL_FIXTURE)
        self.assertEqual(review['instrumentedProfile'], PERSISTENT_UNINSTALL_PROFILE)
        self.assertEqual(review['persistentUninstallFixtureSha256'],
                         {'helper': 'e', 'initialWasm': 'e', 'sourceWasm': 'e'})
        self.assertFalse(review['reviewed'])
        self.assertFalse(review['finalSystemAccepted'])

    def test_actual_staged_uninstall_guest_import(self):
        spec = importlib.util.spec_from_file_location('actual_runtime_uninstall', Path(__file__).with_name('check-runtime.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'etc/mingos').mkdir(parents=True)
            hashes = module.check_guest_python(Path(temporary), Path(__file__).parent, 'persistent-uninstall')
            self.assertIn('persistent_uninstall_receipt.py', hashes)


if __name__ == '__main__':
    unittest.main()
