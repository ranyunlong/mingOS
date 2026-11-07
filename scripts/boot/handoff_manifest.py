"""Closed sibling delivery integrity inventory. No assembly, image load or build."""
import argparse,hashlib,json,os,pathlib,sys
import current_source as source
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parent))
import workflow_pack as runtime
FLAGS=source.FLAGS
ROOTS={'runtimePack':'runtime-pack','currentSource':'current-source','cache':'rebuild-inputs/cache','images':'rebuild-inputs/images','updateEvidence':'rebuild-inputs/evidence/update','uninstallEvidence':'rebuild-inputs/evidence/uninstall','licenses':'licenses','receipts':'receipts'}
MEANING='integrity inventory only; recorded build and execution evidence retains its original scope'
def validate(value):
 source.require(type(value) is dict and set(value)=={'schemaVersion','kind','integrityOnly','profile','workflowManifestSha256','currentSourceDescriptorSha256','components','runtimeToolsAreTransportOrigins','requiresCompilerBootstrap','transportMeaning',*FLAGS},'closed sibling handoff required')
 source.require(type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['kind']=='mingos-workflow-sibling-handoff-integrity-v1' and value['profile']=='developer-workflow-v1' and value['integrityOnly'] is True,'handoff kind/profile differs')
 source.require(value['requiresCompilerBootstrap'] is False and value['runtimeToolsAreTransportOrigins'] is False and value['transportMeaning']==MEANING and all(value[k] is False for k in FLAGS),'unearned compiler/origin/execution/acceptance claim')
 source.digest(value['workflowManifestSha256']);source.digest(value['currentSourceDescriptorSha256'])
 components=value['components'];source.require(type(components) is dict and set(components)==set(ROOTS),'all closed sibling components required')
 for name,root in ROOTS.items():
  component=components[name];source.require(type(component) is dict and set(component)=={'root','files','directories'} and component['root']==root,'component root/origin schema differs')
  files,dirs=component['files'],component['directories'];source.require(type(files) is dict and 1<=len(files)<=100000 and dirs==source.closure(files),'complete nonempty component inventory required')
  source.require(not set(files)&set(dirs) and len({p.casefold() for p in set(files)|set(dirs)})==len(set(files)|set(dirs)),'component type/case collision')
  total=0
  for path,row in files.items():
   source.relative(path);source.require(type(row) is dict and set(row)=={'sha256','bytes','mode'} and type(row['bytes']) is int and 0<=row['bytes']<=64*1024**3 and type(row['mode']) is int and row['mode'] in (0o600,0o644,0o700,0o755),'component regular file record differs');source.digest(row['sha256']);total+=row['bytes']
  source.require(total<=256*1024**3,'component byte bound exceeded')
 return value
def scan_component(path):
 source.require(sys.platform=='linux','Linux descriptor inventory required');fd=runtime.open_root(str(path))
 try:files,dirs,snapshots=runtime.inventory(fd);runtime.confirm_inventory(fd,snapshots)
 finally:os.close(fd)
 source.require(all(row['mode'] in (0o700,0o755) for row in dirs.values()),'component directory mode differs')
 return {'files':files,'directories':sorted(dirs)}
def make(root,workflow_pin,source_descriptor_pin):
 root=pathlib.Path(root).absolute();components={name:dict(root=path,**scan_component(root/path)) for name,path in ROOTS.items()}
 return validate({'schemaVersion':1,'kind':'mingos-workflow-sibling-handoff-integrity-v1','integrityOnly':True,'profile':'developer-workflow-v1','workflowManifestSha256':workflow_pin,'currentSourceDescriptorSha256':source_descriptor_pin,'components':components,'runtimeToolsAreTransportOrigins':False,'requiresCompilerBootstrap':False,'transportMeaning':MEANING,**FLAGS})
def verify(root,raw,pin):
 root=pathlib.Path(root).absolute();source.require(source.sha(raw)==source.digest(pin),'external handoff pin differs');value=validate(source.strict.strict_json(raw))
 source.require(sys.platform=='linux','Linux complete sibling verification required')
 for name,component in value['components'].items():source.require(scan_component(root/component['root'])=={k:component[k] for k in ('files','directories')},'complete sibling inventory differs: '+name)
 # Entire outer delivery has exactly the component files, parent directories and manifest.
 expected={component['root']+'/'+name:row for component in value['components'].values() for name,row in component['files'].items()}
 expected['handoff-manifest.json']={'sha256':pin,'bytes':len(raw),'mode':0o644};outer=scan_component(root)
 source.require(outer['files']==expected and outer['directories']==source.closure(expected),'unlisted outer delivery entry')
 verified_runtime=runtime.verify_pack(str(root/ROOTS['runtimePack']),value['workflowManifestSha256'])
 current=root/ROOTS['currentSource'];descriptor_raw=(current/'source-descriptor.json').read_bytes()
 with (current/'source.tar').open('rb') as archive:verified_source=source.verify(archive,descriptor_raw,value['currentSourceDescriptorSha256'])
 source.require(source.sha((current/'source-inventory.json').read_bytes())==verified_source['descriptor']['inventoryManifestSha256'],'standalone current source inventory differs')
 for name in verified_source['manifest']['licenses']:
  row=value['components']['licenses']['files'].get('source/'+name);expected=verified_source['manifest']['files'][name]
  source.require(row and all(row[k]==expected[k] for k in ('sha256','bytes')),'tracked source license omitted/changed: '+name)
 return {'status':'complete-workflow-sibling-integrity-verified','runtime':verified_runtime,'currentSourceDescriptor':verified_source['descriptor'],'components':{name:{'files':len(c['files']),'bytes':sum(r['bytes'] for r in c['files'].values())} for name,c in value['components'].items()},'integrityOnly':True,'requiresCompilerBootstrap':False,**FLAGS}
def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=('make','verify'));parser.add_argument('--root',type=pathlib.Path,required=True);parser.add_argument('--workflow-manifest-sha256');parser.add_argument('--source-descriptor-sha256');parser.add_argument('--handoff-sha256');args=parser.parse_args();path=args.root/'handoff-manifest.json'
 if args.action=='make':
  value=make(args.root,args.workflow_manifest_sha256,args.source_descriptor_sha256);raw=source.encoded(value)
  with source.exclusive(path.absolute()) as output:output.write(raw);output.flush();os.fchmod(output.fileno(),0o644);os.fsync(output.fileno())
  print(json.dumps({'handoffSha256':source.sha(raw),**FLAGS}))
 else:print(json.dumps(verify(args.root,path.read_bytes(),args.handoff_sha256),sort_keys=True))
if __name__=='__main__':main()
