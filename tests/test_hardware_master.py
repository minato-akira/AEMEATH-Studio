import copy,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import Engine,DEFAULT,validate,render,transform_output,HEART
from driver import Driver
from native_lighting import apply_native
class NativeTests(unittest.TestCase):
 def test_legacy_hardware_setting_disabled(self):
  c=copy.deepcopy(DEFAULT);c['settings']['hardware_master']=True
  self.assertFalse(validate(c)['settings']['hardware_master'])
 def test_native_read_write_difference_and_backup(self):
  with tempfile.TemporaryDirectory() as d:
   e=Engine(d);driver=Driver(e,True)
   r=apply_native(driver,dict(mode=7,color='#66D2FF',brightness=2,speed=3,direction=0,colorMode=0))
   self.assertTrue(r['ok']);self.assertEqual(driver.demo.tables[0x13][9],2)
   self.assertEqual(driver.demo.tables[0x13][14:16],bytes(2));self.assertFalse(e.desired)
   self.assertEqual(len(list((Path(d)/'lighting-backups').glob('*.json'))),2)
 def test_heart_rest_black_even_low_gamma(self):
  c=copy.deepcopy(DEFAULT);c['groups']=[c['groups'][1]];c['groups'][0]['brightness']=1
  c['groups'][0].update(background_color='#FFFFFF',background_brightness=1)
  for gamma in (.15,.5,1,3):
   c['settings']['gamma']=gamma
   self.assertTrue(all(not any(v) for v in transform_output(render(c,.7),c['settings']).values()))
 def test_native_mode_cannot_start_stream(self):
  with tempfile.TemporaryDirectory() as d:
   e=Engine(d);c=copy.deepcopy(DEFAULT);c['active_mode']='native';e.save(c);e.start()
   self.assertFalse(e.desired);self.assertIsNone(e.thread)
 def test_official_payload_fixture_usb_and_wireless(self):
  for mode,size in [('USB',64),('2.4G',32)]:
   with tempfile.TemporaryDirectory() as d:
    e=Engine(d);driver=Driver(e,True);driver.demo.mode=mode;driver.demo.length=size
    driver.demo.tables[0x13]=bytearray.fromhex('01 ff ff ff 00 00 00 00 00 01 03 00 00 00 00 00')
    exchange=driver.demo.exchange;calls=[]
    def capture(report,*args):
     calls.append(report);return exchange(report,*args)
    driver.demo.exchange=capture
    apply_native(driver,dict(mode=1,color='#FFFFFF',brightness=3,speed=3,direction=0,colorMode=0))
    self.assertEqual([r[1] for r in calls],[0x13,0x23,0x13])
    self.assertEqual(calls[1][8:24],bytes.fromhex('01 ff ff ff ff 00 00 00 00 03 03 00 00 00 aa 55'))
    self.assertTrue(all(len(r)==size for r in calls))

 def test_delayed_readback_does_not_rewrite(self):
  from unittest.mock import patch
  with tempfile.TemporaryDirectory() as folder:
   e=Engine(folder);driver=Driver(e,True);orig=driver.demo.exchange;calls=[];stale=bytes(driver.demo.tables[0x13]);remaining=[2]
   def exchange(r,*a):
    calls.append(r[1]);reply=orig(r,*a)
    if r[1]==0x13 and 0x23 in calls and remaining[0]:
     remaining[0]-=1
     return reply[:8]+stale+reply[24:]
    return reply
   driver.demo.exchange=exchange
   with patch('native_lighting.time.sleep'):
    result=apply_native(driver,dict(mode=1,color='#66D2FF',brightness=1,speed=3,direction=0,colorMode=0))
   self.assertEqual(result['actual']['brightness'],1);self.assertEqual(calls.count(0x23),1)
 def test_brightness_rejected_has_diagnostic_and_no_false_save(self):
  from unittest.mock import patch
  from native_lighting import NativeLightingError
  with tempfile.TemporaryDirectory() as folder:
   e=Engine(folder);driver=Driver(e,True);original=driver.demo.exchange;calls=[]
   def exchange(r,*a):
    calls.append(r[1]);reply=original(r,*a)
    if r[1]==0x23:driver.demo.tables[0x13][9]=5
    return reply
   driver.demo.exchange=exchange
   with patch('native_lighting.time.sleep'):
    with self.assertRaises(NativeLightingError) as error:
     apply_native(driver,dict(mode=1,color='#66D2FF',brightness=1,speed=3,direction=0,colorMode=0))
   self.assertIn('亮度：要求 1、讀回 5',str(error.exception));self.assertEqual(calls.count(0x23),1)
   self.assertEqual(e.config.get('active_mode','leisure'),'leisure')
   record=__import__('json').loads((Path(folder)/'lighting-backups/latest-native.json').read_text())
   self.assertFalse(record['ok']);self.assertEqual(record['actual']['brightness'],5)
