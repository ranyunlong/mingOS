import copy,importlib.util,json,tempfile,unittest
from pathlib import Path
P=Path(__file__).parent
spec=importlib.util.spec_from_file_location('kotlin_delivery',P/'kotlin_delivery.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def inspection_from_command(argv,cid):
    """Fake daemon parses submitted argv independently of production expectation code."""
    flag=lambda name:argv[argv.index(name)+1]
    mounts=[];effective=[]
    for i,token in enumerate(argv):
        if token!='--mount':continue
        fields={item.split('=',1)[0]:item.split('=',1)[1] if '=' in item else True for item in argv[i+1].split(',')}
        requested={'Type':fields['type'],'Source':fields['source'],'Target':fields['target'],'ReadOnly':bool(fields.get('readonly',False))}
        if fields['type']=='volume':requested['VolumeOptions']={'NoCopy':bool(fields.get('volume-nocopy',False)),**({'Subpath':fields['volume-subpath']} if 'volume-subpath' in fields else {})}
        mounts.append(requested)
        effective.append({'Type':fields['type'],'Source':fields['source'],**({'Name':fields['source']} if fields['type']=='volume' else {}),'Destination':fields['target'],'RW':not requested['ReadOnly']})
    return {'Id':cid,'Image':argv[argv.index('--entrypoint')+2],'Name':flag('--name'),
        'Config':{'User':flag('--user'),'Labels':dict(argv[i+1].split('=',1) for i,x in enumerate(argv) if x=='--label')},
        'HostConfig':{'NetworkMode':flag('--network'),'ReadonlyRootfs':'--read-only' in argv,'Privileged':'--privileged' in argv,
        'NanoCpus':int(float(flag('--cpus'))*1e9),'Memory':int(flag('--memory')[:-1])*1024**3,'PidsLimit':int(flag('--pids-limit')),
        'CapDrop':[argv[i+1] for i,x in enumerate(argv) if x=='--cap-drop'],'CapAdd':[argv[i+1] for i,x in enumerate(argv) if x=='--cap-add'],
        'SecurityOpt':[argv[i+1] for i,x in enumerate(argv) if x=='--security-opt'],'Tmpfs':dict(argv[i+1].split(':',1) for i,x in enumerate(argv) if x=='--tmpfs'),
        'Mounts':mounts,'Binds':None,'VolumesFrom':None},'Mounts':effective,'State':{'Running':False,'ExitCode':0,'OOMKilled':False}}

class FakeDocker:
    """Command recorder with inspection responses; never compiles or starts Docker."""
    def __init__(self,wrong_image=False,missing_cache=False,fail_build=False,tamper=False,effective_drift=False,replay_changed=False,volume_drift=False,drop_after_prepare=False,running_failure=False):
        self.calls=[];self.containers={};self.volumes={'original-cache'};self.wrong_image=wrong_image;self.missing_cache=missing_cache;self.fail_build=fail_build;self.tamper=tamper;self.effective_drift=effective_drift;self.replay_changed=replay_changed;self.volume_drift=volume_drift;self.volume_labels={};self.drop_after_prepare=drop_after_prepare;self.running_failure=running_failure
    def __call__(self,argv,label):
        self.calls.append((list(argv),label))
        if argv[:2]==['image','inspect']:
            return json.dumps([{'Id':'sha256:bad' if self.wrong_image else m.IMAGE,'Os':'linux','Architecture':'amd64','RootFS':{'Layers':['sha256:layer']}}])
        if argv[:2]==['volume','inspect']:
            if self.missing_cache and argv[2]=='original-cache':raise RuntimeError('missing cache')
            if argv[2] not in self.volumes:raise RuntimeError('missing volume')
            return json.dumps([{'Name':argv[2],'Driver':'local','Labels':({} if self.volume_drift else self.volume_labels.get(argv[2],{}))}])
        if argv[:2]==['volume','ls']:return '\n'.join(sorted(self.volumes))
        if argv[:2]==['volume','create']:
            self.volumes.add(argv[-1]);self.volume_labels[argv[-1]]=dict(argv[i+1].split('=',1) for i,x in enumerate(argv) if x=='--label');return argv[-1]
        if argv[0]=='create':
            cid=('%064x'%(len(self.containers)+1));self.containers[cid]=inspection_from_command(argv,cid);return cid
        if argv[0]=='inspect':
            value=copy.deepcopy(self.containers[argv[1]])
            if self.tamper:value['HostConfig']['NetworkMode']='bridge'
            if self.effective_drift:value['Mounts']=[{'Type':'volume','Name':'foreign','Destination':'/out','RW':True}]
            return json.dumps([value])
        if argv[:2]==['start','-a']:
            cid=argv[2];v=self.containers[cid];v['State']={'Running':False,'ExitCode':0,'OOMKilled':False}
            if v['Name'].endswith('synthetic-failure'):
                v['State']={'Running':False,'ExitCode':7,'OOMKilled':False};raise RuntimeError('synthetic expected exit')
            if v['Name'].endswith('synthetic-running'):
                v['State']={'Running':True,'ExitCode':0,'OOMKilled':False};raise RuntimeError('synthetic expected timeout')
            if v['Name'].endswith('synthetic-readonly'):return 'SYNTHETIC_CONTROL '+json.dumps({'status':'readonly-input-control-passed','compileExecuted':False})
            if self.drop_after_prepare and v['Name'].endswith('prepare'):
                self.volumes.remove(next(x for x in self.volumes if x.endswith('-cache') and x!='original-cache'))
            if self.fail_build and v['Name'].endswith('build-read-file'):
                v['State']={'Running':self.running_failure,'ExitCode':42,'OOMKilled':False};raise RuntimeError('compiler failed')
            return json.dumps({'status':'independent-readonly-replay-verified','originalCacheUnchanged':not self.replay_changed})
        if argv[0]=='stop':
            self.containers[argv[-1]]['State']['Running']=False;return argv[-1]
        if argv[0]=='cp':return ''
        raise AssertionError(argv)

class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.repo=self.root/'repo';self.repo.mkdir()
        for name in m.REQUIRED_SOURCE:
            p=self.repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('source')
        self.baseline=self.root/'baseline.json';self.baseline.write_text(json.dumps({'schemaVersion':1,'files':{'caches/modules-2/files-2.1/a.jar':'a'*64}}))
    def options(self,profile='uninstall'):
        return {'repo':self.repo,'output':self.root/'new-output','baseline':self.baseline,'original_cache':'original-cache','profile':profile,'docker':'docker'}
    def test_profiles_and_boundaries(self):
        for profile,count in [('uninstall',4),('update',6),('developer',2)]:
            o=self.options(profile);o['output']=self.root/profile;fake=FakeDocker();result=m.run(o,fake)
            self.assertEqual(result['status'],'pipeline-completed');self.assertEqual(len(result['labels']),count)
            creates=[a for a,_ in fake.calls if a[0]=='create'];self.assertEqual(len(creates),count+3)
            for argv in creates:
                self.assertIn('--pull',argv);self.assertIn('never',argv);self.assertIn(m.IMAGE,argv)
                for text in argv:
                    if 'source=original-cache,' in text:self.assertIn('readonly',text)
            build=[a for a in creates if any('build-'+result['labels'][0] in x for x in a)][0]
            self.assertTrue(any('target=/repo,readonly' in x and 'volume-subpath=repo' in x for x in build))
            verify=creates[-1]
            for destination in ('/repo','/snapshot','/deps/gradle','/out','/original-cache'):
                self.assertTrue(any('target='+destination+',' in x and 'readonly' in x for x in verify),destination)
            self.assertFalse(any(a[0] in ('pull','build','rm','rmi') or a[:2] in (['volume','rm'],['system','prune']) for a,_ in fake.calls))
    def test_missing_cache_and_wrong_image_before_volumes(self):
        for fake in (FakeDocker(missing_cache=True),FakeDocker(wrong_image=True)):
            o=self.options();o['output']=self.root/('attempt'+str(len(list(self.root.iterdir()))))
            with self.assertRaisesRegex((ValueError,RuntimeError),'fixed Linux|missing cache'):m.run(o,fake)
            self.assertFalse(any(a[:2]==['volume','create'] for a,_ in fake.calls))
    def test_existing_output_invalid_profile_and_bad_baseline(self):
        o=self.options();o['output'].mkdir()
        with self.assertRaises(ValueError):m.run(o,FakeDocker())
        o=self.options('unknown');o['output']=self.root/'another'
        with self.assertRaises(ValueError):m.run(o,FakeDocker())
        o=self.options();o['output']=self.root/'bad-baseline';self.baseline.write_text('{"schemaVersion":1,"files":{}}')
        with self.assertRaises(ValueError):m.run(o,FakeDocker())
    def test_first_failure_preserved_and_no_replay(self):
        fake=FakeDocker(fail_build=True);o=self.options()
        with self.assertRaises(RuntimeError):m.run(o,fake)
        failure=json.loads((o['output']/'result.json').read_text());self.assertEqual(failure['status'],'failed');self.assertTrue(failure['resourcesPreserved'])
        self.assertFalse(any('verify-all' in x for a,_ in fake.calls if a[0]=='create' for x in a))
        self.assertFalse(any('build-image-viewer' in x for a,_ in fake.calls if a[0]=='create' for x in a))
    def test_actual_inspection_drift_stops_before_start(self):
        fake=FakeDocker(tamper=True)
        with self.assertRaises(ValueError):m.run(self.options(),fake)
        self.assertFalse(any(a[0]=='start' for a,_ in fake.calls))

    def test_effective_mount_drift_rejected(self):
        fake=FakeDocker(effective_drift=True)
        with self.assertRaisesRegex(ValueError,'effective mount'):m.run(self.options(),fake)
        self.assertFalse(any(a[0]=='start' for a,_ in fake.calls))
    def test_replay_does_not_claim_changed_original_success(self):
        fake=FakeDocker(replay_changed=True)
        with self.assertRaisesRegex(ValueError,'replay'):m.run(self.options(),fake)
        self.assertFalse(any(a[0]=='cp' for a,_ in fake.calls))
    def test_baseline_path_injection_and_source_overlap(self):
        for name in ('../escape','/absolute','a//b','C:/host','a\\b'):
            self.baseline.write_text(json.dumps({'schemaVersion':1,'files':{name:'a'*64}}))
            with self.assertRaises(ValueError):m.run(self.options(),FakeDocker())
        self.baseline.write_text(json.dumps({'schemaVersion':1,'files':{'a':'a'*64}}))
        o=self.options();o['output']=self.repo/'nested'
        with self.assertRaisesRegex(ValueError,'overlap'):m.run(o,FakeDocker())

    def test_new_volume_ownership_drift_rejected(self):
        fake=FakeDocker(volume_drift=True)
        with self.assertRaisesRegex(ValueError,'ownership'):m.run(self.options(),fake)
        self.assertFalse(any(a[0]=='create' for a,_ in fake.calls))

    def test_missing_owned_volume_rechecked_before_next_create(self):
        fake=FakeDocker(drop_after_prepare=True)
        with self.assertRaisesRegex(RuntimeError,'missing volume'):m.run(self.options(),fake)
        self.assertFalse(any('build-read-file' in x for a,_ in fake.calls if a[0]=='create' for x in a))
    def test_failed_start_retains_final_inspection(self):
        fake=FakeDocker(fail_build=True)
        with self.assertRaises(RuntimeError):m.run(self.options(),fake)
        self.assertTrue(any(label=='build-read-file-inspect-after' for _,label in fake.calls))
        self.assertFalse(any(a[0]=='stop' for a,_ in fake.calls))
    def test_running_failed_owned_container_stopped_and_reinspected(self):
        fake=FakeDocker(fail_build=True,running_failure=True)
        with self.assertRaises(RuntimeError):m.run(self.options(),fake)
        stops=[a for a,_ in fake.calls if a[0]=='stop'];self.assertEqual(len(stops),1)
        owned=fake.containers[stops[0][-1]];self.assertTrue(owned['Name'].endswith('build-read-file'));self.assertFalse(owned['State']['Running'])
        self.assertTrue(any(label=='build-read-file-inspect-after-stop' for _,label in fake.calls))

    def test_synthetic_controls_never_invoke_compiler(self):
        fake=FakeDocker();o=self.options();o['synthetic_controls']=True
        result=m.run(o,fake);self.assertEqual(result['status'],'synthetic-input-controls-passed');self.assertFalse(result['compileExecuted'])
        self.assertFalse(any('build-read-file' in x for a,_ in fake.calls if a[0]=='create' for x in a))
        self.assertTrue(any(a[0]=='stop' for a,_ in fake.calls))

    def test_docker_desktop_effective_bind_path_normalization(self):
        argv=['create','--name','probe','--label','org.mingos.generation=g','--user','65534:65534','--network','none','--read-only','--cpus','2','--memory','4g','--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges','--tmpfs','/tmp:'+m.TMPFS,'--mount',r'type=bind,source=C:\Users\src,target=/repo,readonly','--entrypoint','node',m.IMAGE,'-e','0']
        actual=inspection_from_command(argv,'a'*64);actual['Mounts'][0]['Source']='/run/desktop/mnt/host/c/Users/src'
        m.check_inspection(actual,m.expected_inspection(argv,'a'*64))

    def test_retained_actual_docker_init_dto(self):
        value=json.loads((P/'fixtures/kotlin_delivery/actual-init-inspect.json').read_text())[0]
        argv=json.loads((P/'fixtures/kotlin_delivery/actual-init-command.json').read_text())['argv'][1:]
        m.check_inspection(value,m.expected_inspection(argv,value['Id']))

    def test_retained_actual_docker_prepare_dto(self):
        value=json.loads((P/'fixtures/kotlin_delivery/actual-prepare-inspect.json').read_text())[0]
        argv=json.loads((P/'fixtures/kotlin_delivery/actual-prepare-command.json').read_text())['argv'][1:]
        m.check_inspection(value,m.expected_inspection(argv,value['Id']))

if __name__=='__main__':unittest.main(verbosity=2)
