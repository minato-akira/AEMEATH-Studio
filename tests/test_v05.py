import copy,json,sys,unittest,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import DEFAULT,HEART,ORDER,Engine,validate,dataflow_level,render,transform_output,heartbeat
import tempfile
class V05Tests(unittest.TestCase):
 def test_bounds_and_legacy_migration(self):
  cfg=copy.deepcopy(DEFAULT);cfg['settings']['gamma']=.15;cfg['groups'][1]['bpm']=15
  self.assertEqual(validate(cfg)['groups'][1]['bpm'],15)
  for gamma,bpm in ((.14,15),(.15,14)):
   bad=copy.deepcopy(cfg);bad['settings']['gamma']=gamma;bad['groups'][1]['bpm']=bpm
   with self.assertRaises(ValueError):validate(bad)
  cfg['version']=4;cfg['settings'].pop('color_profiles');self.assertEqual(validate(cfg)['version'],6)
 def test_dataflow_columns_are_not_synchronized(self):
  peaks=[max(range(820),key=lambda i:dataflow_level(x,2,i/100,.5)) for x in range(8)]
  self.assertGreater(len(set(peaks)),4)
  self.assertFalse(all(a<=b for a,b in zip(peaks,peaks[1:])))
  self.assertFalse(all(a>=b for a,b in zip(peaks,peaks[1:])))
 def test_bpm15_period(self):
  self.assertAlmostEqual(heartbeat(.64,15),heartbeat(4.64,15));self.assertGreater(heartbeat(.64,15),.99)
 def test_heart_profile_does_not_change_normal_keys(self):
  cfg=copy.deepcopy(DEFAULT);c={k:(100.,80.,60.)for k in ORDER}
  baseline=transform_output(c,cfg['settings']);cfg['settings']['color_profiles']={'heart':dict(color_order='BGR',gamma=.15,red_gain=.8,green_gain=1,blue_gain=1)}
  validate(cfg);changed=transform_output(c,cfg['settings'])
  for k in ORDER:
   if k in HEART:self.assertNotEqual(baseline[k],changed[k])
   else:self.assertEqual(baseline[k],changed[k])
 def test_trial_is_temporary_and_does_not_save(self):
  with tempfile.TemporaryDirectory() as tmp:
   e=Engine(tmp);before=copy.deepcopy(e.config)
   e.set_calibration(dict(rgb=[255,255,255],keys=HEART,brightness=.6,settings=dict(color_order='RGB',gamma=.15,red_gain=.8,green_gain=1,blue_gain=1)))
   self.assertEqual(e.config,before);self.assertFalse((Path(tmp)/'config.json').exists());self.assertGreater(e.snapshot()['calibration']['remaining'],59)
   e.calibration['until']=time.monotonic()-1;self.assertIsNone(e.snapshot()['calibration'])
 def test_trial_rejects_extra_configuration(self):
  with tempfile.TemporaryDirectory() as tmp:
   e=Engine(tmp)
   with self.assertRaises(ValueError):e.set_calibration(dict(rgb=[255,0,0],keys=HEART,settings=dict(DEFAULT['settings'])))
if __name__=='__main__':unittest.main()
