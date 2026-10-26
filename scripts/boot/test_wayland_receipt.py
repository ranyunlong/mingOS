"""Current-client pointer receipts, never timed guesses or synthetic app readiness."""
import importlib.util
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
