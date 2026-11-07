"""STAGED pure creator/inspection controls; no subprocess or Docker access."""
import copy
import unittest

import qualification_volume_plan as plan


class CreatorPlanTests(unittest.TestCase):
    def setUp(self):
        self.cid, self.nonce, self.volume = 'a' * 64, 'b' * 32, 'c' * 64
        self.value = [{'Id': self.cid, 'Image': plan.HELPER_IMAGE,
            'Config': {'Image': plan.HELPER_IMAGE, 'User': '0:0', 'Entrypoint': ['python3'],
                'Cmd': ['-I', '-B', '-c', plan.INITIALIZE], 'Labels': {
                    'org.mingos.restore.nonce': self.nonce, 'org.mingos.restore.role': 'creator'}},
            'State': {'Status': 'created', 'Running': False},
            'HostConfig': {'NetworkMode': 'none', 'ReadonlyRootfs': True, 'Privileged': False,
                'AutoRemove': False, 'CapDrop': ['ALL'], 'CapAdd': ['CHOWN'],
                'SecurityOpt': ['no-new-privileges'], 'NanoCpus': 2 * 10 ** 9,
                'Memory': 4 * 1024 ** 3, 'PidsLimit': 128,
                'RestartPolicy': {'Name': 'no', 'MaximumRetryCount': 0},
                'Mounts': [{'Type': 'volume', 'Target': '/restore', 'VolumeOptions': {'NoCopy': True}}]},
            'Mounts': [{'Type': 'volume', 'Name': self.volume, 'Destination': '/restore',
                'Source': '/var/lib/docker/volumes/' + self.volume + '/_data', 'RW': True, 'Driver': 'local'}]}]

    def test_creator_has_atomic_unnamed_nocopy_mount_fixed_image_and_no_removal(self):
        command = plan.creator_command(self.nonce)
        self.assertEqual(command[:2], ['docker', 'create'])
        self.assertEqual(command[command.index('--mount') + 1], 'type=volume,target=/restore,volume-nocopy')
        self.assertIn(plan.HELPER_IMAGE, command)
        self.assertEqual(command[command.index('--pull') + 1], 'never')
        self.assertNotIn('--rm', command)
        self.assertNotIn('--name', command)
        self.assertFalse(any('source=' in arg for arg in command))
        self.assertNotIn('debian:trixie-slim', command)

    def test_creator_exact_identity_returns_payload_subpath(self):
        result = plan.validate_creator_inspection(self.value, self.cid, self.nonce)
        self.assertEqual(result['volumeName'], self.volume)
        self.assertEqual(result['subpath'], 'payload')
        self.assertEqual(result['controlSubpath'], 'control')
        self.assertEqual(result['creatorId'], self.cid)

    def test_empty_source_is_accepted_but_named_source_is_rejected(self):
        self.value[0]['HostConfig']['Mounts'][0]['Source'] = ''
        plan.validate_creator_inspection(self.value, self.cid, self.nonce)
        self.value[0]['HostConfig']['Mounts'][0]['Source'] = 'caller-selected-existing-volume'
        with self.assertRaisesRegex(ValueError, 'caller-selected'):
            plan.validate_creator_inspection(self.value, self.cid, self.nonce)

    def test_docker_cap_chown_spelling_preserves_exact_single_capability(self):
        self.value[0]['HostConfig']['CapAdd'] = ['CAP_CHOWN']
        plan.validate_creator_inspection(self.value, self.cid, self.nonce)
        for capabilities in (['CAP_CHOWN', 'DAC_OVERRIDE'], ['CAP_SYS_ADMIN'], []):
            with self.subTest(capabilities=capabilities):
                self.value[0]['HostConfig']['CapAdd'] = capabilities
                with self.assertRaisesRegex(ValueError, 'confinement'):
                    plan.validate_creator_inspection(self.value, self.cid, self.nonce)

    def test_exact_cid_nonce_and_single_inspect_required(self):
        for cid, nonce, value in (('a' * 12, self.nonce, self.value),
                (self.cid, 'b' * 31, self.value), (self.cid, self.nonce, []),
                (self.cid, self.nonce, self.value * 2)):
            with self.subTest(cid=cid, nonce=nonce, count=len(value)), self.assertRaises(ValueError):
                plan.validate_creator_inspection(value, cid, nonce)

    def test_malformed_inspection_objects_fail_with_valueerror(self):
        for key in ('Config', 'HostConfig', 'State'):
            for bad in (None, [], 'wrong'):
                with self.subTest(key=key, bad=bad):
                    changed = copy.deepcopy(self.value)
                    changed[0][key] = bad
                    with self.assertRaises(ValueError):
                        plan.validate_creator_inspection(changed, self.cid, self.nonce)
        self.value[0]['Config']['Labels'] = ['wrong']
        with self.assertRaises(ValueError):
            plan.validate_creator_inspection(self.value, self.cid, self.nonce)

    def test_creator_identity_mutations_rejected(self):
        changes = [(('Id',), 'd' * 64), (('Image',), 'sha256:' + 'd' * 64),
            (('Config', 'Image'), 'debian:trixie-slim'), (('Config', 'User'), '65534:65534'),
            (('Config', 'Entrypoint'), ['sh']), (('Config', 'Cmd'), ['-c', 'true']),
            (('Config', 'Labels', 'org.mingos.restore.nonce'), 'd' * 32),
            (('Config', 'Labels', 'org.mingos.restore.role'), 'writer'),
            (('State', 'Status'), 'exited'), (('State', 'Running'), True)]
        for keys, value in changes:
            with self.subTest(keys=keys):
                changed = copy.deepcopy(self.value)
                target = changed[0]
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
                with self.assertRaisesRegex(ValueError, 'identity'):
                    plan.validate_creator_inspection(changed, self.cid, self.nonce)

    def test_confinement_and_auto_remove_mutations_rejected(self):
        for key, value in [('NetworkMode', 'default'), ('ReadonlyRootfs', False), ('Privileged', True),
                ('AutoRemove', True), ('CapDrop', []), ('CapAdd', ['CHOWN', 'DAC_OVERRIDE']),
                ('SecurityOpt', []), ('Memory', 0), ('PidsLimit', 0), ('NanoCpus', 0),
                ('RestartPolicy', {'Name': 'always', 'MaximumRetryCount': 0})]:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.value)
                changed[0]['HostConfig'][key] = value
                with self.assertRaisesRegex(ValueError, 'confinement'):
                    plan.validate_creator_inspection(changed, self.cid, self.nonce)

    def test_unrequested_host_attachments_rejected(self):
        for key in ('Binds', 'VolumesFrom', 'Links', 'Devices', 'DeviceRequests', 'Tmpfs', 'PortBindings', 'ExtraHosts'):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.value)
                changed[0]['HostConfig'][key] = ['unexpected']
                with self.assertRaisesRegex(ValueError, 'attachment'):
                    plan.validate_creator_inspection(changed, self.cid, self.nonce)

    def test_requested_mount_extra_default_override_or_nocopy_false_rejected(self):
        for value in ([], self.value[0]['HostConfig']['Mounts'] * 2,
                [{'Type': 'bind', 'Target': '/restore', 'Source': '/'}],
                [{'Type': 'volume', 'Target': '/restore', 'VolumeOptions': {'NoCopy': False}}]):
            with self.subTest(value=value):
                changed = copy.deepcopy(self.value)
                changed[0]['HostConfig']['Mounts'] = value
                with self.assertRaises(ValueError):
                    plan.validate_creator_inspection(changed, self.cid, self.nonce)

    def test_effective_mount_wrong_or_implicit_image_volume_rejected(self):
        for key, value in [('Type', 'bind'), ('Name', 'caller-cache'), ('Destination', '/other'),
                ('RW', False), ('Driver', 'nfs')]:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.value)
                changed[0]['Mounts'][0][key] = value
                with self.assertRaises(ValueError):
                    plan.validate_creator_inspection(changed, self.cid, self.nonce)
        self.value[0]['Mounts'].append(dict(self.value[0]['Mounts'][0], Destination='/implicit'))
        with self.assertRaisesRegex(ValueError, 'single effective'):
            plan.validate_creator_inspection(self.value, self.cid, self.nonce)

    def test_initializer_retains_descriptors_and_has_no_delete_or_shell(self):
        compile(plan.INITIALIZE, '<staged initializer>', 'exec')
        self.assertIn("os.listdir(root)", plan.INITIALIZE)
        self.assertIn("('payload','control')", plan.INITIALIZE)
        self.assertIn('os.O_NOFOLLOW', plan.INITIALIZE)
        self.assertIn('os.fsync(root)', plan.INITIALIZE)
        for word in ('unlink', 'rmdir', 'shutil', 'subprocess', 'extractall'):
            self.assertNotIn(word, plan.INITIALIZE)


if __name__ == '__main__':
    unittest.main()
