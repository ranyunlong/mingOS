"""Task5 byte controls. Fixture bytes are synthetic, never image integration.

Trusted fixture pins and candidate package reports are deliberately independent.
The same-version substitutions below must fail even if every candidate report
is changed consistently. Actual image observation remains parent-run integration.
"""
import copy
import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class ToolchainBytesTests(unittest.TestCase):
    def feature(self, name):
        path = Path(__file__).with_name('source_build_toolchain.py')
        self.assertTrue(path.is_file(), 'Task5 missing source_build_toolchain implementation')
        spec = importlib.util.spec_from_file_location('task5_toolchain_control', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(getattr(module, name, None)), 'Task5 missing toolchain.' + name)
        return getattr(module, name)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = 'sha256:' + '5' * 64
        self.expected = {'image': self.image, 'executables': {}, 'lockedFiles': {}}
        self.observation = {'schemaVersion': 1, 'image': self.image,
            'executables': {}, 'lockedFiles': {}, 'packageToolchains': [],
            'formalQualification': False, 'finalSystemAccepted': False}
        # This deliberately artificial byte fixture groups executable kinds for
        # validator coverage. No actual Kotlin/Tools/Rust image contains this
        # collection; production must observe each admitted image separately.
        for name, path, version in [('node', '/usr/local/bin/node', 'v24.22.1'),
                ('java', '/opt/java/openjdk/bin/java', '21.0.8'),
                ('rustc', '/usr/local/cargo/bin/rustc', 'rustc fixture'),
                ('cargo', '/usr/local/cargo/bin/cargo', 'cargo fixture')]:
            pin = self.put(path, ('synthetic executable ' + name).encode(), 0o555)
            row = {'path': path, 'resolvedPath': path, 'version': version, **pin}
            self.expected['executables'][name] = copy.deepcopy(row)
            self.observation['executables'][name] = copy.deepcopy(row)
        # These represent locked closure bytes, including archives and wrapper.
        for path in ['/repo/gradle/wrapper/gradle-wrapper.jar',
                '/repo/gradle/wrapper/gradle-wrapper.properties',
                '/deps/gradle/mingos-verified-gradle-8.14.5-bin.zip',
                '/deps/gradle/caches/modules-2/files-2.1/kotlin/compiler.jar']:
            pin = self.put(path, ('synthetic locked ' + path).encode(), 0o444)
            self.expected['lockedFiles'][path] = copy.deepcopy(pin)
            self.observation['lockedFiles'][path] = copy.deepcopy(pin)
        node = self.expected['executables']['node']; java = self.expected['executables']['java']
        archive = self.expected['lockedFiles']['/deps/gradle/mingos-verified-gradle-8.14.5-bin.zip']
        for label in ['notes-demo', 'animated-demo', 'read-file', 'image-viewer', 'text-entry-demo', 'uninstall']:
            self.observation['packageToolchains'].append({'label': label, 'toolchain': {
                'node': node['version'], 'nodePath': node['path'], 'nodeSha256': node['sha256'],
                'javaPath': java['path'], 'javaSha256': java['sha256'], 'gradle': '8.14.5',
                'gradleDistributionSha256': archive['sha256']}})

    def put(self, logical, data, mode):
        path = self.root / logical.lstrip('/')
        path.parent.mkdir(parents=True, exist_ok=True)
        # Only this test's synthetic temporary files may become writable. The
        # fixture runs as UID65534, so prior 0555/0444 files cannot be overwritten
        # until their owner grants write permission; restore the intended mode
        # even if writing fails. Never change frozen source or consumer helpers.
        self.assertTrue(path.is_relative_to(self.root))
        self.assertFalse(path.is_symlink())
        if path.exists():
            self.assertTrue(path.is_file())
            if hasattr(os, 'getuid'): self.assertEqual(path.stat().st_uid, os.getuid())
            path.chmod(0o600)
        try:
            path.write_bytes(data)
        finally:
            if path.exists(): path.chmod(mode)
        return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'mode': mode}

    def verify(self, observation=None):
        return self.feature('verify')(observation or self.observation, self.root, self.expected)

    def test_reopens_independent_executable_and_full_locked_dependency_bytes(self):
        result = self.verify()
        self.assertEqual(result['status'], 'toolchain-bytes-verified')
        self.assertEqual(result['image'], self.image)
        self.assertFalse(result['formalQualification']); self.assertFalse(result['finalSystemAccepted'])
        self.assertEqual(self.observation['packageToolchains'][0]['toolchain']['node'], 'v24.22.1')

    def test_correct_versions_and_consistent_candidate_hashes_cannot_bless_substitution(self):
        verify = self.feature('verify')
        for name in ('node', 'java'):
            with self.subTest(tool=name):
                bad = copy.deepcopy(self.observation)
                logical = bad['executables'][name]['path']
                original = (self.root / logical.lstrip('/')).read_bytes()
                forged = self.put(logical, original + b' substituted', 0o555)
                bad['executables'][name].update(forged)
                for package in bad['packageToolchains']:
                    package['toolchain'][name + 'Sha256'] = forged['sha256']
                with self.assertRaises(ValueError): verify(bad, self.root, self.expected)
                self.put(logical, original, 0o555)

    def test_package_self_report_path_or_hash_cannot_override_actual_bytes(self):
        verify = self.feature('verify')
        for key, value in [('nodeSha256', 'f' * 64), ('javaSha256', 'e' * 64),
                ('nodePath', '/other/node'), ('javaPath', '/other/java'),
                ('gradleDistributionSha256', 'd' * 64)]:
            bad = copy.deepcopy(self.observation)
            bad['packageToolchains'][4]['toolchain'][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError): verify(bad, self.root, self.expected)

    def test_wrong_image_missing_executable_and_each_locked_file_fail(self):
        verify = self.feature('verify')
        with self.assertRaises(ValueError): verify({**self.observation, 'image': 'sha256:' + '6' * 64}, self.root, self.expected)
        for logical in [row['resolvedPath'] for row in self.expected['executables'].values()] + list(self.expected['lockedFiles']):
            path = self.root / logical.lstrip('/')
            original = path.read_bytes()
            # Remove only this test's own temporary regular fixture. Windows
            # requires clearing its read-only attribute before intentional loss.
            path.chmod(0o600); path.unlink()
            with self.subTest(missing=logical), self.assertRaises((ValueError, OSError)): verify(self.observation, self.root, self.expected)
            pin = (next((row for row in self.expected['executables'].values() if row['resolvedPath'] == logical), None)
                   or self.expected['lockedFiles'][logical])
            self.put(logical, original, pin['mode'])

    def test_wrapper_distribution_and_compiler_dependency_drift_fail(self):
        verify = self.feature('verify')
        for logical in self.expected['lockedFiles']:
            path = self.root / logical.lstrip('/'); original = path.read_bytes()
            self.put(logical, original + b' changed', 0o444)
            with self.subTest(locked=logical), self.assertRaises(ValueError): verify(self.observation, self.root, self.expected)
            self.put(logical, original, 0o444)

    @unittest.skipUnless(os.name == 'posix', 'Linux image symlink resolution controls')
    def test_declared_executable_symlink_resolves_inside_image_and_escape_fails(self):
        verify = self.feature('verify')
        node = self.expected['executables']['node']; logical = node['path']
        path = self.root / logical.lstrip('/'); actual = '/opt/node/bin/node'
        raw = path.read_bytes(); path.unlink(); self.put(actual, raw, 0o555)
        path.symlink_to('../../../opt/node/bin/node')
        for value in (self.expected, self.observation): value['executables']['node']['resolvedPath'] = actual
        self.assertEqual(verify(self.observation, self.root, self.expected)['status'], 'toolchain-bytes-verified')
        path.unlink(); path.symlink_to('../../../../outside-node')
        with self.assertRaises((ValueError, OSError)): verify(self.observation, self.root, self.expected)

    def test_observe_requires_fixed_image_and_independent_locked_expectations(self):
        observe = self.feature('observe')
        for image, expected in [('mutable:latest', self.expected),
                (self.image, {'image': self.image}),
                ('sha256:' + '6' * 64, self.expected)]:
            output = self.root / ('observation-' + str(len(list(self.root.iterdir()))))
            with self.subTest(image=image), self.assertRaises(ValueError): observe(image, expected, output)
            self.assertFalse(output.exists(), 'invalid observation must fail before allocation')

    def test_regular_byte_mirror_uses_independently_observed_resolution_without_copying_symlink(self):
        verify = self.feature('verify')
        logical = self.expected['executables']['node']['path']; resolved = '/opt/node/bin/node'
        path = self.root / logical.lstrip('/'); raw = path.read_bytes()
        # This synthetic temp file is intentionally replaced by resolved bytes;
        # clear only its read-only attribute so Windows can perform that removal.
        path.chmod(0o600); path.unlink()
        self.put(resolved, raw, 0o555)
        for record in (self.expected, self.observation): record['executables']['node']['resolvedPath'] = resolved
        self.expected['mirrorLayout'] = 'resolved-regular-files'
        self.assertEqual(verify(self.observation, self.root, self.expected)['status'], 'toolchain-bytes-verified')
        self.assertFalse(path.exists(), 'mirror must not reconstruct or trust executable symlinks')
        bad = copy.deepcopy(self.observation); bad['executables']['node']['resolvedPath'] = '/other/node'
        with self.assertRaises(ValueError): verify(bad, self.root, self.expected)
        self.put(resolved, raw + b' changed during copy', 0o555)
        with self.assertRaises(ValueError): verify(self.observation, self.root, self.expected)


class ObservationWorkerTests(unittest.TestCase):
    """Synthetic daemon responses independently parse argv; no runtime claim."""
    def engine(self, *, timeout=False, drift=False):
        module_path = Path(__file__).with_name('source_build_toolchain.py')
        self.assertTrue(module_path.is_file(), 'Task5 missing source_build_toolchain implementation')
        spec = importlib.util.spec_from_file_location('task5_worker_control', module_path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertTrue(callable(getattr(module, '_ImageEngine', None)), 'Task5 missing _ImageEngine')
        from test_kotlin_delivery import inspection_from_command
        class Evidence:
            def __init__(self): self.receipt = {}; self.calls = []; self.item = None
            def snapshot(self, label): pass
            def run(self, label, command, seconds):
                import json, subprocess
                self.calls.append((label, command.copy()))
                args = command[1:]
                if args[0] == 'create':
                    self.item = inspection_from_command(args, 'd' * 64)
                    at = args.index('--entrypoint')
                    self.item['Config'].update(Entrypoint=[args[at + 1]], Cmd=args[at + 3:])
                    self.item['HostConfig'].update(AutoRemove=False, RestartPolicy={'Name': 'no', 'MaximumRetryCount': 0})
                    return ('d' * 64).encode()
                if args[0] == 'inspect':
                    value = copy.deepcopy(self.item)
                    if drift and any(row[1][0:3] == ['/fixed/wrapper', 'start', '--attach'] for row in self.calls):
                        value['Config']['Labels']['org.mingos.observer.nonce'] = 'foreign'
                    return json.dumps([value]).encode()
                if args[:2] == ['start', '--attach']:
                    if timeout:
                        self.item['State']['Running'] = True
                        raise subprocess.TimeoutExpired(command, seconds, output=b'partial raw log')
                    return b'{"syntheticExternalBoundary":true}'
                if args[0] == 'stop': self.item['State']['Running'] = False; return b''
                raise AssertionError(args)
        evidence = Evidence()
        engine = module._ImageEngine(evidence, 'a' * 32, '/fixed/wrapper',
            'sha256:' + '5' * 64, 'node', timeout=60)
        return engine, evidence

    def test_observer_command_has_full_policy_exact_glue_and_readonly_inputs(self):
        engine, evidence = self.engine()
        mounts = [{'type': 'bind', 'source': '/fixed/harness', 'target': '/harness', 'readonly': True},
                  {'type': 'volume', 'source': 'existing-gradle', 'target': '/deps/gradle', 'readonly': True}]
        command = ['/harness/scripts/boot/source_build_replay.mjs', 'observe', '{"control":true}']
        engine.container('observe-byte-control', mounts, command)
        argv = evidence.calls[0][1]
        for flag, value in [('--pull', 'never'), ('--network', 'none'), ('--user', '65534:65534'),
                ('--cpus', '2'), ('--memory', '4g'), ('--pids-limit', '256'), ('--cap-drop', 'ALL'),
                ('--security-opt', 'no-new-privileges'), ('--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777')]:
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertIn('--read-only', argv); self.assertNotIn('--cap-add', argv)
        self.assertEqual(argv[-3:], command)
        for index, arg in enumerate(argv):
            if arg == '--mount': self.assertIn('readonly', argv[index + 1])
        self.assertEqual(evidence.calls[-1][0], 'observe-byte-control-inspect-after')
        self.assertFalse(any(cmd[1] in ('rm', 'rmi', 'pull', 'build') for _, cmd in evidence.calls))

    def test_timeout_keeps_final_inspection_and_stops_only_revalidated_owned_worker(self):
        import subprocess
        engine, evidence = self.engine(timeout=True)
        with self.assertRaises(subprocess.TimeoutExpired):
            engine.container('timeout-control', [], ['-e', 'synthetic timeout boundary'])
        labels = [row[0] for row in evidence.calls]
        self.assertIn('timeout-control-inspect-after', labels)
        self.assertIn('timeout-control-inspect-before-stop', labels)
        self.assertIn('timeout-control-stop-owned', labels)
        self.assertEqual(labels[-1], 'timeout-control-inspect-after-stop')
        self.assertEqual(evidence.receipt['containers'][-1]['cid'], 'd' * 64)
        self.assertFalse(evidence.item['State']['Running'])
        engine, evidence = self.engine(timeout=True, drift=True)
        with self.assertRaises(ValueError): engine.container('drift-control', [], ['-e', 'synthetic'])
        self.assertFalse(any(cmd[1] == 'stop' for _, cmd in evidence.calls), 'never stop changed ownership')

    def test_copy_opens_resolved_regular_bytes_and_rejects_corrupted_transport(self):
        module_path = Path(__file__).with_name('source_build_toolchain.py')
        self.assertTrue(module_path.is_file(), 'Task5 missing source_build_toolchain implementation')
        spec = importlib.util.spec_from_file_location('task5_copy_control', module_path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.assertTrue(callable(getattr(module, '_copy_observed_tools', None)), 'Task5 missing _copy_observed_tools')
        raw = b'synthetic observed actual bytes'
        observation = {'executables': {'node': {'path': '/usr/local/bin/node', 'resolvedPath': '/opt/node/bin/node',
            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'mode': 0o555}}, 'lockedFiles': {}}
        with tempfile.TemporaryDirectory() as temp:
            for corrupt in (False, True):
                root = Path(temp) / str(corrupt); engine = mock.Mock()
                def cp(args, label, *unused):
                    self.assertEqual(args[:2], ['cp', 'd' * 64 + ':/opt/node/bin/node'])
                    destination = Path(args[2]); self.assertTrue(destination.is_relative_to(root))
                    destination.write_bytes(raw + (b' corruption' if corrupt else b'')); destination.chmod(0o555)
                    return b''
                engine.call.side_effect = cp
                if corrupt:
                    with self.assertRaises(ValueError): module._copy_observed_tools(engine, 'd' * 64, observation, root)
                else:
                    module._copy_observed_tools(engine, 'd' * 64, observation, root)
                    self.assertEqual((root / 'opt/node/bin/node').read_bytes(), raw)
                    self.assertFalse((root / 'usr/local/bin/node').exists())


if __name__ == '__main__': unittest.main()
