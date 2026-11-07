import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(0, '/repo/scripts/boot')
import qualification_volume_worker as worker
import qualification_volume_archive as strict
import linux_host_qualification as q
try:
    import source_archive as api
except ModuleNotFoundError:
    api = None

FIXTURE = Path(os.environ.get('MINGOS_SOURCE_TEST_FIXTURE', '/fixture'))
def sha(raw): return hashlib.sha256(raw).hexdigest()
def encoded(value): return (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()

@unittest.skipUnless(sys.platform=='linux' and 'MINGOS_SOURCE_TEST_FIXTURE' in os.environ,
                     'requires an explicitly supplied read-only Linux source fixture')
class SourceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(api, 'source archive data layer is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.freeze = encoded({'source': {'files': {'hello.txt': {'sha256':sha(b'sealed source fixture\n'),'bytes':22}}}})

    def exported(self):
        archive = self.base/'source.tar'; descriptor = self.base/'descriptor.json'
        result = api.export(FIXTURE, self.freeze, sha(self.freeze), archive, descriptor)
        return archive, descriptor, sha(descriptor.read_bytes()), result

    def check(self, archive, descriptor, pin=None):
        with archive.open('rb') as incoming:
            return api.verify(incoming, descriptor.read_bytes(), pin or sha(descriptor.read_bytes()))

    def rewrite(self, archive, descriptor, modify):
        # Repack and re-pin transport to isolate mapping/schema validation from byte pin checks.
        members=[]
        with tarfile.open(archive, 'r:') as tf:
            for member in tf:
                members.append((member,tf.extractfile(member).read() if member.isfile() else b''))
        members=modify(members)
        entries=[]
        for member,raw in members:
            if member.name == strict.MANIFEST_NAME: continue
            row={'path':member.name.rstrip('/'),'type':'directory' if member.isdir() else 'file','mode':member.mode,'uid':member.uid,'gid':member.gid}
            if member.isfile(): row.update(size=len(raw),sha256=sha(raw),nlink=1)
            entries.append(row)
        manifest=encoded({'schemaVersion':1,'uid':65534,'gid':65534,'entries':entries})
        members=[(m,r) for m,r in members if m.name != strict.MANIFEST_NAME]
        m=tarfile.TarInfo(strict.MANIFEST_NAME);m.uid=m.gid=65534;m.mode=0o644;m.size=len(manifest);members.append((m,manifest))
        with tarfile.open(archive,'w',format=tarfile.USTAR_FORMAT) as tf:
            for member,raw in members:
                member.size=len(raw);tf.addfile(member,io.BytesIO(raw))
        desc=json.loads(descriptor.read_bytes());desc['archiveSha256']=sha(archive.read_bytes());desc['archiveBytes']=archive.stat().st_size;desc['transportManifestSha256']=sha(manifest)
        for member,raw in members:
            if member.name=='source-map.json':desc['sourceMapSha256']=sha(raw)
        descriptor.write_bytes(encoded(desc))

    def change_map(self,archive,descriptor,mutate):
        def change(rows):
            result=[]
            for member,raw in rows:
                if member.name=='source-map.json':
                    value=json.loads(raw);mutate(value);raw=encoded(value)
                result.append((member,raw))
            return result
        self.rewrite(archive,descriptor,change)

    def test_export_verify_and_restore_preserve_exact_source(self):
        archive,descriptor,pin,result=self.exported();plan=self.check(archive,descriptor,pin)
        self.assertEqual(plan['mapping']['files'][0]['alias'],'f/000001')
        self.assertEqual(plan['mapping']['files'][0]['path'],'hello.txt')
        with archive.open('rb') as stream:receipt=api.restore(stream,descriptor.read_bytes(),pin,self.base/'consumer')
        self.assertEqual((self.base/'consumer/hello.txt').read_bytes(),b'sealed source fixture\n')
        self.assertEqual(stat.S_IMODE((self.base/'consumer/hello.txt').stat().st_mode),0o644)
        self.assertTrue((self.base/'consumer/empty/nested').is_dir())
        self.assertFalse((self.base/'consumer/artifacts').exists())
        self.assertFalse(receipt['sourceBuildReplayed']);self.assertFalse(result['compilerExecuted'])

    def test_transport_is_accepted_by_unchanged_anonymous_worker(self):
        archive,descriptor,pin,_=self.exported()
        root=self.base/'worker';root.mkdir(mode=0o700);(root/'payload').mkdir(mode=0o700);(root/'control').mkdir(mode=0o700)
        with archive.open('rb') as stream:receipt=worker.restore(root,stream,json.loads(descriptor.read_bytes())['archiveSha256'],'a'*32)
        self.assertEqual((root/'payload/f/000001').read_bytes(),b'sealed source fixture\n')
        self.assertEqual(receipt['status'],'payload-restored')

    def test_wrong_descriptor_pin_rejected_before_destination_write(self):
        a,d,p,_=self.exported()
        with a.open('rb') as stream,self.assertRaises(ValueError):api.restore(stream,d.read_bytes(),'0'*64,self.base/'consumer')
        self.assertFalse((self.base/'consumer').exists())

    def test_wrong_cold_inventory_rejected_before_archive_creation(self):
        raw=encoded({'source':{'files':{}}})
        with self.assertRaises(ValueError):api.export(FIXTURE,raw,sha(raw),self.base/'bad.tar',self.base/'bad.json')
        self.assertFalse((self.base/'bad.tar').exists())

    def test_mutable_source_mount_rejected(self):
        source=self.base/'source';source.mkdir();(source/'x').write_bytes(b'x')
        raw=encoded({'source':{'files':{'x':{'bytes':1,'sha256':sha(b'x')}}}})
        with self.assertRaises(ValueError):api.export(source,raw,sha(raw),self.base/'bad.tar',self.base/'bad.json')

    def test_changed_archive_rejected(self):
        a,d,_,_=self.exported();raw=bytearray(a.read_bytes());raw[1024]^=1;a.write_bytes(raw)
        with self.assertRaises(ValueError):self.check(a,d)

    def test_existing_destination_is_preserved(self):
        a,d,p,_=self.exported();target=self.base/'consumer';target.mkdir();(target/'sentinel').write_text('keep')
        with a.open('rb') as stream,self.assertRaises((ValueError,FileExistsError)):api.restore(stream,d.read_bytes(),p,target)
        self.assertEqual((target/'sentinel').read_text(),'keep')

    def test_symlink_parent_cannot_escape_restore(self):
        a,d,p,_=self.exported();outside=self.base/'outside';outside.mkdir();(self.base/'alias').symlink_to(outside,target_is_directory=True)
        with a.open('rb') as stream,self.assertRaises(ValueError):api.restore(stream,d.read_bytes(),p,self.base/'alias/consumer')
        self.assertFalse((outside/'consumer').exists())

    def test_map_traversal_rejected_even_when_archive_repinned(self):
        a,d,_,_=self.exported();self.change_map(a,d,lambda m:m['files'][0].update(path='../escape'))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_duplicate_alias_rejected(self):
        a,d,_,_=self.exported();self.change_map(a,d,lambda m:m['files'].append(dict(m['files'][0],path='other')))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_directory_closure_rejected(self):
        a,d,_,_=self.exported();self.change_map(a,d,lambda m:m['directories'].pop('empty'))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_generated_source_directory_rejected_in_mapping(self):
        a,d,_,_=self.exported();self.change_map(a,d,lambda m:m['directories'].update({'artifacts':{'mode':493,'uid':0,'gid':0}}))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_map_file_digest_must_bind_transport_alias(self):
        a,d,_,_=self.exported();self.change_map(a,d,lambda m:m['files'][0].update(sha256='0'*64))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_map_unknown_fields_rejected(self):
        a,d,_,_=self.exported();self.change_map(a,d,lambda m:m.update(run='arbitrary command'))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_archive_owner_drift_rejected(self):
        a,d,_,_=self.exported()
        def alter(rows):
            for member,_raw in rows:
                if member.name=='f/000001':member.uid=0
            return rows
        self.rewrite(a,d,alter)
        with self.assertRaises(ValueError):self.check(a,d)

    def test_long_original_path_is_data_only(self):
        a,d,_,_=self.exported();long='a'*120+'/'+ 'b'*120+'/'+ 'c'*100
        def mutate(m):
            m['files'][0]['path']=long;m['directories'].update({'a'*120:{'mode':493,'uid':0,'gid':0},'a'*120+'/'+ 'b'*120:{'mode':493,'uid':0,'gid':0}})
            m['sourceInventorySha256']=sha(encoded({long:{'bytes':22,'sha256':sha(b'sealed source fixture\n')}}))
        self.change_map(a,d,mutate)
        desc=json.loads(d.read_bytes());inventory={long:{'bytes':22,'sha256':sha(b'sealed source fixture\n')}};desc['sourceInventorySha256']=sha(encoded(inventory));d.write_bytes(encoded(desc))
        with a.open('rb') as stream:api.restore(stream,d.read_bytes(),sha(d.read_bytes()),self.base/'long')
        self.assertEqual((self.base/'long'/long).read_bytes(),b'sealed source fixture\n')

    def test_duplicate_original_path_rejected(self):
        a,d,_,_=self.exported()
        self.change_map(a,d,lambda m:m['files'].append(dict(m['files'][0],alias='f/000002')))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_unmapped_transport_member_rejected(self):
        a,d,_,_=self.exported()
        def extra(rows):
            item=tarfile.TarInfo('extra');item.uid=item.gid=65534;item.mode=0o644;rows.append((item,b'extra'));return rows
        self.rewrite(a,d,extra)
        with self.assertRaises(ValueError):self.check(a,d)

    def test_symlink_transport_member_rejected(self):
        a,d,_,_=self.exported()
        def symlink(rows):
            for member,_raw in rows:
                if member.name=='f/000001':member.type=tarfile.SYMTYPE;member.linkname='/escape'
            return rows
        self.rewrite(a,d,symlink)
        with self.assertRaises(ValueError):self.check(a,d)

    def test_pax_transport_member_rejected(self):
        a,d,_,_=self.exported()
        def pax(rows):
            item=tarfile.TarInfo('pax');item.uid=item.gid=65534;item.mode=0o644;item.type=tarfile.XHDTYPE;rows.append((item,b'extension'));return rows
        self.rewrite(a,d,pax)
        with self.assertRaises(ValueError):self.check(a,d)

    def test_map_alias_mode_drift_rejected(self):
        a,d,_,_=self.exported();self.change_map(a,d,lambda m:m['files'][0].update(mode=0o755))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_unearned_compiler_claim_rejected(self):
        a,d,_,_=self.exported();value=json.loads(d.read_bytes());value['compilerExecuted']=True;d.write_bytes(encoded(value))
        with self.assertRaises(ValueError):self.check(a,d)

    def test_wrong_raw_freeze_pin_rejected(self):
        with self.assertRaises(ValueError):api.export(FIXTURE,self.freeze,'0'*64,self.base/'bad.tar',self.base/'bad.json')
        self.assertFalse((self.base/'bad.tar').exists())

    def test_source_scan_rejects_symlink_and_hardlink(self):
        source=self.base/'source';source.mkdir();(source/'x').write_bytes(b'x');(source/'alias').symlink_to(source/'x')
        with self.assertRaises(ValueError):api.scan(source)
        (source/'alias').unlink();os.link(source/'x',source/'alias')
        with self.assertRaises(ValueError):api.scan(source)

    def test_complete_consumer_scan_detects_generated_extras(self):
        source=self.base/'consumer';source.mkdir();(source/'x').write_bytes(b'x');(source/'artifacts').mkdir();(source/'artifacts/extra').write_bytes(b'x')
        source.chmod(0o755);(source/'x').chmod(0o644);(source/'artifacts').chmod(0o755);(source/'artifacts/extra').chmod(0o644)
        observed,_=api.scan(source,excluded=())
        self.assertIn('artifacts/extra',observed['files'])

if __name__=='__main__':unittest.main()
