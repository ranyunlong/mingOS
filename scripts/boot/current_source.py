"""Current Git source integrity transport. No cold-build identity or execution proof.

Standalone companion strict_ustar.py is the only dependency. Verify is portable;
exclusive descriptor-based restore requires Linux and never calls extractall.
"""
import argparse,hashlib,io,json,os,pathlib,re,stat,subprocess,sys,tarfile
import strict_ustar as strict
FLAGS={'compilerExecuted':False,'sourceBuildReplayed':False,'guestBooted':False,
       'publisherAuthenticated':False,'formalQualification':False,'finalSystemAccepted':False}
MAX_SOURCE=16*1024**3;MAX_SOURCE_FILE=2*1024**3;MAX_FILES=50000
MANIFEST_KIND='mingos-current-git-source-inventory-v1';ARCHIVE_KIND='mingos-current-git-source-alias-ustar-v1'
HEX40=re.compile(r'[0-9a-f]{40}\Z');MAP='source-map.json'
def require(ok,message):
 if not ok:raise ValueError('current source: '+message)
def encoded(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)+'\n').encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def digest(value):require(isinstance(value,str) and strict.HEX.fullmatch(value),'lowercase SHA256 required');return value
def relative(value):
 require(isinstance(value,str) and value and len(value.encode('utf-8'))<=4095 and '\\' not in value and ':' not in value and not any(ord(c)<32 or ord(c)==127 for c in value),'portable relative path required')
 for p in value.split('/'):
  require(p not in ('','.','..') and len(p.encode('utf-8'))<=255 and p==p.rstrip(' .') and p.casefold()!='.git','unsafe path component')
  require(not re.fullmatch(r'(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?',p,re.I),'Windows device name forbidden')
 return value
def license_path(name):
 p=pathlib.PurePosixPath(name);return bool(re.match(r'^(license|licence|copying|notice|copyright)([._-]|$)',p.name,re.I) or any(part.casefold() in ('licenses','licences') for part in p.parts[:-1]))
def closure(names):return sorted({str(p) for name in names for p in pathlib.PurePosixPath(name).parents if str(p)!='.'})
def validate_manifest(value):
 require(type(value) is dict and set(value)=={'schemaVersion','kind','repositoryCommit','algorithm','modePolicy','gitIndex','explicitPublicFiles','files','directories','licenses','workingTreeMayDifferFromCommit',*FLAGS},'closed current source manifest required')
 require(type(value['schemaVersion']) is int and value['schemaVersion']==1 and value['kind']==MANIFEST_KIND and value['algorithm']=='sha256','source manifest version differs')
 require(isinstance(value['repositoryCommit'],str) and HEX40.fullmatch(value['repositoryCommit']),'externally recorded SHA1 Git commit required')
 require(value['modePolicy']=='git-index-mode-and-explicit-extra-0644' and value['workingTreeMayDifferFromCommit'] is True and all(value[k] is False for k in FLAGS),'unearned source/build claim or mode policy')
 files,index,extras=value['files'],value['gitIndex'],value['explicitPublicFiles']
 require(type(files) is dict and 1<=len(files)<=MAX_FILES and type(index) is dict and index and type(extras) is list and extras==sorted(set(extras)),'bounded sorted source inventories required')
 require(set(index).isdisjoint(extras) and set(files)==set(index)|set(extras),'complete tracked/explicit public partition required')
 require(value['directories']==closure(files) and value['licenses']==sorted(n for n in files if license_path(n)),'complete directory/license inventories required')
 names=set(files)|set(value['directories']);require(len({n.casefold() for n in names})==len(names),'portable case collision')
 require(not set(files)&set(value['directories']),'file/directory collision')
 total=0
 for name in names:relative(name)
 for name,row in index.items():
  require(type(row) is dict and set(row)=={'mode','blob'} and type(row['mode']) is int and row['mode'] in (0o100644,0o100755) and isinstance(row['blob'],str) and HEX40.fullmatch(row['blob']),'stage-zero regular Git index entry required')
 for number,(name,row) in enumerate(sorted(files.items()),1):
  require(type(row) is dict and set(row)=={'sha256','bytes','mode','selection','alias'},'closed source record required');digest(row['sha256'])
  require(type(row['bytes']) is int and 0<=row['bytes']<=MAX_SOURCE_FILE,'source file bound exceeded');total+=row['bytes']
  require(row['alias']==f'f/{number:06d}' and type(row['mode']) is int and row['mode']==(stat.S_IMODE(index[name]['mode']) if name in index else 0o644) and row['selection']==('git-index' if name in index else 'explicit-public'),'source alias/mode/selection differs')
 require(total<=MAX_SOURCE,'complete source byte bound exceeded');return value
def no_links(path):
 for p in reversed((path,*path.parents)):
  info=p.lstat();require(not stat.S_ISLNK(info.st_mode) and not (getattr(info,'st_file_attributes',0)&0x400),'symlink/reparse point forbidden')
def stamp(info):
 # Windows lstat ctime may report birth time while fstat reports change time.
 # Compare stable identity/bytes/mtime plus birthtime on Windows; Linux uses ctime.
 return (info.st_dev,info.st_ino,info.st_mode,info.st_nlink,info.st_size,info.st_mtime_ns,
         getattr(info,'st_birthtime_ns',None) if sys.platform=='win32' else info.st_ctime_ns)
def cross_api_stamp(info):
 # Windows path stat synthesizes execute bits from .bat/.cmd/.exe/.com suffixes;
 # fd stat has no filename. Normalize only those bits across APIs. Same-API
 # path and fd checks below still compare every mode bit through stamp().
 value=stamp(info)
 return value[:2]+(value[2]&~0o111,)+value[3:] if sys.platform=='win32' else value

def file_record(root,name,expected):
 path=root/name;no_links(path);before=path.lstat();require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=MAX_SOURCE_FILE,'bounded single-link regular source required')
 fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0));h=hashlib.sha256();size=0
 try:
  opened=os.fstat(fd);require(cross_api_stamp(opened)==cross_api_stamp(before),'source replaced before open')
  while True:
   raw=os.read(fd,1024**2)
   if not raw:break
   size+=len(raw);require(size<=MAX_SOURCE_FILE,'source grew beyond limit');h.update(raw)
  after=os.fstat(fd);require(stamp(after)==stamp(opened) and after.st_ctime_ns==opened.st_ctime_ns,'source changed while reading')
 finally:os.close(fd)
 no_links(path);require(stamp(path.lstat())==stamp(before),'source changed after close')
 return {**expected,'sha256':h.hexdigest(),'bytes':size}
def source_inventory(root,expected):return {name:file_record(pathlib.Path(root),name,row) for name,row in sorted(expected.items())}
def make_manifest(root,commit,index,extras):
 require(type(index) is dict and type(extras) is list and len(extras)==len(set(extras)) and set(index).isdisjoint(extras),'tracked and explicit extras must be disjoint')
 names=set(index)|set(extras)
 for name in names:relative(name)
 require(len({n.casefold() for n in names|set(closure(names))})==len(names|set(closure(names))),'portable case collision')
 value={'schemaVersion':1,'kind':MANIFEST_KIND,'repositoryCommit':commit,'algorithm':'sha256','modePolicy':'git-index-mode-and-explicit-extra-0644','gitIndex':dict(sorted(index.items())),'explicitPublicFiles':sorted(extras),'files':{},'directories':closure(names),'licenses':sorted(n for n in names if license_path(n)),'workingTreeMayDifferFromCommit':True,**FLAGS}
 for number,name in enumerate(sorted(names),1):
  row={'mode':stat.S_IMODE(index[name]['mode']) if name in index else 0o644,'selection':'git-index' if name in index else 'explicit-public','alias':f'f/{number:06d}'}
  value['files'][name]=file_record(pathlib.Path(root),name,row)
 return validate_manifest(value)
def plan(repo,commit,extras):
 repo=pathlib.Path(repo).resolve();require(HEX40.fullmatch(commit),'exact external Git commit required')
 def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
 require(git('rev-parse','HEAD').decode().strip()==commit,'external commit differs from current HEAD')
 index={};index_raw=git('ls-files','--stage','-z')
 public_untracked=git('ls-files','--others','--exclude-standard','-z','--','scripts','crates','docs','probes','assets','apps','packages')
 omitted={name.decode('utf-8') for name in public_untracked.split(b'\0') if name}-set(extras)
 require(not omitted,'explicitly list all untracked public files with --extra: '+', '.join(sorted(omitted)))
 for raw in index_raw.split(b'\0'):
  if not raw:continue
  header,name=raw.split(b'\t',1);mode,blob,stage=header.decode('ascii').split();name=name.decode('utf-8')
  require(stage=='0' and name not in index,'conflicted/duplicate Git index entry');index[name]={'mode':int(mode,8),'blob':blob}
 value=make_manifest(repo,commit,index,extras)
 require(git('rev-parse','HEAD').decode().strip()==commit and git('ls-files','--stage','-z')==index_raw,'HEAD/index changed during source plan');return value
def exclusive(path,mode=0o644):
 path=pathlib.Path(path);require(path.is_absolute() and '..' not in path.parts,'absolute exclusive output required');no_links(path.parent)
 return os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),mode),'wb')
def export(root,manifest_raw,manifest_pin,archive,descriptor):
 root=pathlib.Path(root).resolve();archive=pathlib.Path(archive).absolute();descriptor=pathlib.Path(descriptor).absolute()
 require(sha(manifest_raw)==digest(manifest_pin),'external source manifest pin differs');value=validate_manifest(strict.strict_json(manifest_raw));require(manifest_raw==encoded(value),'canonical source manifest bytes required')
 require(archive!=descriptor and all(path!=root/name for path in (archive,descriptor) for name in value['files']),'output/source overlap')
 require(not os.path.lexists(archive) and not os.path.lexists(descriptor),'fresh absent outputs required')
 require(source_inventory(root,value['files'])==value['files'],'source differs from admitted manifest')
 entries=[{'path':'f','type':'directory','mode':0o755,'uid':65534,'gid':65534},{'path':MAP,'type':'file','mode':0o644,'uid':65534,'gid':65534,'size':len(manifest_raw),'sha256':manifest_pin,'nlink':1}]
 entries += [{'path':r['alias'],'type':'file','mode':r['mode'],'uid':65534,'gid':65534,'size':r['bytes'],'sha256':r['sha256'],'nlink':1} for _,r in sorted(value['files'].items())]
 transport_raw=encoded({'schemaVersion':1,'uid':65534,'gid':65534,'entries':entries});strict.validate_manifest(strict.strict_json(transport_raw))
 def member(name,mode,size=0,directory=False):
  info=tarfile.TarInfo(name);info.uid=info.gid=65534;info.mode=mode;info.size=size;info.mtime=0;info.type=tarfile.DIRTYPE if directory else tarfile.REGTYPE;return info
 with exclusive(archive) as output:
  with tarfile.open(fileobj=output,mode='w',format=tarfile.USTAR_FORMAT) as tar:
   tar.addfile(member(strict.MANIFEST_NAME,0o644,len(transport_raw)),io.BytesIO(transport_raw));tar.addfile(member('f',0o755,directory=True));tar.addfile(member(MAP,0o644,len(manifest_raw)),io.BytesIO(manifest_raw))
   for name,row in sorted(value['files'].items()):
    no_links(root/name)
    with (root/name).open('rb') as source:tar.addfile(member(row['alias'],row['mode'],row['bytes']),source)
  output.flush();os.fsync(output.fileno())
 require(source_inventory(root,value['files'])==value['files'],'source changed after archive copy')
 with archive.open('rb') as incoming:archive_hash,archive_size=strict._hash(incoming)
 desc={'schemaVersion':1,'kind':ARCHIVE_KIND,'repositoryCommit':value['repositoryCommit'],'inventoryManifestSha256':manifest_pin,'archiveSha256':archive_hash,'archiveBytes':archive_size,'transportManifestSha256':sha(transport_raw),'fileCount':len(value['files']),'directoryCount':len(value['directories']),'sourceBytes':sum(r['bytes'] for r in value['files'].values()),**FLAGS}
 raw=encoded(desc)
 with archive.open('rb') as incoming:verify(incoming,raw,sha(raw))
 with exclusive(descriptor) as output:output.write(raw);output.flush();os.fsync(output.fileno())
 return {'descriptorSha256':sha(raw),'archiveSha256':archive_hash,**FLAGS}
def verify(source,descriptor_raw,descriptor_pin):
 require(sha(descriptor_raw)==digest(descriptor_pin),'external descriptor pin differs');d=strict.strict_json(descriptor_raw)
 require(type(d) is dict and set(d)=={'schemaVersion','kind','repositoryCommit','inventoryManifestSha256','archiveSha256','archiveBytes','transportManifestSha256','fileCount','directoryCount','sourceBytes',*FLAGS},'closed current source descriptor required')
 require(type(d['schemaVersion']) is int and d['schemaVersion']==1 and d['kind']==ARCHIVE_KIND and all(d[k] is False for k in FLAGS),'descriptor version/claims differ')
 for name in ('inventoryManifestSha256','archiveSha256','transportManifestSha256'):digest(d[name])
 require(all(type(d[k]) is int for k in ('archiveBytes','fileCount','directoryCount','sourceBytes')),'integer descriptor counts required')
 result=strict.verify_volume_archive(source,d['archiveSha256']);require(result['archiveBytes']==d['archiveBytes'] and result['manifestSha256']==d['transportManifestSha256'],'archive descriptor binding differs')
 entries={r['path']:r for r in result['entries']};mapped=entries.get(MAP);require(mapped and mapped['type']=='file' and mapped['mode']==0o644 and mapped['size']<=strict.MAX_MANIFEST and mapped['sha256']==d['inventoryManifestSha256'],'bounded source map binding required')
 source.seek(mapped['offset']);manifest_raw=strict._read_exact(source,mapped['size']);manifest=validate_manifest(strict.strict_json(manifest_raw));require(manifest_raw==encoded(manifest),'noncanonical manifest')
 require(d['repositoryCommit']==manifest['repositoryCommit'] and d['fileCount']==len(manifest['files']) and d['directoryCount']==len(manifest['directories']) and d['sourceBytes']==sum(r['bytes'] for r in manifest['files'].values()),'source descriptor inventory binding differs')
 require(set(entries)=={'f',MAP}|{r['alias'] for r in manifest['files'].values()},'alias transport inventory differs');require(entries['f']['type']=='directory' and entries['f']['mode']==0o755,'alias directory differs')
 for row in manifest['files'].values():
  item=entries[row['alias']];require(item['type']=='file' and item['size']==row['bytes'] and item['mode']==row['mode'] and item['sha256']==row['sha256'],'alias file binding differs')
 require(strict._hash(source)==(d['archiveSha256'],d['archiveBytes']),'archive changed during source verification')
 return {'descriptor':d,'manifest':manifest,'transport':result}
def open_directory(path):
 path=pathlib.Path(path);require(path.is_absolute() and '..' not in path.parts,'absolute destination required');fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 try:
  for part in path.parts[1:]:child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
  return fd
 except BaseException:os.close(fd);raise
def restore(source,descriptor_raw,descriptor_pin,destination):
 require(sys.platform=='linux','Linux descriptor/no-follow restore required');verified=verify(source,descriptor_raw,descriptor_pin);manifest=verified['manifest'];destination=pathlib.Path(destination)
 parent=open_directory(destination.parent)
 try:os.mkdir(destination.name,0o700,dir_fd=parent);root=os.open(destination.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
 finally:os.close(parent)
 def descend(parts):
  fd=os.dup(root)
  try:
   for part in parts:child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
   return fd
  except BaseException:os.close(fd);raise
 entries={r['path']:r for r in verified['transport']['entries']}
 try:
  for name in sorted(manifest['directories'],key=lambda n:(len(n.split('/')),n)):
   parts=name.split('/');fd=descend(parts[:-1])
   try:os.mkdir(parts[-1],0o755,dir_fd=fd);os.chmod(parts[-1],0o755,dir_fd=fd,follow_symlinks=False)
   finally:os.close(fd)
  for name,row in sorted(manifest['files'].items()):
   parts=name.split('/');parent=descend(parts[:-1])
   try:fd=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
   finally:os.close(parent)
   try:
    source.seek(entries[row['alias']]['offset']);remaining=row['bytes'];h=hashlib.sha256()
    while remaining:
     raw=strict._read_exact(source,min(remaining,1024**2));remaining-=len(raw);h.update(raw)
     while raw:n=os.write(fd,raw);require(n>0,'restore write made no progress');raw=raw[n:]
    require(h.hexdigest()==row['sha256'],'restored source bytes differ');os.fchmod(fd,row['mode']);os.fsync(fd)
   finally:os.close(fd)
  require(source_inventory(destination,manifest['files'])==manifest['files'],'restored source hash inventory differs')
  require(all(stat.S_IMODE((destination/name).lstat().st_mode)==row['mode'] for name,row in manifest['files'].items()) and all(stat.S_IMODE((destination/name).lstat().st_mode)==0o755 for name in manifest['directories']),'restored normalized modes differ')
  require({p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}==set(manifest['files']) and {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_dir()}==set(manifest['directories']),'complete restored inventory differs')
  require(strict._hash(source)==(verified['descriptor']['archiveSha256'],verified['descriptor']['archiveBytes']),'archive changed during restore');os.fsync(root)
 finally:os.close(root)
 return {'status':'current-source-exclusively-restored-and-verified','fileCount':len(manifest['files']),'repositoryCommit':manifest['repositoryCommit'],**FLAGS}
def main():
 parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
 p=sub.add_parser('plan');p.add_argument('--repo',type=pathlib.Path,required=True);p.add_argument('--commit',required=True);p.add_argument('--extra',action='append',default=[]);p.add_argument('--output',type=pathlib.Path,required=True)
 p=sub.add_parser('export');p.add_argument('--repo',type=pathlib.Path,required=True);p.add_argument('--manifest',type=pathlib.Path,required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--archive',type=pathlib.Path,required=True);p.add_argument('--descriptor',type=pathlib.Path,required=True)
 for action in ('verify','restore'):
  p=sub.add_parser(action);p.add_argument('--archive',type=pathlib.Path,required=True);p.add_argument('--descriptor',type=pathlib.Path,required=True);p.add_argument('--descriptor-sha256',required=True)
  if action=='restore':p.add_argument('--destination',type=pathlib.Path,required=True)
 args=parser.parse_args()
 if args.action=='plan':
  value=plan(args.repo,args.commit,args.extra);raw=encoded(value)
  with exclusive(args.output.absolute()) as output:output.write(raw);output.flush();os.fsync(output.fileno())
  print(json.dumps({'manifestSha256':sha(raw),'fileCount':len(value['files']),'licenseFileCount':len(value['licenses']),**FLAGS}));return
 if args.action=='export':result=export(args.repo,args.manifest.read_bytes(),args.manifest_sha256,args.archive,args.descriptor)
 else:
  with args.archive.open('rb') as source:
   result=restore(source,args.descriptor.read_bytes(),args.descriptor_sha256,args.destination) if args.action=='restore' else {'status':'current-source-archive-bytes-verified','descriptor':verify(source,args.descriptor.read_bytes(),args.descriptor_sha256)['descriptor']}
 print(json.dumps(result,sort_keys=True))
if __name__=='__main__':main()
