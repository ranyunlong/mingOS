"""Real executable wrapper boundary; fake Docker is explicitly an external seam."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from docker_transport import DockerTransport
try:
    import source_build_guest as target
except ModuleNotFoundError:
    target = None


class EnvironmentTests(unittest.TestCase):
    def test_sanitizes_all_connection_environment_case_insensitively(self):
        self.assertIsNotNone(target, 'source_build_guest implementation missing')
        hostile = ['DOCKER_HOST', 'DOCKER_CONTEXT', 'docker_config', 'Tls_VERIFY',
                   'CONTAINERD_ADDRESS', 'CONTAINERD_NAMESPACE', 'CONTAINERD_SNAPSHOTTER',
                   'containerd_other', 'ConTaInErD_Custom', 'https_proxy', 'HTTP_PROXY',
                   'ALL_PROXY', 'NO_PROXY', 'FTP_PROXY', 'SSL_CERT_FILE', 'SSL_CERT_DIR']
        with mock.patch.dict(os.environ, {**dict.fromkeys(hostile, 'hostile'), 'CONTROL_KEEP': 'ok'}, clear=True):
            self.assertEqual(target.wrapper_environment(), {'CONTROL_KEEP': 'ok'})

    def test_fresh_admission_rejects_stale_handle_and_failed_child(self):
        self.assertIsNotNone(target, 'source_build_guest implementation missing')
        import wsl_environment_guest as s2
        handle = {'generation': 'control-generation', 'pid': 12, 'starttime': '34',
                  'exeSha256': 'a' * 64, 'namespaces': {'mnt': 'mnt:[1]', 'net': 'net:[2]', 'cgroup': 'cgroup:[3]'},
                  'socket': {'device': 4, 'inode': 5, 'uid': 0, 'gid': 0},
                  'daemonId': 'control-daemon',
                  'containerdNamespaces': {'containers': 'control-generation', 'plugins': 'control-generation-plugins'}}
        stale = {**handle, 'socket': {**handle['socket'], 'inode': 6}}
        with mock.patch.object(s2, 'observe_handle', return_value=stale):
            with self.assertRaisesRegex(ValueError, 'stale'):
                target._live_admission(handle, 30)
        with mock.patch.object(s2, 'observe_handle', return_value=handle), \
                mock.patch.object(s2, 'admit_environment', return_value=subprocess.CompletedProcess([], 7)):
            with self.assertRaises(ValueError): target._live_admission(handle, 30)


@unittest.skipUnless(os.name == 'posix', 'Linux executable/shebang controls')
class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(target, 'source_build_guest implementation missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.binary = self.base / 'docker-control'
        self.binary.write_text('#!' + sys.executable + '\n' +
            'import json,os,sys\n'
            'if os.environ.get("CONTROL_KOTLIN"):\n'
            ' os.write(2,b"external Docker seam\\n");sys.exit(23)\n'
            'if sys.argv[-1:] == ["binary"]:\n'
            ' os.write(1,sys.stdin.buffer.read());os.write(2,b"\\x00\\xffstderr");sys.exit(37)\n'
            'os.write(1,json.dumps({"argv":sys.argv,"env":dict(os.environ)}).encode())\n', encoding='utf8')
        self.binary.chmod(0o555)
        self.transport = DockerTransport(str(self.binary), 'unix:///run/control/docker.sock', str(self.base / 'client'))
        self.wrapper = self.base / 'docker-wrapper'

    def create(self):
        return target.create_wrapper(self.transport, self.wrapper)

    def invoke(self, args, **kwargs):
        return subprocess.run([str(self.wrapper), *args], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False, **kwargs)

    def test_actual_wrapper_argv_environment_and_no_extra_stdout(self):
        self.create()
        env = {**os.environ, 'DOCKER_HOST': 'bad', 'DoCkEr_Context': 'bad',
               'CONTAINERD_ADDRESS': 'bad', 'containerd_NAMESPACE': 'bad',
               'CONTAINERD_SNAPSHOTTER': 'bad', 'ConTaInErD_Extra': 'bad', 'https_proxy': 'bad'}
        result = self.invoke(['version', '--format', '{{json .}}'], env=env)
        self.assertEqual((result.returncode, result.stderr), (0, b''))
        value = json.loads(result.stdout)
        self.assertEqual(value['argv'], [str(self.binary), '--host', 'unix:///run/control/docker.sock',
                         '--config', str(self.base / 'client'), 'version', '--format', '{{json .}}'])
        self.assertFalse(any(k.upper().startswith(('DOCKER_', 'TLS_', 'CONTAINERD_')) for k in value['env']))
        self.assertNotIn('https_proxy', value['env'])
        self.assertTrue(self.wrapper.is_absolute() and os.access(self.wrapper, os.X_OK))

    def test_actual_binary_stdio_and_exit_status(self):
        self.create()
        data = bytes(range(256)) * 4097
        result = self.invoke(['binary'], input=data)
        self.assertEqual(result.stdout, data)
        self.assertEqual(result.stderr, b'\x00\xffstderr')
        self.assertEqual(result.returncode, 37)

    def test_all_override_forms_rejected_before_external_executable(self):
        self.create()
        cases = []
        for flag in ['--host', '--context', '--config', '--tls', '--tlsverify',
                     '--tlscacert', '--tlscert', '--tlskey', '-H', '-c']:
            cases += [[flag, 'hostile'], [flag + '=hostile']]
        cases += [['-Hhostile'], ['-chostile'], ['-DHhostile'], ['-Dcother'],
                  ['-D', '-H', 'hostile'], ['--', '--host=hostile']]
        for args in cases:
            with self.subTest(args=args):
                result = self.invoke(args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')

    def test_frozen_module_binary_and_wrapper_pins_detect_drift(self):
        record = self.create()
        pins = target.wrapper_pins(record)
        for name in ['wrapper', 'transport', 'binary']:
            path = Path(pins[name]['path'])
            self.assertEqual(pins[name]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        self.wrapper.chmod(0o755)
        self.wrapper.write_text(self.wrapper.read_text() + '# mutation\n')
        with self.assertRaises(ValueError): target.wrapper_pins(record)

    def test_every_phase_repeats_live_admission_instead_of_cached_record(self):
        import wsl_environment_guest as s2
        record = self.create()
        record['admission'] = {'status': 'caller-supplied-cached-record-is-not-authority'}
        handle = {'generation': 'control-generation', 'pid': 12, 'starttime': '34',
                  'exeSha256': 'a' * 64, 'namespaces': {'mnt': 'mnt:[1]', 'net': 'net:[2]', 'cgroup': 'cgroup:[3]'},
                  'socket': {'device': 4, 'inode': 5, 'uid': 0, 'gid': 0},
                  'daemonId': 'control-daemon', 'containerdNamespaces': {}}
        observations = []
        def observe(generation, pid):
            self.assertEqual((generation, pid), ('control-generation', 12))
            value = {**handle, 'controlObservation': len(observations) + 1}
            observations.append(value)
            return value
        def admitted(supplied, argv, *, timeout):
            self.assertEqual((supplied, argv, timeout), (handle, ['/usr/bin/true'], 30))
            return subprocess.CompletedProcess(argv, 0)
        def readlink(path):
            return handle['namespaces'][path.rsplit('/', 1)[1]]
        # The privileged accepted S2 process boundary is external to this unit
        # control. All wrapper pins/traversal and repeated comparison stay real.
        with mock.patch.object(s2, 'inner_transport', return_value=self.transport), \
                mock.patch.object(s2, 'observe_handle', side_effect=observe), \
                mock.patch.object(s2, 'admit_environment', side_effect=admitted), \
                mock.patch.object(target.os, 'readlink', side_effect=readlink), \
                mock.patch.object(Path, 'read_text', return_value=str(os.getpid()) + '\n'):
            first = target.wrapper_pins(record, handle=handle)
            second = target.wrapper_pins(record, handle=handle)
        self.assertEqual(first['admission']['beforePins']['before']['controlObservation'], 1)
        self.assertEqual(first['admission']['afterPins']['after']['controlObservation'], 4)
        self.assertEqual(second['admission']['beforePins']['before']['controlObservation'], 5)
        self.assertEqual(second['admission']['afterPins']['after']['controlObservation'], 8)

    def test_exclusive_output_and_unsafe_executable(self):
        self.create()
        with self.assertRaises(FileExistsError): self.create()
        link = self.base / 'link-binary'
        link.symlink_to(self.binary)
        with self.assertRaises((ValueError, OSError)):
            target.create_wrapper(DockerTransport(str(link), self.transport.endpoint,
                                  self.transport.config_directory), self.base / 'unsafe-wrapper')

    def test_current_kotlin_runner_receives_absolute_executable_string(self):
        import kotlin_delivery
        self.create()
        repo = self.base / 'repo'; repo.mkdir()
        for name in kotlin_delivery.REQUIRED_SOURCE:
            p = repo / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'control')
        baseline = self.base / 'baseline.json'
        baseline.write_text(json.dumps({'schemaVersion': 1, 'files': {'module.jar': 'a' * 64}}))
        output = self.base / 'kotlin-result'
        # Real run -> CapturedDocker -> executable wrapper -> external seam.
        # The seam intentionally fails first image inspection; no fake success/compilation.
        with mock.patch.dict(os.environ, {'CONTROL_KOTLIN': '1'}):
            with self.assertRaisesRegex(RuntimeError, 'fixed-image'):
                kotlin_delivery.run({'repo': repo, 'baseline': baseline, 'output': output,
                    'original_cache': 'control-gradle', 'profile': 'developer-workflow',
                    'docker': str(self.wrapper)})
        command = json.loads((output / 'fixed-image.command.json').read_bytes())
        self.assertEqual(command['argv'][0], str(self.wrapper))
        self.assertEqual((output / 'fixed-image.stderr.log').read_bytes(), b'external Docker seam\n')
        self.assertEqual(json.loads((output / 'fixed-image.exit.json').read_bytes())['exitCode'], 23)
        result = json.loads((output / 'result.json').read_bytes())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['validationKind'], 'actual-docker')
        # Existing runner's actual-docker label alone cannot establish real acceptance.
        with self.assertRaises(ValueError): target.require_actual_runner_result(result)
        for bad in [{'status': 'pipeline-completed', 'validationKind': 'fake-control'},
                    {'status': 'synthetic-input-controls-passed', 'validationKind': 'actual-docker'}]:
            with self.assertRaises(ValueError): target.require_actual_runner_result(bad)

    def test_current_cache_restore_receives_absolute_executable_string(self):
        import developer_cache
        import developer_cache_worker as worker
        self.create()
        cargo, gradle = self.base / 'cargo', self.base / 'gradle'
        cargo.mkdir(); gradle.mkdir()
        (cargo / 'crate').write_bytes(b'cargo'); (cargo / 'crate').chmod(0o644)
        (gradle / 'module.jar').write_bytes(b'gradle'); (gradle / 'module.jar').chmod(0o644)
        baseline = self.base / 'baseline.json'
        baseline.write_text(json.dumps({'schemaVersion': 1, 'files': worker.file_map(worker.scan(gradle))}))
        inputs = self.base / 'inputs'; inputs.mkdir()
        worker.export_inputs(cargo, gradle, baseline, inputs)
        pin = hashlib.sha256((inputs / 'developer-inputs.json').read_bytes()).hexdigest()
        output = self.base / 'cache-result'
        with mock.patch.dict(os.environ, {'CONTROL_KOTLIN': '1'}):
            with self.assertRaisesRegex(ValueError, 'fixed-image-inspect'):
                developer_cache.restore(inputs, pin, output, docker=str(self.wrapper))
        result = json.loads((output / 'result.json').read_bytes())
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['operations'][0]['command'][0], str(self.wrapper))
        self.assertEqual(result['operations'][0]['exitCode'], 23)
        self.assertEqual(result['validationKind'], 'actual-docker')
        # This exercises real restore input verification and real wrapper invocation;
        # fake external Docker fails before allocation and is never accepted.


if __name__ == '__main__': unittest.main()
