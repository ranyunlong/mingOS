"""Persistent update guest role tests; synthetic content is never VM evidence."""
import copy
import unittest
from types import SimpleNamespace
import guest
from bootlib import PERSISTENT_UPDATE_FIXTURE, PERSISTENT_UPDATE_ARGS


def session():
    return {'profile':'persistent-update', 'persistentInstallState':'/data/mingos/install-root',
            'catalogPath':'/data/mingos/install-root/catalog.json',
            'installSource':PERSISTENT_UPDATE_FIXTURE['sourceManifest'],
            'persistentUpdateFixture':PERSISTENT_UPDATE_FIXTURE.copy(), 'extraArgs':PERSISTENT_UPDATE_ARGS.copy(),
            'catalogSha256':'a'*64}


def context(number):
    boot='one' if number == 1 else 'two'
    return {'schemaVersion':1,'bootId':boot,'bootNumber':number,
            'role':'initialize-first' if number == 1 else 'open-existing',
            'marker':{'bootId':boot,'counter':number},
            'previousMarker':None if number == 1 else {'bootId':'one','counter':1},
            'diskUuid':'disk','registryRoot':'/data/mingos/install-root',
            'catalogPath':'/data/mingos/install-root/catalog.json', 'dataRoot':'/data/mingos'}


def helper_receipt(initial=False):
    from test_infra import InfraTests
    value=InfraTests().persistent_helper_receipt(False)
    content=value['content']
    target=next(app for app in content['apps'] if app['app'] == 2)
    old_paths=[target[key+'Path'] for key in ('manifest','wasm')]
    content['files']=[item for item in content['files'] if item['path'] not in old_paths]
    target.update(packageId='dev.ming.persistent-update-demo',version='0.1.0' if initial else '0.2.0')
    directory='packages/'+target['packageId'].encode().hex()+'/'+target['version']+'-'+target['fingerprint']
    for key,name in [('manifest','app.json'),('wasm','main.wasm')]:
        target[key+'Path']=directory+'/'+name
        content['files'].append({'path':target[key+'Path'],'sha256':target[key+'Sha256']})
        if not initial:
            content['files'].append({'path':target[key+'Path'].replace('/0.2.0-','/0.1.0-'),'sha256':target[key+'Sha256']})
    content['apps'].sort(key=lambda x:x['packageId']);content['files'].sort(key=lambda x:x['path'])
    value.update(status='initialized-first' if initial else 'opened-existing',
                 privateContent=guest.persistent_update_expected_private(initial,'a'*64))
    expected={'initializedRoots':1,'packagesInstalled':2,'publisherProvisioned':1,'bootstrapPublished':1,
              'catalogWrites':2,'rootPublished':1,'sourceBundlesCreated':1,'privateSeedWrites':1,
              'recoveryInvocations':0,'uiUpdatesCommitted':0}
    value['actions']={key:count if initial else 0 for key,count in expected.items()}
    return value


class PersistentUpdateGuestTests(unittest.TestCase):
    def test_only_first_pre_initializes_others_reopen_without_sources(self):
        for number in (1,2):
            for phase in ('pre-desktop','post-desktop'):
                argv=guest.persistent_update_helper_argv(session(),context(number),phase)
                initial=number == 1 and phase == 'pre-desktop'
                self.assertEqual(argv[1], 'initialize-first' if initial else 'open-existing')
                self.assertEqual(len(argv),12 if initial else 5)
                self.assertNotIn('recover-update',argv)
                if initial:
                    self.assertEqual(argv[3:7], ['/data/mingos','/opt/mingos/packages/bootstrap/main.wasm',
                                                '/opt/mingos/packages/old/main.wasm','/opt/mingos/packages/new/main.wasm'])
                else:
                    self.assertEqual(argv[2:-1], ['/data/mingos/install-root','/data/mingos'])
            argv=guest.desktop_argv(session(),context(number))
            self.assertEqual('--install-source' in argv,number == 1)
            self.assertNotIn('--events',argv)
            self.assertIn('persistent-update-pointer-ready-v1',argv)

    def test_context_uses_own_prefix_and_verified_fresh_marker_chain(self):
        for number in (1,2):
            expected=context(number)
            platform=SimpleNamespace(mounted=True,disk_uuid='disk',boot_id=expected['bootId'],
                marker=expected['marker'],previous=expected['previousMarker'])
            line=f'mingos.persistent_update_boot={number} mingos.persistent_update_role={expected["role"]}'
            self.assertEqual(guest.persistent_update_context(session(),platform,line),expected)
            for bad in ('',line.replace('persistent_update','persistent_install'),
                        line+' mingos.persistent_update_boot=1',line+' mingos.persistent_uninstall_role=open-uninstalled'):
                with self.subTest(line=bad),self.assertRaises(ValueError):
                    guest.persistent_update_context(session(),platform,bad)
            for key,value in [('mounted',False),('disk_uuid',''),('marker',{'bootId':expected['bootId'],'counter':True}),
                              ('previous',{'bootId':expected['bootId'],'counter':1})]:
                bad=copy.copy(platform);setattr(bad,key,value)
                with self.subTest(key=key),self.assertRaises(ValueError):
                    guest.persistent_update_context(session(),bad,line)

    def test_snapshot_distinguishes_physical_seed_actions_from_ui_commit(self):
        for number in (1,2):
            for phase in ('pre-desktop','post-desktop'):
                initial=number == 1 and phase == 'pre-desktop'
                receipt=helper_receipt(initial)
                snapshot=guest.persistent_update_snapshot(receipt,context(number),phase,
                    None if phase == 'pre-desktop' else 'session',None if phase == 'pre-desktop' else 42)
                self.assertEqual(snapshot['envelope']['actions']['uiUpdatesCommitted'],0)
                self.assertEqual(snapshot['envelope']['actions']['packagesInstalled'],2 if initial else 0)
                for change in (lambda x:x['actions'].update(uiUpdatesCommitted=1),
                               lambda x:x['actions'].update(recoveryInvocations=1),
                               lambda x:x.update(privateContent=guest.persistent_update_expected_private(not initial,'a'*64))):
                    bad=copy.deepcopy(receipt);change(bad)
                    with self.assertRaises(ValueError):
                        guest.persistent_update_snapshot(bad,context(number),phase,
                            None if phase == 'pre-desktop' else 'session',None if phase == 'pre-desktop' else 42)

    def test_post_matches_real_commit_and_preserves_non_files_baseline(self):
        before=guest.persistent_update_snapshot(helper_receipt(True),context(1),'pre-desktop',None)
        after=guest.persistent_update_snapshot(helper_receipt(),context(1),'post-desktop','session',42)
        parser=SimpleNamespace(committed_content=after['content'],private_content=after['privateContent'])
        guest.verify_persistent_update_after(before,after,parser,1)
        for key in ('content','privateContent'):
            bad=copy.deepcopy(after);bad[key]=None
            with self.assertRaises((RuntimeError,TypeError)):
                guest.verify_persistent_update_after(before,bad,parser,1)
        guest.verify_persistent_update_after(after,copy.deepcopy(after),parser,2)
        bad=copy.deepcopy(after);bad['privateContent']['nonFilesSha256']='b'*64
        parser.private_content=bad['privateContent']
        with self.assertRaises(RuntimeError):guest.verify_persistent_update_after(before,bad,parser,1)

    def test_helper_transport_checks_frozen_binary_and_stdout_file_identity(self):
        import hashlib
        import json
        from unittest.mock import MagicMock,patch
        configured=session();configured['persistentUpdateFixtureSha256']={'helper':hashlib.sha256(b'helper').hexdigest()}
        for number,phase in ((1,'pre-desktop'),(1,'post-desktop'),(2,'pre-desktop'),(2,'post-desktop')):
            value=helper_receipt(number == 1 and phase == 'pre-desktop')
            helper,path=MagicMock(),MagicMock();helper.read_bytes.return_value=b'helper'
            post=phase == 'post-desktop'
            with patch.object(guest,'Path',side_effect=[helper,path]), \
                 patch.object(guest,'read_persistent_receipt',return_value=value), \
                 patch.object(guest.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(value),stderr='')) as run, \
                 patch.object(guest,'emit') as emit:
                result=guest.prepare_persistent_snapshot(configured,context(number),phase,'session' if post else None,
                                                         update=True,desktop_pid=42 if post else None)
                run.assert_called_once_with(guest.persistent_update_helper_argv(configured,context(number),phase),
                                            capture_output=True,text=True,timeout=60)
                self.assertEqual(emit.call_args.args[0],'persistent-update-state')
                self.assertEqual(emit.call_args.kwargs['receipt'],result)
        for failure in ('hash','status','stdout','duplicate-json'):
            helper,path=MagicMock(),MagicMock();helper.read_bytes.return_value=b'drift' if failure == 'hash' else b'helper'
            value=helper_receipt();other=copy.deepcopy(value);other['privateContent']['nonFilesSha256']='b'*64
            stdout=json.dumps(other if failure == 'stdout' else value)
            if failure == 'duplicate-json':stdout=stdout[:-1]+',"schemaVersion":1}'
            with patch.object(guest,'Path',side_effect=[helper,path]), \
                 patch.object(guest,'read_persistent_receipt',return_value=value), \
                 patch.object(guest.subprocess,'run',return_value=SimpleNamespace(returncode=1 if failure == 'status' else 0,stdout=stdout,stderr='failure')) as run, \
                 patch.object(guest,'emit'):
                with self.subTest(failure=failure),self.assertRaises((ValueError,RuntimeError)):
                    guest.prepare_persistent_snapshot(configured,context(2),'pre-desktop',update=True)
                if failure == 'hash':run.assert_not_called()

    def test_runtime_smoke_imports_staged_update_parser_without_guest_startup(self):
        import importlib.util
        from pathlib import Path
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('check_runtime_update',Path(guest.__file__).with_name('check-runtime.py'))
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        with patch.object(runtime,'copy_guest_scripts',return_value={'guest.py':'a'*64}) as copy_scripts, \
             patch.object(runtime.subprocess,'run') as run:
            result=runtime.check_guest_python(Path('/staged'),Path('/scripts'),'persistent-update')
            self.assertEqual(result,{'guest.py':'a'*64})
            copy_scripts.assert_called_once_with(Path('/staged'),Path('/scripts'),'persistent-update')
            args=run.call_args.args[0]
            self.assertIn('from persistent_update_receipt import PersistentUpdateReceipts',args[-1])
            self.assertIn('import guest',args[-1])
            self.assertEqual(run.call_args.kwargs['check'],True)
        import subprocess
        with patch.object(runtime,'copy_guest_scripts',return_value={}), \
             patch.object(runtime.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['python'],stderr='missing update parser')):
            with self.assertRaisesRegex(RuntimeError,'missing update parser'):
                runtime.check_guest_python(Path('/staged'),Path('/scripts'),'persistent-update')

    def test_private_tree_requires_exact_alpha_layout_and_real_digest(self):
        for initial in (True,False):
            value=guest.persistent_update_expected_private(initial, "a"*64)
            guest.persistent_update_private(value,initial)
            for change in (lambda x:x.update(extra=1),lambda x:x.update(namespaceExists=False),
                           lambda x:x['files'].update(entries=99),lambda x:x['files'].update(sha256='0'*64)):
                bad=copy.deepcopy(value);change(bad)
                with self.assertRaises(ValueError):guest.persistent_update_private(bad,initial)
        old=guest.persistent_update_expected_private(True, "a"*64)
        with self.assertRaises(ValueError):guest.persistent_update_private(old,False)

if __name__ == '__main__':unittest.main()
