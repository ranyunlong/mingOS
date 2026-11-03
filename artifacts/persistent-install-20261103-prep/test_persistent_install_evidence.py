"""Synthetic verifier controls; these never constitute a real boot qualification."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
SPEC=importlib.util.spec_from_file_location('persistent_audit',HERE/'verify-persistent-install-evidence.py')
audit=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)

def content(include_new=True):
    files={name:hashlib.sha256(name.encode()).hexdigest() for name in audit.ROOT_FILES.values()}
    apps=[]
    for app,package,version in [(1,'dev.ming.text-entry-demo','0.1.0')]+([(2,'dev.ming.install-demo','1.0.0')] if include_new else []):
        fp=hashlib.sha256(package.encode()).hexdigest();base=f'packages/{package.encode().hex()}/{version}-{fp}'
        manifest=base+'/app.json';wasm=base+'/main.wasm';files[manifest]=hashlib.sha256(manifest.encode()).hexdigest();files[wasm]=hashlib.sha256(wasm.encode()).hexdigest()
        apps.append(dict(app=app,packageId=package,version=version,fingerprint=fp,manifestPath=manifest,manifestSha256=files[manifest],wasmPath=wasm,wasmSha256=files[wasm]))
    return dict(**{field:files[name] for field,name in audit.ROOT_FILES.items()},apps=sorted(apps,key=lambda x:x['packageId']),files=[dict(path=k,sha256=v) for k,v in sorted(files.items())])

def context(number):
    previous=None if number==1 else {'bootId':'boot-1','counter':1}
    return dict(bootId=f'boot-{number}',sessionId=f'session-{number}',bootNumber=number,diskUuid='disk-1',marker={'bootId':f'boot-{number}','counter':number},previousMarker=previous)

def receipt(number,phase='post-desktop'):
    ctx=context(number);first=number==1 and phase=='pre-desktop'
    env=dict(schemaVersion=1,**ctx,role='initialize-first' if number==1 else 'open-existing',registryRoot=audit.ROOT,catalogPath=audit.ROOT+'/catalog.json',phase=phase,status='initialized-first' if first else 'opened-existing',actions={k:1 if first else 0 for k in audit.ACTION_KEYS})
    if phase=='pre-desktop':env['sessionId']=None
    return {'envelope':env,'content':content(not first)}

class SnapshotTests(unittest.TestCase):
    def test_valid_content_with_distinct_legal_envelopes_is_accepted(self):
        one,two=receipt(1),receipt(2,'pre-desktop')
        self.assertNotEqual(one['envelope'],two['envelope'])
        self.assertEqual(audit.compare_reboot_snapshots(one,two,context(1),context(2)),one['content'])
    def test_initial_snapshot_only_app1_and_all_five_init_actions(self):
        audit.validate_snapshot(receipt(1,'pre-desktop'),context(1),'pre-desktop')
        bad=receipt(1,'pre-desktop');bad['content']=content()
        with self.assertRaises(ValueError):audit.validate_snapshot(bad,context(1),'pre-desktop')
    def test_envelope_swap_boot_reuse_role_marker_and_nonzero_open_writes_rejected(self):
        for field,value in [('bootId','boot-1'),('bootNumber',1),('role','initialize-first'),('diskUuid','other'),('marker',{'bootId':'boot-2','counter':1}),('previousMarker',None),('sessionId','session-2')]:
            with self.subTest(field=field):
                one,two=receipt(1),receipt(2,'pre-desktop');two['envelope'][field]=value
                with self.assertRaises(ValueError):audit.compare_reboot_snapshots(one,two,context(1),context(2))
        for field in audit.ACTION_KEYS:
            two=receipt(2,'pre-desktop');two['envelope']['actions'][field]=1
            with self.assertRaises(ValueError):audit.compare_reboot_snapshots(receipt(1),two,context(1),context(2))
        two=receipt(2,'pre-desktop');two['envelope']=receipt(1)['envelope']
        with self.assertRaises(ValueError):audit.compare_reboot_snapshots(receipt(1),two,context(1),context(2))
    def test_unknown_missing_fields_rejected_at_every_schema_level(self):
        original=receipt(2,'pre-desktop')
        for path in [[],['envelope'],['envelope','actions'],['content'],['content','apps',0],['content','files',0]]:
            for op in ['unknown','missing']:
                bad=copy.deepcopy(original);obj=bad
                for key in path:obj=obj[key]
                if op=='unknown':obj['unexpected']=True
                else:obj.pop(next(iter(obj)))
                with self.subTest(path=path,op=op),self.assertRaises(ValueError):audit.validate_snapshot(bad,context(2),'pre-desktop')
    def test_all_stable_content_drift_rejected(self):
        for path in [['catalogSha256'],['registrySha256'],['trustSha256'],['bootstrapSha256'],['apps',0,'app'],['apps',0,'packageId'],['apps',0,'version'],['apps',0,'fingerprint'],['apps',0,'manifestPath'],['apps',0,'manifestSha256'],['apps',0,'wasmPath'],['apps',0,'wasmSha256'],['files',0,'path'],['files',0,'sha256']]:
            bad=receipt(2,'pre-desktop');obj=bad['content']
            for key in path[:-1]:obj=obj[key]
            obj[path[-1]]=999 if type(obj[path[-1]]) is int else 'f'*64
            with self.subTest(path=path),self.assertRaises(ValueError):audit.compare_reboot_snapshots(receipt(1),bad,context(1),context(2))
    def test_pending_journal_locks_unknown_and_escaping_paths_rejected(self):
        for name in ['desktop-install-pending-v1.json','update-journal-v1','.ming-installed-packages.lock','.ming-publisher-keyring.lock','unknown','../escape','/absolute','packages/aa/x/app.json']:
            bad=content();bad['files'].append({'path':name,'sha256':'a'*64});bad['files'].sort(key=lambda x:x['path'])
            with self.subTest(name=name),self.assertRaises(ValueError):audit.validate_content(bad,True)
    def test_sorted_unique_complete_file_enumeration_required(self):
        for mutation in ['duplicate','missing','unsorted','different-hash','extra-app','bool-id']:
            bad=content()
            if mutation=='duplicate':bad['files'].append(copy.deepcopy(bad['files'][0]))
            elif mutation=='missing':bad['files'].pop(0)
            elif mutation=='unsorted':bad['files'].reverse()
            elif mutation=='different-hash':bad['files'][0]['sha256']='f'*64
            elif mutation=='extra-app':bad['apps'].append(copy.deepcopy(bad['apps'][0]))
            else:bad['apps'][0]['app']=True
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):audit.validate_content(bad,True)
    def test_legal_history_pairs_allowed_but_incomplete_or_unbounded_paths_rejected(self):
        directory='packages/aa/0.0.1-'+'a'*64
        original=content()
        original['files'] += [dict(path=directory+'/'+name,sha256='b'*64) for name in ['app.json','main.wasm']]
        original['files'].sort(key=lambda item:item['path'])
        audit.validate_content(original,True)
        for mutation in ['missing-pair','odd-hex','long-hex','long-version','too-many-files']:
            bad=copy.deepcopy(original)
            if mutation=='missing-pair':bad['files']=[item for item in bad['files'] if item['path']!=directory+'/main.wasm']
            elif mutation=='too-many-files':
                bad['files'] += [dict(path=f'packages/{i:04x}/0.0.1-'+('a'*64)+'/'+name,sha256='b'*64)
                                 for i in range(125) for name in ['app.json','main.wasm']]
            else:
                replacement={'odd-hex':'a','long-hex':'a'*514,'long-version':'aa'}[mutation]
                for item in bad['files']:
                    if item['path'].startswith(directory+'/'):
                        item['path']=item['path'].replace('packages/aa/','packages/'+replacement+'/')
                        if mutation=='long-version':item['path']=item['path'].replace('0.0.1-','1'*100+'.0.1-')
            bad['files'].sort(key=lambda item:item['path'])
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):audit.validate_content(bad,True)
    def test_all_snapshots_keep_verified_initial_package_trust_bootstrap_and_source(self):
        source=next(app for app in content()['apps'] if app['app']==2)
        signed=dict(initialContent=content(False),source=dict(storageFingerprint=source['fingerprint'],manifestSha256=source['manifestSha256'],wasmSha256=source['wasmSha256']))
        audit.bind_signed_content(content(),signed,True);audit.bind_signed_content(content(False),signed,False)
        for field in ['initial-fingerprint','initial-manifest','initial-wasm','trustSha256','bootstrapSha256','source-fingerprint','source-manifest','source-wasm']:
            bad=copy.deepcopy(signed)
            if field.startswith('initial-'):
                key={'initial-fingerprint':'fingerprint','initial-manifest':'manifestSha256','initial-wasm':'wasmSha256'}[field]
                bad['initialContent']['apps'][0][key]='f'*64
            elif field.startswith('source-'):
                key={'source-fingerprint':'storageFingerprint','source-manifest':'manifestSha256','source-wasm':'wasmSha256'}[field];bad['source'][key]='f'*64
            else:bad['initialContent'][field]='f'*64
            with self.subTest(field=field),self.assertRaises(ValueError):audit.bind_signed_content(content(),bad,True)
    def test_reused_boot_or_session_identity_rejected_even_when_content_matches(self):
        for field in ['bootId','sessionId']:
            one,two=receipt(1),receipt(2,'pre-desktop');ctx=context(2);ctx[field]=context(1)[field];two['envelope'][field]=None if field=='sessionId' else ctx[field]
            with self.assertRaises(ValueError):audit.compare_reboot_snapshots(one,two,context(1),ctx)
    def test_duplicate_json_keys_nonfinite_and_truncated_stream_rejected(self):
        for value in ['{"x":1,"x":2}','{"x":NaN}','{"x":1e999}']:
            with self.assertRaises(ValueError):audit.base.strict_json(value)
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            p=Path(tmp)/'raw.jsonl';p.write_text('{"uptime":1}')
            with self.assertRaises(ValueError):audit.raw_rows(p,'uptime')

class RawSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.review=json.loads((HERE/'session-review.draft.json').read_text())
        self.report={'bootNumber':2};ctx=context(2)
        self.events=[{'type':'data-marker'},{'type':'persistent-install-state','argv':audit.expected_helper_argv(self.review,2,'pre-desktop'),'code':0,'stdout':'helper completion','stderr':'','receipt':receipt(2,'pre-desktop')},{'type':'process-start','name':'desktop','argv':['/opt/mingos/ming-app-host','desktop',audit.ROOT+'/catalog.json','--renderer','cpu']+self.review['extraArgs']+['--persistent-install-state',audit.ROOT]},{'type':'desktop-state','state':{'sessionId':'session-2'}},{'type':'desktop-workers-stopped'},{'type':'persistent-install-state','argv':audit.expected_helper_argv(self.review,2,'post-desktop'),'code':0,'stdout':'helper completion','stderr':'','receipt':receipt(2)},{'type':'data-synced'},{'type':'guest-result',**{k:ctx[k] for k in ['bootId','diskUuid','marker','previousMarker']}}]
        for event in self.events:
            if event.get('type')=='persistent-install-state':
                r=event['receipt'];event['stdout']=json.dumps({'schemaVersion':1,'status':r['envelope']['status'],'actions':r['envelope']['actions'],'content':r['content']})
        self.outcome={'droppedTrace':0,'trace':[]}
    def check(self):
        return audit.audit_snapshot_stream([{'offset':n*100,'endOffset':(n+1)*100,'event':e} for n,e in enumerate(self.events)],self.report,self.review,self.outcome)
    def test_raw_open_existing_pre_post_snapshot_control(self):
        self.assertEqual(self.check()['context'],context(2))
    def test_first_helper_requires_template_and_reviewed_hash_without_changing_reopen(self):
        self.report={'bootNumber':1};ctx=context(1)
        self.review['catalogSha256']=receipt(1,'pre-desktop')['content']['catalogSha256']
        self.events[1]['receipt']=receipt(1,'pre-desktop');self.events[5]['receipt']=receipt(1)
        self.events[1]['argv']=audit.expected_helper_argv(self.review,1,'pre-desktop')
        self.events[5]['argv']=audit.expected_helper_argv(self.review,1,'post-desktop')
        self.events[2]['argv']+=['--install-source','/opt/mingos/install-source/app.json']
        self.events[3]['state']['sessionId']='session-1'
        self.events[7].update({k:ctx[k] for k in ['bootId','diskUuid','marker','previousMarker']})
        for index in [1,5]:
            r=self.events[index]['receipt'];self.events[index]['stdout']=json.dumps({'schemaVersion':1,'status':r['envelope']['status'],'actions':r['envelope']['actions'],'content':r['content']})
        self.assertEqual(self.check()['context'],ctx)
        self.assertEqual(self.events[1]['argv'][-3:-1],['/opt/mingos/catalog.json',self.review['catalogSha256']])
        self.assertEqual(self.events[5]['argv'],[self.review['persistentInstallFixture']['helper'],'open-existing',audit.ROOT,self.review['persistentInstallFixture']['receipt']])
        original=copy.deepcopy(self.events)
        for mutation in ['old-cli','wrong-template','wrong-hash','catalog-drift','template-on-reopen']:
            self.events=copy.deepcopy(original)
            if mutation=='old-cli':del self.events[1]['argv'][-3:-1]
            elif mutation=='wrong-template':self.events[1]['argv'][-3]='/tmp/other-catalog.json'
            elif mutation=='wrong-hash':self.events[1]['argv'][-2]='f'*64
            elif mutation=='template-on-reopen':self.events[5]['argv'][-1:-1]=['/opt/mingos/catalog.json',self.review['catalogSha256']]
            else:
                r=self.events[1]['receipt'];r['content']['catalogSha256']='f'*64
                next(f for f in r['content']['files'] if f['path']=='catalog.json')['sha256']='f'*64
                self.events[1]['stdout']=json.dumps({'schemaVersion':1,'status':r['envelope']['status'],'actions':r['envelope']['actions'],'content':r['content']})
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.check()
    def test_reseed_source_override_legacy_events_or_installed_trace_rejected(self):
        original=copy.deepcopy(self.events)
        for change in ['helper-seed','desktop-source','legacy-fixture','installed','updated','seeded']:
            self.events=copy.deepcopy(original);self.outcome={'droppedTrace':0,'trace':[]}
            if change=='helper-seed':self.events[1]['argv'][1]='initialize-first'
            elif change=='desktop-source':self.events[2]['argv']+=['--install-source','/opt/mingos/install-source/app.json']
            elif change=='legacy-fixture':self.events.insert(1,{'type':'install-fixture'})
            else:self.outcome['trace']=[{'kind':change}]
            with self.subTest(change=change),self.assertRaises(ValueError):self.check()
    def test_wrong_phase_reordered_missing_or_nonzero_helper_rejected(self):
        original=copy.deepcopy(self.events)
        for change in ['before-marker','after-sync','missing','helper-failed','wrong-number','fake-no-write','stdout-drift','stdout-duplicate-key']:
            self.events=copy.deepcopy(original);self.report={'bootNumber':2}
            if change=='before-marker':self.events[0],self.events[1]=self.events[1],self.events[0]
            elif change=='after-sync':self.events[5],self.events[6]=self.events[6],self.events[5]
            elif change=='missing':self.events.pop(5)
            elif change=='helper-failed':self.events[1]['code']=1
            elif change=='wrong-number':self.report['bootNumber']=1
            elif change=='stdout-drift':
                raw=json.loads(self.events[1]['stdout']);raw['content']['registrySha256']='f'*64;self.events[1]['stdout']=json.dumps(raw)
            elif change=='stdout-duplicate-key':self.events[1]['stdout']='{"schemaVersion":1,"schemaVersion":1}'
            else:self.events[1]['receipt']['envelope']['actions']['packagesInstalled']=1;self.events[1]['noWrite']=True
            with self.subTest(change=change),self.assertRaises(ValueError):self.check()
    def test_pair_rejects_forged_success_without_review_or_raw_inputs(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            root=Path(tmp);(root/'review.json').write_text(json.dumps(self.review))
            args=SimpleNamespace(repo=HERE.parents[1],inputs=root,review=root/'review.json',bundle=root/'bundle',
                pair=root/'pair',fixture_verification=root/'signed.json')
            with self.assertRaisesRegex(ValueError,'review is pending'):audit.pair(args)

class InputTests(unittest.TestCase):
    def test_two_reviewed_inputs_have_actual_dock_buttons_and_distinct_roles(self):
        one=json.loads((HERE/'input-boot1.json').read_text());two=json.loads((HERE/'input-boot2.json').read_text());audit.validate_input(one,1);audit.validate_input(two,2)
        self.assertEqual([len(one['steps']),len(two['steps'])],[4,2])
    def test_timer_script_action_wrong_dock_point_and_missing_button_rejected(self):
        for mutation in ['action','afterMs','bad-point','missing-down','autostart-ready-gate','bool-schema']:
            spec=json.loads((HERE/'input-boot2.json').read_text());dock=spec['steps'][0]
            if mutation=='action':dock['action']={'op':'launch','app':2}
            elif mutation=='afterMs':dock['afterMs']=30000
            elif mutation=='bad-point':dock['events'][0]['y']=744
            elif mutation=='missing-down':dock['events'].pop(1)
            elif mutation=='bool-schema':spec['schemaVersion']=True
            else:dock['requireApps']=[1,2]
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):audit.validate_input(spec,2)
    def test_boot2_install_or_wrong_role_inputs_rejected(self):
        one=json.loads((HERE/'input-boot1.json').read_text())
        with self.assertRaises(ValueError):audit.validate_input(one,2)
    def test_draft_unapproved_and_binary_fields_unresolved(self):
        review=json.loads((HERE/'session-review.draft.json').read_text());self.assertIs(review['reviewed'],False);self.assertIsNone(review['reviewer']);self.assertIsNone(review['hostSha256'])

class TransactionReceiptTests(unittest.TestCase):
    def setUp(self):
        self.context=context(1);self.snapshots={'pre-desktop':receipt(1,'pre-desktop'),'post-desktop':receipt(1)}
        self.source=next(app for app in content()['apps'] if app['app']==2)
        self.signed={'source':{'storageFingerprint':self.source['fingerprint'],
            'manifestSha256':self.source['manifestSha256'],'wasmSha256':self.source['wasmSha256']}}
        self.commit={'schemaVersion':1,'status':'committed','packageId':self.source['packageId'],
            'version':self.source['version'],'fingerprint':self.source['fingerprint'],'registryRoot':audit.ROOT,
            'catalogPath':audit.ROOT+'/catalog.json','content':content(),'persistentBlockConfirmed':False}
        self.wrapper={'kind':'persistent-install-commit','bootId':'boot-1','sessionId':'session-1','desktopPid':44,'receipt':self.commit}
        self.outcome={'trace':[{'kind':'persistent-install-commit','detail':json.dumps(self.commit)},
            {'kind':'installed','detail':'dev.ming.install-demo'}]}
    def check(self,number=1,wrappers=None):
        lines=b''.join(b'MING_PERSISTENT_INSTALL '+json.dumps(wrapper).encode()+b'\n' for wrapper in
            ([self.wrapper] if wrappers is None else wrappers))
        events=[{'type':'log','name':'desktop-wayland.stderr.log','encoding':'base64','offset':0,
            'data':base64.b64encode(lines or b'Wayland diagnostic\n').decode()}]
        return audit.audit_transaction_receipts(events,context(number),44,self.outcome,self.snapshots,self.signed)
    def test_commit_binds_signed_source_trace_and_final_content(self):
        result=self.check()
        self.assertEqual(result['receipt'],self.commit)
        self.assertEqual(result['offset'],0)
        self.assertGreater(result['endOffset'],0)
    def test_missing_duplicate_identity_schema_and_content_pseudoproofs_rejected(self):
        original=copy.deepcopy(self.wrapper)
        for mutation in ('missing','duplicate','pid','boot','session','unknown-wrapper','unknown-receipt',
                         'bool-schema','blocked','source-fingerprint','manifest-drift','content-drift','trace-missing','trace-drift','installed-missing'):
            with self.subTest(mutation=mutation):
                self.setUp();wrappers=None
                if mutation=='missing':wrappers=[]
                elif mutation=='duplicate':wrappers=[self.wrapper,self.wrapper]
                elif mutation in ('pid','boot','session'):
                    self.wrapper[{'pid':'desktopPid','boot':'bootId','session':'sessionId'}[mutation]]='forged'
                elif mutation=='unknown-wrapper':self.wrapper['extra']=True
                elif mutation=='unknown-receipt':self.commit['extra']=True
                elif mutation=='bool-schema':self.commit['schemaVersion']=True
                elif mutation=='blocked':self.commit['persistentBlockConfirmed']=True
                elif mutation=='source-fingerprint':self.signed['source']['storageFingerprint']='f'*64
                elif mutation=='manifest-drift':self.signed['source']['manifestSha256']='f'*64
                elif mutation=='content-drift':self.snapshots['post-desktop']['content']['registrySha256']='f'*64
                elif mutation=='trace-missing':self.outcome['trace'].pop(0)
                elif mutation=='trace-drift':self.outcome['trace'][0]['detail']=json.dumps(dict(self.commit,version='9.9.9'))
                else:self.outcome['trace'].pop()
                with self.assertRaises(ValueError):self.check(wrappers=wrappers)
        self.wrapper=original
    def test_any_failed_transaction_or_boot2_commit_is_rejected(self):
        failure={'kind':'persistent-install-failed','bootId':'boot-1','sessionId':'session-1','desktopPid':44,
            'receipt':{'schemaVersion':1,'status':'rolled-back','stage':'catalog-write','persistentBlockConfirmed':False,
                'compensationAttempted':False,'compensationStage':None,'packageId':'dev.ming.install-demo',
                'version':'1.0.0','fingerprint':self.source['fingerprint']}}
        for number,wrappers in ((1,[failure,self.wrapper]),(2,[self.wrapper]),(2,[failure])):
            with self.subTest(number=number),self.assertRaises(ValueError):self.check(number,wrappers)
        self.outcome={'trace':[]};self.snapshots={'pre-desktop':receipt(2,'pre-desktop'),'post-desktop':receipt(2)}
        self.assertIsNone(self.check(2,[])['receipt'])

class StageDriverTests(unittest.TestCase):
    def test_unreviewed_stage_fails_before_creating_output(self):
        from unittest.mock import patch
        import sys
        stage_spec=importlib.util.spec_from_file_location('persistent_stage',HERE/'run-linux-stage.py')
        stage=importlib.util.module_from_spec(stage_spec);stage_spec.loader.exec_module(stage)
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            out=Path(tmp)/'must-not-exist'
            argv=['run-linux-stage.py','bundle','--repo',str(HERE.parents[1]),'--inputs',str(HERE),'--review',str(HERE/'session-review.draft.json'),'--output',str(out)]
            with patch.object(sys,'platform','linux'),patch.object(sys,'argv',argv):
                with self.assertRaisesRegex(ValueError,'main-thread session/payload review is pending'):stage.main()
            self.assertFalse(out.exists())

if __name__=='__main__':unittest.main()
