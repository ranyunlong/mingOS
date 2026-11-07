"""Uninstall portable export negative controls; synthetic data never boots."""
import json
import copy
import hashlib
import types
import os
import shutil
import linux_host_qualification as q
import linux_host_build as builder
import test_linux_host_build as build_controls
from pathlib import Path
import tempfile
import unittest
from unittest import mock

try:
    import linux_host_uninstall_export as export
except ModuleNotFoundError:
    export = None


class UninstallExportControls(unittest.TestCase):
    def test_independent_profile_and_source_checker_binding(self):
        self.assertIsNotNone(export, 'independent uninstall export required')
        self.assertEqual(export.PROFILE, 'persistent-uninstall-linux-portable-export')
        self.assertEqual(export.DRIVER, 'source/scripts/boot/linux_host_uninstall_export.py')
        self.assertEqual(export.CHECKER_BASE, 'source/scripts/boot/qualification_uninstall')

    def test_bootstrap_rejects_update_profile_and_unsealed_importable_source(self):
        source = {'scripts/boot/linux_host_uninstall_export.py': {'sha256': 'a' * 64, 'bytes': 3}}
        value = {'profile': 'persistent-uninstall-linux-runtime', 'runtimeSource': source}
        verified = {'manifest': {'bindings': {'runtimeFreezeSha256': 'b' * 64},
                    'files': {export.DRIVER: dict(source['scripts/boot/linux_host_uninstall_export.py'], mode=0o644)}}}
        with mock.patch.object(export, 'read_file', return_value={'sha256': 'b' * 64}), \
             mock.patch.object(export, 'read_json', return_value=value):
            self.assertEqual(export.verify_bootstrap(Path('/export'), verified), value)
            value['profile'] = 'persistent-update-linux-runtime'
            with self.assertRaises(ValueError): export.verify_bootstrap(Path('/export'), verified)
            value['profile'] = 'persistent-uninstall-linux-runtime'
            verified['manifest']['files']['source/evil.py'] = {'sha256': 'c' * 64, 'bytes': 4, 'mode': 0o644}
            with self.assertRaises(ValueError): export.verify_bootstrap(Path('/export'), verified)

    def test_noncanonical_paths_duplicate_json_and_nonfinite_numbers_rejected(self):
        for path in ('../escape', '/absolute', 'a//b', 'a/./b', 'C:/host', 'a\\b'):
            with self.subTest(path=path), self.assertRaises(ValueError): export.relative(path)
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError): export.json_bytes(raw)

    @unittest.skipUnless(os.name == 'posix', 'requires Linux file identity semantics')
    def test_manifest_rejects_tampering_extra_files_and_update_profile(self):
        self.assertIsNotNone(export)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            driver = root / export.DRIVER
            driver.parent.mkdir(parents=True)
            driver.write_text('raise RuntimeError("must never import unverified data")\n')
            files, directories = export.file_inventory(root)
            value = {'schemaVersion': 1, 'profile': export.PROFILE, 'algorithm': 'sha256', 'files': files,
                     'directories': directories, 'bindings': {}, 'replayDriver': export.DRIVER,
                     'formalQualification': False, 'finalSystemAccepted': False}
            manifest = root / export.MANIFEST
            manifest.write_text(json.dumps(value))
            self.assertEqual(export.verify_manifest(root)['fileCount'], 1)
            driver.write_text('changed')
            with self.assertRaises(ValueError): export.verify_manifest(root)
            driver.write_text('raise RuntimeError("must never import unverified data")\n')
            (root / 'extra').write_text('unbound')
            with self.assertRaises(ValueError): export.verify_manifest(root)
            (root / 'extra').unlink()
            value['profile'] = 'persistent-update-linux-portable-export'
            manifest.write_text(json.dumps(value))
            with self.assertRaises(ValueError): export.verify_manifest(root)


class RawChainControls(unittest.TestCase):
    """API controls for relocated raw replay, without forging guest acceptance."""
    def replay(self, mutate=None):
        import bootlib
        import bundle
        import infra
        import linux_host_uninstall_pair as pair
        import linux_host_uninstall_runtime as runtime
        root = Path('/sealed')
        raw = {1: b'{"steps":[1]}', 2: b'{"steps":[2]}'}
        hashes = {n: hashlib.sha256(value).hexdigest() for n, value in raw.items()}
        identity = 'b' * 64
        review = {'profile': 'persistent-uninstall'}
        signed = {'synthetic': 'signature API tested separately'}
        package = {'apps': [{'app': 1}]}
        removed = {'packageId': 'dev.ming.install-demo', 'version': '1.0.0'}
        private = {'files': [{'path': 'note.txt', 'sha256': 'c' * 64}]}
        one = {'bootId': 'one', 'sessionId': 'session-one', 'report': {'diskUuid': 'uuid'},
               'after': {'envelope': {'diskUuid': 'uuid', 'marker': {'counter': 1}},
                         'content': package, 'removed': removed, 'privateContent': private}}
        two = {'bootId': 'two', 'sessionId': 'session-two', 'report': {'diskUuid': 'uuid'},
               'before': {'envelope': {'diskUuid': 'uuid', 'previousMarker': {'counter': 1}},
                          'content': copy.deepcopy(package), 'removed': copy.deepcopy(removed),
                          'privateContent': copy.deepcopy(private)}}
        two['after'] = copy.deepcopy(two['before'])
        if mutate: mutate(one, two)
        checker = types.SimpleNamespace(PORTABLE=False, REPO=root / 'source',
            verify_fixture_directory=mock.Mock(return_value=signed), verify_boot=mock.Mock(side_effect=[one, two]))
        manifest = {'payloadProfile': 'persistent-uninstall', 'sessionReview': review,
                    'instrumentedQualification': True, 'diagnosticOnly': False,
                    'diagnosticProfile': 'input-pointer-ready-v1', 'payloadSha256': {}}
        def read(path):
            return review if path.name == 'review.json' else signed if path.name == 'fixture-verification.json' else {'status': 'same-bundle-disk-cold-boot-verified'}
        def read_file(path, *args, **kwargs):
            record = {'sha256': identity}
            if kwargs.get('data'):
                return record, raw[1 if 'boot-1.' in path.name else 2]
            return record
        with mock.patch.object(export, '_load', return_value=checker), \
             mock.patch.object(export, 'read_json', side_effect=read), \
             mock.patch.object(export, 'read_file', side_effect=read_file), \
             mock.patch.object(bootlib, 'checked_session'), \
             mock.patch.object(bootlib, 'persistent_uninstall_session_contract'), \
             mock.patch.object(bootlib, 'validate_bundle', return_value=manifest), \
             mock.patch.object(bootlib, 'validate_reviewed_input', side_effect=lambda review, path, n: (raw[n], hashes[n])), \
             mock.patch.object(bundle, 'verify_payload'), \
             mock.patch.object(infra, 'validate_cold_pair'), \
             mock.patch.object(pair, 'validate_strong_pair'), \
             mock.patch.object(runtime, 'INPUT_HASHES', hashes):
            result = export.replay_raw(root)
        self.assertEqual([len(call.args) for call in checker.verify_boot.call_args_list], [7, 7])
        return result

    def test_success_flags_require_matching_content_removed_and_private_snapshots(self):
        result = self.replay()
        self.assertTrue(result['persistentUninstallNormalRebootVerified'])
        self.assertTrue(result['privateDataPreserved'])
        self.assertFalse(result['finalSystemAccepted'])

    def test_changed_removed_private_post_snapshot_marker_or_identity_rejected(self):
        for mutation in (
            lambda one, two: two['before'].update(removed=None),
            lambda one, two: two['after'].update(privateContent={'files': []}),
            lambda one, two: two['after'].update(content={'apps': []}),
            lambda one, two: two['before']['envelope'].update(previousMarker={'counter': 2}),
            lambda one, two: two.update(sessionId=one['sessionId']),
            lambda one, two: two.update(bootId=one['bootId'])):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): self.replay(mutation)


@unittest.skipUnless(os.name == 'posix', 'requires Linux source/stage/seal identity semantics')
class ColdEvidenceControls(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(export, 'independent Linux portable exporter must exist')
        self.control = build_controls.ColdBuildControls('test_live_fixed_linux_isolation')
        self.control.profile = 'persistent-uninstall-linux-host'
        self.control.setUp()
        self.addCleanup(self.control.doCleanups)
        self.c = self.control
        self.unit_elf = self.c.target / 'release/deps/ming_app_host-aabbccdd'
        for path in (self.c.target / 'release/ming-app-host', self.c.target / 'release/examples/prepare_persistent_uninstall_fixture', self.unit_elf):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'\x7fELFsynthetic never executed')
            path.chmod(0o755)
        self.finish_build()

    def finish_build(self, mutate=None, *, relink_host=False, mutate_artifacts=None):
        def stage(directory):
            q.write_json(directory / 'environment.json', self.c.observed)
            q.write_json(directory / 'cold-target.json', {'path': str(self.c.target), 'literalEmpty': True, 'entries': []})
            env = builder.build_environment(self.c.cargo, self.c.target, directory, self.c.packages,
                {label: record['path'] for label, record in self.c.value['packages'].items()}, profile=self.c.profile)
            q.write_json(directory / 'build-environment.json', {'initial': {key: value for key, value in env.items() if not key.startswith('MING_')}, 'explicit': env})
            commands = directory / 'commands'
            commands.mkdir()
            for number, entry in enumerate(q.build_commands(self.c.profile), 1):
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
            pre_seals = builder.seal_artifacts(self.c.target, profile=self.c.profile)
            pre_artifacts = builder.built_artifacts(self.c.target, profile=self.c.profile)
            if relink_host:
                host = self.c.target / 'release/ming-app-host'
                host.write_bytes(host.read_bytes() + b' synthetic explicit integration relink')
            final_seals = builder.seal_artifacts(self.c.target, profile=self.c.profile)
            artifacts = builder.built_artifacts(self.c.target, profile=self.c.profile)
            if mutate_artifacts:
                mutate_artifacts(pre_artifacts, artifacts, pre_seals, final_seals)
            q.write_json(directory / 'pre-explicit-artifact-seal.json', pre_seals)
            q.write_json(directory / 'final-artifact-seal.json', final_seals)
            q.write_json(directory / 'pre-explicit-artifacts.json', pre_artifacts)
            q.write_json(directory / 'artifacts.json', artifacts)
            return {'coldBuildCommands': 7, 'target': str(self.c.target), 'formalQualification': False, 'finalSystemAccepted': False}
        q.run_stage(self.c.root, 'build', self.c.freeze_sha, stage)

    def verify(self):
        return export.verify_cold_build(self.c.root, self.c.freeze, self.c.target, self.c.packages, self.c.repo)

    def test_seven_exact_cold_commands_summaries_and_elf_bind(self):
        value = self.verify()
        self.assertEqual(value['commands'], 7)
        self.assertFalse(value['formalQualification'])

    def test_distinct_pre_explicit_and_final_host_each_bind_their_own_seal(self):
        shutil.rmtree(self.c.root / 'stages/build')
        self.finish_build(relink_host=True)
        directory = self.c.root / 'stages/build'
        pre = q.read_json(directory / 'pre-explicit-artifacts.json')
        final = q.read_json(directory / 'artifacts.json')
        self.assertNotEqual(pre['release/ming-app-host'], final['release/ming-app-host'])
        self.assertEqual(pre['release/examples/prepare_persistent_uninstall_fixture'],
                         final['release/examples/prepare_persistent_uninstall_fixture'])
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
        for path in (self.c.repo / 'Cargo.toml', self.c.packages / 'uninstall/main.wasm', self.c.target / 'release/ming-app-host'):
            original = path.read_bytes()
            path.write_bytes(b'changed')
            with self.subTest(path=path), self.assertRaises(ValueError): self.verify()
            path.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
