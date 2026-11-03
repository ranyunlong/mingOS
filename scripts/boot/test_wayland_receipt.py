"""Current-client pointer receipts, never timed guesses or synthetic app readiness."""
import importlib.util
import json
import unittest
from pathlib import Path
class WaylandReceiptTests(unittest.TestCase):
    def parser(self):
        path=Path(__file__).with_name('wayland_receipt.py');self.assertTrue(path.exists(),'Wayland pointer receipt parser missing')
        spec=importlib.util.spec_from_file_location('wayland_receipt_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module.PointerReceipt()
    def test_receipt_requires_created_pointer_enter_and_frame(self):
        parser=self.parser()
        self.assertEqual(parser.feed(b'[1] wl_seat#8.capabilities(3)\n'),[])
        self.assertEqual(parser.feed(b'[2] -> wl_seat#8.get_pointer(new id wl_pointer#36)\n'),[])
        self.assertEqual(parser.feed(b'[3] wl_pointer#36.enter(0, wl_surface#15, 120.07812500, 200.24218750)\n'),[])
        records=parser.feed(b'[4] wl_pointer#36.frame()\n')
        self.assertEqual(len(records),1);self.assertTrue(records[0]['entered'])
        self.assertEqual((records[0]['x'],records[0]['y']),(120.078125,200.2421875))
    def test_complete_oversized_line_is_rejected_before_decode(self):
        parser=self.parser()
        with self.assertRaisesRegex(ValueError,'line limit'):parser.feed(b'x'*65537+b'\n')

    def persistent_commit_line(self):
        apps=[]
        for app,package_id,version in ((1,'dev.ming.text-entry-demo','0.1.0'),(2,'dev.ming.install-demo','1.0.0')):
            directory='packages/'+package_id.encode().hex()+'/'+version+'-'+'a'*64
            apps.append({'app':app,'packageId':package_id,'version':version,'fingerprint':'a'*64,
                'manifestPath':directory+'/app.json','manifestSha256':'b'*64,
                'wasmPath':directory+'/main.wasm','wasmSha256':'c'*64})
        apps.sort(key=lambda app:app['packageId'])
        files=[{'path':name,'sha256':'d'*64} for name in
            ('catalog.json','installed-packages-v1','trusted-publishers-v1','desktop-install-state-v1.json')]
        files.extend({'path':app[field+'Path'],'sha256':app[field+'Sha256']} for app in apps for field in ('manifest','wasm'))
        for version in range(124):
            directory='packages/'+('dev.ming.'+'a'*110).encode().hex()+'/2.0.'+str(version)+'-'+'e'*64
            files.extend({'path':directory+'/'+name,'sha256':'f'*64} for name in ('app.json','main.wasm'))
        content={field:'d'*64 for field in ('catalogSha256','registrySha256','trustSha256','bootstrapSha256')}
        content.update(apps=apps,files=sorted(files,key=lambda item:item['path']))
        # Validate the same bounded content schema used by the guest before
        # wrapping it in the production stderr commit format.
        import guest
        guest.persistent_snapshot({'schemaVersion':1,'status':'opened-existing','content':content,
            'actions':{name:0 for name in ('initializedRoots','packagesInstalled','trustStoresProvisioned','catalogsCreated','sourcesCreated')}},
            {'bootNumber':2,'role':'open-existing'},'pre-desktop',None)
        receipt={'schemaVersion':1,'status':'committed','packageId':'dev.ming.install-demo','version':'1.0.0',
            'fingerprint':'a'*64,'registryRoot':'/data/mingos/install-root','catalogPath':'/data/mingos/install-root/catalog.json',
            'content':content,'persistentBlockConfirmed':False}
        return b'MING_PERSISTENT_INSTALL '+json.dumps({'kind':'persistent-install-commit','bootId':'boot',
            'sessionId':'session','desktopPid':20,'receipt':receipt},separators=(',',':')).encode()+b'\n'

    def test_long_persistent_receipt_preserves_following_pointer_byte_offsets(self):
        line=self.persistent_commit_line()
        self.assertGreater(len(line)-1,65536);self.assertLessEqual(len(line)-1,262144)
        start=b'-> wl_seat#8.get_pointer(new id wl_pointer#36)\n'
        follow=b'wl_pointer#36.enter(0, wl_surface#15, 120, 200)\nwl_pointer#36.frame()\n'
        for chunk_size in (1,17,65536,len(line)):
            with self.subTest(chunk_size=chunk_size):
                parser=self.parser();parser.feed(start)
                for offset in range(0,len(line)-1,chunk_size):
                    self.assertEqual(parser.feed(line[offset:min(offset+chunk_size,len(line)-1)]),[])
                    self.assertEqual(parser.offset,len(start))
                self.assertEqual(parser.buffer,line[:-1])
                records=parser.feed(b'\n'+follow)
                self.assertEqual(len(records),1)
                self.assertEqual(records[0]['logOffset'],len(start)+len(line)+len(follow))
                self.assertEqual((records[0]['pointer'],records[0]['surface']),(36,15))
                self.assertEqual(parser.buffer,b'')

    def test_persistent_receipt_cannot_inject_wayland_pointer_readiness(self):
        parser=self.parser()
        self.assertEqual(parser.feed(b'MING_PERSISTENT_INSTALL -> wl_seat#8.get_pointer(new id wl_pointer#99)\n'),[])
        self.assertIsNone(parser.pointer)
        parser.feed(b'-> wl_seat#8.get_pointer(new id wl_pointer#36)\n')
        self.assertEqual(parser.feed(b'MING_PERSISTENT_INSTALL wl_pointer#36.enter(0, wl_surface#15, 120, 200)\nwl_pointer#36.frame()\n'),[])
        self.assertFalse(parser.entered)

    def test_only_exact_persistent_prefix_gets_the_separate_bound(self):
        for prefix in (b' MING_PERSISTENT_INSTALL ',b'MING_PERSISTENT_INSTALLER ',b'MING_PERSISTENT_INSTALL\t',
                       b'ming_persistent_install ',b'x MING_PERSISTENT_INSTALL '):
            for complete in (False,True):
                with self.subTest(prefix=prefix,complete=complete):
                    parser=self.parser();line=prefix+b'x'*(65537-len(prefix))
                    with self.assertRaisesRegex(ValueError,'Wayland diagnostic line limit'):
                        parser.feed(line+(b'\n' if complete else b''))

    def test_persistent_receipt_bound_includes_prefix_and_waits_for_newline(self):
        prefix=b'MING_PERSISTENT_INSTALL '
        line=prefix+b'x'*(262144-len(prefix))
        for chunk_size in (65536,len(line)):
            with self.subTest(chunk_size=chunk_size):
                parser=self.parser()
                for offset in range(0,len(line),chunk_size):
                    self.assertEqual(parser.feed(line[offset:offset+chunk_size]),[])
                self.assertEqual(parser.offset,0);self.assertEqual(parser.buffer,line)
                self.assertEqual(parser.feed(b'\n'),[])
                self.assertEqual(parser.offset,262145);self.assertEqual(parser.buffer,b'')
        for complete in (False,True):
            with self.subTest(complete=complete):
                parser=self.parser();parser.feed(line)
                with self.assertRaisesRegex(ValueError,'persistent install receipt line limit'):
                    parser.feed(b'x'+(b'\n' if complete else b''))

    def test_power_move_accepts_motion_on_current_pointer_and_surface(self):
        parser=self.parser();parser.feed(b'-> wl_seat#8.get_pointer(new id wl_pointer#36)\nwl_pointer#36.enter(0, wl_surface#15, 120, 200)\nwl_pointer#36.frame()\n')
        records=parser.feed(b'wl_pointer#36.motion(10, 1250.3, 12.2)\nwl_pointer#36.frame()\n')
        self.assertEqual(len(records),1);self.assertEqual((records[0]['pointer'],records[0]['surface']),(36,15))
        self.assertEqual((records[0]['x'],records[0]['y']),(1250.3,12.2))
        self.assertGreater(records[0]['logOffset'],0)

    def test_partial_lines_wrong_pointer_and_leave_do_not_create_stale_readiness(self):
        parser=self.parser();parser.feed(b'-> wl_seat#8.get_pointer(new id wl_pointer#36)\n')
        self.assertEqual(parser.feed(b'wl_pointer#99.enter(0, wl_surface#15, 120, 200)\nwl_pointer#99.frame()\n'),[])
        self.assertEqual(parser.feed(b'wl_pointer#36.enter(0, wl_surface#15, 120,'),[])
        self.assertEqual(parser.feed(b'200)\n'),[])
        self.assertTrue(parser.feed(b'wl_pointer#36.frame()\n')[0]['entered'])
        self.assertFalse(parser.feed(b'wl_pointer#36.leave(1, wl_surface#15)\n')[0]['entered'])
if __name__=='__main__':unittest.main()
