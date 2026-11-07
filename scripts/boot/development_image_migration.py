#!/usr/bin/env python3
"""Separate fixed development OCI-index archive migration; default is read-only.
Historical qualification archive/load contracts remain unchanged.

Callers must keep all archive inputs immutable throughout verification and load;
retained file descriptors and boundary hashes detect changes but do not prevent
concurrent writers. Load receipts preserve ordinary error-path partial results.
They use file fsync and in-place rewrites, not an atomic power-loss journal.
"""
import argparse,base64,gzip,hashlib,importlib.util,json,os,stat,subprocess,tarfile
from contextlib import ExitStack
from pathlib import Path

# Reuse unchanged historical low-level envelope/JSON/path/hash helpers only.
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('historical_archive_helpers',HERE/'qualification_image_archive.py')
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
INDEX='application/vnd.oci.image.index.v1+json'
MANIFEST='application/vnd.oci.image.manifest.v1+json'
CONFIG='application/vnd.oci.image.config.v1+json'
LAYER='application/vnd.oci.image.layer.v1.tar'
LAYER_GZIP='application/vnd.oci.image.layer.v1.tar+gzip'
EMPTY='application/vnd.oci.empty.v1+json'
INTOTO='application/vnd.in-toto+json'
ATTEST='application/vnd.docker.attestation.manifest.v1+json'
SLSA='https://slsa.dev/provenance/v1'
FIXED={
 'rust':{'imageId':'sha256:88624fbbfcb6b7ac6f1207491053cbe6fd435262f0ce547443fb36760c7e9d80','file':'rust-88624fbbfcb6.tar','sha256':'cd3694137b219951f96a08aa18252d920c06a8f1a9e3cf81a75647d6214f4ce4','bytes':775440384},
 'tools':{'imageId':'sha256:a085efa9e8b4958b1943ee87857313f3d2666f696f730f242daef643347b0091','file':'tools-a085efa9e8b4.tar','sha256':'ce80c9b105991700f13abba0c2537ab72e8143e25ebf8159c78bbaa7e774b3c9','bytes':712949248},
 'kotlin':{'imageId':'sha256:5fba561826e826a26e5179d8b58425d0b5125d679e7dc64459d38bad0c6e417f','file':'kotlin-5fba561826e8.tar','sha256':'63cf8cc9e3e466a97e2da4bb1277a2872e70476b6311463e6773f07d667aa77d','bytes':241299968}}

def require(condition,message):
    if not condition:raise ValueError(message)
def exact(value,keys,message):require(isinstance(value,dict) and set(value)==set(keys),message)
def _verify_stream(source,expected_id,external_sha,external_size):
    """Private synthetic-testable core; public APIs accept fixed role names only."""
    require(isinstance(expected_id,str) and h.SHA256.fullmatch(expected_id),'exact index ID required')
    require(isinstance(external_sha,str) and h.HEX.fullmatch(external_sha) and type(external_size)is int and external_size>0,'external archive SHA/size required')
    before=h.archive_hash(source);require(before==(external_sha,external_size),'external archive SHA/size mismatch')
    used={'index.json','oci-layout','manifest.json'};edges=[]
    try:
        h._validate_envelope(source)
        with tarfile.open(fileobj=source,mode='r:') as tf:
            members={};hashes={}
            for member in tf:
                name=h.safe_name(member.name.rstrip('/') if member.isdir() else member.name)
                require(name not in members,'duplicate tar member')
                require(member.type in (tarfile.REGTYPE,tarfile.AREGTYPE,tarfile.DIRTYPE) and not member.pax_headers and member.sparse is None,'unsafe rawtar member')
                require(0<=member.size<=h.MAX_LAYER,'tar member byte limit')
                require(member.isdir() or name in used or (name.startswith('blobs/sha256/') and h.HEX.fullmatch(name[13:])),'unsupported archive member')
                members[name]=member
                require(len(members)<=4096,'tar member limit')
            for name,member in members.items():
                if member.isfile() and name.startswith('blobs/sha256/'):
                    with tf.extractfile(member) as stream:raw_digest,size=h.stream_hash(stream,h.MAX_LAYER)
                    require(name=='blobs/sha256/'+raw_digest and size==member.size,'blob filename digest mismatch')
                    hashes[name]={'sha256':raw_digest,'bytes':size}
            def metadata(name):
                require(name in members and members[name].isfile(),'required metadata missing')
                return h._metadata(tf,members[name])[1]
            def desc(value,types,*,inline=False):
                require(isinstance(value,dict),'descriptor must be an object')
                clean=dict(value)
                if inline:
                    require(clean.get('data')=='e30=','strict empty config inline bytes required')
                    raw=base64.b64decode(clean.pop('data'),validate=True);require(raw==b'{}','empty config data mismatch')
                require(set(clean)<= {'mediaType','digest','size','annotations','platform'},'unsupported descriptor child/reference')
                name=h._descriptor(clean,tf,members,types,used)
                if inline:
                    with tf.extractfile(members[name]) as stream:require(stream.read()==raw,'inline descriptor differs from blob')
                edges.append({'name':name,'mediaType':clean['mediaType'],'digest':clean['digest'],'bytes':clean['size']})
                return name
            exact(metadata('oci-layout'),['imageLayoutVersion'],'OCI layout fields differ');require(metadata('oci-layout')=={'imageLayoutVersion':'1.0.0'},'OCI layout version differs')
            wrapper=metadata('index.json');exact(wrapper,['schemaVersion','mediaType','manifests'],'export index wrapper schema differs')
            require(type(wrapper['schemaVersion'])is int and wrapper['schemaVersion']==2 and wrapper['mediaType']==INDEX and isinstance(wrapper['manifests'],list) and len(wrapper['manifests'])==1,'one exact root index descriptor required')
            root_descriptor=wrapper['manifests'][0];exact(root_descriptor,['mediaType','digest','size'],'root index descriptor fields differ')
            require(root_descriptor['digest']==expected_id,'export root does not bind requested index ID')
            root_name=desc(root_descriptor,{INDEX});require(root_name=='blobs/sha256/'+expected_id[7:],'root index identity differs')
            root=metadata(root_name);exact(root,['schemaVersion','mediaType','manifests'],'requested ID must identify exact OCI index')
            require(type(root['schemaVersion'])is int and root['schemaVersion']==2 and root['mediaType']==INDEX and isinstance(root['manifests'],list) and len(root['manifests'])==2,'one runnable and one attestation index child required')
            runnable=[];attestations=[]
            for child in root['manifests']:
                require(isinstance(child,dict),'invalid root child')
                if child.get('platform')=={'architecture':'amd64','os':'linux'}:
                    exact(child,['mediaType','digest','size','platform'],'runnable descriptor fields differ');runnable.append(child)
                elif child.get('platform')=={'architecture':'unknown','os':'unknown'}:
                    exact(child,['mediaType','digest','size','platform','annotations'],'unknown child is not strict attestation');attestations.append(child)
                else:raise ValueError('unsupported platform/index child')
            require(len(runnable)==len(attestations)==1,'unique linux amd64 runnable and strict attestation required')
            runnable=runnable[0];attestation=attestations[0]
            manifest_name=desc(runnable,{MANIFEST});image=metadata(manifest_name)
            exact(image,['schemaVersion','mediaType','config','layers'],'runnable manifest fields differ')
            require(type(image['schemaVersion'])is int and image['schemaVersion']==2 and image['mediaType']==MANIFEST and isinstance(image['layers'],list) and 0<len(image['layers'])<=256,'invalid runnable manifest')
            exact(image['config'],['mediaType','digest','size'],'config descriptor fields differ')
            config_name=desc(image['config'],{CONFIG});config=metadata(config_name)
            require(isinstance(config,dict) and config.get('architecture')=='amd64' and config.get('os')=='linux','runnable config platform differs')
            rootfs=config.get('rootfs');exact(rootfs,['type','diff_ids'],'config rootfs schema differs')
            require(rootfs['type']=='layers' and isinstance(rootfs['diff_ids'],list) and len(rootfs['diff_ids'])==len(image['layers']) and all(isinstance(x,str) and h.SHA256.fullmatch(x) for x in rootfs['diff_ids']),'ordered rootfs diffID contract differs')
            layers=[];layer_reports=[]
            for descriptor,want_diff in zip(image['layers'],rootfs['diff_ids']):
                exact(descriptor,['mediaType','digest','size'],'layer descriptor fields differ')
                name=desc(descriptor,{LAYER,LAYER_GZIP});layers.append(name)
                with tf.extractfile(members[name]) as stream:
                    if descriptor['mediaType']==LAYER_GZIP:
                        with gzip.GzipFile(fileobj=stream) as expanded:actual,expanded_size=h.stream_hash(expanded,h.MAX_LAYER)
                    else:actual,expanded_size=h.stream_hash(stream,h.MAX_LAYER)
                require('sha256:'+actual==want_diff,'ordered layer diffID mismatch')
                layer_reports.append({'path':name,'digest':descriptor['digest'],'bytes':descriptor['size'],'diffId':want_diff,'expandedBytes':expanded_size})
            record=metadata('manifest.json');require(isinstance(record,list) and len(record)==1,'single Docker manifest required');record=record[0]
            exact(record,['Config','RepoTags','Layers'],'Docker manifest fields differ')
            require(record['RepoTags'] is None,'RepoTags must be null; tag overwrite forbidden')
            require(record['Config']==config_name and record['Layers']==layers,'Docker manifest and OCI runnable config/layer order differ')
            require(attestation['annotations']=={'vnd.docker.reference.digest':runnable['digest'],'vnd.docker.reference.type':'attestation-manifest'},'unknown child lacks exact attestation binding')
            attestation_name=desc(attestation,{MANIFEST});att=metadata(attestation_name)
            exact(att,['schemaVersion','mediaType','artifactType','config','layers','subject'],'unsupported attestation child/reference')
            require(type(att['schemaVersion'])is int and att['schemaVersion']==2 and att['mediaType']==MANIFEST and att['artifactType']==ATTEST,'attestation manifest identity differs')
            subject={k:runnable[k] for k in ('mediaType','digest','size')};require(att['subject']==subject,'attestation subject differs from runnable')
            desc(att['subject'],{MANIFEST})
            exact(att['config'],['mediaType','digest','size','data'],'empty attestation config descriptor differs');desc(att['config'],{EMPTY},inline=True)
            require(isinstance(att['layers'],list) and len(att['layers'])==1,'one bound in-toto attestation layer required')
            proof=att['layers'][0];exact(proof,['mediaType','digest','size','annotations'],'attestation payload descriptor fields differ')
            require(proof['annotations']=={'in-toto.io/predicate-type':SLSA},'unknown attestation predicate descriptor')
            proof_name=desc(proof,{INTOTO});statement=metadata(proof_name)
            exact(statement,['_type','predicateType','subject','predicate'],'unknown in-toto statement child')
            require(statement['_type']=='https://in-toto.io/Statement/v1' and statement['predicateType']==SLSA and isinstance(statement['predicate'],dict),'unsupported attestation statement')
            subjects=statement['subject'];require(isinstance(subjects,list) and len(subjects)==1,'one statement subject required');exact(subjects[0],['name','digest'],'statement subject fields differ')
            require(isinstance(subjects[0]['name'],str) and bool(subjects[0]['name']) and subjects[0]['digest']=={'sha256':runnable['digest'][7:]},'statement subject digest differs')
            for name,member in members.items():
                if member.isdir():require(any(x.startswith(name+'/') for x in used),'unreferenced archive directory')
                else:require(name in used,'unreachable extra archive blob/payload')
            result={'imageId':expected_id,'archiveSha256':before[0],'archiveBytes':before[1],'identityKind':'oci-index','rootIndex':root_name,'runnableManifestDigest':runnable['digest'],'configDigest':image['config']['digest'],'configObject':config,'layers':layer_reports,'diffIds':rootfs['diff_ids'],'descriptorEdges':edges,'allBlobHashes':hashes,'memberCount':len(members),'attestationManifestDigest':attestation['digest'],'attestationBytesAndSubjectVerified':True,'attestationAuthenticityVerified':False,'RepoTags':None,'historicalAssetsRestored':False,'formalQualification':False,'finalSystemAccepted':False,'guestStarted':False}
    except (tarfile.TarError,OSError,EOFError) as error:raise ValueError('invalid raw Docker/OCI archive: '+str(error)) from error
    require(h.archive_hash(source)==before,'archive changed during verification')
    return result

def _open_one(role,root,stack):
    require(role in FIXED,'fixed rust/tools/kotlin role required');entry=FIXED[role]
    root=Path(root).absolute();path=h.safe_asset(root,entry['file']);stream=stack.enter_context(path.open('rb'))
    before=os.fstat(stream.fileno());require(stat.S_ISREG(before.st_mode) and before.st_nlink==1,'single-link regular archive required')
    result=_verify_stream(stream,entry['imageId'],entry['sha256'],entry['bytes']);result.update(role=role,archive=str(path),sourceStamp=_source_stamp(stream))
    after=os.fstat(stream.fileno());require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'archive metadata changed')
    return stream,result

def verify_one(role,root):
    require(role in FIXED,'fixed rust/tools/kotlin role required')
    with ExitStack() as stack:return _open_one(role,root,stack)[1]
def _prepare_all(root,stack):return [_open_one(role,root,stack) for role in FIXED]
def verify_all(root):
    with ExitStack() as stack:
        images=[value for _,value in _prepare_all(root,stack)]
        return {'status':'development-toolchain-archives-readonly-verified','images':images,'loadExecuted':False,'historicalAssetsRestored':False,'formalQualification':False,'finalSystemAccepted':False,'guestStarted':False}

def _source_stamp(source):
    value=os.fstat(source.fileno())
    return {'dev':value.st_dev,'ino':value.st_ino,'bytes':value.st_size,'mtimeNs':value.st_mtime_ns,'ctimeNs':value.st_ctime_ns}

def _check_sealed_source(source,value):
    require(_source_stamp(source)==value['sourceStamp'],'sealed archive file stamp changed before load')
    require(h.archive_hash(source)==(value['archiveSha256'],value['archiveBytes']),'sealed archive hash/size changed before load')
    require(_source_stamp(source)==value['sourceStamp'],'sealed archive stamp changed while hashing')

def _exclusive_result(path,stack):
    require(path is not None,'explicit new result sink required before load')
    path=Path(path)
    require('..' not in path.parts,'result path traversal forbidden');path=path.absolute()
    for parent in (*reversed(path.parent.parents),path.parent):
        info=parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&0x400,'result parent must be a real directory')
    fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    sink=stack.enter_context(os.fdopen(fd,'w+',encoding='utf8',newline='\n'))
    info=os.fstat(sink.fileno());require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'exclusive regular result sink required')
    return sink

def load_all(root,docker='docker',runner=None,result_path=None):
    """Load immutable caller inputs with error-path receipts and per-fd admission checks.

    Result updates are fsynced file writes, not atomic across power loss.
    """
    require(isinstance(docker,str) and bool(docker),'Docker executable required')
    receipt={'schemaVersion':1,'status':'validating','images':[],'operations':[],'loadExecuted':False,'loadAttempted':False,'completedLoads':[],'verifiedInspections':[],'historicalAssetsRestored':False,'formalQualification':False,'finalSystemAccepted':False,'guestStarted':False}
    invoke=runner or h._docker_run;phase,key='result-sink',None
    with ExitStack() as stack:
        # Exclusive sink creation and an fsynced initial file write precede any load.
        sink=_exclusive_result(result_path,stack)
        def persist():
            sink.seek(0);sink.truncate();json.dump(receipt,sink,indent=2);sink.write('\n');sink.flush();os.fsync(sink.fileno())
        persist()
        try:
            phase='all-archive-verification';prepared=_prepare_all(root,stack)
            receipt['images']=[value for _,value in prepared]
            phase='all-archive-load-admission'
            for source,value in prepared:
                key=value['role'];_check_sealed_source(source,value)
            for source,value in prepared:
                key=value['role'];phase='per-archive-load-admission'
                # Earlier Docker operations may take minutes: recheck THIS fd now.
                _check_sealed_source(source,value)
                phase='load';receipt['status']='loading';receipt['loadAttempted']=True;persist()
                source.seek(0)
                try:
                    result,_=invoke(receipt,key+'-load',[docker,'image','load','--quiet'],1800,stdin=source)
                    receipt['loadExecuted']=True
                except Exception:
                    # The reused capture helper translates TimeoutExpired to ValueError.
                    if receipt['operations'] and receipt['operations'][-1].get('key')==key+'-load' and (receipt['operations'][-1].get('timedOut') or receipt['operations'][-1].get('exitCode') is not None):receipt['loadExecuted']=True
                    raise
                require(result.returncode==0,'Docker load failed')
                receipt['completedLoads'].append(key);persist()
                _check_sealed_source(source,value)
                phase='inspect';persist()
                result,_=invoke(receipt,key+'-inspect',[docker,'image','inspect',value['imageId']],60)
                require(result.returncode==0,'Docker inspect failed after load')
                values=h.strict_json(result.stdout);require(isinstance(values,list) and len(values)==1,'one loaded image inspection required');loaded=values[0]
                require(loaded.get('Id')==value['imageId'] and loaded.get('Descriptor',{}).get('digest')==value['imageId'] and loaded.get('Descriptor',{}).get('mediaType')==INDEX,'loaded index ID not preserved')
                require(loaded.get('Architecture')=='amd64' and loaded.get('Os')=='linux' and loaded.get('RootFS')=={'Type':'layers','Layers':value['diffIds']},'loaded runnable platform/rootfs differs')
                require(loaded.get('Config')==value['configObject'].get('config'),'loaded runtime config differs')
                receipt['verifiedInspections'].append(key);persist()
            receipt['status']='development-toolchain-load-inspect-verified';persist();return receipt
        except Exception as error:
            receipt['status']='failed';receipt['failure']={'phase':phase,'key':key,'error':repr(error)}
            receipt['partialLoadPossible']=receipt['loadAttempted'];persist()
            raise h.RestoreError(str(error),receipt) from error

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive-root',type=Path,required=True);parser.add_argument('--result',type=Path,required=True);parser.add_argument('--load',action='store_true',help='explicitly load AFTER all three archives verify');parser.add_argument('--docker',default='docker');args=parser.parse_args()
    if args.load:
        result=load_all(args.archive_root,args.docker,result_path=args.result);print(result['status']);return
    require(not args.result.exists(),'new result file required')
    try:result=verify_all(args.archive_root)
    except Exception as error:
        with args.result.open('x',encoding='utf8') as stream:json.dump({'status':'failed','error':repr(error),'loadRequested':args.load,'formalQualification':False,'finalSystemAccepted':False},stream,indent=2)
        raise
    with args.result.open('x',encoding='utf8') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(result['status'])
if __name__=='__main__':main()
