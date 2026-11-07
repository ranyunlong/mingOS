import gzip,hashlib,importlib.util,io,json,tarfile,tempfile,unittest,subprocess,sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
P=Path(__file__).parent
spec=importlib.util.spec_from_file_location('migration',P/'development_image_migration.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def rawjson(value):return json.dumps(value,separators=(',',':')).encode()
def fixture(change=None):
    blobs={}
    def blob(raw,media,**kw):
        digest=hashlib.sha256(raw).hexdigest();blobs['blobs/sha256/'+digest]=raw
        return {'mediaType':media,'digest':'sha256:'+digest,'size':len(raw),**kw}
    layers=[];diff=[]
    for value in (b'first-layer',b'second-layer'):
        diff.append('sha256:'+hashlib.sha256(value).hexdigest());layers.append(blob(gzip.compress(value,mtime=0),m.LAYER_GZIP))
    config=blob(rawjson({'architecture':'amd64','os':'linux','rootfs':{'type':'layers','diff_ids':diff},'config':{}}),m.CONFIG)
    image={'schemaVersion':2,'mediaType':m.MANIFEST,'config':config,'layers':layers}
    if change=='layer-order':image['layers']=list(reversed(layers))
    manifest=blob(rawjson(image),m.MANIFEST)
    empty=blob(b'{}',m.EMPTY,data='e30=')
    if change=='embedded-empty':empty['data']='e10='
    statement={'_type':'https://in-toto.io/Statement/v1','predicateType':m.SLSA,'subject':[{'name':'pkg:docker/test','digest':{'sha256':manifest['digest'][7:]}}],'predicate':{}}
    attest_layer=blob(rawjson(statement),m.INTOTO,annotations={'in-toto.io/predicate-type':m.SLSA})
    attest={'schemaVersion':2,'mediaType':m.MANIFEST,'artifactType':m.ATTEST,'config':empty,'layers':[attest_layer],'subject':manifest}
    if change=='unknown-attestation-key':attest['unknownChild']={'digest':'sha256:'+'0'*64}
    att=blob(rawjson(attest),m.MANIFEST,platform={'architecture':'unknown','os':'unknown'},annotations={'vnd.docker.reference.digest':manifest['digest'],'vnd.docker.reference.type':'attestation-manifest'})
    runnable={**manifest,'platform':{'architecture':'amd64','os':'linux'}}
    if change=='unknown-index-child':att.pop('annotations')
    index={'schemaVersion':2,'mediaType':m.INDEX,'manifests':[runnable,att]}
    if change=='third-child':index['manifests'].append(att)
    root=blob(rawjson(index),m.INDEX)
    wrapper={'schemaVersion':2,'mediaType':m.INDEX,'manifests':[root]}
    if change=='config-as-index':wrapper['manifests']=[config]
    record={'Config':'blobs/sha256/'+config['digest'][7:],'RepoTags':None,'Layers':['blobs/sha256/'+x['digest'][7:] for x in image['layers']]}
    if change=='index-as-config':record['Config']='blobs/sha256/'+root['digest'][7:]
    if change=='tags':record['RepoTags']=['overwrite:latest']
    files={'oci-layout':rawjson({'imageLayoutVersion':'1.0.0'}),'index.json':rawjson(wrapper),'manifest.json':rawjson([record]),**blobs}
    if change=='extra':files['blobs/sha256/'+hashlib.sha256(b'extra').hexdigest()]=b'extra'
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as tf:
        for name,raw in files.items():
            info=tarfile.TarInfo(name);info.size=len(raw);tf.addfile(info,io.BytesIO(raw))
    return stream.getvalue(),root['digest'],config['digest']

class ArchiveControls(unittest.TestCase):
    def test_real_cli_help_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,'-I','-B',str(P.resolve()/'development_image_migration.py'),'--help'],cwd=directory,capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--archive-root',result.stdout)
        self.assertIn('--load',result.stdout)

    def check(self,raw,index):return m._verify_stream(io.BytesIO(raw),index,hashlib.sha256(raw).hexdigest(),len(raw))
    def test_valid_index_graph_and_ordered_diffids(self):
        raw,index,config=fixture();v=self.check(raw,index);self.assertEqual(v['imageId'],index);self.assertEqual(v['configDigest'],config);self.assertEqual(len(v['layers']),2);self.assertFalse(v['attestationAuthenticityVerified'])
    def test_config_id_cannot_replace_index_identity(self):
        raw,index,config=fixture()
        with self.assertRaises(ValueError):self.check(raw,config)
    def test_config_index_substitution_and_unknown_attestation_rejected(self):
        for mutation in ('config-as-index','index-as-config','unknown-attestation-key','unknown-index-child','third-child'):
            with self.subTest(mutation=mutation):
                raw,index,_=fixture(mutation)
                with self.assertRaises(ValueError):self.check(raw,index)
    def test_extra_truncated_tags_and_embedded_data_rejected(self):
        for mutation in ('extra','tags','embedded-empty'):
            with self.subTest(mutation=mutation):
                raw,index,_=fixture(mutation)
                with self.assertRaises(ValueError):self.check(raw,index)
        raw,index,_=fixture()
        with self.assertRaises(ValueError):self.check(raw[:len(raw)//2],index)
    def test_ordered_layer_diffids_rejected(self):
        raw,index,_=fixture('layer-order')
        with self.assertRaisesRegex(ValueError,'diff'):self.check(raw,index)
    def test_external_hash_and_size_bind_entire_envelope(self):
        raw,index,_=fixture()
        for digest,size in (('0'*64,len(raw)),(hashlib.sha256(raw).hexdigest(),len(raw)+1)):
            with self.assertRaises(ValueError):m._verify_stream(io.BytesIO(raw),index,digest,size)
    def test_public_role_api_is_fixed(self):
        with self.assertRaises(ValueError):m.verify_one('arbitrary',Path('unused'))
    def test_load_does_not_start_before_all_verification(self):
        from unittest.mock import patch
        calls=[]
        with patch.object(m,'_prepare_all',side_effect=ValueError('bad last archive')):
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError,'bad last'):m.load_all(Path('.'),runner=lambda *a,**k:calls.append(a),result_path=Path(tmp)/'receipt.json')
        self.assertEqual(calls,[])


class LoadControls(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.sink=self.root/'result.json';self.streams=[];self.prepared=[];self.calls=[]
        for role in m.FIXED:
            stream=tempfile.TemporaryFile();self.addCleanup(stream.close);raw=(role+' original bytes').encode();stream.write(raw);stream.flush();stream.seek(0)
            value={'role':role,'imageId':m.FIXED[role]['imageId'],'archiveSha256':hashlib.sha256(raw).hexdigest(),'archiveBytes':len(raw),'diffIds':['sha256:'+'1'*64],'configObject':{'config':{'Env':['A=B']}},'sourceStamp':self.stamp(stream)}
            self.prepared.append((stream,value));self.streams.append(stream)
    def stamp(self,s):
        x=__import__('os').fstat(s.fileno());return {'dev':x.st_dev,'ino':x.st_ino,'bytes':x.st_size,'mtimeNs':x.st_mtime_ns,'ctimeNs':x.st_ctime_ns}
    def runner(self,receipt,key,command,timeout,**kwargs):
        self.calls.append(command);operation={'key':key,'command':command,'exitCode':0,'stdout':'load output','stderr':'diagnostic','timedOut':False};receipt['operations'].append(operation)
        if command[1:3]==['image','load']:return SimpleNamespace(returncode=0,stdout=b'loaded',stderr=b''),operation
        role=key.removesuffix('-inspect');v=next(x for _,x in self.prepared if x['role']==role)
        loaded={'Id':v['imageId'],'Descriptor':{'digest':v['imageId'],'mediaType':m.INDEX},'Architecture':'amd64','Os':'linux','RootFS':{'Type':'layers','Layers':v['diffIds']},'Config':v['configObject']['config']}
        return SimpleNamespace(returncode=0,stdout=json.dumps([loaded]).encode(),stderr=b''),operation
    def run_load(self,runner=None):
        with patch.object(m,'_prepare_all',return_value=self.prepared):return m.load_all(self.root,runner=runner or self.runner,result_path=self.sink)
    def test_success_fake_load_only_after_sink_and_all_verified(self):
        def runner(*args,**kwargs):
            self.assertTrue(self.sink.is_file());self.assertIn(json.loads(self.sink.read_text())['status'],('loading','validating'));return self.runner(*args,**kwargs)
        result=self.run_load(runner);self.assertEqual(result['status'],'development-toolchain-load-inspect-verified');self.assertEqual(len(self.calls),6);self.assertEqual(json.loads(self.sink.read_text())['status'],result['status'])
    def test_existing_result_sink_blocks_before_first_load(self):
        self.sink.write_text('preserve')
        with self.assertRaises((ValueError,FileExistsError)):self.run_load()
        self.assertEqual(self.calls,[]);self.assertEqual(self.sink.read_text(),'preserve')
    def test_second_archive_changed_during_first_load_stops_next_boundary(self):
        def runner(receipt,key,*args,**kwargs):
            result=self.runner(receipt,key,*args,**kwargs)
            if key=='rust-load':
                stream=self.streams[1];stream.seek(0);stream.write(b'changed');stream.flush()
            return result
        with self.assertRaises(m.h.RestoreError) as caught:self.run_load(runner)
        self.assertEqual(len([c for c in self.calls if c[1:3]==['image','load']]),1)
        self.assertTrue(caught.exception.receipt['loadAttempted']);self.assertEqual(caught.exception.receipt['failure']['key'],'tools');self.assertEqual(json.loads(self.sink.read_text())['status'],'failed')
    def test_nonzero_inspect_identity_and_timeout_keep_partial_receipt(self):
        for failure in ('nonzero','identity','timeout'):
            with self.subTest(failure=failure):
                self.sink=self.root/(failure+'.json');self.calls=[]
                def runner(receipt,key,command,timeout,**kwargs):
                    value,operation=self.runner(receipt,key,command,timeout,**kwargs)
                    if key=='rust-inspect':
                        if failure=='nonzero':operation['exitCode']=9;value.returncode=9
                        elif failure=='identity':value.stdout=b'[{"Id":"foreign"}]'
                        else:operation.update(timedOut=True,stdout='partial out',stderr='partial err');raise subprocess.TimeoutExpired(command,timeout,output=b'partial out',stderr=b'partial err')
                    return value,operation
                with self.assertRaises(m.h.RestoreError) as caught:self.run_load(runner)
                receipt=caught.exception.receipt;self.assertTrue(receipt['loadAttempted']);self.assertEqual(receipt['failure']['phase'],'inspect');self.assertEqual(len(receipt['operations']),2)
                self.assertEqual(json.loads(self.sink.read_text())['operations'],receipt['operations'])
                if failure=='timeout':self.assertEqual(receipt['operations'][-1]['stderr'],'partial err')

    def test_nonzero_first_load_retains_operation_without_completion(self):
        def runner(receipt,key,command,timeout,**kwargs):
            value,operation=self.runner(receipt,key,command,timeout,**kwargs)
            value.returncode=5;operation['exitCode']=5
            return value,operation
        with self.assertRaises(m.h.RestoreError) as caught:self.run_load(runner)
        receipt=caught.exception.receipt
        self.assertTrue(receipt['loadExecuted']);self.assertTrue(receipt['partialLoadPossible'])
        self.assertEqual(receipt['completedLoads'],[]);self.assertEqual(len(receipt['operations']),1)
        self.assertEqual(receipt['failure']['phase'],'load')
        self.assertEqual(json.loads(self.sink.read_text()),receipt)

    def test_capture_helper_launch_oserror_retains_unexecuted_operation(self):
        with patch.object(m,'_prepare_all',return_value=self.prepared),patch.object(m.h.subprocess,'run',side_effect=FileNotFoundError('synthetic missing Docker executable')):
            with self.assertRaises(m.h.RestoreError) as caught:m.load_all(self.root,result_path=self.sink)
        receipt=caught.exception.receipt
        self.assertFalse(receipt['loadExecuted']);self.assertTrue(receipt['loadAttempted'])
        self.assertEqual(receipt['completedLoads'],[]);self.assertEqual(len(receipt['operations']),1)
        self.assertIn('error',receipt['operations'][0])
        self.assertEqual(json.loads(self.sink.read_text()),receipt)

    def test_real_capture_helper_load_timeout_marks_partial_execution(self):
        error=subprocess.TimeoutExpired(['docker','image','load'],1800,output=b'partial real helper out',stderr=b'partial real helper err')
        with patch.object(m,'_prepare_all',return_value=self.prepared),patch.object(m.h.subprocess,'run',side_effect=error):
            with self.assertRaises(m.h.RestoreError) as caught:m.load_all(self.root,result_path=self.sink)
        receipt=caught.exception.receipt;self.assertTrue(receipt['loadExecuted']);self.assertTrue(receipt['partialLoadPossible']);self.assertEqual(receipt['operations'][0]['stdout'],'partial real helper out');self.assertTrue(receipt['operations'][0]['timedOut'])

if __name__=='__main__':unittest.main(verbosity=2)
