"""Safe synthetic controls. No Docker, mkfs, VM, or guest execution."""
import hashlib,importlib.util,json,os,shutil,sys,tempfile,unittest
from pathlib import Path
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]

def load(test):
    path=HERE/'developer_vm_driver.py'
    test.assertTrue(path.is_file(),'fixed Linux driver is missing')
    spec=importlib.util.spec_from_file_location('driver_control',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class SyntheticAPI:
    def __init__(self,fail='',locked=False):self.calls=[];self.fail=fail;self.locked=locked;self.index=0
    def call(self,name,args):
        self.calls.append((name,args))
        if name==self.fail:raise RuntimeError('synthetic '+name+' failure')
    def verify(self,*args):
        self.call('verify',args);return {'status':'developer-pack-bytes-verified','manifestSha256':args[1]}
    def init(self,*args):self.call('init',args);return {'status':'developer-instance-initialized'}
    def start(self,*args):
        self.index+=1;self.call('start'+str(self.index),args)
        return {'status':'developer-session-normal','normalBoots':self.index,'generation':'same-generation','bootId':'boot'+str(self.index),'sessionId':'session'+str(self.index)}
    def inspect(self,*args):
        self.call('inspect'+str(self.index),args)
        return {'status':'developer-instance-inspected','normalBoots':self.index,'state':'locked' if self.locked else 'ready','generation':'same-generation'}

class PairControls(unittest.TestCase):
    def test_verify_stage_never_initializes_or_boots(self):
        d=load(self);self.assertTrue(hasattr(d,'dispatch'),'closed verify/pair dispatch missing')
        api=SyntheticAPI();result=d.dispatch(api,'verify','a'*64,lambda *args:None)
        self.assertEqual([name for name,_ in api.calls],['verify']);self.assertEqual(result['manifestSha256'],'a'*64)
        with self.assertRaises(ValueError):d.dispatch(api,'reopen','a'*64,lambda *args:None)
    def test_exact_pair_uses_same_instance_results_and_runtime(self):
        d=load(self);api=SyntheticAPI();result=d.pair(api,'a'*64,lambda *args:None)
        self.assertEqual([name for name,_ in api.calls],['verify','init','start1','inspect1','start2','inspect2'])
        first=api.calls[2][1];second=api.calls[4][1]
        self.assertEqual(tuple(v.as_posix() if isinstance(v,Path) else v for v in first),('/pack','a'*64,'/work/mingos-boot-infra-20261025/developer-instance.json','/results/boot-0001','/run/mingos-runtime'))
        self.assertEqual(second[3].as_posix(),'/results/boot-0002');self.assertEqual(first[2],second[2]);self.assertEqual(first[4],second[4])
        self.assertEqual(result['status'],'developer-preview-pair-observed');self.assertFalse(result['applicationPersistenceVerified']);self.assertFalse(result['finalSystemAccepted'])
    def test_failure_never_advances_or_retries(self):
        d=load(self)
        for failure in ('verify','init','start1','inspect1','start2','inspect2'):
            api=SyntheticAPI(fail=failure)
            with self.assertRaises(RuntimeError):d.pair(api,'a'*64,lambda *args:None)
            names=[name for name,_ in api.calls]
            self.assertEqual(names[-1],failure);self.assertEqual(len(names),len(set(names)))
    def test_locked_inspect_blocks_second_boot(self):
        d=load(self);api=SyntheticAPI(locked=True)
        with self.assertRaises(ValueError):d.pair(api,'a'*64,lambda *args:None)
        self.assertEqual([name for name,_ in api.calls],['verify','init','start1','inspect1'])
    def test_bad_pin_rejected_before_api(self):
        d=load(self);api=SyntheticAPI()
        with self.assertRaises(ValueError):d.pair(api,'A'*64,lambda *args:None)
        self.assertEqual(api.calls,[])

class SourceControls(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def frozen(self,d):
        files={}
        for name in d.SOURCE_FILES:
            source=HERE/'developer_vm_driver.py' if name=='developer_vm_driver.py' else REPO/name
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
            raw=target.read_bytes();files[name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
        raw=json.dumps({'schemaVersion':1,'profile':'developer-vm-trusted-source-v1','files':files},sort_keys=True,separators=(',',':')).encode()
        (self.root/'source-manifest.json').write_bytes(raw);return hashlib.sha256(raw).hexdigest()
    def test_frozen_actual_import_and_session_contract_with_real_profile(self):
        d=load(self);pin=self.frozen(d);d.verify_source(self.root,pin)
        # Run in an isolated subprocess so module cache cannot mask a missing source.
        import subprocess
        command="import sys;sys.path.insert(0,sys.argv[1]);import developer_vm,developer_profile as p;from isolation_profiles import load_profile; r=dict(reviewed=True,reviewer='synthetic control',profile=p.PROFILE,sessionMode='bounded',extraArgs=p.ARGS,hostSha256='a'*64,catalogSha256='a'*64,payloadFiles={},packageEvidenceSha256={'1':'a'*64,'2':'a'*64},hostProvenance={k:'a'*64 for k in p.HOST_PROVENANCE},strictProbeProfile='source-v2',strictProbeSha256=load_profile('source-v2')['artifactSha256'],probeSourceFreezeSha256='a'*64,probeBuildReceiptSha256='a'*64,diagnosticOnly=True,instrumentedQualification=False,formalQualification=False,finalSystemAccepted=False);p.session_contract(r)"
        args=[sys.executable,'-I','-B','-c',command,str(self.root/'scripts/boot')]
        result=subprocess.run(args,capture_output=True);self.assertEqual(result.returncode,0,result.stderr.decode())
        (self.root/'probes/linux-isolation-source-v2/profile.json').unlink()
        result=subprocess.run(args,capture_output=True);self.assertNotEqual(result.returncode,0);self.assertIn(b'profile.json',result.stderr)
    def test_changed_missing_extra_and_linked_source_refused(self):
        d=load(self);pin=self.frozen(d)
        target=self.root/'Cargo.lock';raw=target.read_bytes();target.write_bytes(raw+b'changed')
        with self.assertRaises(ValueError):d.verify_source(self.root,pin)
        target.write_bytes(raw);(self.root/'extra').write_text('extra')
        with self.assertRaises(ValueError):d.verify_source(self.root,pin)
        (self.root/'extra').unlink();target.unlink()
        with self.assertRaises(ValueError):d.verify_source(self.root,pin)
    def test_wrong_manifest_pin_refused(self):
        d=load(self);self.frozen(d)
        with self.assertRaises(ValueError):d.verify_source(self.root,'0'*64)

if __name__=='__main__':unittest.main(verbosity=2)
