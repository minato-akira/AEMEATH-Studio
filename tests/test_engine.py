import copy
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import DEFAULT,KEYS,ORDER,HEART,POSITIONS,validate,render,heartbeat,Engine
from protocol import frames,rgb565,check_report,query_info
from native_hid import CAPS,OVERLAPPED,INTERFACE

class ProtocolTests(unittest.TestCase):
    def test_usb_boundaries(self):
        color={k:(k,255,128) for k in ORDER};r=frames(color,ORDER)
        self.assertEqual(len(r),7);self.assertEqual([x[2] for x in r],[56]*6+[24]);self.assertEqual([x[6] for x in r],[0]*6+[1])
        raw=b''.join(x[8:8+x[2]] for x in r)
        self.assertEqual(raw,b''.join(bytes((k,*color[k])) for k in ORDER))
        self.assertTrue(all(len(x)==64 and x[:2]==b'\xaa\x32' for x in r))
    def test_24g_rgb565_and_padding(self):
        c={k:(255,0,0) for k in ORDER};r=frames(c,ORDER,'2.4G')
        self.assertEqual(len(r),7);self.assertEqual([x[:4] for x in r],[bytes((0xaa,0x35,i,0)) for i in range(7)])
        raw=b''.join(x[4:] for x in r)
        self.assertEqual(raw[:180],b'\xf8\x00'*90);self.assertEqual(raw[180:],bytes(16))
        self.assertEqual(rgb565((255,255,255)),b'\xff\xff');self.assertEqual(rgb565((0,255,0)),b'\x07\xe0')
    def test_commands_and_structs(self):
        for cmd in (0x24,0x42,0x4f,0x80):
            with self.assertRaises(ValueError):check_report(bytes((0xaa,cmd))+bytes(62))
        self.assertEqual(ctypes.sizeof(CAPS),64)
        self.assertEqual(ctypes.sizeof(OVERLAPPED),32 if ctypes.sizeof(ctypes.c_void_p)==8 else 20)
    def test_device_info_chunks(self):
        self.assertEqual(query_info(64)[2],48)
        self.assertEqual(query_info(32,24)[2:8],bytes((24,24,0,0,1,0)))

class EngineTests(unittest.TestCase):
    def test_heart_mapping_and_layout(self):
        self.assertEqual(len(HEART),20);self.assertEqual(len(ORDER),90);self.assertEqual(len(set(ORDER)),90)
        self.assertEqual(next(k['row'] for k in KEYS if k['id']==45),0)
        self.assertEqual(next(k['row'] for k in KEYS if k['id']==93),3)
        self.assertEqual(next(k['row'] for k in KEYS if k['id']==90),4)
        for i,a in enumerate(KEYS):
            for b in KEYS[i+1:]:
                if a['row']==b['row']:self.assertTrue(a['x']+a['w']<=b['x']+.001 or b['x']+b['w']<=a['x']+.001,(a,b))
    def test_heartbeat_period_and_rest(self):
        period=60/72
        self.assertGreater(heartbeat(period*.16),.99);self.assertGreater(heartbeat(period*.36),.6)
        self.assertLess(heartbeat(period*.8),.04);self.assertAlmostEqual(heartbeat(period*.16),heartbeat(period*1.16))
    def test_reactive_is_local_and_bounded(self):
        cfg=copy.deepcopy(DEFAULT);cfg['groups']=[dict(name='試驗',keys=ORDER,effect='觸鍵星光',color='#FFFFFF',brightness=1,speed=1,bpm=72)]
        self.assertEqual(render(cfg,0)[34],(0,0,0))
        self.assertEqual(render(cfg,1,[(34,1)])[34],(255,255,255));self.assertEqual(render(cfg,1,[(34,1)])[35],(0,0,0))
        self.assertEqual(render(cfg,4,[(34,1)])[34],(0,0,0))
        cfg['groups'][0]['effect']='星光漣漪';self.assertGreater(sum(render(cfg,1,[(34,1)])[34]),0)
    def test_config_validation_and_persistence(self):
        cfg=copy.deepcopy(DEFAULT);validate(cfg)
        for value in (float('nan'),float('inf'),-1,2):
            c=copy.deepcopy(cfg);c['groups'][0]['brightness']=value
            with self.assertRaises(ValueError):validate(c)
        with tempfile.TemporaryDirectory() as d:
            e=Engine(d);e.save(cfg);self.assertEqual(Engine(d).config,validate(cfg))
            for _ in range(100):e.press(34)
            self.assertLessEqual(len(e.events),64)
    def test_worker_streams_only_realtime_and_stops(self):
        for mode in ('USB','2.4G'):
            with tempfile.TemporaryDirectory() as d:
                engine=Engine(d);engine.config['settings']['hardware_master']=False;reports=[];closed=[]
                class FakeDevice:
                    def __init__(self,description):self.mode=description['mode']
                    def info(self):return {'lightingVersion':0,'battery':80}
                    def exchange(self,report,timeout,strict):
                        reports.append(report)
                        if len(reports)==7:engine.stop_event.set()
                        return b'ack'
                    def close(self):closed.append(True)
                with patch('native_hid.enumerate_devices',return_value=[{'mode':mode}]),patch('native_hid.Device',FakeDevice):
                    engine.start();engine.thread.join(timeout=2)
                self.assertFalse(engine.thread.is_alive());self.assertEqual(len(reports),7);self.assertTrue(closed)
                self.assertTrue(all(r[1]==(0x32 if mode=='USB' else 0x35)for r in reports))
    def test_wrong_64byte_lighting_version_does_not_stream(self):
        with tempfile.TemporaryDirectory() as d:
            e=Engine(d);reports=[]
            class Fake:
                def __init__(self,*args):pass
                def info(self):return {'lightingVersion':0,'battery':80}
                def close(self):pass
                def exchange(self,*args):reports.append(args)
            with patch('native_hid.enumerate_devices',return_value=[{'mode':'2.4G-v1'}]),patch('native_hid.Device',Fake):
                e.start();e.thread.join(timeout=2)
            self.assertFalse(e.desired);self.assertEqual(reports,[]);self.assertIn('lightingVersion',e.error)
    def test_identify_does_not_change_config(self):
        cfg=copy.deepcopy(DEFAULT);render(cfg,1,identify=[34]);self.assertEqual(cfg,DEFAULT)
        self.assertEqual(render(cfg,1,identify=[34])[35],(0,0,0))
if __name__=='__main__':unittest.main()
