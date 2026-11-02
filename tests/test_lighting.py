"""回歸：暗部色偏、單次熄滅門檻、下行資料流與前後端一致性。"""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine import SETTINGS,DEFAULT,KEYS,ORDER,render,transform_output,quantize_color,dataflow_level,color_faithful

class LightingTests(unittest.TestCase):
    def config(self,color='#E45879',brightness=1,effect='恆亮'):
        cfg=copy.deepcopy(DEFAULT)
        cfg['settings']=copy.deepcopy(SETTINGS)
        cfg['groups']=[dict(name='回歸',keys=ORDER,color=color,brightness=brightness,effect=effect,speed=.7,bpm=72)]
        return cfg
    def test_dimming_keeps_fractional_color_ratios(self):
        for brightness in (.0001,.001,.01,.11,.5,1):
            c=render(self.config(brightness=brightness),.7)[ORDER[0]]
            for value,source in zip(c,(228,88,121)):
                self.assertAlmostEqual(value/source,brightness)
        self.assertEqual(render(self.config(brightness=0),.7)[ORDER[0]],(0,0,0))
    def test_no_red_tail_in_pink_dark_fade(self):
        for mode in ('USB','2.4G','2.4G-v1'):
            cfg=self.config(brightness=.01)
            self.assertEqual(transform_output(render(cfg,0),cfg['settings'],mode)[ORDER[0]],(0,0,0))
            for i in range(1001):
                raw=tuple(v*i/1000 for v in (228,88,121));q=quantize_color(raw,mode)
                if any(q):
                    self.assertTrue(color_faithful(raw,q))
                    self.assertTrue(all(q))
    def test_dark_fade_has_one_cutoff_not_repeated_blinks(self):
        for mode in ('USB','2.4G'):
            for color in ((228,88,121),(102,210,255),(255,255,255),(255,1,1),(0,255,0),(50,255,170)):
                levels=[any(quantize_color(tuple(n*i/1000 for n in color),mode)) for i in range(1001)]
                self.assertFalse(levels[0]);self.assertTrue(levels[-1])
                self.assertEqual(sum(a!=b for a,b in zip(levels,levels[1:])),1,(mode,color))
    def test_gamma_changes_brightness_not_individual_channel_curve(self):
        raw={0:(80.,40.,20.)}
        for gamma in (.5,1,2):
            s=dict(SETTINGS,gamma=gamma)
            q=transform_output(raw,s)[0]
            if any(q):self.assertTrue(color_faithful(raw[0],q))
    def test_data_head_moves_down_and_tail_stays_above(self):
        x=10.;speed=.7;y=2.
        t=((y+1.2-((int(x)*73+int(x)*int(x)*19)%101)/101*8.2)%8.2)/(speed*2)
        
        self.assertGreater(dataflow_level(x,y,t,speed),.89)
        self.assertGreater(dataflow_level(x,y-.5,t,speed),dataflow_level(x,y+.5,t,speed))
        self.assertAlmostEqual(dataflow_level(x,y,t,speed),dataflow_level(x,y+1,t+1/(speed*2),speed))
    @unittest.skipUnless(shutil.which('node'),'JavaScript 比對需要 Node（程式執行本身不需要）')
    def test_python_js_outputs_match(self):
        cases=[]
        for effect in ('恆亮','心跳','呼吸','波浪','核心資料流','彩虹'):
            for level in (.01,.11,.7):
                for mode in ('USB','2.4G'):
                    cfg=self.config(brightness=level,effect=effect);cfg['settings']['gamma']=.8
                    cases.append(dict(config=cfg,t=.73,mode=mode))
                    other=copy.deepcopy(cfg);other['settings']['color_profiles']={'heart':dict(color_order='BGR',gamma=.15,red_gain=.8,green_gain=1,blue_gain=.9)};cases.append(dict(config=other,t=.73,mode=mode))
        path=Path(__file__).resolve().parents[1]/'web/visual.js'
        script="const v=require(process.argv[1]);let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>{const p=JSON.parse(s);process.stdout.write(JSON.stringify(p.cases.map(c=>v.output(v.render(c.config,p.keys,c.t),c.config.settings,c.mode))))});"
        js=json.loads(subprocess.run(['node','-e',script,str(path)],input=json.dumps(dict(keys=KEYS,cases=cases)),text=True,capture_output=True,check=True).stdout)
        for case,result in zip(cases,js):
            expected=transform_output(render(case['config'],case['t']),case['config']['settings'],case['mode'])
            self.assertEqual(result,{str(k):list(v) for k,v in expected.items()})
if __name__=='__main__':unittest.main()
