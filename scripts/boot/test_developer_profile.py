"""Synthetic contract controls, not actual compiler, guest or qualification proof."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import developer_profile as developer
from bootlib import checked_session, qemu_command, sha256, validate_reviewed_input
from bundle import verify_payload
from isolation_profiles import load_profile

ARGS = ['--duration-ms', '15000', '--isolation', 'strict', '--data-root', '/data/mingos',
        '--state-file', '/run/evidence/desktop-state.json']
CATALOG = {'schemaVersion': 1, 'apps': [
    {'id': 1, 'title': 'Text Entry', 'manifest': 'packages/text-entry-demo/app.json'},
    {'id': 2, 'title': 'Motion', 'manifest': 'packages/animated-demo/app.json'}], 'autostart': [1, 2]}
EVIDENCE = ('command.json', 'inputs-before.json', 'inputs-after.json', 'toolchain.json',
            'java.stdout.log', 'java.stderr.log', 'gradle-version.stdout.log',
            'gradle-version.stderr.log', 'build.stdout.log', 'build.stderr.log',
            'dependencies-before.json', 'dependencies-after.json', 'dependencies-comparison.json')


class DeveloperProfileTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='developer-profile-control-')
        self.addCleanup(self.scratch.cleanup)
        self.payload = Path(self.scratch.name) / 'payload'
        self.payload.mkdir()
        self.put('ming-app-host', b'\x7fELF synthetic host; never executed')
        self.put('catalog.json', CATALOG)
        for name, sdk in (('text-entry-demo', 6), ('animated-demo', 5)):
            prefix = 'packages/' + name + '/'
            manifest = {'schemaVersion': 1, 'id': 'dev.ming.' + name,
                        'version': '0.1.0', 'sdkVersion': sdk, 'entry': 'main.wasm'}
            self.put(prefix + 'app.json', manifest)
            self.put(prefix + 'compiler.wasm', b'\0asm\x01\0\0\0synthetic raw ' + name.encode())
            self.put(prefix + 'main.wasm', b'\0asm\x01\0\0\0synthetic adapted ' + name.encode())
            command = ['java', '--offline', '--rerun-tasks', '--no-build-cache',
                       '--no-configuration-cache', ':' + name + ':compileProductionExecutableKotlinWasmWasi']
            inputs = {'files': {'samples/' + name + '/app.json': sha256(self.payload / prefix / 'app.json')},
                      'directories': ['samples', 'samples/' + name]}
            for leaf in EVIDENCE:
                value = b'synthetic evidence\n'
                if leaf == 'command.json': value = {'argv': command}
                elif leaf in ('inputs-before.json', 'inputs-after.json'): value = inputs
                elif leaf.endswith('.json'): value = {'synthetic': True}
                self.put(prefix + 'evidence/' + leaf, value)
            info = {'sdkVersion': sdk, 'kotlin': '2.3.20', 'adapterVersion': 1,
                    'fixture': None, 'rawSha256': sha256(self.payload / prefix / 'compiler.wasm'),
                    'packagedSha256': sha256(self.payload / prefix / 'main.wasm'),
                    'inputSha256': inputs['files']}
            self.put(prefix + 'build-info.json', info)
            receipt = {'schemaVersion': 1, 'status': 'passed', 'sample': name, 'fixture': None,
                       'offline': True, 'compilerExitCode': 0, 'compilerSignal': None, 'kotlin': '2.3.20',
                       'formalQualification': False, 'finalSystemAccepted': False,
                       'rawToPackageReplayVerified': True, 'sourceInputsStable': True,
                       'dependencyInputsStable': True, 'package': info, 'inputSha256': inputs['files'],
                       'inputDirectories': inputs['directories'], 'command': command,
                       'manifestSha256': sha256(self.payload / prefix / 'app.json'),
                       'rawSha256': info['rawSha256'], 'packagedSha256': info['packagedSha256'],
                       'stdoutSha256': sha256(self.payload / prefix / 'evidence/build.stdout.log'),
                       'stderrSha256': sha256(self.payload / prefix / 'evidence/build.stderr.log'),
                       'evidenceSha256': {'evidence/' + leaf: sha256(self.payload / prefix / 'evidence' / leaf)
                                          for leaf in EVIDENCE}}
            self.put(prefix + 'build-receipt.json', receipt)
        self.put('provenance/host/freeze.json', {'schemaVersion': 1, 'source': {'files': {'Cargo.lock':
                 {'sha256': 'a' * 64, 'bytes': 1}}}, 'formalQualification': False, 'finalSystemAccepted': False})
        self.put('provenance/host/artifacts.json', {'release/ming-app-host': self.record('ming-app-host')})
        self.put('provenance/host/build-result.json', {'stage': 'build', 'status': 'passed',
                 'freezeSha256': sha256(self.payload / 'provenance/host/freeze.json'),
                 'formalQualification': False, 'finalSystemAccepted': False,
                 'products': {'artifacts.json': self.record('provenance/host/artifacts.json')}})
        self.review = {'reviewed': True, 'reviewer': 'synthetic control only', 'profile': 'developer-vm-v1',
                       'sessionMode': 'bounded', 'extraArgs': ARGS[:], 'diagnosticOnly': True,
                       'instrumentedQualification': False, 'formalQualification': False, 'finalSystemAccepted': False,
                       'strictProbeProfile': 'source-v2', 'strictProbeSha256': load_profile('source-v2')['artifactSha256'],
                       'probeSourceFreezeSha256': 'b' * 64, 'probeBuildReceiptSha256': 'c' * 64}
        self.reseal()

    def put(self, name, value):
        path = self.payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else (json.dumps(value) + '\n').encode())

    def record(self, name):
        path = self.payload / name
        return {'sha256': sha256(path), 'bytes': path.stat().st_size}

    def reseal(self):
        self.review.update(hostSha256=sha256(self.payload / 'ming-app-host'),
                           catalogSha256=sha256(self.payload / 'catalog.json'),
                           payloadFiles={p.relative_to(self.payload).as_posix(): sha256(p)
                                         for p in self.payload.rglob('*') if p.is_file()},
                           packageEvidenceSha256={str(i): sha256(self.payload / 'packages' / name / 'build-receipt.json')
                                                  for i, name in ((1, 'text-entry-demo'), (2, 'animated-demo'))},
                           hostProvenance={key: sha256(self.payload / 'provenance/host' / name)
                                           for key, name in (('freezeSha256', 'freeze.json'),
                                                             ('resultSha256', 'build-result.json'),
                                                             ('artifactsSha256', 'artifacts.json'))})

    def check(self, review=None, catalog=None):
        function = getattr(developer, 'verify_developer_vm', None)
        self.assertTrue(callable(function), 'missing independent developer payload validator')
        return function(self.payload, self.review if review is None else review,
                        CATALOG if catalog is None else catalog)

    def test_valid_compose_sdk6_and_compose_sdk5_payload(self):
        self.assertEqual(self.check(), CATALOG)

    def test_checked_session_accepts_exact_strict_fifteen_second_contract(self):
        try: actual = checked_session(self.review, self.payload)
        except ValueError as error: self.fail('exact developer session refused: ' + str(error))
        self.assertEqual(actual, self.review)

    def test_bundle_dispatches_independent_profile(self):
        try: actual = verify_payload(self.payload, self.review)
        except ValueError as error: self.fail('developer bundle dispatch absent: ' + str(error))
        self.assertEqual(actual, CATALOG)

    def test_session_rejects_duration_isolation_renderer_event_and_role_overrides(self):
        for key, value in (('extraArgs', ['--duration-ms', '15000']),
                           ('extraArgs', ARGS[:3] + ['off'] + ARGS[4:]),
                           ('extraArgs', ['--duration-ms', '15001'] + ARGS[2:]),
                           ('extraArgs', ARGS + ['--renderer', 'gpu']),
                           ('extraArgs', ARGS + ['--events', '/tmp/events.json']),
                           ('sessionMode', 'long-lived'), ('instrumentedQualification', True),
                           ('formalQualification', True), ('finalSystemAccepted', True),
                           ('diagnosticOnly', False), ('strictProbeProfile', 'legacy-v1'),
                           ('strictProbeSha256', {}), ('probeSourceFreezeSha256', None),
                           ('probeBuildReceiptSha256', '0'), ('inputScriptSha256', '0' * 64),
                           ('installFixture', {}), ('updateFixture', {}), ('bootRole', 'initialize-first'),
                           ('instrumentedProfile', 'persistent-install-pointer-ready-v1')):
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'developer'):
                checked_session(dict(self.review, **{key: value}), self.payload)

    def test_rejects_catalog_aliases_authority_and_boolean_ids(self):
        for mutate in (lambda c: c.update(schemaVersion=True),
                       lambda c: c['apps'][0].update(id=True),
                       lambda c: c['apps'][1].update(manifest='packages/text-entry-demo/app.json'),
                       lambda c: c['apps'][1].update(slots=[1]),
                       lambda c: c.update(autostart=[1, 1])):
            catalog = copy.deepcopy(CATALOG); mutate(catalog)
            with self.assertRaises(ValueError): self.check(catalog=catalog)

    def test_rejects_changed_evidence_missing_and_extra_payload_files(self):
        for leaf in ('packages/animated-demo/evidence/command.json', 'provenance/host/artifacts.json'):
            path = self.payload / leaf; raw = path.read_bytes(); path.write_bytes(b'changed')
            with self.subTest(leaf=leaf), self.assertRaises(ValueError): self.check()
            path.write_bytes(raw)
        path = self.payload / 'extra-fixture'; path.write_bytes(b'forbidden')
        self.reseal()
        with self.assertRaises(ValueError): self.check()

    def test_rejects_receipt_claims_even_when_review_resealed(self):
        path = self.payload / 'packages/text-entry-demo/build-receipt.json'
        original = json.loads(path.read_text())
        for field, value in (('compilerExitCode', True), ('status', 'failed'), ('fixture', 'old'),
                             ('manifestSha256', '0' * 64), ('rawToPackageReplayVerified', False),
                             ('evidenceSha256', {}), ('formalQualification', True)):
            changed = dict(original, **{field: value}); self.put(path.relative_to(self.payload), changed); self.reseal()
            with self.subTest(field=field), self.assertRaises(ValueError): self.check()

    def test_rejects_wrong_sdk_identity_even_with_receipt_resealed(self):
        self.put('packages/animated-demo/app.json', {'schemaVersion': 1, 'id': 'dev.ming.animated-demo',
                 'version': '0.1.0', 'sdkVersion': 6, 'entry': 'main.wasm'})
        self.reseal()
        with self.assertRaises(ValueError): self.check()

    def test_host_build_result_and_artifact_must_bind_host_and_freeze(self):
        for name in ('provenance/host/build-result.json', 'provenance/host/artifacts.json'):
            path = self.payload / name; original = path.read_bytes()
            self.put(name, {}); self.reseal()
            with self.subTest(name=name), self.assertRaises(ValueError): self.check()
            path.write_bytes(original)

    def test_developer_never_accepts_input_scripts_or_qualification_boot_number(self):
        path = self.payload / 'catalog.json'
        for candidate, number in ((path, None), (None, 1)):
            with self.subTest(number=number), self.assertRaisesRegex(ValueError, 'developer'):
                validate_reviewed_input(self.review, candidate, number)
        self.assertEqual(validate_reviewed_input(self.review, None), (None, None))

    def test_bundle_options_require_source_probe_and_refuse_instrumentation_before_writes(self):
        validate = getattr(developer, 'validate_bundle_options', None)
        self.assertTrue(callable(validate), 'missing developer bundle options validator')
        for profile, probes, diagnostics in (('legacy-v1', self.payload, False),
                                             ('source-v2', None, False), ('source-v2', self.payload, True)):
            with self.subTest(profile=profile, probes=probes, diagnostics=diagnostics), self.assertRaises(ValueError):
                validate(self.review, profile, probes, input_diagnostics=diagnostics)

    def test_original_desktop_cannot_adopt_developer_strict_args(self):
        old = {'reviewed': True, 'reviewer': 'control', 'profile': 'desktop', 'sessionMode': 'bounded',
               'hostSha256': self.review['hostSha256'], 'catalogSha256': self.review['catalogSha256'],
               'extraArgs': ['--duration-ms', '15000']}
        self.assertEqual(checked_session(old, self.payload), old)
        with self.assertRaises(ValueError): checked_session(dict(old, extraArgs=ARGS), self.payload)

    def test_qemu_dryrun_preserves_native_fixed_resources_and_has_no_qualification_role(self):
        argv = qemu_command(Path('/bundle'), Path('/out'), data_disk={'imagePath': '/work/dev.raw',
                            'serial': 'mingos-dev-control', 'uuid': '11111111-1111-1111-1111-111111111111'})
        for flag, value in (('-machine', 'q35'), ('-accel', 'tcg,thread=multi'), ('-smp', '2'),
                            ('-m', '2048'), ('-display', 'none'), ('-nic', 'none')):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertNotIn('persistent_', argv[-1])
        self.assertIn('mingos.data_uuid=11111111-1111-1111-1111-111111111111', argv[-1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
