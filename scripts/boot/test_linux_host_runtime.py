"""Runtime extension controls; synthetic fixtures never start a guest."""
import copy
import contextlib
import importlib.util
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import linux_host_qualification as q

try:
    import linux_host_runtime as runtime
except ModuleNotFoundError:
    runtime = None


class RuntimeControls(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runtime, 'separate runtime extension module required')

    def test_public_cli_dispatches_exact_prepare_paths_without_running_other_stages(self):
        self.assertTrue(callable(getattr(runtime, 'main', None)), 'runtime stage CLI required')
        paths = {'repo': '/repo', 'build_root': '/coldbuild', 'build_freeze': '/coldbuild/freeze.json',
                 'target': '/target', 'packages_root': '/packages', 'checker_dir': '/assets/checker',
                 'input1': '/assets/input-boot1.json', 'input2': '/assets/input-boot2.json',
                 'tools_receipt': '/assets/tools.json', 'tools_manifest': '/assets/tools-manifest.json',
                 'probe_dir': '/assets/probes'}
        argv = ['--stage', 'prepare', '--root', '/prep']
        for name, path in paths.items():
            argv.extend(['--' + name.replace('_', '-'), path])
        with mock.patch.object(runtime, 'prepare_runtime') as prepare, \
             mock.patch.object(runtime, 'materialize_fixture') as fixture, \
             mock.patch.object(runtime, 'bundle_runtime') as bundle, \
             mock.patch.object(runtime, 'admit_runtime') as admission:
            runtime.main(argv)
            prepare.assert_called_once_with(Path('/prep'), **{key: Path(value) for key, value in paths.items()})
            fixture.assert_not_called()
            bundle.assert_not_called()
            admission.assert_not_called()

    def test_incomplete_or_conflicting_cli_is_rejected_before_stage_attempt(self):
        self.assertTrue(callable(getattr(runtime, 'main', None)), 'runtime stage CLI required')
        for argv in (['--stage', 'prepare', '--root', '/prep', '--repo', '/repo'],
                     ['--stage', 'bundle', '--root', '/prep'],
                     ['--stage', 'pair', '--root', '/prep', '--repo', '/repo'],
                     ['--stage', 'admission', '--probe-qmp', '/tmp/out', '--root', '/prep', '--repo', '/repo']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(__import__('io').StringIO()), \
                 mock.patch.object(runtime, 'prepare_runtime') as prepare, \
                 mock.patch.object(runtime, 'bundle_runtime') as bundle, \
                 mock.patch.object(runtime, 'admit_runtime') as admission:
                with self.assertRaises(SystemExit) as caught:
                    runtime.main(argv)
                self.assertEqual(caught.exception.code, 2)
                prepare.assert_not_called()
                bundle.assert_not_called()
                admission.assert_not_called()

    def test_source_extension_preserves_production_inputs_and_records_tooling_delta(self):
        original = {'Cargo.lock': {'sha256': 'a'}, 'crates/host.rs': {'sha256': 'b'},
                    'scripts/boot/linux_host_build.py': {'sha256': 'c'}}
        current = dict(original, **{'scripts/boot/linux_host_runtime.py': {'sha256': 'd'}})
        self.assertEqual(runtime.source_extension(original, current),
                         {'scripts/boot/linux_host_runtime.py': {'before': None, 'after': {'sha256': 'd'}}})
        for name in ('Cargo.lock', 'crates/host.rs'):
            changed = copy.deepcopy(current)
            changed[name] = {'sha256': 'e'}
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'production'):
                runtime.source_extension(original, changed)

    def test_source_extension_rejects_production_addition_and_removal(self):
        for before, after in (({}, {'crates/new.rs': {}}), ({'Cargo.toml': {}}, {})):
            with self.assertRaisesRegex(ValueError, 'production'):
                runtime.source_extension(before, after)

    def test_source_extension_binds_public_coordinator_updates_and_rejects_unlisted_scripts(self):
        names = ('scripts/boot/new-linux-host-build.ps1', 'scripts/boot/snapshot_linux_host_source.py',
                 'scripts/boot/test_snapshot_linux_host_source.py', 'scripts/boot/test-new-linux-host-build.ps1',
                 'scripts/boot/new-linux-host-runtime.ps1', 'scripts/boot/snapshot_linux_host_runtime.py',
                 'scripts/boot/test_snapshot_linux_host_runtime.py', 'scripts/boot/test-new-linux-host-runtime.ps1')
        current = {name: {'sha256': 'd'} for name in names}
        self.assertEqual(set(runtime.source_extension({}, current)), set(names))
        with self.assertRaisesRegex(ValueError, 'production'):
            runtime.source_extension({}, {'scripts/boot/unlisted-coordinator.ps1': {'sha256': 'd'}})

    def test_only_exact_reviewed_init_and_attribute_normalization_is_allowed(self):
        for name, (before, after) in runtime.SOURCE_NORMALIZATIONS.items():
            self.assertEqual(runtime.source_extension({name: before}, {name: after}),
                             {name: {'before': before, 'after': after}})
            for old, new in ((before, dict(after, sha256='f' * 64)), (None, after), (before, None)):
                with self.subTest(name=name, old=old, new=new), self.assertRaisesRegex(ValueError, 'normalization'):
                    runtime.source_extension({name: old}, {name: new})

    def test_only_exact_reviewed_startup_diagnostics_source_transition_is_allowed(self):
        repo = Path(__file__).resolve().parents[2]
        for name, (before, after) in runtime.REVIEWED_RUNTIME_CHANGES.items():
            self.assertEqual(q.stable_hash(repo / name), after)
            self.assertEqual(runtime.source_extension({name: before}, {name: after}),
                             {name: {'before': before, 'after': after}})
            for old, new in ((before, dict(after, sha256='f' * 64)), (None, after), (before, None)):
                with self.subTest(name=name, old=old, new=new), self.assertRaisesRegex(ValueError, 'transition'):
                    runtime.source_extension({name: old}, {name: new})

    def test_native_inputs_bind_exact_historical_bytes_and_step_geometry(self):
        repo = Path(__file__).resolve().parents[2]
        checker = repo / 'artifacts/persistent-update-20261104-prep'
        result = runtime.native_inputs(checker / 'input-boot1.json', checker / 'input-boot2.json')
        self.assertEqual([result[str(n)]['steps'] for n in (1, 2)], [5, 2])
        self.assertEqual([result[str(n)]['minimumSeconds'] for n in (1, 2)], [670, 520])
        with tempfile.TemporaryDirectory() as temp:
            changed = Path(temp) / 'input.json'
            changed.write_bytes((checker / 'input-boot1.json').read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'native input'):
                runtime.native_inputs(changed, checker / 'input-boot2.json')

    def test_cold_build_binding_requires_passed_full_stage_and_exact_elf_hashes(self):
        self.assertTrue(callable(getattr(runtime, 'cold_build_binding', None)), 'cold build binding required')
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            cold, target, repo = (base / n for n in ('cold', 'target', 'repo'))
            for path in (cold, target, repo):
                path.mkdir()
            (repo / 'Cargo.lock').write_bytes(b'lock')
            q.write_json(cold / 'freeze.json', {'profile': 'persistent-update-linux-host',
                         'source': {'files': q.source_inventory(repo)}})
            artifacts = {}
            for name in ('release/ming-app-host', 'release/examples/prepare_persistent_update_fixture'):
                path = target / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'\x7fELFunit-only')
                path.chmod(0o755)
                artifacts[name] = q.stable_hash(path)
            digest = q.stable_hash(cold / 'freeze.json')['sha256']
            def action(stage):
                q.write_json(stage / 'artifacts.json', artifacts)
                return {'coldBuildCommands': 10}
            q.run_stage(cold, 'build', digest, action)
            result = runtime.cold_build_binding(cold, cold / 'freeze.json', target, repo)
            self.assertEqual(result['artifacts'], artifacts)
            self.assertEqual(result['freezeSha256'], digest)
            (target / 'release/ming-app-host').write_bytes(b'\x7fELFchanged')
            with self.assertRaisesRegex(ValueError, 'artifact'):
                runtime.cold_build_binding(cold, cold / 'freeze.json', target, repo)

    def test_tools_manifest_rejects_image_and_immutable_file_drift(self):
        self.assertTrue(callable(getattr(runtime, 'tools_binding', None)), 'tools binding required')
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            tool = base / 'tool'
            tool.write_bytes(b'unit-only tool')
            inspection = [{'Id': runtime.TOOLS_IMAGE, 'RootFS': {'Layers': ['sha256:unit']}}]
            manifest = {'schemaVersion': 1, 'files': {str(tool): q.stable_hash(tool)}}
            self.assertEqual(runtime.tools_binding(inspection, manifest)['files'], manifest['files'])
            with self.assertRaisesRegex(ValueError, 'image'):
                runtime.tools_binding([dict(inspection[0], Id='sha256:wrong')], manifest)
            tool.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'tool'):
                runtime.tools_binding(inspection, manifest)

    def test_prepare_freezes_independent_inputs_once_without_mutating_cold_freeze(self):
        self.assertTrue(callable(getattr(runtime, 'prepare_runtime', None)), 'runtime prepare required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshot = {'schemaVersion': 1, 'profile': 'persistent-update-linux-runtime',
                        'coldBuild': {'freezeSha256': 'a' * 64}, 'runtimeSource': {'file': 'hash'},
                        'formalQualification': False, 'finalSystemAccepted': False}
            with mock.patch.object(runtime, 'runtime_snapshot', return_value=snapshot):
                runtime.prepare_runtime(root, repo=root, build_root=root, build_freeze=root / 'cold.json',
                    target=root, packages_root=root, checker_dir=root, input1=root, input2=root,
                    tools_receipt=root, tools_manifest=root, probe_dir=root)
                self.assertEqual(q.read_json(root / 'runtime-freeze.json'), snapshot)
                self.assertEqual(q.read_json(root / 'runtime-prepare-result.json')['status'], 'passed')
                with self.assertRaises(FileExistsError):
                    runtime.prepare_runtime(root)

    def test_prepare_failure_retains_attempt_and_failed_result(self):
        self.assertTrue(callable(getattr(runtime, 'prepare_runtime', None)), 'runtime prepare required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.object(runtime, 'runtime_snapshot', side_effect=ValueError('cold input changed')):
                with self.assertRaisesRegex(ValueError, 'cold input'):
                    runtime.prepare_runtime(root)
            self.assertTrue((root / 'runtime-prepare-attempt.json').is_file())
            self.assertEqual(q.read_json(root / 'runtime-prepare-result.json')['status'], 'failed')

    def test_fixture_command_preserves_checker_absolute_paths_and_seed_role(self):
        self.assertTrue(callable(getattr(runtime, 'fixture_command', None)), 'exact helper command required')
        command = runtime.fixture_command('a' * 64)
        self.assertEqual(command, ['/prep/inputs/payload/prepare-persistent-update-fixture',
            'initialize-first', '/prep/build/fixture/data/install-root', '/prep/build/fixture/data',
            '/prep/inputs/payload/packages/bootstrap/main.wasm', '/prep/inputs/payload/packages/old/main.wasm',
            '/prep/inputs/payload/packages/new/main.wasm', '/prep/build/fixture/install-source/app.json',
            '/prep/inputs/payload/catalog.json', 'a' * 64, 'seed', '/prep/build/fixture/fixture-receipt.json'])

    def test_failed_fixture_never_runs_checker_or_bundle_and_preserves_result(self):
        self.assertTrue(callable(getattr(runtime, 'materialize_fixture', None)), 'real fixture stage required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            q.write_json(root / 'runtime-freeze.json', {'unitOnly': True})
            with mock.patch.object(runtime, 'load_runtime', return_value={'unitOnly': True}), \
                 mock.patch.object(runtime, '_runtime_root'), \
                 mock.patch.object(runtime, '_runtime_session', return_value=contextlib.nullcontext(), create=True), \
                 mock.patch.object(runtime, '_materialize_fixture', side_effect=RuntimeError('helper failed')):
                with self.assertRaisesRegex(RuntimeError, 'helper failed'):
                    runtime.materialize_fixture(root, root)
            self.assertEqual(q.read_json(root / 'stages/build/result.json')['status'], 'failed')
            with mock.patch.object(runtime, 'load_runtime', return_value={'unitOnly': True}), \
                 mock.patch.object(runtime, '_runtime_root'), \
                 mock.patch.object(runtime, '_runtime_session', return_value=contextlib.nullcontext(), create=True):
                with self.assertRaises(FileExistsError):
                    runtime.materialize_fixture(root, root)

    def test_unreviewed_payload_cannot_start_bundle(self):
        self.assertTrue(callable(getattr(runtime, 'verify_review', None)), 'primary review gate required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'build').mkdir()
            base = {'reviewed': False, 'reviewer': None, 'hostSha256': 'a' * 64}
            q.write_json(root / 'build/review-base.json', base)
            q.write_json(root / 'review.json', base)
            with self.assertRaisesRegex(ValueError, 'review'):
                runtime.verify_review(root)
            (root / 'review.json').unlink()
            q.write_json(root / 'review.json', dict(base, reviewed=True, reviewer='independent-root'))
            self.assertEqual(runtime.verify_review(root)['reviewer'], 'independent-root')

    def test_bundle_command_keeps_strict_pointer_instrumentation_and_no_vm(self):
        self.assertTrue(callable(getattr(runtime, 'bundle_command', None)), 'bundle command required')
        command = runtime.bundle_command(Path('/prep'), Path('/repo'), Path('/probes'))
        for flag in ('--input-diagnostics', '--wayland-client-diagnostics', '--pointer-handshake-diagnostics'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--isolation-profile') + 1], 'source-v2')
        self.assertNotIn('vm.py', ' '.join(command))

    def test_initramfs_checks_required_bytes_and_rejects_duplicate_or_escaping_members(self):
        self.assertTrue(callable(getattr(runtime, 'inspect_initramfs', None)), 'initramfs inspection required')
        def member(name, data=b'', mode=0o100644):
            rawname = name.encode() + b'\0'
            fields = [0, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(rawname), 0]
            header = b'070701' + b''.join(('%08x' % n).encode() for n in fields)
            entry = header + rawname
            entry += b'\0' * (-len(entry) % 4)
            return entry + data + b'\0' * (-len(data) % 4)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'initramfs.gz'
            expected = {'init': hashlib.sha256(b'actual-init').hexdigest()}
            with gzip.open(path, 'wb') as archive:
                archive.write(member('init', b'actual-init') + member('TRAILER!!!'))
            self.assertEqual(runtime.inspect_initramfs(path, expected)['requiredFiles'], expected)
            complete = path.read_bytes()
            path.write_bytes(complete[:-4])
            with self.assertRaises((ValueError, EOFError, OSError)):
                runtime.inspect_initramfs(path, expected)
            for entries in ((member('init', b'bad'),), (member('../escape'),),
                            (member('init', b'actual-init'), member('init', b'actual-init'))):
                with gzip.open(path, 'wb') as archive:
                    archive.write(b''.join(entries) + member('TRAILER!!!'))
                with self.subTest(entries=entries), self.assertRaises(ValueError):
                    runtime.inspect_initramfs(path, expected)

    def test_real_openssl_positive_and_tampered_negative_probe(self):
        self.assertTrue(callable(getattr(runtime, 'crypto_probe', None)), 'real crypto probe required')
        with tempfile.TemporaryDirectory() as temp:
            result = runtime.crypto_probe(Path(temp))
            self.assertTrue(result['ed25519PositiveVerified'])
            self.assertTrue(result['ed25519NegativeRejected'])

    def test_paused_qmp_probe_has_no_kernel_disk_or_guest_devices(self):
        self.assertTrue(callable(getattr(runtime, 'qmp_probe', None)), 'bounded no-kernel QMP probe required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'runtime').mkdir(mode=0o700)
            result = runtime.qmp_probe(root, root / 'runtime')
            self.assertTrue(result['pausedNoKernelQmpQuitVerified'])
            self.assertFalse(result['guestStarted'])
            command = result['command']
            self.assertIn('-S', command)
            for flag in ('-kernel', '-initrd', '-drive', '-device'):
                self.assertNotIn(flag, command)

    def test_admission_failure_is_retained_and_cannot_be_retried(self):
        self.assertTrue(callable(getattr(runtime, 'admit_runtime', None)), 'no-guest admission stage required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            q.write_json(root / 'runtime-freeze.json', {'unitOnly': True})
            with mock.patch.object(runtime, 'load_runtime', return_value={}), \
                 mock.patch.object(runtime, '_runtime_root'), \
                 mock.patch.object(runtime, 'verify_bundle_products'), \
                 mock.patch.object(runtime, '_runtime_session', return_value=contextlib.nullcontext()), \
                 mock.patch.object(runtime, '_admit_action', side_effect=RuntimeError('preflight failed')):
                with self.assertRaisesRegex(RuntimeError, 'preflight failed'):
                    runtime.admit_runtime(root, root)
                self.assertEqual(q.read_json(root / 'stages/admission/result.json')['status'], 'failed')
                with self.assertRaises(FileExistsError):
                    runtime.admit_runtime(root, root)

    def test_geometry_extension_binds_unit_elf_original_cargo_log_and_exact_execution(self):
        self.assertTrue(callable(getattr(runtime, 'geometry_binding', None)), 'geometry provenance binding required')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, assets, target, cold = (root / n for n in ('repo', 'assets', 'target', 'cold'))
            for path in (repo, assets, target, cold):
                path.mkdir()
            original_assets = Path(__file__).resolve().parents[2] / 'artifacts/persistent-update-20261104-prep'
            (assets / 'run-stage.py').write_bytes((original_assets / 'run-stage.py').read_bytes())
            sources = {}
            for name in ('graphics/desktop_shell.rs', 'desktop_model.rs', 'desktop_native.rs'):
                relative = 'crates/ming-app-host/src/' + name
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'unit source')
                sources[relative] = q.stable_hash(path)['sha256']
            unit = target / 'release/deps/ming_app_host-123abc'
            unit.parent.mkdir(parents=True)
            unit.write_bytes(b'\x7fELFunit-only')
            logs = cold / 'stages/build/commands/01'
            logs.mkdir(parents=True)
            (logs / 'stderr.log').write_text('Running unittests src/lib.rs (' + str(unit) + ')\n')
            points = [[1148, 12], [880, 478], [676, 766], [96, 104], [1238, 12]]
            geometry = {'schemaVersion': 1, 'width': 1280, 'height': 800, 'boot1Points': points,
                        'boot2Points': [points[2], points[4]], 'app2Dock': {'index': 1, 'count': 2}}
            raw = 'MING_PERSISTENT_UPDATE_GEOMETRY ' + json.dumps(geometry) + '\n'
            raw += 'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 8 filtered out; finished in 0.01s\n'
            (assets / 'geometry.stdout.log').write_text(raw)
            stdout_hash = q.stable_hash(assets / 'geometry.stdout.log')['sha256']
            q.write_json(assets / 'input-geometry.json', {'schemaVersion': 1, 'geometry': geometry,
                'sourceSha256': sources, 'test': {'matched': 1, 'passed': 1, 'failed': 0, 'ignored': 0},
                'stdoutSha256': stdout_hash})
            execution = {'schemaVersion': 1, 'binaryPath': str(unit), 'binarySha256': q.stable_hash(unit)['sha256'],
                'argv': [str(unit), '--exact', 'desktop_native::input_tests::persistent_update_two_app_input_geometry', '--nocapture'],
                'exitCode': 0, 'stdoutSha256': stdout_hash, 'coldBuildResultSha256': 'a' * 64}
            q.write_json(assets / 'geometry-execution.json', execution)
            binding = {'root': str(cold), 'target': str(target), 'buildResult': {'sha256': 'a' * 64}}
            self.assertEqual(runtime.geometry_binding(assets, repo, binding)['binarySha256'], execution['binarySha256'])
            (logs / 'stderr.log').write_text('no compiler unit artifact\n')
            with self.assertRaisesRegex(ValueError, 'Cargo'):
                runtime.geometry_binding(assets, repo, binding)

    def test_small_tmpfs_probe_checks_real_child_lock_without_disk_capacity_budget(self):
        self.assertTrue(callable(getattr(runtime, 'tmpfs_probe', None)), 'bounded tmpfs probe required')
        with tempfile.TemporaryDirectory() as temp:
            result = runtime.tmpfs_probe(Path(temp))
            self.assertTrue(result['lockCompetitorBlocked'])
            self.assertTrue(result['lockAvailableAfterRelease'])
            self.assertTrue(result['fileAndDirectoryFsyncVerified'])
            self.assertLess(result['observedAvailableBytes'], 2 * 1024 ** 3)


if __name__ == '__main__':
    unittest.main()
