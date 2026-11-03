"""Closed uninstall guest roles; helper transport is mocked, never a VM."""
import copy
import json
import hashlib
from unittest.mock import MagicMock, patch
import unittest
from types import SimpleNamespace

import guest


def session():
    return {'profile': 'persistent-uninstall', 'persistentInstallState': '/data/mingos/install-root',
            'catalogPath': '/data/mingos/install-root/catalog.json', 'catalogSha256': '9' * 64,
            'extraArgs': ['--duration-ms', '0', '--isolation', 'strict', '--data-root', '/data/mingos',
                          '--state-file', '/run/evidence/desktop-state.json',
                          '--qualification-receipts', 'persistent-uninstall-pointer-ready-v1'],
            'installSource': '/opt/mingos/install-source/app.json',
            'persistentUninstallFixture': {
                'helper': '/opt/mingos/prepare-persistent-uninstall-fixture',
                'installRoot': '/data/mingos/install-root',
                'initialWasm': '/opt/mingos/packages/initial/main.wasm',
                'sourceWasm': '/opt/mingos/packages/new/main.wasm',
                'sourceManifest': '/opt/mingos/install-source/app.json',
                'receipt': '/run/evidence/persistent-uninstall-fixture.json'}}


def context(number):
    boot = 'one' if number == 1 else 'two'
    return {'schemaVersion': 1, 'bootId': boot, 'bootNumber': number,
            'role': 'initialize-first' if number == 1 else 'open-uninstalled',
            'marker': {'bootId': boot, 'counter': number},
            'previousMarker': None if number == 1 else {'bootId': 'one', 'counter': 1},
            'diskUuid': 'disk', 'registryRoot': '/data/mingos/install-root',
            'catalogPath': '/data/mingos/install-root/catalog.json'}


def helper_receipt(initial=False):
    # Use the existing bounded content fixture; this is an independent role schema.
    from test_infra import InfraTests
    value = InfraTests().persistent_helper_receipt(True)
    value['status'] = 'initialized-first' if initial else 'opened-uninstalled'
    value['actions'] = {key: int(initial) for key in value['actions']}
    value['removed'] = value['privateContent'] = None
    if not initial:
        item = InfraTests().persistent_helper_receipt(False)['content']['apps'][0]
        value['content']['files'].extend({'path': item[key + 'Path'], 'sha256': item[key + 'Sha256']}
                                       for key in ('manifest', 'wasm'))
        value['content']['files'].sort(key=lambda row: row['path'])
        value['removed'] = {key: item[key] for key in ('packageId', 'version', 'fingerprint')}
        value['privateContent'] = {'packageId': 'dev.ming.install-demo', 'files': [
            {'path': 'note.txt', 'size': 5, 'sha256': '8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8'}]}
    return value


class PersistentUninstallGuestTests(unittest.TestCase):
    def test_boot2_desktop_has_no_source_or_events(self):
        for number in (1, 2):
            argv = guest.desktop_argv(session(), context(number))
            self.assertIn('--persistent-install-state', argv)
            self.assertNotIn('--events', argv)
            self.assertEqual('--install-source' in argv, number == 1)
            self.assertIn('/data/mingos/install-root/catalog.json', argv)

    def test_only_boot1_pre_initializes_and_other_three_phases_strictly_reopen(self):
        for number in (1, 2):
            for phase in ('pre-desktop', 'post-desktop'):
                argv = guest.persistent_uninstall_helper_argv(session(), context(number), phase)
                initial = number == 1 and phase == 'pre-desktop'
                self.assertEqual(argv[1], 'initialize-first' if initial else 'open-uninstalled')
                if initial:
                    self.assertEqual(len(argv), 9)
                else:
                    self.assertEqual(argv, ['/opt/mingos/prepare-persistent-uninstall-fixture', 'open-uninstalled',
                        '/data/mingos/install-root', '/opt/mingos/packages/new/main.wasm', '/data/mingos',
                        '/run/evidence/persistent-uninstall-fixture.json'])

    def test_four_snapshot_roles_require_exact_actions_removed_and_private_data(self):
        for number in (1, 2):
            for phase in ('pre-desktop', 'post-desktop'):
                initial = number == 1 and phase == 'pre-desktop'
                snapshot = guest.persistent_uninstall_snapshot(helper_receipt(initial), context(number), phase,
                    None if phase == 'pre-desktop' else 'desktop-session', None if phase == 'pre-desktop' else 42)
                self.assertEqual(snapshot['removed'] is None, initial)
                self.assertEqual(snapshot['privateContent'] is None, initial)
                self.assertTrue(all(value == int(initial) for value in snapshot['envelope']['actions'].values()))
        original = helper_receipt()
        for change in (lambda r: r.update(extra=0), lambda r: r.update(removed=None),
                       lambda r: r['removed'].update(version='2.0.0'),
                       lambda r: r['privateContent'].update(files=[]),
                       lambda r: r['privateContent']['files'][0].update(size=0),
                       lambda r: r['privateContent']['files'][0].update(path='../note.txt'),
                       lambda r: r['actions'].update(sourcesCreated=1)):
            value = copy.deepcopy(original); change(value)
            with self.assertRaises(ValueError):
                guest.persistent_uninstall_snapshot(value, context(2), 'pre-desktop', None)

    def test_helper_transport_executes_all_four_real_argv_and_validates_both_receipts(self):
        configured = session()
        configured['persistentUninstallFixtureSha256'] = {'helper': hashlib.sha256(b'helper').hexdigest()}
        for number in (1, 2):
            for phase in ('pre-desktop', 'post-desktop'):
                value = helper_receipt(number == 1 and phase == 'pre-desktop')
                helper, path = MagicMock(), MagicMock()
                helper.read_bytes.return_value = b'helper'
                post = phase == 'post-desktop'
                with patch.object(guest, 'Path', side_effect=[helper, path]), \
                     patch.object(guest, 'read_persistent_receipt', return_value=value) as read, \
                     patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(
                         returncode=0, stdout=json.dumps(value), stderr='')) as run, \
                     patch.object(guest, 'emit') as emit:
                    result = guest.prepare_persistent_snapshot(configured, context(number), phase,
                        'session' if post else None, uninstall=True, desktop_pid=42 if post else None)
                    run.assert_called_once_with(guest.persistent_uninstall_helper_argv(configured, context(number), phase),
                                                capture_output=True, text=True, timeout=60)
                    path.unlink.assert_called_once_with(missing_ok=True)
                    read.assert_called_once_with(path)
                    self.assertEqual(result['envelope']['desktopPid'], 42 if post else None)
                    self.assertEqual(emit.call_args.args[0], 'persistent-uninstall-state')
                    self.assertEqual(emit.call_args.kwargs['receipt'], result)
                    self.assertTrue(all(v == int(number == 1 and not post) for v in result['envelope']['actions'].values()))

    def test_helper_transport_rejects_hash_drift_failure_and_stdout_file_disagreement(self):
        configured = session()
        configured['persistentUninstallFixtureSha256'] = {'helper': hashlib.sha256(b'helper').hexdigest()}
        for failure in ('hash', 'status', 'stdout', 'duplicate-json'):
            helper, path = MagicMock(), MagicMock()
            helper.read_bytes.return_value = b'changed' if failure == 'hash' else b'helper'
            value = helper_receipt()
            other = copy.deepcopy(value); other['content']['files'].pop()
            stdout = json.dumps(other if failure == 'stdout' else value)
            if failure == 'duplicate-json': stdout = stdout[:-1] + ',"schemaVersion":1}'
            with patch.object(guest, 'Path', side_effect=[helper, path]), \
                 patch.object(guest, 'read_persistent_receipt', return_value=value), \
                 patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(
                     returncode=1 if failure == 'status' else 0, stdout=stdout, stderr='failure')) as run, \
                 patch.object(guest, 'emit'):
                with self.assertRaises((ValueError, RuntimeError)):
                    guest.prepare_persistent_snapshot(configured, context(2), 'pre-desktop', uninstall=True)
                if failure == 'hash': run.assert_not_called()

    def test_post_evidence_matches_commit_and_all_three_reopen_content_components(self):
        snapshot = guest.persistent_uninstall_snapshot(helper_receipt(), context(2), 'pre-desktop', None)
        parser = SimpleNamespace(uninstalled_content=snapshot['content'], removed=snapshot['removed'],
                                 private_content=snapshot['privateContent'])
        for number in (1, 2):
            after = copy.deepcopy(snapshot)
            after['envelope'].update(bootId='different', phase='post-desktop', sessionId='session', desktopPid=42)
            guest.verify_persistent_uninstall_after(snapshot, after, parser, number)
            for key in ('content', 'removed', 'privateContent'):
                bad = copy.deepcopy(after); bad[key] = None
                with self.assertRaises(RuntimeError): guest.verify_persistent_uninstall_after(snapshot, bad, parser, number)

    def test_context_requires_new_kernel_role_and_verified_disk_marker(self):
        for number in (1, 2):
            expected = context(number)
            platform = SimpleNamespace(mounted=True, disk_uuid='disk', boot_id=expected['bootId'],
                                       marker=expected['marker'], previous=expected['previousMarker'])
            line = f'mingos.persistent_uninstall_boot={number} mingos.persistent_uninstall_role={expected["role"]}'
            self.assertEqual(guest.persistent_uninstall_context(session(), platform, line), expected)
            for invalid in ('', line.replace('persistent_uninstall', 'persistent_install'),
                            line + f' mingos.persistent_uninstall_boot={number}',
                            line + ' mingos.persistent_install_boot=1'):
                with self.assertRaises(ValueError): guest.persistent_uninstall_context(session(), platform, invalid)
            for key, value in (('mounted', False), ('disk_uuid', ''), ('marker', {'bootId': expected['bootId'], 'counter': True}),
                               ('previous', {'bootId': expected['bootId'], 'counter': 1})):
                bad = copy.copy(platform); setattr(bad, key, value)
                with self.assertRaises(ValueError): guest.persistent_uninstall_context(session(), bad, line)


if __name__ == '__main__':
    unittest.main()
