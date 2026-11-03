"""Boundary tests run without Docker, kernel, or a running guest."""
import base64
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from bootlib import checked_path, checked_session, qemu_command, sha256, validate_bundle, validate_kernel_config

class BootBoundaryTests(unittest.TestCase):
    def persistent_bundle(self, base, profile='persistent-install'):
        first, second = base / 'boot1.json', base / 'boot2.json'
        plan = {'schemaVersion': 1, 'requireApps': [1], 'steps': [
            {'name': 'stop', 'events': [{'kind': 'pointer', 'x': 1250, 'y': 12},
                                     {'kind': 'button', 'button': 'left', 'down': True},
                                     {'kind': 'button', 'button': 'left', 'down': False}],
             'expect': {'phase': 'stopped'}}]}
        first.write_text(json.dumps(plan)); second.write_text(json.dumps(plan, indent=2))
        review = {'reviewed': True, 'reviewer': 'test only', 'profile': profile,
                  'inputScripts': {'boot1Sha256': sha256(first), 'boot2Sha256': sha256(second)},
                  'extraArgs': ['--state-file', '/run/evidence/desktop-state.json']}
        review_path = base / 'review.json'; review_path.write_text(json.dumps(review))
        for name in ('vmlinuz', 'initramfs.cpio.gz'): (base / name).write_bytes(name.encode())
        manifest = {'reviewSha256': sha256(review_path), 'sessionReview': review,
                    'infraApiVersion': 1, 'payloadProfile': profile,
                    'instrumentedQualification': True, 'diagnosticProfile': 'input-pointer-ready-v1',
                    'inputDiagnostics': True,
                    'sha256': {name: sha256(base / name) for name in ('vmlinuz', 'initramfs.cpio.gz')}}
        (base / 'bundle.json').write_text(json.dumps(manifest))
        return review_path, first, second

    def test_persistent_vm_dry_run_records_number_hash_and_kernel_role_without_qemu(self):
        import vm
        with tempfile.TemporaryDirectory() as work:
            base = Path(work); review, first, _ = self.persistent_bundle(base)
            disk = {'imagePath': '/task/disk.raw', 'uuid': 'disk-uuid', 'serial': 'MINGDATA0123456789ab'}
            argv = ['vm.py', '--bundle', str(base), '--review', str(review), '--output', str(base / 'out'),
                    '--input-script', str(first), '--boot-number', '1', '--work-root', '/unused-test-root',
                    '--data-manifest', 'disk.json', '--diagnostic-split-input', '--diagnostic-pointer-handshake', '--dry-run']
            with mock.patch.object(sys, 'argv', argv), mock.patch('data_disk.load_disk', return_value=disk), \
                 mock.patch('data_disk.lease', return_value=contextlib.nullcontext()), \
                 mock.patch('subprocess.Popen', side_effect=AssertionError('dry run started a process')):
                with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(vm.main(), 0)
            result = json.loads((base / 'out/result.json').read_text())
            self.assertFalse(result['bootAttempted'])
            self.assertEqual(result['bootNumber'], 1)
            self.assertEqual(result['inputScriptSha256'], sha256(first))
            self.assertIn('mingos.persistent_install_role=initialize-first', result['argv'][-1])

    def test_persistent_uninstall_vm_dry_run_binds_both_fixed_kernel_roles(self):
        import vm
        for number, role in ((1, 'initialize-first'), (2, 'open-uninstalled')):
            with self.subTest(number=number), tempfile.TemporaryDirectory() as work:
                base = Path(work); review, first, second = self.persistent_bundle(base, 'persistent-uninstall')
                source = first if number == 1 else second
                disk = {'imagePath': '/task/disk.raw', 'uuid': 'disk-uuid', 'serial': 'MINGDATA0123456789ab'}
                argv = ['vm.py', '--bundle', str(base), '--review', str(review), '--output', str(base / 'out'),
                        '--input-script', str(source), '--boot-number', str(number), '--work-root', '/unused-test-root',
                        '--data-manifest', 'disk.json', '--diagnostic-split-input', '--diagnostic-pointer-handshake', '--dry-run']
                with mock.patch.object(sys, 'argv', argv), mock.patch('data_disk.load_disk', return_value=disk), \
                     mock.patch('data_disk.lease', return_value=contextlib.nullcontext()), \
                     mock.patch('subprocess.Popen', side_effect=AssertionError('dry run started a process')):
                    try:
                        with contextlib.redirect_stdout(io.StringIO()): result_code = vm.main()
                    except ValueError as error: self.fail('uninstall dry-run rejected: ' + str(error))
                self.assertEqual(result_code, 0)
                result = json.loads((base / 'out/result.json').read_text())
                self.assertFalse(result['bootAttempted'])
                self.assertEqual((result['bootNumber'], result['inputScriptSha256']), (number, sha256(source)))
                self.assertIn('mingos.persistent_uninstall_boot=' + str(number), result['argv'][-1])
                self.assertIn('mingos.persistent_uninstall_role=' + role, result['argv'][-1])
                self.assertNotIn('mingos.persistent_install_', result['argv'][-1])

    def test_persistent_vm_rejects_duplicate_and_unknown_input_fields_before_process_start(self):
        import vm
        for profile in ('persistent-install', 'persistent-uninstall'):
            for invalid in ('duplicate-key', 'unknown-root'):
                with self.subTest(profile=profile, invalid=invalid), tempfile.TemporaryDirectory() as work:
                    base = Path(work); review_path, source, _ = self.persistent_bundle(base, profile)
                    if invalid == 'duplicate-key':
                        source.write_text(source.read_text().replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1', 1))
                    else:
                        value = json.loads(source.read_text()); value['unknown'] = True; source.write_text(json.dumps(value))
                    review = json.loads(review_path.read_text()); review['inputScripts']['boot1Sha256'] = sha256(source)
                    review_path.write_text(json.dumps(review))
                    manifest = json.loads((base / 'bundle.json').read_text())
                    manifest.update(sessionReview=review, reviewSha256=sha256(review_path)); (base / 'bundle.json').write_text(json.dumps(manifest))
                    disk = {'imagePath': '/task/disk.raw', 'uuid': 'disk-uuid', 'serial': 'MINGDATA0123456789ab'}
                    argv = ['vm.py', '--bundle', str(base), '--review', str(review_path), '--output', str(base / 'out'),
                            '--input-script', str(source), '--boot-number', '1', '--work-root', '/unused-test-root',
                            '--data-manifest', 'disk.json', '--diagnostic-split-input', '--diagnostic-pointer-handshake', '--dry-run']
                    with mock.patch.object(sys, 'argv', argv), mock.patch('data_disk.load_disk', return_value=disk), \
                         mock.patch('data_disk.lease', return_value=contextlib.nullcontext()), \
                         mock.patch('subprocess.Popen', side_effect=AssertionError('invalid input started process')), \
                         contextlib.redirect_stdout(io.StringIO()):
                        with self.assertRaises(ValueError): vm.main()

    def test_vm_keeps_reviewed_input_bytes_when_path_changes_after_validation(self):
        import vm
        for profile in ('persistent-install', 'persistent-uninstall', 'data-text'):
            for restore in (False, True):
                with self.subTest(profile=profile, restore=restore), tempfile.TemporaryDirectory() as work:
                    base = Path(work); review_path, source, _ = self.persistent_bundle(base, profile=profile)
                    approved = source.read_bytes(); approved_hash = sha256(source)
                    approved_spec = json.loads(approved)
                    changed = json.loads(approved)
                    changed['steps'][0]['name'] = 'UNREVIEWED'
                    changed['steps'][0]['events'][0]['x'] = 99
                    review = json.loads(review_path.read_text())
                    if profile not in ('persistent-install', 'persistent-uninstall'):
                        review.update(profile=profile, inputScriptSha256=approved_hash)
                        del review['inputScripts']
                        review_path.write_text(json.dumps(review))
                        manifest = json.loads((base / 'bundle.json').read_text())
                        manifest.update(sessionReview=review, reviewSha256=sha256(review_path))
                        (base / 'bundle.json').write_text(json.dumps(manifest))
                    disk = {'imagePath': '/task/disk.raw', 'uuid': 'disk-uuid', 'serial': 'MINGDATA0123456789ab'}
                    loads = []
                    def load_disk(*args):
                        loads.append(None)
                        if len(loads) == 1: source.write_text(json.dumps(changed))
                        elif restore: source.write_bytes(approved)
                        return disk
                    plans = []; input_plan = vm.InputPlan
                    def build_plan(*args, **kwargs):
                        plan = input_plan(*args, **kwargs); plans.append(plan); return plan
                    argv = ['vm.py', '--bundle', str(base), '--review', str(review_path),
                            '--output', str(base / 'out'), '--input-script', str(source),
                            '--work-root', '/unused-test-root', '--data-manifest', 'disk.json',
                            '--diagnostic-split-input', '--diagnostic-pointer-handshake', '--dry-run']
                    if profile in ('persistent-install', 'persistent-uninstall'): argv += ['--boot-number', '1']
                    with mock.patch.object(sys, 'argv', argv), \
                         mock.patch('data_disk.load_disk', side_effect=load_disk), \
                         mock.patch('data_disk.lease', return_value=contextlib.nullcontext()), \
                         mock.patch.object(vm, 'InputPlan', side_effect=build_plan), \
                         mock.patch('subprocess.Popen', side_effect=AssertionError('dry run started a process')):
                        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(vm.main(), 0)
                    result = json.loads((base / 'out/result.json').read_text())
                    self.assertEqual(plans[0].spec, approved_spec)
                    self.assertEqual(result['inputScriptSha256'], approved_hash)

    def test_cold_runner_freezes_distinct_scripts_and_passes_fixed_numbers(self):
        spec = importlib.util.spec_from_file_location('cold_boot_test', Path(__file__).with_name('cold-boot.py'))
        cold = importlib.util.module_from_spec(spec); spec.loader.exec_module(cold)
        with tempfile.TemporaryDirectory() as work:
            base = Path(work); review, first, second = self.persistent_bundle(base)
            commands = []
            def run_vm(argv, **kwargs):
                commands.append(argv)
                number = len(commands)
                self.assertEqual(argv[argv.index('--boot-number') + 1], str(number))
                source = first if number == 1 else second
                self.assertEqual(Path(argv[argv.index('--input-script') + 1]).read_bytes(), source.read_bytes())
                output = Path(argv[argv.index('--output') + 1]); output.mkdir()
                result = {'status': 'normal-desktop-poweroff', 'bootId': 'boot-' + str(number),
                          'bundleIdentity': sha256(base / 'bundle.json'), 'diskUuid': 'disk',
                          'bootNumber': number, 'inputScriptSha256': sha256(source),
                          'marker': {'bootId': 'boot-' + str(number), 'counter': number},
                          'previousMarker': None if number == 1 else {'bootId': 'boot-1', 'counter': 1}}
                (output / 'result.json').write_text(json.dumps(result))
                return subprocess.CompletedProcess(argv, 0)
            argv = ['cold-boot.py', '--bundle', str(base), '--review', str(review), '--work-root', '/unused',
                    '--data-manifest', 'disk.json', '--output', str(base / 'cold'),
                    '--boot1-input-script', str(first), '--boot2-input-script', str(second)]
            with mock.patch.object(sys, 'argv', argv), mock.patch('subprocess.run', side_effect=run_vm):
                with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(cold.main(), 0)
            self.assertEqual(len(commands), 2)
            before = len(commands)
            # Swapping reviewed paths must fail before the first subprocess/output directory.
            argv[-3], argv[-1] = argv[-1], argv[-3]
            argv[argv.index('--output') + 1] = str(base / 'rejected')
            with mock.patch.object(sys, 'argv', argv), mock.patch('subprocess.run', side_effect=run_vm):
                with self.assertRaises(ValueError): cold.main()
            self.assertEqual(len(commands), before)
            self.assertFalse((base / 'rejected').exists())

    def test_cold_runner_reads_each_persistent_input_once_and_freezes_it_before_first_vm(self):
        spec = importlib.util.spec_from_file_location('cold_boot_frozen_test', Path(__file__).with_name('cold-boot.py'))
        cold = importlib.util.module_from_spec(spec); spec.loader.exec_module(cold)
        for profile in ('persistent-install', 'persistent-uninstall'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as work:
                base = Path(work); review, first, second = self.persistent_bundle(base, profile)
                sources = {1: first.read_bytes(), 2: second.read_bytes()}
                digests = {1: sha256(first), 2: sha256(second)}
                original_open = Path.open; reads = {first: 0, second: 0}; commands = []
                def open_file(path, *args, **kwargs):
                    if path in reads and args and args[0] == 'rb': reads[path] += 1
                    return original_open(path, *args, **kwargs)
                def run_vm(command, **kwargs):
                    number = len(commands) + 1; commands.append(command)
                    source = Path(command[command.index('--input-script') + 1])
                    self.assertNotIn(source, (first, second))
                    self.assertEqual(source.read_bytes(), sources[number])
                    self.assertEqual(command[command.index('--boot-number') + 1], str(number))
                    if number == 1: second.write_text('{"unreviewed":true}')
                    output = Path(command[command.index('--output') + 1]); output.mkdir()
                    result = {'status': 'normal-desktop-poweroff', 'bootId': 'boot-' + str(number),
                              'bundleIdentity': sha256(base / 'bundle.json'), 'diskUuid': 'disk',
                              'bootNumber': number, 'inputScriptSha256': digests[number],
                              'marker': {'bootId': 'boot-' + str(number), 'counter': number},
                              'previousMarker': None if number == 1 else {'bootId': 'boot-1', 'counter': 1}}
                    (output / 'result.json').write_text(json.dumps(result))
                    return subprocess.CompletedProcess(command, 0)
                argv = ['cold-boot.py', '--bundle', str(base), '--review', str(review), '--work-root', '/unused',
                        '--data-manifest', 'disk.json', '--output', str(base / 'cold'),
                        '--boot1-input-script', str(first), '--boot2-input-script', str(second)]
                with mock.patch.object(sys, 'argv', argv), mock.patch.object(Path, 'open', autospec=True, side_effect=open_file), \
                     mock.patch('subprocess.run', side_effect=run_vm):
                    try:
                        with contextlib.redirect_stdout(io.StringIO()): code = cold.main()
                    except ValueError as error: self.fail('persistent cold inputs rejected: ' + str(error))
                self.assertEqual(code, 0, (base / 'cold/result.json').read_text())
                self.assertEqual(len(commands), 2)
                self.assertEqual(reads, {first: 1, second: 1})

    def test_uninstall_bundle_copies_and_hashes_the_same_relay_bytes(self):
        import bundle
        copier = getattr(bundle, 'copy_guest_scripts', None)
        self.assertTrue(callable(copier), 'guest script copy/hash boundary missing')
        scripts = Path(__file__).resolve().parent
        for profile in ('persistent-install', 'persistent-uninstall'):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as work:
                root = Path(work); (root / 'etc/mingos').mkdir(parents=True)
                hashes = copier(root, scripts, profile)
                relay = root / 'etc/mingos/persistent_uninstall_receipt.py'
                if profile == 'persistent-uninstall':
                    self.assertEqual(relay.read_bytes(), (scripts / relay.name).read_bytes())
                    self.assertEqual(hashes[relay.name], sha256(relay))
                else:
                    self.assertFalse(relay.exists())
                    self.assertNotIn(relay.name, hashes)
                for name, digest in hashes.items():
                    target = root / ('init' if name == 'init' else 'etc/mingos/' + name)
                    self.assertEqual(digest, sha256(target))

    def test_uninstall_cold_preflights_second_input_schema_before_creating_output_or_first_vm(self):
        spec = importlib.util.spec_from_file_location('cold_boot_input_schema_test', Path(__file__).with_name('cold-boot.py'))
        cold = importlib.util.module_from_spec(spec); spec.loader.exec_module(cold)
        for invalid in ('duplicate-key', 'unknown-root', 'unknown-step'):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as work:
                base = Path(work); review_path, first, second = self.persistent_bundle(base, 'persistent-uninstall')
                value = json.loads(second.read_text())
                if invalid == 'duplicate-key': second.write_text(second.read_text().replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1', 1))
                else:
                    if invalid == 'unknown-root': value['unknown'] = True
                    else: value['steps'][0]['unknown'] = True
                    second.write_text(json.dumps(value))
                review = json.loads(review_path.read_text()); review['inputScripts']['boot2Sha256'] = sha256(second)
                review_path.write_text(json.dumps(review))
                manifest = json.loads((base / 'bundle.json').read_text())
                manifest.update(sessionReview=review, reviewSha256=sha256(review_path)); (base / 'bundle.json').write_text(json.dumps(manifest))
                argv = ['cold-boot.py', '--bundle', str(base), '--review', str(review_path), '--work-root', '/unused',
                        '--data-manifest', 'disk.json', '--output', str(base / 'cold'),
                        '--boot1-input-script', str(first), '--boot2-input-script', str(second)]
                with mock.patch.object(sys, 'argv', argv), mock.patch('subprocess.run', side_effect=AssertionError('invalid boot2 started VM')), \
                     contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(ValueError): cold.main()
                self.assertFalse((base / 'cold').exists())

    def test_uninstall_cold_first_failure_preserves_report_and_never_runs_second_vm(self):
        spec = importlib.util.spec_from_file_location('cold_boot_failure_test', Path(__file__).with_name('cold-boot.py'))
        cold = importlib.util.module_from_spec(spec); spec.loader.exec_module(cold)
        with tempfile.TemporaryDirectory() as work:
            base = Path(work); review, first, second = self.persistent_bundle(base, 'persistent-uninstall')
            commands = []
            def run_vm(command, **kwargs):
                commands.append(command)
                self.assertEqual(len(commands), 1, 'failed first boot must never start boot2')
                output = Path(command[command.index('--output') + 1]); output.mkdir()
                (output / 'result.json').write_text(json.dumps({'status': 'failed', 'failureEvidence': 'preserve'}))
                return subprocess.CompletedProcess(command, 1)
            argv = ['cold-boot.py', '--bundle', str(base), '--review', str(review), '--work-root', '/unused',
                    '--data-manifest', 'disk.json', '--output', str(base / 'cold'),
                    '--boot1-input-script', str(first), '--boot2-input-script', str(second)]
            with mock.patch.object(sys, 'argv', argv), mock.patch('subprocess.run', side_effect=run_vm):
                with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(cold.main(), 1)
            self.assertEqual(len(commands), 1)
            report = json.loads((base / 'cold/result.json').read_text())
            self.assertEqual(report['status'], 'failed'); self.assertFalse(report['finalSystemAccepted'])
            self.assertEqual(json.loads((base / 'cold/boot-1/result.json').read_text())['failureEvidence'], 'preserve')
            self.assertFalse((base / 'cold/boot-2').exists())

    def test_persistent_boot_number_binds_qemu_kernel_role_and_requires_disk(self):
        disk = {'imagePath': '/task/disk.raw', 'uuid': 'disk-uuid', 'serial': 'MINGDATA0123456789ab'}
        for number, role in ((1, 'initialize-first'), (2, 'open-existing')):
            command = qemu_command(Path('/bundle'), Path('/out'), data_disk=disk, boot_number=number)
            kernel = command[command.index('-append') + 1]
            self.assertIn('mingos.persistent_install_boot=' + str(number), kernel)
            self.assertIn('mingos.persistent_install_role=' + role, kernel)
        for number in (0, 3, True, '1'):
            with self.subTest(number=number), self.assertRaises(ValueError):
                qemu_command(Path('/bundle'), Path('/out'), data_disk=disk, boot_number=number)
        with self.assertRaises(ValueError): qemu_command(Path('/bundle'), Path('/out'), boot_number=1)
        with self.assertRaises(ValueError): qemu_command(Path('/bundle'), Path('/out'), paused=True, data_disk=disk, boot_number=1)
        legacy = qemu_command(Path('/bundle'), Path('/out'), data_disk=disk)
        self.assertNotIn('mingos.persistent_install', legacy[legacy.index('-append') + 1])

    def test_pending_review_does_not_create_rootfs(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            payload = base / "payload"; payload.mkdir()
            review = base / "review.json"; review.write_text('{"reviewed":false}')
            output = base / "bundle"
            proc = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("bundle.py")),
                                   "--payload", str(payload), "--review", str(review), "--output", str(output)],
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("review is pending", proc.stderr)
            self.assertFalse(output.exists())

    def test_guest_log_decoder_preserves_bytes_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            original = b"guest-log\xff\x00\n"
            entry = {"type": "log", "name": "desktop.log", "offset": 0, "encoding": "base64",
                     "data": base64.b64encode(original).decode()}
            source = base / "guest.jsonl"
            source.write_bytes((json.dumps(entry) + "\n").encode() + b'{"type":')
            proc = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("extract-evidence.py")),
                                   "--input", str(source), "--output", str(base / "decoded")],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual((base / "decoded/desktop.log").read_bytes(), original)
            self.assertEqual(json.loads(proc.stdout)["incompleteTrailingLines"], 1)

    def test_guest_log_decoder_rejects_missing_chunk(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            source = base / "guest.jsonl"
            source.write_text(json.dumps({"type": "log", "name": "desktop.log", "offset": 8,
                                          "encoding": "base64", "data": "YQ=="}) + "\n")
            proc = subprocess.run([sys.executable, "-B", str(Path(__file__).with_name("extract-evidence.py")),
                                   "--input", str(source), "--output", str(base / "decoded")],
                                  capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("out of sequence", proc.stderr)

    def test_payload_path_cannot_escape_package(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            for value in ("../secret", "/etc/passwd", "C:/secret", "x\\secret"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    checked_path(base, value)

    def test_pending_review_does_not_authorize_bundle_or_boot(self):
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(ValueError, "review"):
                checked_session({"reviewed": False}, Path(work))

    def test_review_is_bound_to_exact_host_and_catalog(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            (base / "ming-app-host").write_bytes(b"linux-release-host")
            (base / "catalog.json").write_text("{}")
            review = {"reviewed": True, "reviewer": "main-thread", "sessionMode": "bounded",
                      "hostSha256": sha256(base / "ming-app-host"),
                      "catalogSha256": sha256(base / "catalog.json"),
                      "extraArgs": ["--duration-ms", "60000"]}
            self.assertEqual(checked_session(review, base), review)
            (base / "ming-app-host").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "host"):
                checked_session(review, base)

    def test_bounded_session_keeps_current_limit_and_native_renderer(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            (base / "ming-app-host").write_bytes(b"host")
            (base / "catalog.json").write_text("{}")
            review = {"reviewed": True, "reviewer": "main-thread", "sessionMode": "bounded",
                      "hostSha256": sha256(base / "ming-app-host"),
                      "catalogSha256": sha256(base / "catalog.json")}
            for extra in (["--duration-ms", "60001"], ["--backend", "offscreen"], [],
                          ["--duration-ms", "60000", "--duration-ms", "60001"]):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    checked_session(dict(review, extraArgs=extra), base)

    def test_public_probe_bounded_session_requires_data_root_and_state_file(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            (base / "ming-app-host").write_bytes(b"host")
            (base / "catalog.json").write_text("{}")
            review = {"reviewed": True, "reviewer": "main-thread", "profile": "public-probe",
                      "sessionMode": "bounded", "hostSha256": sha256(base / "ming-app-host"),
                      "catalogSha256": sha256(base / "catalog.json"),
                      "extraArgs": ["--duration-ms", "15000", "--isolation", "strict",
                                    "--data-root", "/data/mingos", "--state-file",
                                    "/run/evidence/desktop-state.json"]}
            self.assertEqual(checked_session(review, base), review)
            with self.assertRaisesRegex(ValueError, "data-root"):
                checked_session(dict(review, extraArgs=["--duration-ms", "15000"]), base)

    def test_qemu_only_uses_task_files_no_host_devices_or_network(self):
        command = qemu_command(Path("/bundle"), Path("/out"), paused=True)
        self.assertIn("-S", command)
        self.assertIn("tcg,thread=multi", command)
        self.assertEqual(command[command.index("-nic") + 1], "none")
        self.assertIn("virtio-vga,xres=1280,yres=800", command)
        self.assertIn("rng-random,id=mingrng,filename=/dev/urandom", command)
        self.assertIn("virtio-rng-pci,rng=mingrng", command)
        self.assertNotIn("-kernel", command)
        self.assertNotIn("-initrd", command)
        self.assertNotIn("-drive", command)

    def test_builtin_virtio_console_is_valid_and_missing_gpu_is_rejected(self):
        settings = "\n".join(("CONFIG_DEVTMPFS=y", "CONFIG_RD_GZIP=y",
                              "CONFIG_SERIAL_8250_CONSOLE=y", "CONFIG_DRM_VIRTIO_GPU=m",
                              "CONFIG_VIRTIO_CONSOLE=y", "CONFIG_VIRTIO_INPUT=m", "CONFIG_VIRTIO_PCI=y",
                              "CONFIG_HW_RANDOM_VIRTIO=m", "CONFIG_INPUT_EVDEV=m", "CONFIG_EXT4_FS=m", "CONFIG_VIRTIO_BLK=m",
                              "CONFIG_CGROUPS=y", "CONFIG_MEMCG=y", "CONFIG_CGROUP_SCHED=y", "CONFIG_CFS_BANDWIDTH=y", "CONFIG_CGROUP_PIDS=y", "CONFIG_SECCOMP_FILTER=y", "CONFIG_NAMESPACES=y"))
        validate_kernel_config(settings)
        with self.assertRaisesRegex(ValueError, "DRM_VIRTIO_GPU"):
            validate_kernel_config(settings.replace("CONFIG_DRM_VIRTIO_GPU=m", "# disabled"))
        with self.assertRaisesRegex(ValueError, "HW_RANDOM_VIRTIO"):
            validate_kernel_config(settings.replace("CONFIG_HW_RANDOM_VIRTIO=m", "# disabled"))
        with self.assertRaisesRegex(ValueError, "INPUT_EVDEV"):
            validate_kernel_config(settings.replace("CONFIG_INPUT_EVDEV=m", "# disabled"))

    def test_changed_bundle_is_rejected_before_qemu(self):
        with tempfile.TemporaryDirectory() as work:
            base = Path(work)
            for name in ("vmlinuz", "initramfs.cpio.gz"):
                (base / name).write_bytes(name.encode())
            manifest = {"sha256": {name: sha256(base / name) for name in
                                    ("vmlinuz", "initramfs.cpio.gz")}, "reviewSha256": "a" * 64}
            (base / "bundle.json").write_text(json.dumps(manifest))
            validate_bundle(base, "a" * 64)
            (base / "vmlinuz").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "vmlinuz"):
                validate_bundle(base, "a" * 64)

if __name__ == "__main__":
    unittest.main()
