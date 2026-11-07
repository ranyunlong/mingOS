"""Real private copies and fake CLI subprocesses; no real Docker resources.

Fixtures and partial failure receipts remain in the ignored artifacts tree.
These tests prove orchestration, not Linux semantics.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import qualification_volume_restore as restore
import test_qualification_volume_archive as fixtures


class HostRestoreControls(unittest.TestCase):
    def setUp(self):
        parent = Path(os.environ.get('MINGOS_RESTORE_TEST_OUTPUT',
            Path(__file__).resolve().parents[2] / 'artifacts/qualification-restore-controls'))
        parent.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix='host-', dir=parent))
        self.assets, self.fake = self.root / 'assets', self.root / 'fake'
        self.assets.mkdir(); self.fake.mkdir()
        self.output, self.manifest = self.root / 'retained-receipts', self.assets / 'manifest.json'
        self.sentinel = self.root / 'caller-existing-cache.sentinel'
        self.sentinel.write_bytes(b'existing caller resource unchanged')
        fixture = fixtures.VolumeArchiveTests()
        fixture.setUp()
        self.volume = self.assets / 'cache.tar'
        self.volume.write_bytes(fixture.raw())
        self.record = {'schemaVersion': 1, 'images': [], 'volume': {'name': 'caller-existing-cache',
            'archive': 'cache.tar', 'archiveSha256': fixtures.digest(self.volume.read_bytes())}}
        for key, image_id in restore.EXPECTED_IMAGE_IDS.items():
            path = self.assets / (key + '.tar')
            path.write_bytes(key.encode())
            self.record['images'].append({'key': key, 'archive': path.name,
                'archiveSha256': fixtures.digest(path.read_bytes()), 'imageId': image_id})
        self.write_manifest()

    def write_manifest(self):
        self.manifest.write_text(json.dumps(self.record), encoding='utf-8')

    def run_restore(self, scenario='success'):
        prefix = [sys.executable, '-B', str(Path(__file__).with_name('fake_volume_docker.py')), str(self.fake), scenario]
        return restore.restore_volume(self.assets, self.manifest, self.output, prefix)

    def events(self):
        path = self.fake / 'events.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def assert_no_cleanup(self):
        for event in self.events():
            args = event['argv']
            self.assertFalse(any(word in args for word in ('rm', 'rmi', 'prune', '--rm', 'volume')),
                             'no cleanup or standalone volume command is allowed')
        self.assertEqual(self.sentinel.read_bytes(), b'existing caller resource unchanged')

    def rejected(self, scenario):
        with self.assertRaises(restore.RestoreError) as error:
            self.run_restore(scenario)
        self.assertEqual(error.exception.receipt['status'], 'failed')
        self.assertFalse(error.exception.receipt['formalQualification'])
        self.assertFalse(error.exception.receipt['finalSystemAccepted'])
        self.assert_no_cleanup()
        return error.exception.receipt

    def test_success_orders_exact_cids_and_streams_private_bytes_to_distinct_ro_verifier(self):
        result = self.run_restore()
        self.assertEqual(result['status'], 'volume-restored-readonly-verified')
        self.assertFalse(result['formalQualification'])
        self.assertFalse(result['finalSystemAccepted'])
        self.assertEqual(result['provenanceName'], 'caller-existing-cache')
        self.assertEqual(result['volumeName'], 'f' * 64)
        self.assertEqual(result['subpath'], 'payload')
        self.assertEqual(len({result['creatorId'], result['restoreContainerId'], result['verifyContainerId']}), 3)
        starts = [event for event in self.events() if event['argv'][0] == 'start']
        self.assertEqual([event['role'] for event in starts], ['creator', 'restore', 'verify'])
        self.assertEqual(starts[1]['stdinSha256'], self.record['volume']['archiveSha256'])
        self.assertEqual(Path(result['privateCopy']['path']).read_bytes(), self.volume.read_bytes())
        self.assertEqual(result['restore']['files'], result['readonlyVerify']['files'])
        self.assertTrue(result['readonlyVerify']['statvfsReadOnly'])
        self.assertTrue(list(self.output.glob('receipt-*-success.json')))
        self.assertTrue(all(Path(op['stdoutPath']).is_file() and Path(op['stderrPath']).is_file() for op in result['operations']))
        self.assert_no_cleanup()

    def test_last_member_corruption_is_rejected_before_any_docker_subprocess(self):
        raw = self.volume.read_bytes().replace(b'hello', b'jello')
        self.volume.write_bytes(raw)
        self.record['volume']['archiveSha256'] = fixtures.digest(raw)
        self.write_manifest()
        result = self.rejected('success')
        self.assertEqual(result['operations'], [])
        self.assertEqual(self.events(), [])
        self.assertEqual((self.output / 'archive-privatecopy.tar').read_bytes(), raw)
        self.assertTrue(list(self.output.glob('receipt-*-failure.json')))

    def test_desktop_requested_mount_accepts_only_exact_drive_translation(self):
        result = self.run_restore()
        saved = json.loads((self.fake / 'fake-state.json').read_text())
        item = saved['containers'][result['restoreContainerId']]
        helper = r'C:\Users\alice\private-helper'
        mapped = '/run/desktop/mnt/host/c/Users/alice/private-helper'
        item['HostConfig']['Mounts'][1]['Source'] = mapped
        item['Mounts'][1]['Source'] = mapped
        command = restore.worker_command(result['nonce'], result['volumeName'], helper, False, result['archiveSha256'])
        restore.validate_worker_inspection([item], result['restoreContainerId'], result['nonce'],
            result['volumeName'], helper, command, False)
        for source in (mapped + '-other', mapped.lower(), mapped.replace('/c/', '/d/')):
            with self.subTest(source=source):
                item['HostConfig']['Mounts'][1]['Source'] = source
                with self.assertRaisesRegex(ValueError, 'requested mounts'):
                    restore.validate_worker_inspection([item], result['restoreContainerId'], result['nonce'],
                        result['volumeName'], helper, command, False)

    def test_binary_privatecopy_preserves_lf_bytes_without_windows_translation(self):
        fixture = fixtures.VolumeArchiveTests()
        fixture.setUp()
        changed = copy.deepcopy(fixture.manifest)
        changed['entries'][1].update(size=3, sha256=fixtures.digest(b'a\nb'))
        raw = fixture.raw(changed, [fixture.payload[0], fixtures.member('cache/data', b'a\nb')])
        self.volume.write_bytes(raw)
        self.record['volume']['archiveSha256'] = fixtures.digest(raw)
        self.write_manifest()
        result = self.run_restore()
        self.assertEqual(Path(result['privateCopy']['path']).read_bytes(), raw)
        for name, record in result['helperSnapshot']['files'].items():
            self.assertEqual(hashlib.sha256((Path(result['helperSnapshot']['path']) / name).read_bytes()).hexdigest(), record['sha256'])

    def test_wrong_fixed_image_contract_fails_before_any_subprocess(self):
        self.record['images'][0]['imageId'] = 'sha256:' + '0' * 64
        self.write_manifest()
        result = self.rejected('success')
        self.assertEqual(result['operations'], [])
        self.assertEqual(self.events(), [])

    def test_wrong_image_archive_digest_fails_before_any_subprocess(self):
        self.record['images'][2]['archiveSha256'] = '0' * 64
        self.write_manifest()
        result = self.rejected('success')
        self.assertEqual(result['operations'], [])

    def test_duplicate_outer_json_key_rejected_before_any_subprocess(self):
        raw = self.manifest.read_text().replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1')
        self.manifest.write_text(raw)
        result = self.rejected('success')
        self.assertEqual(result['operations'], [])

    def test_outer_archive_traversal_rejected(self):
        self.record['volume']['archive'] = '../caller-existing-cache.sentinel'
        self.write_manifest()
        result = self.rejected('success')
        self.assertEqual(result['operations'], [])

    def test_privatecopy_never_reuses_or_overwrites_existing_evidence_root(self):
        self.output.mkdir()
        retained = self.output / 'archive-privatecopy.tar'
        retained.write_bytes(b'prior failure must remain')
        self.rejected('success')
        self.assertEqual(retained.read_bytes(), b'prior failure must remain')
        self.assertEqual(self.events(), [])

    def test_caller_named_creator_source_is_rejected_before_start(self):
        result = self.rejected('named-creator')
        self.assertTrue(result.get('creatorId'))
        self.assertFalse(any(event['argv'][0] == 'start' for event in self.events()))

    def test_implicit_extra_creator_mount_is_rejected_before_start(self):
        self.rejected('extra-creator-mount')
        self.assertFalse(any(event['argv'][0] == 'start' for event in self.events()))

    def test_wrong_creator_cid_rejected_before_start(self):
        self.rejected('wrong-creator-cid')
        self.assertFalse(any(event['argv'][0] == 'start' for event in self.events()))

    def test_failed_initializer_stops_before_restore_create_and_preserves_all_logs(self):
        result = self.rejected('fail-creator')
        self.assertEqual(result['operations'][-1]['exitCode'], 17)
        self.assertIn('first failure retained', Path(result['operations'][-1]['stderrPath']).read_text())
        self.assertEqual([event.get('role') for event in self.events() if event['argv'][0] == 'create'], ['creator'])

    def test_wrong_worker_user_rejected_before_archive_stream(self):
        result = self.rejected('wrong-worker-user')
        self.assertTrue(result.get('restoreContainerId'))
        self.assertEqual([event.get('role') for event in self.events() if event['argv'][0] == 'start'], ['creator'])

    def test_worker_failure_stops_before_verify_and_retains_ids(self):
        result = self.rejected('fail-restore')
        self.assertEqual(result['operations'][-1]['exitCode'], 17)
        self.assertTrue(result.get('creatorId') and result.get('volumeName') and result.get('restoreContainerId'))
        self.assertNotIn('verifyContainerId', result)

    def test_still_running_writer_stops_before_verify(self):
        result = self.rejected('running-writer')
        self.assertNotIn('verifyContainerId', result)
        self.assertEqual([event.get('role') for event in self.events() if event['argv'][0] == 'create'], ['creator', 'restore'])

    def test_bad_worker_receipt_stops_before_verify_create(self):
        result = self.rejected('bad-worker-receipt')
        self.assertNotIn('verifyContainerId', result)

    def test_rw_effective_verifier_mount_is_rejected_before_verify_start(self):
        result = self.rejected('verify-rw')
        self.assertTrue(result.get('verifyContainerId'))
        self.assertEqual([event.get('role') for event in self.events() if event['argv'][0] == 'start'], ['creator', 'restore'])

    def test_verifier_failure_keeps_volume_and_all_container_ids(self):
        result = self.rejected('fail-verify')
        self.assertTrue(all(result.get(key) for key in ('creatorId', 'restoreContainerId', 'verifyContainerId', 'volumeName')))
        self.assertEqual(result['operations'][-1]['exitCode'], 17)

    def test_verifier_changed_hash_and_false_readonly_claim_both_fail(self):
        self.rejected('bad-verify-receipt')
        # A separate invocation gets separate evidence; failed resources stay.
        self.output = self.root / 'second-retained-receipts'
        self.fake = self.root / 'second-fake'
        self.fake.mkdir()
        self.rejected('false-readonly')

    def test_changed_privatecopy_after_helper_inspection_stops_before_creator(self):
        original = restore.Evidence.run
        def changed(evidence, key, command, timeout, **kwargs):
            result = original(evidence, key, command, timeout, **kwargs)
            if key == 'helper-image-inspect':
                with (evidence.root / 'archive-privatecopy.tar').open('r+b') as stream:
                    stream.seek(0); stream.write(b'changed!'); stream.flush()
            return result
        with mock.patch.object(restore.Evidence, 'run', new=changed):
            self.rejected('success')
        self.assertFalse(any(event['argv'][0] == 'create' for event in self.events()))

    def test_timeout_retains_attempt_partial_outputs_and_prevents_followup(self):
        original = subprocess.run
        def timeout_restore(command, **kwargs):
            if 'start' in command and '--interactive' in command:
                kwargs['stdout'].write(b'partial restore stdout')
                kwargs['stdout'].flush()
                kwargs['stderr'].write(b'partial restore stderr')
                kwargs['stderr'].flush()
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            return original(command, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=timeout_restore):
            result = self.rejected('success')
        last = result['operations'][-1]
        self.assertTrue(last['timedOut'])
        self.assertEqual(last['stdout'], 'partial restore stdout')
        self.assertEqual(last['stderr'], 'partial restore stderr')
        self.assertEqual(Path(last['stdoutPath']).read_bytes(), b'partial restore stdout')
        self.assertNotIn('verifyContainerId', result)
        self.assertTrue(list(self.output.glob('receipt-*-failure.json')))

    def test_verifier_timeout_retains_all_ids_partial_output_and_stops(self):
        original = subprocess.run
        def timeout_verify(command, **kwargs):
            if 'start' in command and '--interactive' in command and 'input' in kwargs:
                kwargs['stdout'].write(b'partial readonly stdout')
                kwargs['stdout'].flush()
                raise subprocess.TimeoutExpired(command, kwargs['timeout'])
            return original(command, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=timeout_verify):
            result = self.rejected('success')
        self.assertTrue(all(result.get(key) for key in ('creatorId', 'restoreContainerId', 'verifyContainerId', 'volumeName')))
        self.assertEqual(result['operations'][-1]['key'], 'verify-start')
        self.assertTrue(result['operations'][-1]['timedOut'])
        self.assertEqual(result['operations'][-1]['stdout'], 'partial readonly stdout')
        self.assertEqual(self.events()[-1]['argv'][0:2], ['container', 'inspect'])
        self.assertTrue(list(self.output.glob('receipt-*-failure.json')))

    def test_exit_zero_with_oom_dead_or_restart_is_not_normalized_into_success(self):
        for index, scenario in enumerate(('oom-creator', 'dead-restore', 'restarting-verify')):
            with self.subTest(scenario=scenario):
                self.output = self.root / ('state-receipts-' + str(index))
                self.fake = self.root / ('state-fake-' + str(index))
                self.fake.mkdir()
                result = self.rejected(scenario)
                self.assertIn('exit successfully', result['failure']['error'])
                self.assertEqual(result['operations'][-1]['key'], scenario.split('-')[1] + '-final-inspect')


class HostInspectionControls(unittest.TestCase):
    def test_exact_windows_path_and_exact_desktop_drive_mapping_only(self):
        requested = r'C:\Users\alice\private-helper'
        self.assertTrue(restore.effective_helper_source_matches(requested, requested))
        self.assertTrue(restore.effective_helper_source_matches('/run/desktop/mnt/host/c/Users/alice/private-helper', requested))
        for wrong in ('/run/desktop/mnt/host/d/Users/alice/private-helper',
                '/run/desktop/mnt/host/c/Users/alice/private-helper-extra',
                '/run/desktop/mnt/host/c/Users/alice/private-helper/../other',
                '/run/desktop/mnt/host/c/users/alice/private-helper',
                '/run/desktop/mnt/host/c/Users/alice//private-helper',
                '/host_mnt/c/Users/alice/private-helper', '/private-helper', None):
            with self.subTest(wrong=wrong):
                self.assertFalse(restore.effective_helper_source_matches(wrong, requested))

    def test_no_unc_relative_parent_or_linux_source_remapping(self):
        for request in (r'\\server\share\helper', r'C:\Users\..\helper',
                        r'C:\Users\\helper', 'C:relative/helper', '/tmp/helper'):
            with self.subTest(request=request):
                self.assertFalse(restore.effective_helper_source_matches('/run/desktop/mnt/host/c/Users/helper', request))
        self.assertTrue(restore.effective_helper_source_matches('/tmp/helper', '/tmp/helper'))

    def test_exited_state_requires_explicit_false_oom_dead_restart(self):
        cid = 'a' * 64
        valid = {'Id': cid, 'State': {'Status': 'exited', 'Running': False, 'ExitCode': 0,
            'OOMKilled': False, 'Dead': False, 'Restarting': False}}
        restore._one_inspection(json.dumps([valid]).encode(), cid, exited=True)
        for key in ('OOMKilled', 'Dead', 'Restarting'):
            for value in (True, None, 0):
                with self.subTest(key=key, value=value):
                    changed = copy.deepcopy(valid)
                    changed['State'][key] = value
                    with self.assertRaises(ValueError):
                        restore._one_inspection(json.dumps([changed]).encode(), cid, exited=True)


if __name__ == '__main__':
    unittest.main()
