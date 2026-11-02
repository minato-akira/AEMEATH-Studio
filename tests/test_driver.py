import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import SETTINGS,Engine,DEFAULT,transform_output,render,ORDER
from driver import Driver,binding,encode_macros,decode_macros,HID
from protocol import check_report,write_table,query_table

class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.engine=Engine(self.temp.name);self.driver=Driver(self.engine,True)
    def tearDown(self):self.engine.stop();self.temp.cleanup()
    def test_sleep_preserves_reserved_bytes_and_restore(self):
        self.driver.demo.tables[0x11][:]=bytes(range(56));before=bytes(range(56))
        r=self.driver.change({'operation':'sleep','minutes':10});after=bytes(self.driver.demo.tables[0x11]);expected=bytearray(before);expected[3]=10
        self.assertTrue(r['verified']);self.assertEqual(after,expected)
        listed={item['name']:item for item in self.driver.backups()};self.assertIn(r['backup'],listed);self.assertIn('睡眠',listed[r['backup']]['description'])
        self.driver.restore(r['backup']);self.assertEqual(self.driver.demo.tables[0x11],before)
    def test_one_key_preserves_all_other_slots(self):
        self.driver.demo.tables[0x12][:]=bytes([42])*512
        r=self.driver.change({'operation':'binding','source':49,'layer':'normal','binding':{'type':'mt','target':4,'hold':224,'delay':400}})
        expected=bytearray([42]*512);expected[196:200]=bytes([9,224,4,40]);self.assertEqual(self.driver.demo.tables[0x12],expected);self.assertTrue(r['verified'])
        with self.assertRaises(ValueError):self.driver.change({'operation':'binding','source':85,'binding':{'type':'default'}})
    def test_24g_table_paging_preserves_data(self):
        self.driver.demo.length=32;self.driver.demo.mode='2.4G'
        self.driver.change({'operation':'binding','source':34,'layer':'fn','binding':{'type':'default'}})
        self.assertEqual(self.driver.demo.tables[0x16][136:140],bytes([2,0,HID[34],0]))
        with self.assertRaises(ValueError):self.driver.change({'operation':'binding','source':1,'layer':'fn','binding':{'type':'default'}})
    def test_macro_roundtrip_and_binding(self):
        macro={'id':3,'actions':[{'delay':10,'keyCode':4,'isPress':True,'actionType':3},{'delay':40,'keyCode':4,'isPress':False,'actionType':3}]}
        raw=encode_macros([macro],4608);decoded=decode_macros(raw);self.assertEqual(decoded[0]['actions'],macro['actions'])
        self.driver.change({'operation':'macros','macros':[macro]})
        self.driver.change({'operation':'binding','source':0,'binding':{'type':'macro','macro':3,'mode':1,'repeat':2}})
        self.assertEqual(self.driver.demo.tables[0x12][:4],bytes([6,3,1,2]))
        with self.assertRaises(ValueError):encode_macros([dict(id=1,actions=macro['actions'][:1])],4608)
        with self.assertRaises(ValueError):encode_macros([macro],400)
    def test_socd_pair_and_unlink(self):
        self.driver.change({'operation':'socd','source':49,'peer':51,'mode':3})
        expected=bytes([11,3,4,7]);self.assertEqual(self.driver.demo.tables[0x12][196:200],expected)
        self.driver.change({'operation':'binding','source':49,'binding':{'type':'default'}})
        self.assertEqual(self.driver.demo.tables[0x12][204:208],bytes([2,0,HID[51],0]))
    def test_native_read_snapshot(self):
        s=self.driver.load();self.assertEqual(s['macroCapacity'],4608);self.assertTrue(s['simulated']);self.assertEqual(len(s['normal']),90)
    def test_bounds_reject_unknown_commands(self):
        for cmd in (0x24,0x42,0x4f,0x80):
            with self.assertRaises(ValueError):check_report(bytes((0xaa,cmd))+bytes(62))
        with self.assertRaises(ValueError):write_table(0x22,bytes(513),64)
        with self.assertRaises(ValueError):query_table(0x15,64,5100,56)
    def test_color_pipeline(self):
        s=copy.deepcopy(SETTINGS);self.assertEqual(transform_output({0:(255,128,0)},s)[0],(255,128,0))
        s['color_order']='BGR';self.assertEqual(transform_output({0:(255,128,0)},s)[0],(0,128,255))
        s['gamma']=.5;s['color_order']='RGB';self.assertGreater(transform_output({0:(16,0,0)},s)[0][0],16)
    def test_dataflow_is_spatial_and_reproducible(self):
        cfg=copy.deepcopy(DEFAULT);cfg['groups']=[dict(name='資料流',keys=ORDER,effect='核心資料流',color='#66D2FF',brightness=.7,speed=.7,bpm=72)]
        c=render(cfg,1.2);self.assertGreater(len(set(c.values())),2);self.assertEqual(c,render(cfg,1.2));self.assertNotEqual(c,render(cfg,1.5))
if __name__=='__main__':unittest.main()
