"""Explicit Docker boundary tests; subprocesses are replaced, capture stays real."""
import io
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    from docker_transport import DockerTransport
except ImportError:
    DockerTransport = None


class DockerTransportTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(DockerTransport, 'explicit Docker transport is not implemented')
        self.client = DockerTransport('/usr/local/bin/docker', 'unix:///run/p01/docker.sock', '/opt/p01/client')

    def test_explicit_endpoint(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://bad', 'DOCKER_CONTEXT': 'bad'}):
            self.assertEqual(self.client.argv(['info']), ['/usr/local/bin/docker', '--host', 'unix:///run/p01/docker.sock', '--config', '/opt/p01/client', 'info'])

    def test_constructor_rejects_invalid_values_without_subprocess(self):
        with patch('subprocess.run') as run:
            for executable, endpoint, config in [('', 'unix:///run/a', '/cfg'), ('docker', 'unix:///run/a', '/cfg'), ('/bin/../docker', 'unix:///run/a', '/cfg'), ('/bin/docker\n', 'unix:///run/a', '/cfg'), ('/bin/docker', '', '/cfg'), ('/bin/docker', 'tcp://localhost:2375', '/cfg'), ('/bin/docker', 'unix://relative', '/cfg'), ('/bin/docker', 'unix:///run/../a', '/cfg'), ('/bin/docker', 'unix:///run/a', 'relative'), ('/bin/docker', 'unix:///run/a', 'C:\\a\\..\\b')]:
                with self.subTest(values=(executable, endpoint, config)), self.assertRaises(ValueError):
                    DockerTransport(executable, endpoint, config)
            run.assert_not_called()

    def test_windows_absolute_and_npipe(self):
        client = DockerTransport('C:\\Program Files\\Docker\\docker.exe', 'npipe:////./pipe/p01', 'C:\\p01\\config')
        self.assertEqual(client.argv(['info'])[1:5], ['--host', 'npipe:////./pipe/p01', '--config', 'C:\\p01\\config'])

    def test_overrides_rejected_without_subprocess(self):
        with patch('subprocess.run') as run:
            for option in ['--host', '--host=unix:///bad', '-H', '-Hunix:///bad', '--context', '--context=bad', '-cbad', '--config', '--config=/bad', '--tls', '--tls=false', '--tlsverify', '--tlscacert=/bad', '--tlscert', '--tlskey=/bad']:
                with self.subTest(option=option), self.assertRaises(ValueError):
                    self.client.run(['info', option], timeout=1)
            for args in ['info', [None], ['info\x00']]:
                with self.assertRaises(ValueError):
                    self.client.run(args, timeout=1)
            run.assert_not_called()

    def test_combined_short_connection_overrides_never_execute(self):
        cases = [['-DHunix:///bad', 'info'], ['-Dcother', 'info'],
                 ['-DDcother', 'info'], ['-DH', 'unix:///bad', 'info'],
                 ['-Dc', 'other', 'info'], ['-DDc', 'other', 'info'],
                 ['-vHunix:///bad', 'info'], ['-itH', 'unix:///bad', 'info']]
        for args in cases:
            for entry in ['run', 'migration_runner']:
                with self.subTest(args=args, entry=entry), patch('subprocess.run') as run:
                    run.return_value = subprocess.CompletedProcess([], 0, b'', b'')
                    receipt = {'operations': []}
                    with self.assertRaises(ValueError):
                        if entry == 'run':
                            self.client.run(args, timeout=1)
                        else:
                            self.client.migration_runner(receipt, 'info', [self.client.executable, *args], 1)
                    run.assert_not_called()
                    self.assertEqual(receipt['operations'], [])

    def test_normal_values_and_separate_short_options_are_preserved(self):
        args = ['-D', 'image', 'inspect', '-f', '{{.Config.Hostname}}', 'cache:check']
        self.assertEqual(self.client.argv(args)[5:], args)
        self.assertEqual(self.client.argv(['-l=trace', 'info'])[5:], ['-l=trace', 'info'])
    def test_run_cleans_environment_and_keeps_binary_stdin(self):
        bad = {'DOCKER_HOST': 'bad', 'DOCKER_CONTEXT': 'bad', 'DOCKER_TLS_VERIFY': '1', 'DOCKER_CERT_PATH': 'bad', 'HTTP_PROXY': 'bad', 'https_proxy': 'bad', 'ALL_PROXY': 'bad', 'NO_PROXY': 'bad', 'KEEP': 'yes'}
        with tempfile.TemporaryFile('w+b') as source, patch.dict(os.environ, bad), patch('subprocess.run') as run:
            original = dict(os.environ)
            expected = subprocess.CompletedProcess([], 0, b'ok', b'')
            run.return_value = expected
            self.assertIs(self.client.run(['image', 'load'], timeout=17, stdin=source), expected)
            kwargs = run.call_args.kwargs
            self.assertIs(kwargs['stdin'], source)
            self.assertEqual(kwargs['timeout'], 17)
            self.assertIs(kwargs['shell'], False)
            self.assertEqual(kwargs['env']['KEEP'], 'yes')
            self.assertFalse(any(key.upper().startswith('DOCKER_') or key.upper().endswith('_PROXY') for key in kwargs['env']))
            self.assertEqual(dict(os.environ), original)

    def test_loader_validation_before_subprocess(self):
        with patch('subprocess.run') as run:
            for command, kwargs in [(['/bin/other', 'image', 'load'], {}), ([], {}), ([self.client.executable, 'info'], {'env': {}}), ([self.client.executable, 'info'], {'shell': True}), ([self.client.executable, 'info'], {'input': b'x'}), ([self.client.executable, '--host=unix:///bad'], {})]:
                with self.assertRaises(ValueError):
                    self.client.migration_runner({'operations': []}, 'load', command, 30, **kwargs)
            for source in [b'bytes', io.BytesIO(b'x'), io.StringIO('text')]:
                with self.assertRaises(ValueError):
                    self.client.run(['image', 'load'], timeout=30, stdin=source)
            with tempfile.TemporaryFile('w+t') as source, self.assertRaises(ValueError):
                self.client.run(['image', 'load'], timeout=30, stdin=source)
            run.assert_not_called()

    def test_adapter_keeps_capture_nonzero_timeout_and_stdin(self):
        receipt = {'operations': []}
        with tempfile.TemporaryFile('w+b') as source, patch('subprocess.run') as run:
            run.return_value = subprocess.CompletedProcess([], 7, b'partial', b'failure')
            result, operation = self.client.migration_runner(receipt, 'load', [self.client.executable, 'image', 'load'], 23, stdin=source)
            self.assertEqual(result.returncode, 7)
            self.assertIs(operation, receipt['operations'][0])
            self.assertEqual(operation['exitCode'], 7)
            self.assertEqual(operation['stdout'], 'partial')
            self.assertEqual(operation['stderr'], 'failure')
            self.assertIs(run.call_args.kwargs['stdin'], source)
            self.assertIs(run.call_args.kwargs['shell'], False)
            self.assertEqual(run.call_args.kwargs['timeout'], 23)
            self.assertEqual(operation['command'], ['/usr/local/bin/docker', '--host', 'unix:///run/p01/docker.sock', '--config', '/opt/p01/client', 'image', 'load'])
            run.side_effect = subprocess.TimeoutExpired([], 23, output=b'partial', stderr=b'timeout')
            with self.assertRaises(ValueError):
                self.client.migration_runner(receipt, 'load', [self.client.executable, 'image', 'load'], 23, stdin=source)
            self.assertTrue(receipt['operations'][1]['timedOut'])
            self.assertEqual(receipt['operations'][1]['stdout'], 'partial')
            self.assertEqual(receipt['operations'][1]['stderr'], 'timeout')


if __name__ == '__main__':
    unittest.main()
