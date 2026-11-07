import copy,hashlib,io,json,os,pathlib,sys,tarfile,tempfile,unittest
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import current_source as c
class CurrentSource(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)/'repo';self.root.mkdir();(self.root/'src').mkdir();(self.root/'src/main.py').write_bytes(b'print(1)\n');(self.root/'LICENSE').write_bytes(b'fixture license\n');(self.root/'src/new.py').write_bytes(b'# explicit public\n')
  self.index={'LICENSE':{'mode':33188,'blob':'1'*40},'src/main.py':{'mode':33261,'blob':'2'*40}}
  self.manifest=c.make_manifest(self.root,'a'*40,self.index,['src/new.py'])
 def tearDown(self):self.tmp.cleanup()
 def export(self):
  archive=pathlib.Path(self.tmp.name)/'source.tar';descriptor=pathlib.Path(self.tmp.name)/'descriptor.json';raw=c.encoded(self.manifest);c.export(self.root,raw,c.sha(raw),archive,descriptor);return archive,descriptor
 def test_full_current_tracked_and_explicit_extra(self):self.assertEqual(set(self.manifest['files']),{'LICENSE','src/main.py','src/new.py'})
 def test_license_inventory_complete(self):self.assertEqual(self.manifest['licenses'],['LICENSE'])
 def test_external_commit_is_recorded(self):self.assertEqual(self.manifest['repositoryCommit'],'a'*40)
 def git_mock(self,argv,**kwargs):
  args=argv[3:]
  if args==['rev-parse','HEAD']:raw=b'a'*40+b'\n'
  elif args[:2]==['ls-files','--stage']:raw=b''.join(('%o %s 0\t%s'%(row['mode'],row['blob'],name)).encode()+b'\0' for name,row in sorted(self.index.items()))
  else:raw=b'src/new.py\0'
  return SimpleNamespace(stdout=raw)
 def test_git_plan_rejects_omitted_explicit_public(self):
  with patch.object(c.subprocess,'run',side_effect=self.git_mock):
   with self.assertRaises(ValueError):c.plan(self.root,'a'*40,[])
 def test_git_plan_records_current_bytes_and_index(self):
  with patch.object(c.subprocess,'run',side_effect=self.git_mock):self.assertEqual(c.plan(self.root,'a'*40,['src/new.py']),self.manifest)
 def test_wrong_external_git_commit_rejected(self):
  with patch.object(c.subprocess,'run',side_effect=self.git_mock):
   with self.assertRaises(ValueError):c.plan(self.root,'b'*40,['src/new.py'])
 def test_index_symlink_mode_rejected(self):
  index=copy.deepcopy(self.index);index['src/main.py']['mode']=0o120000
  with self.assertRaises(ValueError):c.make_manifest(self.root,'a'*40,index,[])
 def test_complete_archive_verifies_outside_checkout(self):
  archive,descriptor=self.export();raw=descriptor.read_bytes()
  with archive.open('rb') as stream:result=c.verify(stream,raw,c.sha(raw))
  self.assertEqual(result['manifest'],self.manifest);self.assertFalse(result['descriptor']['sourceBuildReplayed'])
 def test_manifest_unknown_field_rejected(self):
  value=copy.deepcopy(self.manifest);value['coldFreezeSha256']='b'*64
  with self.assertRaises(ValueError):c.validate_manifest(value)
 def test_missing_license_inventory_rejected(self):
  value=copy.deepcopy(self.manifest);value['licenses']=[]
  with self.assertRaises(ValueError):c.validate_manifest(value)
 def test_false_build_claim_rejected(self):
  value=copy.deepcopy(self.manifest);value['sourceBuildReplayed']=True
  with self.assertRaises(ValueError):c.validate_manifest(value)
 def test_extra_cannot_overlap_tracked(self):
  with self.assertRaises(ValueError):c.make_manifest(self.root,'a'*40,self.index,['LICENSE'])
 def test_unlisted_public_file_not_silently_included(self):self.assertNotIn('unselected.py',self.manifest['files'])
 def test_changed_file_refuses_export(self):
  (self.root/'src/main.py').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.export()
 def test_wrong_external_manifest_pin(self):
  with self.assertRaises(ValueError):c.export(self.root,c.encoded(self.manifest),'f'*64,pathlib.Path(self.tmp.name)/'bad.tar',pathlib.Path(self.tmp.name)/'bad.json')
 def test_wrong_external_descriptor_pin(self):
  a,d=self.export()
  with a.open('rb') as stream:
   with self.assertRaises(ValueError):c.verify(stream,d.read_bytes(),'f'*64)
 def test_archive_tamper(self):
  a,d=self.export();raw=bytearray(a.read_bytes());raw[1536]^=1
  with self.assertRaises(ValueError):c.verify(io.BytesIO(raw),d.read_bytes(),c.sha(d.read_bytes()))
 def test_preexisting_output_preserved(self):
  a,d=self.export();before=a.read_bytes()
  with self.assertRaises((ValueError,FileExistsError)):c.export(self.root,c.encoded(self.manifest),c.sha(c.encoded(self.manifest)),a,pathlib.Path(self.tmp.name)/'new.json')
  self.assertEqual(a.read_bytes(),before)
 def test_directory_closure_rejected(self):
  value=copy.deepcopy(self.manifest);value['directories']=[]
  with self.assertRaises(ValueError):c.validate_manifest(value)
 def test_safe_portable_paths(self):
  for value in ('../x','/absolute','a\\b','a//b','.git/config','C:/x','a/../b','a\x00b','CON','a/NUL.txt','a/b.','a/b ','a:b'):
   with self.subTest(value=value),self.assertRaises(ValueError):c.relative(value)
 def test_casefold_collision(self):
  (self.root/'LICENSE2').write_bytes(b'x');index=copy.deepcopy(self.index);index['src/MAIN.py']={'mode':33188,'blob':'3'*40}
  with self.assertRaises(ValueError):c.make_manifest(self.root,'a'*40,index,[])
 def test_duplicate_json_keys(self):
  with self.assertRaises(ValueError):c.strict.strict_json(b'{"schemaVersion":1,"schemaVersion":1}')
 def forged(self,mutation):
  archive,descriptor=self.export();members=[]
  with tarfile.open(archive,'r:') as incoming:
   for item in incoming:
    stream=incoming.extractfile(item) if item.isfile() else None;members.append((item,stream.read() if stream else b''))
  mutation(members);out=io.BytesIO()
  with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT) as tar:
   for item,raw in members:tar.addfile(item,io.BytesIO(raw) if item.isfile() else None)
  raw=out.getvalue();d=json.loads(descriptor.read_bytes());d['archiveSha256']=c.sha(raw);d['archiveBytes']=len(raw);dr=c.encoded(d)
  with self.assertRaises(ValueError):c.verify(io.BytesIO(raw),dr,c.sha(dr))
 def test_hardlink_tar_rejected(self):
  def mutate(rows):rows[-1][0].type=tarfile.LNKTYPE;rows[-1][0].linkname='f/000001';rows[-1][0].size=0
  self.forged(mutate)
 def test_symlink_tar_rejected(self):
  def mutate(rows):rows[-1][0].type=tarfile.SYMTYPE;rows[-1][0].linkname='/etc/passwd';rows[-1][0].size=0
  self.forged(mutate)
 def test_duplicate_tar_member_rejected(self):self.forged(lambda rows:rows.append(copy.deepcopy(rows[-1])))
 def test_tar_member_wrong_mode_rejected(self):self.forged(lambda rows:setattr(rows[-1][0],'mode',0o777))
 def test_tar_member_wrong_owner_rejected(self):self.forged(lambda rows:setattr(rows[-1][0],'uid',0))
 def test_traversal_tar_member_rejected(self):self.forged(lambda rows:setattr(rows[-1][0],'name','../escape'))
 def test_absolute_tar_member_rejected(self):self.forged(lambda rows:setattr(rows[-1][0],'name','/escape'))
 def test_nonzero_archive_trailer_rejected(self):
  a,d=self.export();raw=a.read_bytes()+b'X'*512;value=json.loads(d.read_bytes());value['archiveSha256']=c.sha(raw);value['archiveBytes']=len(raw);dr=c.encoded(value)
  with self.assertRaises(ValueError):c.verify(io.BytesIO(raw),dr,c.sha(dr))
 def test_descriptor_cold_identity_rejected(self):
  a,d=self.export();value=json.loads(d.read_bytes());value['coldFreezeSha256']='b'*64;raw=c.encoded(value)
  with a.open('rb') as source:
   with self.assertRaises(ValueError):c.verify(source,raw,c.sha(raw))
 def test_descriptor_boolean_count_rejected(self):
  a,d=self.export();value=json.loads(d.read_bytes());value['fileCount']=True;raw=c.encoded(value)
  with a.open('rb') as source:
   with self.assertRaises(ValueError):c.verify(source,raw,c.sha(raw))
 @unittest.skipUnless(sys.platform=='linux','Linux descriptor restore control; root must run separately')
 def test_exclusive_restore_and_full_reinventory(self):
  a,d=self.export();out=pathlib.Path(self.tmp.name)/'outside-checkout';raw=d.read_bytes()
  with a.open('rb') as stream:c.restore(stream,raw,c.sha(raw),out)
  self.assertEqual((out/'src/main.py').read_bytes(),b'print(1)\n')
  self.assertEqual(c.source_inventory(out,self.manifest['files']),self.manifest['files'])
 @unittest.skipUnless(sys.platform=='linux','Linux descriptor restore control; root must run separately')
 def test_restore_rejects_existing_symlink_root(self):
  a,d=self.export();out=pathlib.Path(self.tmp.name)/'symlink';out.symlink_to(self.root,target_is_directory=True)
  with a.open('rb') as stream:
   with self.assertRaises((ValueError,FileExistsError)):c.restore(stream,d.read_bytes(),c.sha(d.read_bytes()),out)
if __name__=='__main__':unittest.main()
