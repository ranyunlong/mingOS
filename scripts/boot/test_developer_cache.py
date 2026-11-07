"""Synthetic/local controls. Fake daemon tests do not export/restore real caches."""
import copy,hashlib,importlib.util,io,json,os,stat,sys,tarfile,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import contextlib
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import developer_cache_worker as w
import developer_cache as host

class FakeEvidence:
    """Synthetic daemon DTOs only; no actual Docker or cache migration proof."""
    def __init__(self,fail=False,running=False,drift=False,missing=False):
        self.receipt={};self.calls=[];self.fail=fail;self.running=running;self.drift=drift;self.missing=missing;self.item=None
    def run(self,key,command,timeout,**kwargs):
        self.calls.append((key,command));args=command[1:]
        if args[:2]==['volume','inspect']:
            if self.missing:raise RuntimeError('volume missing')
            return json.dumps([{'Name':args[2],'Driver':'local','Labels':{}}]).encode()
        if args[0]=='create':
            one=lambda flag:args[args.index(flag)+1]
            requested=[];effective=[]
            for i,token in enumerate(args):
                if token!='--mount':continue
                fields={};flags=set()
                for field in args[i+1].split(','):
                    if '=' in field:k,v=field.split('=',1);fields[k]=v
                    else:flags.add(field)
                record={'Type':fields['type'],'Source':fields['source'],'Target':fields['target'],'ReadOnly':'readonly' in flags}
                if fields['type']=='volume':record['VolumeOptions']={'NoCopy':True,**({'Subpath':fields['volume-subpath']} if 'volume-subpath' in fields else {})}
                requested.append(record);effective.append({'Type':fields['type'],'Name':fields['source'],'Source':fields['source'],'Destination':fields['target'],'RW':'readonly' not in flags})
            cid='a'*64;position=args.index(host.IMAGE)
            self.item={'Id':cid,'Image':host.IMAGE,'Config':{'User':one('--user'),'Entrypoint':['python3'],'Cmd':args[position+1:],'Labels':dict(args[i+1].split('=',1) for i,x in enumerate(args) if x=='--label'),'Env':[]},'HostConfig':{'NetworkMode':'none','ReadonlyRootfs':True,'Privileged':False,'NanoCpus':2000000000,'Memory':4294967296,'PidsLimit':128,'CapDrop':['ALL'],'CapAdd':['CHOWN'] if '--cap-add' in args else [],'SecurityOpt':['no-new-privileges'],'AutoRemove':False,'RestartPolicy':{'Name':'no','MaximumRetryCount':0},'Mounts':requested},'Mounts':effective,'State':{'Status':'created','Running':False,'ExitCode':0,'OOMKilled':False}}
            return cid.encode()
        if args[0]=='inspect':
            item=copy.deepcopy(self.item)
            if self.drift:item['HostConfig']['NetworkMode']='bridge'
            return json.dumps([item]).encode()
        if args[0]=='start':
            self.item['State']={'Status':'running' if self.running else 'exited','Running':self.running,'ExitCode':7 if self.fail else 0,'OOMKilled':False}
            if self.fail:raise TimeoutError('synthetic timeout') if self.running else RuntimeError('synthetic exit7')
            return b'{"validationKind":"fake-control"}'
        if args[0]=='stop':self.item['State'].update(Status='exited',Running=False);return b''
        raise AssertionError(args)

class FakeDockerTests(unittest.TestCase):
    def engine(self,**options):
        evidence=FakeEvidence(**options);engine=host.Engine(evidence,'b'*32);engine.volumes['original']={};return engine,evidence
    def invoke(self,engine):return engine.container('control',[host.mount('volume','original','/input',True)],['-I','-B','-c','print(1)'])
    def test_synthetic_success_retains_pre_post_inspection(self):
        engine,e=self.engine();self.invoke(engine)
        self.assertIn('control-inspect-before',[k for k,_ in e.calls]);self.assertIn('control-inspect-after',[k for k,_ in e.calls])
        self.assertFalse(any(command[1] in ('rm','rmi','pull','build') for _,command in e.calls))
    def test_failed_start_always_inspected_and_no_next_stage(self):
        engine,e=self.engine(fail=True)
        with self.assertRaises(RuntimeError):self.invoke(engine)
        self.assertEqual(e.calls[-1][0],'control-inspect-after')
    def test_timeout_stops_only_owned_cid_and_reinspects(self):
        engine,e=self.engine(fail=True,running=True)
        with self.assertRaises(TimeoutError):self.invoke(engine)
        stops=[command for _,command in e.calls if command[1]=='stop']
        self.assertEqual(stops,[['docker','stop','--time','10','a'*64]])
        self.assertEqual(e.calls[-1][0],'control-inspect-after-stop')
    def test_inspection_drift_prevents_start(self):
        engine,e=self.engine(drift=True)
        with self.assertRaises(ValueError):self.invoke(engine)
        self.assertFalse(any(command[1]=='start' for _,command in e.calls))
    def test_missing_input_refused_before_create(self):
        engine,e=self.engine(missing=True)
        with self.assertRaises(RuntimeError):self.invoke(engine)
        self.assertFalse(any(command[1]=='create' for _,command in e.calls))
    def test_host_restore_pin_refused_without_docker_or_output(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);(root/'developer-inputs.json').write_text('{}')
            with self.assertRaises(ValueError):host.restore(root,'0'*64,root/'new-output',docker='must-not-execute')
            self.assertFalse((root/'new-output').exists())
    def test_original_creator_worker_wrapper_also_stops_owned_timeout(self):
        engine,e=self.engine(fail=True,running=True)
        with self.assertRaises(TimeoutError):self.invoke(engine)
        def validate(value):self.assertEqual(value[0]['Id'],'a'*64);self.assertEqual(value[0]['Image'],host.IMAGE)
        with self.assertRaises(TimeoutError):host.start_retained(engine,'a'*64,'existing',validate,['start','--attach','a'*64])
        self.assertEqual(e.calls[-1][0],'existing-inspect-after-stop')

class HostBoundaryTests(unittest.TestCase):
    def test_harness_drift_or_missing_refuses_before_create_and_after_run_drift_fails(self):
        for mode in ('changed','missing','after'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                directory=Path(temp);helper=directory/'worker.py';helper.write_bytes(b'print(1)\n')
                expected={'worker.py':dict(sha256=w.sha(helper),bytes=helper.stat().st_size)}
                calls=[]
                class Engine:
                    def container(self,*args,**kwargs):
                        calls.append(args);helper.write_bytes(b'print(2)\n');return 'cid',b'{}'
                if mode=='changed':helper.write_bytes(b'print(0)\n')
                if mode=='missing':helper.unlink()
                with self.assertRaises((ValueError,FileNotFoundError)):
                    host.run_harness(Engine(),directory,expected,'export',[],['fixed-worker'])
                self.assertEqual(len(calls),1 if mode=='after' else 0)
    def test_path_fd_comparison_excludes_only_windows_cross_ctime(self):
        before=(1,2,3,4,5,6,7)
        for platform in ('win32','linux'):
            with patch.object(host.sys,'platform',platform):
                self.assertTrue(host.same_path_fd_stamp(before,before))
                self.assertEqual(host.same_path_fd_stamp(before,(*before[:-1],8)),platform=='win32')
                for index in range(6):
                    changed=list(before);changed[index]+=1
                    self.assertFalse(host.same_path_fd_stamp(before,tuple(changed)))
    def test_windows_snapshot_preserves_path_and_fd_own_ctime_checks(self):
        path=(1,2,3,4,5,6,100);fd=(1,2,3,4,5,6,200)
        for change in ('path','fd'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                after_fd=(*fd[:-1],201) if change=='fd' else fd
                after_path=(*path[:-1],101) if change=='path' else path
                # The fd-change branch short-circuits before the final path read.
                with patch.object(host.sys,'platform','win32'),patch.object(host.content,'stamp',side_effect=[path,fd,after_fd,after_path]):
                    with self.assertRaisesRegex(ValueError,'helper source changed during copy'):
                        host.snapshot_helpers(Path(temp))
                self.assertEqual(list((Path(temp)/'harness').iterdir()),[])
    @unittest.skipUnless(sys.platform=='win32','requires actual Windows stat/fstat semantics')
    def test_windows_actual_snapshot_helpers_copy_without_docker(self):
        sources=[host.HERE/'developer_cache_worker.py',Path(host.archive.__file__),Path(host.worker.__file__)]
        expected={path.name:path.read_bytes() for path in sources}
        with tempfile.TemporaryDirectory() as temp,patch('subprocess.Popen',side_effect=AssertionError('Docker/process forbidden')):
            target,hashes=host.snapshot_helpers(Path(temp))
            self.assertEqual(set(hashes),set(expected))
            self.assertEqual({path.name:path.read_bytes() for path in target.iterdir()},expected)
            host.original._check_helpers(target,hashes)
    def test_snapshot_helpers_copies_checked_bytes_and_rejects_source_identity_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            target,hashes=host.snapshot_helpers(Path(temp))
            self.assertEqual(len(hashes),3)
            host.original._check_helpers(target,hashes)
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(host.content,'stamp',side_effect=[('before',),('before',),('changed',)]):
                with self.assertRaisesRegex(ValueError,'helper source changed during copy'):
                    host.snapshot_helpers(Path(temp))
            self.assertEqual(list((Path(temp)/'harness').iterdir()),[])
    def test_anonymous_workers_reinspect_missing_or_relabelled_volume_before_each_create(self):
        for stage in ('restore','verify'):
            for failure in ('missing','labels'):
                with self.subTest(stage=stage,failure=failure),tempfile.TemporaryDirectory() as temp:
                    output=Path(temp);private=output/'dependencies.tar';private.write_bytes(b'archive')
                    evidence=FakeEvidence();engine=host.Engine(evidence,'b'*32)
                    calls=[];volume='c'*64;cid='a'*64
                    def daemon(key,command,timeout,**kwargs):
                        calls.append(key)
                        if command[1:3]==['volume','inspect']:
                            if key==stage+'-volume-before-create':
                                if failure=='missing':raise RuntimeError('volume missing')
                                return json.dumps([dict(Name=volume,Driver='local',Labels={'changed':'yes'})]).encode()
                            return json.dumps([dict(Name=volume,Driver='local',Labels={})]).encode()
                        if key.endswith('-create'):return cid.encode()
                        return b'[]'
                    evidence.run=daemon
                    prepared=dict(archiveSha256='d'*64,archiveBytes=7)
                    with contextlib.ExitStack() as stack:
                        patches=[(host.content,'verify_export',lambda *a:({'archive':{'sha256':'d'*64}},prepared)),
                            (host.original,'private_copy_archive',lambda *a:(private,prepared)),
                            (host.original,'_freeze_helpers',lambda *a:(output,{})),
                            (host.original,'_check_helpers',lambda *a:None),
                            (host.original,'_one_inspection',lambda *a:{}),
                            (host.plan,'validate_creator_inspection',lambda *a:{'volumeName':volume}),
                            (host.original,'worker_command',lambda *a:['create']),
                            (host.original,'validate_worker_inspection',lambda *a:None),
                            (host,'start_retained',lambda *a,**k:b'{}'),
                            (host.archive,'_hash',lambda *a:('d'*64,7)),
                            (host.original,'_worker_result',lambda *a:{})]
                        for owner,name,value in patches:stack.enter_context(patch.object(owner,name,value))
                        with self.assertRaises((ValueError,RuntimeError)):
                            host.restore_to_anonymous(engine,output,'e'*64,output,{})
                    self.assertIn(stage+'-volume-before-create',calls)
                    self.assertNotIn(stage+'-create',calls)

class ProjectionTests(unittest.TestCase):
    def test_reversible_exact_mappings_and_bad_names(self):
        for role,path,want in [('cargo','src/a','c/src/a'),('gradle','caches/a','g/a'),('gradle','caches','g'),('gradle','wrapper/a','d/wrapper/a')]:
            self.assertEqual(w.physical(role,path),want)
            self.assertEqual(w.logical(want),(role,path))
        for path in ('../x','a//b','/root','a\\b','C:/x'):
            with self.assertRaises(ValueError):w.physical('cargo',path)
        with self.assertRaises(ValueError):w.logical('foreign/a')
        with self.assertRaises(ValueError):w.logical('d/caches/a')
    def test_only_explicit_cargo_640_is_normalized(self):
        self.assertEqual(w.target_mode('cargo','file',0o640),0o644)
        self.assertEqual(w.target_mode('gradle','file',0o600),0o600)
        for role,kind,mode in [('gradle','file',0o640),('cargo','file',0o777),('cargo','file',0o4644),('cargo','link',0o644)]:
            with self.assertRaises(ValueError):w.target_mode(role,kind,mode)
    def test_real_long_pom_identity_tar_fails_and_projection_succeeds(self):
        path='caches/modules-2/files-2.1/org.jetbrains.kotlin.plugin.compose/org.jetbrains.kotlin.plugin.compose.gradle.plugin/2.3.20/'+'6'*40+'/org.jetbrains.kotlin.plugin.compose.gradle.plugin-2.3.20.pom'
        with self.assertRaises(ValueError):tarfile.TarInfo(path).tobuf(tarfile.USTAR_FORMAT)
        tarfile.TarInfo(w.physical('gradle',path)).tobuf(tarfile.USTAR_FORMAT)

@unittest.skipUnless(sys.platform=='linux','real Unix file/mode controls require Linux')
class LocalRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.cargo=self.root/'cargo';self.gradle=self.root/'gradle'
        self.cargo.mkdir();self.gradle.mkdir()
        self.put(self.cargo/'src/crate/data',b'cargo',0o640)
        self.put(self.gradle/'caches/a.jar',b'gradle',0o644)
        self.put(self.gradle/'wrapper/gradle.zip',b'zip',0o600)
        (self.gradle/'.tmp').mkdir()
        self.baseline=self.root/'baseline.json'
        self.baseline.write_text(json.dumps({'schemaVersion':1,'files':w.file_map(w.scan(self.gradle))}))
    def put(self,path,raw,mode):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);path.chmod(mode)
    def export(self):
        out=self.root/'export';out.mkdir()
        descriptor=w.export_inputs(self.cargo,self.gradle,self.baseline,out)
        return out,descriptor
    def test_actual_small_strict_archive_and_unchanged_worker_roundtrip(self):
        out,descriptor=self.export();digest=w.sha(out/'developer-inputs.json')
        checked,plan=w.verify_export(out,digest)
        self.assertEqual(checked,descriptor)
        self.assertEqual(descriptor['normalizations'],[{'role':'cargo','path':'src/crate/data','originalMode':0o640,'targetMode':0o644}])
        self.assertEqual(stat.S_IMODE((self.cargo/'src/crate/data').stat().st_mode),0o640)
        restore=self.root/'restore';restore.mkdir(mode=0o700)
        for name in ('payload','control'):(restore/name).mkdir(mode=0o700)
        nonce='a'*32
        with (out/'dependencies.tar').open('rb') as source:receipt=w.original_worker.restore(restore,source,descriptor['archive']['sha256'],nonce)
        self.assertEqual(receipt['status'],'payload-restored')
        target_cargo=self.root/'new-cargo';target_gradle=self.root/'new-gradle'
        target_cargo.mkdir(mode=0o700);target_gradle.mkdir(mode=0o700)
        result=w.project_consumers(restore/'payload',target_cargo,target_gradle,descriptor)
        self.assertEqual(result['status'],'consumer-projection-verified')
        self.assertEqual({p.name for p in (target_cargo/'cargo').iterdir()},{'registry'})
        self.assertEqual(w.file_map(w.scan(target_gradle)),json.loads(self.baseline.read_text())['files'])
        self.assertEqual(stat.S_IMODE((target_cargo/'cargo/registry/src/crate/data').stat().st_mode),0o644)
        self.assertEqual(w.verify_consumers(target_cargo,target_gradle,descriptor)['status'],'consumer-readonly-content-verified')
    def full_baseline(self):
        inventory=w.scan(self.gradle)
        return dict(schemaVersion=1,files=w.file_map(inventory),
            directories=['']+sorted(name for name,e in inventory.items() if e['type']=='directory'),
            originalCacheUnchanged=True,compileExecuted=False,
            comparison={'opaqueProvenance':'never execute $(anything)'})
    def test_real_baseline_layout_preserved_and_reopened(self):
        baseline=self.full_baseline();raw=(json.dumps(baseline,indent=3)+'\n').encode()
        self.baseline.write_bytes(raw)
        out,d=self.export()
        self.assertEqual((out/'gradle-baseline.json').read_bytes(),raw)
        self.assertEqual(self.baseline.read_bytes(),raw)
        self.assertEqual(w.verify_export(out,w.sha(out/'developer-inputs.json'))[0],d)
    def test_baseline_closed_layout_strict_schema_flags_and_complete_directories(self):
        good=self.full_baseline()
        changes=[dict(schemaVersion=True),dict(files={}),dict(directories=good['directories'][1:]),
            dict(directories=good['directories']+['unlisted']),dict(directories=good['directories']+['']),
            dict(originalCacheUnchanged=False),dict(compileExecuted=True),dict(extra=True),
            dict(directories='caches')]
        for index,change in enumerate(changes):
            with self.subTest(change=change):
                self.baseline.write_text(json.dumps(dict(good,**change)))
                out=self.root/('bad-'+str(index));out.mkdir()
                with self.assertRaises(ValueError):w.export_inputs(self.cargo,self.gradle,self.baseline,out)
                self.assertEqual(list(out.iterdir()),[])
        self.baseline.write_text(json.dumps({'schemaVersion':True,'files':good['files']}))
        with self.assertRaises(ValueError):self.export()
    def test_reopen_rejects_byte_repinned_baseline_directory_or_flag_drift(self):
        self.baseline.write_text(json.dumps(self.full_baseline()));out,d=self.export()
        for change in (dict(directories=['']),dict(originalCacheUnchanged=False),dict(compileExecuted=True)):
            baseline=dict(self.full_baseline(),**change)
            (out/'gradle-baseline.json').write_text(json.dumps(baseline))
            altered=copy.deepcopy(d);altered['baseline'].update(sha256=w.sha(out/'gradle-baseline.json'),bytes=(out/'gradle-baseline.json').stat().st_size)
            (out/'developer-inputs.json').write_text(json.dumps(altered))
            with self.assertRaises(ValueError):w.verify_export(out,w.sha(out/'developer-inputs.json'))
    def test_baseline_mismatch_and_extra_file_refused(self):
        self.put(self.gradle/'added.jar',b'extra',0o644)
        with self.assertRaises(ValueError):self.export()
    def test_bad_external_pin_and_tampered_archive(self):
        out,d=self.export()
        with self.assertRaises(ValueError):w.verify_export(out,'0'*64)
        digest=w.sha(out/'developer-inputs.json')
        with (out/'dependencies.tar').open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(ValueError):w.verify_export(out,digest)
    def test_descriptor_closed_mapping_and_normalization(self):
        out,d=self.export()
        for mutate in (lambda v:v.update(extra=True),lambda v:v['mapping'].update(cargo='elsewhere'),lambda v:v.update(normalizations=[])):
            altered=copy.deepcopy(d);mutate(altered)
            (out/'developer-inputs.json').write_text(json.dumps(altered))
            with self.assertRaises(ValueError):w.verify_export(out,w.sha(out/'developer-inputs.json'))
    def test_links_and_mode_drift_refused(self):
        os.link(self.cargo/'src/crate/data',self.cargo/'src/crate/hardlink')
        with self.assertRaises(ValueError):w.scan(self.cargo)
    def test_nonempty_target_refused_before_any_copy(self):
        out,d=self.export();a=self.root/'a';b=self.root/'b';a.mkdir();b.mkdir();(b/'existing').write_text('x')
        with self.assertRaises(ValueError):w.project_consumers(self.root/'nonexistent',a,b,d)
        self.assertEqual(list(a.iterdir()),[])
    def test_descriptor_missing_directory_and_extra_payload_refused(self):
        out,d=self.export();a=self.root/'a';b=self.root/'b';a.mkdir();b.mkdir()
        with self.assertRaises(ValueError):w.project_consumers(out,a,b,d)
    def test_export_extra_file_and_descriptor_symlink_refused(self):
        out,d=self.export();pin=w.sha(out/'developer-inputs.json')
        (out/'extra').write_text('unlisted')
        with self.assertRaises(ValueError):w.verify_export(out,pin)
        (out/'extra').unlink() # Test scratch only; production has no deletion.
        saved=self.root/'saved-descriptor.json';(out/'developer-inputs.json').rename(saved)
        (out/'developer-inputs.json').symlink_to(saved)
        with self.assertRaises(ValueError):w.verify_export(out,pin)

if __name__=='__main__':unittest.main(verbosity=2)
