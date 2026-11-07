import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest
import tempfile
from unittest import mock

HERE = Path(__file__).resolve().parent
SOURCE = HERE
sys.path.insert(0, str(SOURCE))
if len(HERE.parents)>2: sys.path.insert(1, str(HERE.parents[2] / 'scripts/boot'))

class WorkflowTests(unittest.TestCase):
    def profile(self):
        self.assertTrue((SOURCE / 'workflow_profile.py').is_file(), 'independent workflow profile is missing')
        import workflow_profile
        return workflow_profile

    def control(self):
        self.assertTrue((SOURCE / 'workflow_control.py').is_file(), 'bounded interactive controller is missing')
        import workflow_control
        return workflow_control

    def review(self):
        m = self.profile()
        from isolation_profiles import load_profile
        return dict(reviewed=True, reviewer='independent-root-review', profile=m.PROFILE,
            sessionMode='long-lived', extraArgs=m.ARGS.copy(), hostSha256='a'*64,
            catalogSha256='b'*64, payloadFiles={}, packageEvidenceSha256={'1':'c'*64,'2':'d'*64},
            hostProvenance={k:'e'*64 for k in m.HOST_PROVENANCE}, strictProbeProfile='source-v2',
            strictProbeSha256=load_profile('source-v2')['artifactSha256'],
            probeSourceFreezeSha256='f'*64, probeBuildReceiptSha256='a'*64,
            diagnosticOnly=True, instrumentedQualification=False, formalQualification=False,
            finalSystemAccepted=False, budgetEvidence=m.BUDGETS.copy(),
            inputScriptSha256=None, controlPolicy=m.CONTROL_POLICY)

    def test_independent_fixed_contract(self):
        m = self.profile(); r = self.review()
        self.assertEqual(m.session_contract(r), r)
        self.assertEqual(m.ARGS[1], '0')
        self.assertEqual(m.APPS, ((1,'notes-demo',6),(2,'animated-demo',5)))
        self.assertEqual(len(m.expected_files()),41)
        for key,value in [('profile','developer-vm-v1'),('sessionMode','bounded'),
                           ('formalQualification',True),('inputScriptSha256','bad')]:
            bad=copy.deepcopy(r); bad[key]=value
            with self.assertRaises(ValueError): m.session_contract(bad)
        for key in m.BUDGETS:
            bad=copy.deepcopy(r); bad['budgetEvidence'][key]+=1
            with self.assertRaises(ValueError): m.session_contract(bad)

    def test_preview_remains_input_forbidden(self):
        self.profile()
        from bootlib import validate_reviewed_input
        with self.assertRaises(ValueError): validate_reviewed_input({'profile':'developer-vm-v1'},Path('never'))
        with self.assertRaises(ValueError): validate_reviewed_input({'profile':'developer-vm-v1'},None,boot_number=1)

    def test_replay_is_exact_reviewed_bytes_and_interactive_has_no_replay(self):
        self.profile()
        from bootlib import validate_reviewed_input, reviewed_input_spec
        review=self.review()
        self.assertEqual(validate_reviewed_input(review,None),(None,None))
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'input.json'
            raw=b'{"schemaVersion":1,"requireApps":[1],"steps":[]}'
            path.write_bytes(raw)
            with self.assertRaises(ValueError): validate_reviewed_input(review,path)
            review['inputScriptSha256']=hashlib.sha256(raw).hexdigest()
            self.assertEqual(validate_reviewed_input(review,path),(raw,hashlib.sha256(raw).hexdigest()))
            with self.assertRaises(ValueError): validate_reviewed_input(review,None)
            with self.assertRaises(ValueError): validate_reviewed_input(review,path,boot_number=1)
            path.write_bytes(raw+b' ')
            with self.assertRaises(ValueError): validate_reviewed_input(review,path)
            with self.assertRaises(ValueError): reviewed_input_spec(review,b'{"x":1,"x":2}')

    def test_command_schema_and_text_bounds(self):
        m=self.control()
        self.assertEqual(m.command_steps({'op':'click','x':1,'y':2})[0]['events'][0],{'kind':'pointer','x':1,'y':2})
        self.assertGreater(len(m.command_steps({'op':'type','text':'Ab9 hello'})),0)
        for command in ({'op':'qmp','command':'quit'},{'op':'capture','path':'../../x'},
                        {'op':'key','qcode':'f12'},{'op':'type','text':'中'},
                        {'op':'type','text':'a'*513},{'op':'click','x':True,'y':2}):
            with self.assertRaises(ValueError): m.command_steps(command)

    def test_request_identity_and_replay(self):
        m=self.control()
        guard=m.RequestGuard('a'*64,'instance-one')
        state={'sessionId':'session-one','revision':3,'bootId':'boot-one','phase':'ready',
            'root':{'width':1280,'height':800},'apps':[]}
        request={'schemaVersion':1,'token':'a'*64,'instance':'instance-one','sequence':1,
                 'sessionId':'session-one','revision':3,'command':{'op':'capture'},
                 'inputEpoch':0,'layoutSha256':m.layout_digest(state)}
        self.assertEqual(guard.accept(request,state),{'op':'capture'})
        with self.assertRaises(ValueError): guard.accept(request,state)
        for change in ({'token':'b'*64},{'instance':'old-instance'},{'sessionId':'old-session'},
                       {'revision':4},{'extra':1},{'inputEpoch':1},{'layoutSha256':'f'*64}):
            bad=dict(request,sequence=2,**change)
            with self.assertRaises(ValueError): guard.accept(bad,state)

    def test_interactive_vm_admission_reaches_runtime_without_an_input_script(self):
        """Exercise actual main argument admission; no filesystem lease or VM runs."""
        import vm
        review=self.review()
        manifest=dict(sessionReview=review,infraApiVersion=1,inputDiagnostics=True,
            diagnosticProfile='input-pointer-ready-v1',developerProfile='developer-workflow-v1',
            workflowBudgets=self.profile().BUDGETS.copy(),formalQualification=False,
            finalSystemAccepted=False,instrumentedQualification=False)
        class RuntimeBoundary(Exception):pass
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'review.json';path.write_text(json.dumps(review))
            argv=['vm.py','--bundle','/pack/bundle','--review',str(path),'--output','/results/boot-0001',
                '--timeout','900','--work-root','/work/mingos-boot-infra-20261025',
                '--data-manifest','disks/workflow-128.json','--runtime-root','/run/mingos-runtime',
                '--workflow-control','/run/mingos-runtime/control-0001',
                '--diagnostic-split-input','--diagnostic-pointer-handshake']
            with mock.patch.object(sys,'argv',argv),mock.patch.object(vm,'validate_bundle',return_value=manifest), \
                 mock.patch('data_disk.runtime_directory',side_effect=RuntimeBoundary), \
                 mock.patch('subprocess.Popen',side_effect=AssertionError('admission started a process')):
                with self.assertRaises(RuntimeBoundary):vm.main()

    def test_other_profile_cannot_use_control_to_bypass_reviewed_split_input(self):
        import contextlib,io,vm
        review=self.review();review['profile']='developer-vm-v1'
        manifest=dict(sessionReview=review,inputDiagnostics=True,diagnosticProfile='input-pointer-ready-v1')
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'review.json';path.write_text(json.dumps(review))
            argv=['vm.py','--bundle','/pack/bundle','--review',str(path),'--output','/results/unused',
                '--workflow-control','/run/mingos-runtime/control','--diagnostic-split-input',
                '--diagnostic-pointer-handshake']
            with mock.patch.object(sys,'argv',argv),mock.patch.object(vm,'validate_bundle',return_value=manifest), \
                 mock.patch('data_disk.runtime_directory',side_effect=AssertionError('invalid input reached runtime')), \
                 contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):vm.main()

if __name__ == '__main__': unittest.main()
