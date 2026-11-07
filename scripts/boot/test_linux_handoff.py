import hashlib,json,pathlib,shutil,sys,tempfile,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import current_source as c
import handoff_manifest as h
@unittest.skipUnless(sys.platform=='linux','Linux complete descriptor delivery controls; root runs separately')
class LinuxHandoff(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.base=pathlib.Path(self.temp.name);self.root=self.base/'delivery';self.root.mkdir();self.root.chmod(0o755)
  for path in h.ROOTS.values():(self.root/path).mkdir(parents=True,exist_ok=True)
  for p in self.root.rglob('*'):
   if p.is_dir():p.chmod(0o755)
  def write(path,raw):path.write_bytes(raw);path.chmod(0o644);return {'sha256':c.sha(raw),'bytes':len(raw),'mode':0o644}
  self.write=write;runtime=self.root/'runtime-pack';files={};origins={}
  for role in ('host','applications','probe','guest','launcher'):
   for suffix in ('source','receipt'):
    name=role+'-'+suffix+'.json';files[name]={**write(runtime/name,b'{}\n'),'origin':role}
   origins[role]={'kind':'source-build','sourceManifest':role+'-source.json','buildReceipt':role+'-receipt.json'}
  for role in ('kernel','runtimeTools'):
   name=role+'.json';files[name]={**write(runtime/name,b'{}\n'),'origin':role};origins[role]={'kind':'tool-image','imageId':'sha256:'+'1'*64,'receipt':name}
  manifest={'schemaVersion':1,'profile':'developer-workflow-v1','purpose':'developer-workflow-input-only','algorithm':'sha256','files':files,'directories':{},'origins':origins,'trust':{'purpose':'fixture-development-only','publisherKeyId':'mingos-dev-2026','releaseSigning':False},'formalQualification':False,'finalSystemAccepted':False};raw=c.encoded(manifest);write(runtime/'workflow-manifest.json',raw);self.runtime_pin=c.sha(raw)
  repo=self.base/'repo';repo.mkdir();write(repo/'LICENSE',b'fixture license\n');index={'LICENSE':{'mode':0o100644,'blob':'2'*40}};source_manifest=c.make_manifest(repo,'a'*40,index,[]);raw=c.encoded(source_manifest);source_dir=self.root/'current-source';write(source_dir/'source-inventory.json',raw);c.export(repo,raw,c.sha(raw),source_dir/'source.tar',source_dir/'source-descriptor.json');self.source_pin=c.sha((source_dir/'source-descriptor.json').read_bytes())
  license_dir=self.root/'licenses/source';license_dir.mkdir();license_dir.chmod(0o755);write(license_dir/'LICENSE',b'fixture license\n')
  for name in ('cache','images','updateEvidence','uninstallEvidence','receipts'):write(self.root/h.ROOTS[name]/'fixture.json',b'{}\n')
  value=h.make(self.root,self.runtime_pin,self.source_pin);raw=c.encoded(value);write(self.root/'handoff-manifest.json',raw);self.raw=raw;self.pin=c.sha(raw)
 def tearDown(self):self.temp.cleanup()
 def test_complete_sibling_delivery_verifies(self):self.assertEqual(h.verify(self.root,self.raw,self.pin)['status'],'complete-workflow-sibling-integrity-verified')
 def test_unlisted_outer_file_rejected(self):
  self.write(self.root/'surprise',b'not inventoried')
  with self.assertRaises(ValueError):h.verify(self.root,self.raw,self.pin)
 def test_changed_sibling_rejected(self):
  self.write(self.root/'rebuild-inputs/images/fixture.json',b'changed')
  with self.assertRaises(ValueError):h.verify(self.root,self.raw,self.pin)
if __name__=='__main__':unittest.main()
