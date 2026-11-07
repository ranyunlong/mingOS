import copy,pathlib,sys,unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import handoff_manifest as h
class Handoff(unittest.TestCase):
 def fixture(self):
  components={name:{'root':root,'files':{'evidence.json':{'sha256':'1'*64,'bytes':1,'mode':420}},'directories':[]} for name,root in h.ROOTS.items()}
  return {'schemaVersion':1,'kind':'mingos-workflow-sibling-handoff-integrity-v1','integrityOnly':True,'profile':'developer-workflow-v1','workflowManifestSha256':'2'*64,'currentSourceDescriptorSha256':'3'*64,'components':components,'runtimeToolsAreTransportOrigins':False,'requiresCompilerBootstrap':False,'transportMeaning':'integrity inventory only; recorded build and execution evidence retains its original scope',**h.FLAGS}
 def test_valid_separate_components(self):h.validate(self.fixture())
 def reject(self,fn):
  value=self.fixture();fn(value)
  with self.assertRaises(ValueError):h.validate(value)
 def test_missing_update_evidence_rejected(self):self.reject(lambda v:v['components'].pop('updateEvidence'))
 def test_missing_uninstall_evidence_rejected(self):self.reject(lambda v:v['components'].pop('uninstallEvidence'))
 def test_images_cannot_be_tools_origin(self):self.reject(lambda v:v['components']['images'].update(origin='runtimeTools'))
 def test_rebuild_cannot_move_into_runtime(self):self.reject(lambda v:v['components']['cache'].update(root='runtime-pack/cache'))
 def test_compiler_bootstrap_requirement_rejected(self):self.reject(lambda v:v.update(requiresCompilerBootstrap=True))
 def test_empty_component_rejected(self):self.reject(lambda v:v['components']['licenses'].update(files={}))
 def test_unearned_guest_claim_rejected(self):self.reject(lambda v:v.update(guestBooted=True))
 def test_tools_transport_origin_claim_rejected(self):self.reject(lambda v:v.update(runtimeToolsAreTransportOrigins=True))
if __name__=='__main__':unittest.main()
