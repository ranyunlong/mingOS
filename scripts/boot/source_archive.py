"""Ignored Linux source archive data layer; no Docker, compiler or guest calls.

USTAR stores short aliases only. Original paths are inert validated JSON data.
Original source ownership is recorded; consumer/transport ownership is 65534.
"""
import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tarfile

REPO = Path(os.environ.get('MINGOS_SOURCE_ARCHIVE_REPO', '/repo'))
sys.path.insert(0, str(REPO/'scripts/boot'))
import linux_host_qualification as q
import qualification_volume_archive as strict

MAP_NAME = 'source-map.json'
FALSE_FLAGS = {'compilerExecuted':False,'sourceBuildReplayed':False,
               'guestBooted':False,'formalQualification':False,'finalSystemAccepted':False}
DESCRIPTOR_KEYS = {'schemaVersion','kind','archiveSha256','archiveBytes',
    'transportManifestSha256','sourceMapSha256','sourceInventorySha256','coldFreezeSha256',
    'consumerUid','consumerGid'} | set(FALSE_FLAGS)

def require(condition, message):
    if not condition: raise ValueError(message)

def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)+'\n').encode()

def sha(raw): return hashlib.sha256(raw).hexdigest()

def digest(value):
    require(isinstance(value,str) and strict.HEX.fullmatch(value), 'exact lowercase SHA256 required')
    return value

def linux():
    require(sys.platform=='linux' and os.geteuid()==65534 and os.getegid()==65534,
            'Linux UID/GID65534 required')
    require(Path(q.__file__).parent==REPO/'scripts/boot' and Path(strict.__file__).parent==REPO/'scripts/boot',
            'trusted helper import roots differ')

def relative(value, directory=False, allow_generated=False):
    require(isinstance(value,str) and value and len(value.encode())<=4095 and '\\' not in value and ':' not in value,
            'canonical source path required')
    parts=value.split('/')
    require(all(p not in ('','.','..') and len(p.encode())<=255 for p in parts)
        and not any(ord(c)<32 or ord(c)==127 for c in value),'unsafe source path')
    require(allow_generated or not any(p in q.EXCLUDED for p in (parts if directory else parts[:-1])),
            'generated directory forbidden in source mapping')
    return value

def metadata(value, directory=False):
    require(isinstance(value,dict) and set(value)=={'mode','uid','gid'}, 'closed source metadata required')
    require(type(value['mode']) is int and value['mode'] in (strict.DIRECTORY_MODES if directory else strict.FILE_MODES),
            'source mode forbidden')
    require(all(type(value[k]) is int and 0<=value[k]<2**32 for k in ('uid','gid')), 'source owner invalid')

def scan(root, excluded=q.EXCLUDED):
    """Descriptor-only complete source inventory; excluded directories match q exactly."""
    fd=q.open_directory(Path(root)); files={}; directories={}; stamps={}
    root_stamp=q.stamp(os.fstat(fd))
    def meta(info): return {'mode':stat.S_IMODE(info.st_mode),'uid':info.st_uid,'gid':info.st_gid}
    root_meta=meta(os.fstat(fd));metadata(root_meta,True)
    def visit(parent,prefix):
        before=q.stamp(os.fstat(parent));names=sorted(os.listdir(parent))
        for name in names:
            info=os.stat(name,dir_fd=parent,follow_symlinks=False);path=prefix+name
            if stat.S_ISDIR(info.st_mode) and name in excluded: continue
            isdir=stat.S_ISDIR(info.st_mode);relative(path,isdir,allow_generated=True); row=meta(info);metadata(row,isdir);stamps[path]=q.stamp(info)
            if isdir:
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
                try:
                    require(q.stamp(os.fstat(child))==q.stamp(info),'source directory replaced')
                    directories[path]=row;visit(child,path+'/')
                finally:os.close(child)
            else: row.update(q.hash_at(parent,name,info));files[path]=row
            require(q.stamp(os.stat(name,dir_fd=parent,follow_symlinks=False))==q.stamp(info),'source entry changed')
        require(names==sorted(os.listdir(parent)) and before==q.stamp(os.fstat(parent)),'source directory changed')
    try:
        visit(fd,'')
        current=q.open_directory(Path(root))
        try:require(q.stamp(os.fstat(current))==root_stamp,'source root replaced')
        finally:os.close(current)
    finally:os.close(fd)
    return {'root':root_meta,'directories':dict(sorted(directories.items())),
            'files':dict(sorted(files.items()))},dict(stamps, __root__=root_stamp)

def inventory(mapping):
    return {row['path']:{'bytes':row['bytes'],'sha256':row['sha256']} for row in mapping['files']}

def validate_mapping(value):
    require(isinstance(value,dict) and set(value)=={'schemaVersion','coldFreezeSha256',
        'sourceInventorySha256','root','directories','files'} and type(value['schemaVersion']) is int
        and value['schemaVersion']==1,'closed source map required')
    digest(value['coldFreezeSha256']);digest(value['sourceInventorySha256']);metadata(value['root'],True)
    dirs=value['directories'];files=value['files'];require(isinstance(dirs,dict) and isinstance(files,list),'source map inventory required')
    require(0<len(files)<=strict.MAX_ENTRIES-3 and len(dirs)+len(files)<=strict.MAX_ENTRIES,'source entry limit')
    for path,row in dirs.items():relative(path,True);metadata(row,True)
    seen=set(dirs);aliases=set()
    for index,row in enumerate(files,1):
        require(isinstance(row,dict) and set(row)=={'path','alias','mode','uid','gid','bytes','sha256'},'closed source file mapping required')
        path=relative(row['path']);require(path not in seen,'duplicate source path or type collision');seen.add(path)
        require(row['alias']==f'f/{index:06d}' and row['alias'] not in aliases,'canonical unique alias required');aliases.add(row['alias'])
        metadata({k:row[k] for k in ('mode','uid','gid')});digest(row['sha256'])
        require(type(row['bytes']) is int and 0<=row['bytes']<=strict.MAX_FILE,'invalid source byte count')
    require([r['path'] for r in files]==sorted(r['path'] for r in files),'sorted source mappings required')
    for path in seen:
        parts=path.split('/')
        for index in range(1,len(parts)):require('/'.join(parts[:index]) in dirs,'complete explicit source directory closure required')
    require(sha(encoded(inventory(value)))==value['sourceInventorySha256'],'mapped source inventory digest differs')
    return value

@contextmanager
def exclusive(path):
    path=Path(path);require(path.is_absolute() and '..' not in path.parts,'absolute output required')
    parent=q.open_directory(path.parent)
    try:
        fd=os.open(path.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=parent)
        with os.fdopen(fd,'wb') as stream:
            yield stream
            stream.flush();os.fsync(stream.fileno())
        os.fsync(parent)
    finally:os.close(parent)

def export(source, freeze_raw, freeze_sha256, archive_path, descriptor_path):
    """Admit source by externally pinned raw cold freeze, export to absent files."""
    linux();source=Path(source);archive_path=Path(archive_path);descriptor_path=Path(descriptor_path)
    require(sha(freeze_raw)==digest(freeze_sha256),'cold freeze SHA differs')
    freeze=strict.strict_json(freeze_raw)
    require(isinstance(freeze,dict) and isinstance(freeze.get('source'),dict)
        and isinstance(freeze['source'].get('files'),dict),'cold source inventory required')
    require(os.statvfs(source).f_flag & os.ST_RDONLY,'read-only source mount required')
    before,identities=scan(source);expected=freeze['source']['files']
    require({path:{'bytes':row['bytes'],'sha256':row['sha256']} for path,row in before['files'].items()}==expected,
            'source differs from pinned cold inventory')
    require(q.source_inventory(source)==expected,'qualification source inventory differs')
    for target in (archive_path,descriptor_path):
        require(target.is_absolute() and '..' not in target.parts and target!=source and not target.is_relative_to(source),
                'output overlaps source or is noncanonical')
        parent=q.open_directory(target.parent);os.close(parent)
        require(not os.path.lexists(target),'absent export outputs required')
    require(archive_path!=descriptor_path,'distinct archive and descriptor required')
    mapping={'schemaVersion':1,'coldFreezeSha256':freeze_sha256,'sourceInventorySha256':sha(encoded(expected)),
        'root':before['root'],'directories':before['directories'],
        'files':[dict(row,path=path,alias=f'f/{index:06d}') for index,(path,row) in enumerate(before['files'].items(),1)]}
    validate_mapping(mapping);map_raw=encoded(mapping);require(len(map_raw)<=strict.MAX_MANIFEST,'bounded source map required')
    entries=[{'path':'f','type':'directory','mode':0o755,'uid':65534,'gid':65534},
        {'path':MAP_NAME,'type':'file','mode':0o644,'uid':65534,'gid':65534,'size':len(map_raw),'sha256':sha(map_raw),'nlink':1}]
    entries += [{'path':r['alias'],'type':'file','mode':r['mode'],'uid':65534,'gid':65534,'size':r['bytes'],'sha256':r['sha256'],'nlink':1} for r in mapping['files']]
    transport_raw=encoded({'schemaVersion':1,'uid':65534,'gid':65534,'entries':entries})
    strict.validate_manifest(strict.strict_json(transport_raw))
    def member(name,mode,size=0,directory=False):
        item=tarfile.TarInfo(name);item.uid=item.gid=65534;item.mode=mode;item.size=size;item.mtime=0
        item.type=tarfile.DIRTYPE if directory else tarfile.REGTYPE;return item
    with exclusive(archive_path) as output:
        with tarfile.open(fileobj=output,mode='w',format=tarfile.USTAR_FORMAT) as tar:
            tar.addfile(member(strict.MANIFEST_NAME,0o644,len(transport_raw)),io.BytesIO(transport_raw))
            tar.addfile(member('f',0o755,directory=True));tar.addfile(member(MAP_NAME,0o644,len(map_raw)),io.BytesIO(map_raw))
            for row in mapping['files']:
                path=source/row['path'];parent=q.open_directory(path.parent)
                try:
                    fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
                    with os.fdopen(fd,'rb') as incoming:
                        require(q.stamp(os.fstat(incoming.fileno()))==identities[row['path']],'source replaced before export')
                        tar.addfile(member(row['alias'],row['mode'],row['bytes']),incoming)
                        require(q.stamp(os.fstat(incoming.fileno()))==identities[row['path']],'source changed during export')
                finally:os.close(parent)
    require(scan(source)==(before,identities) and q.source_inventory(source)==expected,'source changed after export')
    archive_hash=q.stable_hash(archive_path)
    descriptor=dict(schemaVersion=1,kind='mingos-source-alias-ustar-v1',archiveSha256=archive_hash['sha256'],
        archiveBytes=archive_hash['bytes'],transportManifestSha256=sha(transport_raw),sourceMapSha256=sha(map_raw),
        sourceInventorySha256=mapping['sourceInventorySha256'],coldFreezeSha256=freeze_sha256,
        consumerUid=65534,consumerGid=65534,**FALSE_FLAGS)
    descriptor_raw=encoded(descriptor)
    with archive_path.open('rb') as incoming:verify(incoming,descriptor_raw,sha(descriptor_raw))
    with exclusive(descriptor_path) as output:output.write(descriptor_raw)
    return dict(descriptorSha256=sha(descriptor_raw),resourcesPreserved=True,**FALSE_FLAGS)

def verify(source, descriptor_raw, descriptor_sha256):
    """Pure validation of already-opened archive and externally pinned descriptor."""
    require(sha(descriptor_raw)==digest(descriptor_sha256),'external descriptor SHA differs')
    descriptor=strict.strict_json(descriptor_raw)
    require(isinstance(descriptor,dict) and set(descriptor)==DESCRIPTOR_KEYS,'closed descriptor required')
    require(type(descriptor['schemaVersion']) is int and descriptor['schemaVersion']==1
        and descriptor['kind']=='mingos-source-alias-ustar-v1','source descriptor version differs')
    require(all(descriptor[k] is False for k in FALSE_FLAGS),'unearned source build/acceptance flags forbidden')
    require(type(descriptor['consumerUid']) is int and descriptor['consumerUid']==65534
        and type(descriptor['consumerGid']) is int and descriptor['consumerGid']==65534,'consumer owner differs')
    for key in ('archiveSha256','transportManifestSha256','sourceMapSha256','sourceInventorySha256','coldFreezeSha256'):digest(descriptor[key])
    require(type(descriptor['archiveBytes']) is int and 0<descriptor['archiveBytes']<=strict.MAX_ARCHIVE,'bounded archive required')
    plan=strict.verify_volume_archive(source,descriptor['archiveSha256'])
    require(plan['archiveBytes']==descriptor['archiveBytes'] and plan['manifestSha256']==descriptor['transportManifestSha256'],
            'descriptor transport binding differs')
    entries={r['path']:r for r in plan['entries']};mapped=entries.get(MAP_NAME)
    require(mapped is not None and mapped['type']=='file' and mapped['mode']==0o644
        and mapped['size']<=strict.MAX_MANIFEST and mapped['sha256']==descriptor['sourceMapSha256'],'bounded pinned source map required')
    source.seek(mapped['offset']);mapping=validate_mapping(strict.strict_json(strict._read_exact(source,mapped['size'])))
    require(all(mapping[k]==descriptor[k] for k in ('coldFreezeSha256','sourceInventorySha256')),'source provenance binding differs')
    require(set(entries)=={'f',MAP_NAME}|{r['alias'] for r in mapping['files']},'alias transport inventory differs')
    require(entries['f']['type']=='directory' and entries['f']['mode']==0o755,'alias root metadata differs')
    for row in mapping['files']:
        alias=entries[row['alias']]
        require(alias['type']=='file' and alias['mode']==row['mode'] and alias['size']==row['bytes']
            and alias['sha256']==row['sha256'],'mapped alias type/mode/bytes differ')
    require(strict._hash(source)==(descriptor['archiveSha256'],descriptor['archiveBytes']),'archive changed during mapping validation')
    return {'descriptor':descriptor,'mapping':mapping,'transport':plan}

def restore(source, descriptor_raw, descriptor_sha256, destination):
    """Fresh temporary/consumer directory projection; partial output is retained."""
    linux();plan=verify(source,descriptor_raw,descriptor_sha256);mapping=plan['mapping']
    destination=Path(destination);require(destination.is_absolute() and '..' not in destination.parts,'absolute fresh destination required')
    parent=q.open_directory(destination.parent)
    root=None
    try:
        require(not os.path.lexists(destination),'absent consumer directory required')
        os.mkdir(destination.name,0o700,dir_fd=parent)
        root=os.open(destination.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        identity=q.stamp(os.fstat(root))[:2]
        dirs=mapping['directories']
        for name in sorted(dirs,key=lambda n:(n.count('/'),n)):
            path=destination/name;parentfd=q.open_directory(path.parent)
            try:os.mkdir(path.name,0o700,dir_fd=parentfd);os.fsync(parentfd)
            finally:os.close(parentfd)
        aliases={r['path']:r for r in plan['transport']['entries']}
        for row in mapping['files']:
            with exclusive(destination/row['path']) as output:
                source.seek(aliases[row['alias']]['offset']);left=row['bytes'];hashed=hashlib.sha256()
                while left:
                    raw=strict._read_exact(source,min(left,1024**2));output.write(raw);hashed.update(raw);left-=len(raw)
                require(hashed.hexdigest()==row['sha256'],'source archive changed during projection')
                os.fchmod(output.fileno(),row['mode'])
        for name in sorted(dirs,key=lambda n:(-n.count('/'),n)):
            fd=q.open_directory(destination/name)
            try:os.fchmod(fd,dirs[name]['mode']);os.fsync(fd)
            finally:os.close(fd)
        os.fchmod(root,mapping['root']['mode']);os.fsync(root);os.fsync(parent)
        require(q.stamp(os.stat(destination.name,dir_fd=parent,follow_symlinks=False))[:2]==identity,'consumer root replaced')
        observed,_=scan(destination,excluded=())
        expected={'root':dict(mapping['root'],uid=65534,gid=65534),
            'directories':{n:dict(r,uid=65534,gid=65534) for n,r in dirs.items()},
            'files':{r['path']:{k:(65534 if k in ('uid','gid') else r[k]) for k in ('mode','uid','gid','bytes','sha256')} for r in mapping['files']}}
        require(observed==expected and q.source_inventory(destination)==inventory(mapping),'restored complete source inventory differs')
        verify(source,descriptor_raw,descriptor_sha256)
        require(scan(destination,excluded=())[0]==expected,'consumer changed after archive revalidation')
        return dict(status='source-restored-data-layer',descriptorSha256=descriptor_sha256,
                    sourceInventorySha256=mapping['sourceInventorySha256'],resourcesPreserved=True,**FALSE_FLAGS)
    finally:
        if root is not None:os.close(root)
        os.close(parent)

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    exp=sub.add_parser('export');exp.add_argument('--source',required=True);exp.add_argument('--freeze',required=True);exp.add_argument('--freeze-sha256',required=True);exp.add_argument('--archive',required=True);exp.add_argument('--descriptor',required=True)
    for command in ('verify','restore'):
        cmd=sub.add_parser(command);cmd.add_argument('--archive',required=True);cmd.add_argument('--descriptor',required=True);cmd.add_argument('--descriptor-sha256',required=True)
        if command=='restore':cmd.add_argument('--destination',required=True)
    args=parser.parse_args()
    if args.command=='export':result=export(args.source,Path(args.freeze).read_bytes(),args.freeze_sha256,args.archive,args.descriptor)
    else:
        linux();raw=Path(args.descriptor).read_bytes()
        with Path(args.archive).open('rb') as source:
            result=verify(source,raw,args.descriptor_sha256) if args.command=='verify' else restore(source,raw,args.descriptor_sha256,args.destination)
    print(json.dumps(result,sort_keys=True))

if __name__=='__main__':main()
