"""Closed uninstall orchestration controls; no guest or Docker invocation."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def load():
    spec = importlib.util.spec_from_file_location('uninstall_driver_controls', HERE / 'driver.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DriverControls(unittest.TestCase):
    def test_repository_import_and_validate_inputs(self):
        driver = load()
        expected = Path('/repo') if HERE in (Path('/prep'), Path('/assets/checker')) else HERE.parents[2]
        self.assertEqual(driver.REPO, expected)
        with tempfile.TemporaryDirectory() as directory:
            driver.validate_inputs(*driver.input_contracts(), directory)

    @unittest.skipUnless(os.name == 'posix', 'Linux deployment paths require POSIX')
    def test_deployment_import_and_validate_inputs(self):
        source = (HERE / 'driver.py').read_text()
        for filename in ('/assets/checker/driver.py', '/prep/driver.py'):
            with self.subTest(filename=filename):
                code = """
import sys, tempfile
from pathlib import Path
namespace = {'__file__': sys.argv[1], '__name__': 'deployment_driver'}
exec(compile(sys.argv[2], sys.argv[1], 'exec'), namespace)
assert namespace['REPO'] == Path('/repo'), namespace['REPO']
with tempfile.TemporaryDirectory() as directory:
    first, second = namespace['input_contracts']()
    namespace['validate_inputs'](first, second, directory)
    first['steps'].append(first['steps'][-1])
    try:
        namespace['validate_inputs'](first, second, directory)
    except ValueError:
        pass
    else:
        raise AssertionError('mutated input accepted')
import infra
assert Path(infra.__file__).resolve() == Path('/repo/scripts/boot/infra.py')
"""
                result = subprocess.run([sys.executable, '-I', '-B', '-c', code,
                                         filename, source], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_exact_seven_one_inputs_and_retained_budget(self):
        driver = load()
        first, second = driver.input_contracts()
        self.assertEqual([len(first['steps']), len(second['steps'])], [7, 1])
        for number, value in enumerate((first, second), 1):
            self.assertEqual(value, json.loads((HERE / f'input-boot{number}.json').read_bytes()))
        budget = driver.execution_budget(first, second)
        self.assertEqual(budget['minimumBootSeconds'], [770, 470])
        self.assertEqual(budget['timeoutSecondsEach'], 900)
        self.assertEqual(budget['pairTimeoutSeconds'], 1920)
        self.assertEqual(budget['containerTimeoutSeconds'], 1950)
        first['steps'].append(first['steps'][-1])
        with self.assertRaises(ValueError):
            driver.execution_budget(first, second)

    def test_duplicate_json_fields_rejected(self):
        with self.assertRaises(ValueError):
            load().strict_json(b'{"one":1,"one":2}')

    def test_first_failure_latches_and_prevents_second_boot(self):
        driver = load()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            def run(number):
                calls.append(number)
                raise ValueError('synthetic first failure')
            result = driver.run_two_boots(root, run, lambda *args: None)
            self.assertEqual(calls, [1])
            self.assertEqual(result['status'], 'failed')
            self.assertTrue((root / 'first-failure.json').is_file())
            with self.assertRaises(FileExistsError):
                driver.run_two_boots(root, run, lambda *args: None)
            self.assertEqual(calls, [1])

    def test_boot2_app2_rejected_live(self):
        driver = load()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guest.jsonl'
            path.write_text(json.dumps({'type':'desktop-state','state':{'apps':[{'app':2,'state':'closed'}]}})+'\n')
            with self.assertRaises(ValueError):
                driver.RawGuard(path, 2).poll(final=True)

    def test_truncation_and_actual_guest_failure_rejected(self):
        driver = load()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'guest.jsonl'
            for raw in (b'{"type":"guest-start"}', b'{"type":"data-sync-error"}\n'):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    driver.RawGuard(path, 1).poll(final=True)


if __name__ == '__main__':
    unittest.main()
