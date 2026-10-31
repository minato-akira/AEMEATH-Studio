"""v0.6：休閒即時 RGB、獨立內建模式、分區校色與燈效方案。"""
import colorsys
import copy
import json
import logging
import math
import os
from pathlib import Path
import threading
import time
import uuid
from functools import lru_cache
import protocol
from protocol import frames

# 沿用既有 Device.write/check_report，只擴充這兩種固定 16-byte 設定命令。
# 不放寬任意命令、不修改 native_hid.py 或 protocol.py 檔案。
protocol.TABLES.update({0x13:16,0x23:16})
protocol.ALLOWED.update((0x13,0x23))

BASE=Path(__file__).resolve().parent
KEYS=json.loads((BASE/'keys.json').read_text(encoding='utf-8'));ORDER=[k['id'] for k in KEYS];IDS=set(ORDER)
POSITIONS={k['id']:(k['x']+k['w']/2,k['y']+.5) for k in KEYS}
HEART=[k['id'] for k in KEYS if k['heart']]
EFFECTS=['恆亮','呼吸','彩虹','波浪','閃爍','心跳','觸鍵星光','星光漣漪','核心資料流','熄滅']
DATA=Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'AEMEATH_Background'
SETTINGS={'autostart':False,'start_hidden':False,'auto_effects':False,'reconnect':False,'connection':'自動','fps':15,'theme':'晨霧','motion':True,'global_keys':False,'master':1.0,'hardware_master':False,'color_order':'RGB','gamma':1.0,'red_gain':1.0,'green_gain':1.0,'blue_gain':1.0,'idle_minutes':0,'color_profiles':{}}
DEFAULT={'version':6,'groups':[
 {'name':'雪色底光','keys':ORDER,'effect':'恆亮','color':'#ABB8EB','brightness':.24,'speed':.5,'bpm':72},
 {'name':'藍色心型','keys':HEART,'effect':'心跳','color':'#66D2FF','brightness':.85,'speed':1,'bpm':72},
 {'name':'星光功能列','keys':list(range(1,13)),'effect':'彩虹','color':'#F0A8CE','brightness':.35,'speed':.25,'bpm':72}], 'settings':copy.deepcopy(SETTINGS)}

def finite(v,lo,hi):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:raise ValueError('數值超出範圍')
    return v

def validate(cfg):
    if not isinstance(cfg,dict) or not isinstance(cfg.get('groups'),list) or not 1<=len(cfg['groups'])<=32:raise ValueError('群組數量需為 1～32')
    result=copy.deepcopy(cfg)
    for g in result['groups']:
        if not isinstance(g.get('name'),str) or not 1<=len(g['name'].strip())<=40:raise ValueError('群組名稱需為 1～40 字')
        if g.get('effect') not in EFFECTS:raise ValueError('不支援的效果')
        if not isinstance(g.get('keys'),list) or len(g['keys'])>90 or any(type(k)is not int or k not in IDS for k in g['keys']):raise ValueError('無效的鍵盤選區')
        if len(set(g['keys']))!=len(g['keys']):raise ValueError('選區有重複燈號')
        c=g.get('color','')
        if not isinstance(c,str) or len(c)!=7 or c[0]!='#':raise ValueError('顏色需為 #RRGGBB')
        int(c[1:],16);g['color']=c.upper()
        bg=g.setdefault('background_color','#000000');int(bg[1:],16)
        if len(bg)!=7 or bg[0]!='#':raise ValueError('背景顏色無效')
        finite(g.setdefault('background_brightness',0),0,1)
        finite(g.get('brightness'),0,1);finite(g.get('speed'),.1,5);finite(g.setdefault('bpm',72),15,180)
    s=copy.deepcopy(SETTINGS);s.update(result.get('settings',{}))
    for key in ('autostart','start_hidden','auto_effects','reconnect','motion','global_keys','hardware_master'):
        if type(s[key])is not bool:raise ValueError('設定需為開關值')
    if s['connection'] not in ('自動','USB','2.4G'):raise ValueError('無效連線選項')
    if s['theme'] not in ('晨霧','星海','像素'):raise ValueError('無效主題')
    finite(s['fps'],5,20);finite(s['master'],0,1)
    if s['color_order'] not in ('RGB','RBG','GRB','GBR','BRG','BGR'):raise ValueError('色序無效')
    for name in ('red_gain','green_gain','blue_gain'):finite(s[name],0,2)
    finite(s['gamma'],.15,3);finite(s['idle_minutes'],0,30)
    profiles=s.get('color_profiles',{})
    if not isinstance(profiles,dict) or any(k!='heart' for k in profiles):raise ValueError('校色區域無效')
    for profile in profiles.values():validate_color(profile)
    s['hardware_master']=False
    result.update(settings=s,version=6)
    mode=result.setdefault('active_mode','leisure')
    if mode not in ('leisure','native'):raise ValueError('使用模式無效')
    from native_lighting import validate_native
    native=result.setdefault('native_lighting',dict(mode=1,color='#66D2FF',brightness=5,speed=3,direction=0,colorMode=0))
    validate_native(native)
    schemes=result.setdefault('schemes',{'leisure':[],'native':[]})
    if not isinstance(schemes,dict) or set(schemes)!={'leisure','native'}:raise ValueError('方案格式無效')
    for kind,items in schemes.items():
        if not isinstance(items,list) or len(items)>30:raise ValueError('每種模式最多 30 個方案')
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not 1<=len(item['name'].strip())<=40:raise ValueError('方案名稱無效')
            v=item.get('value')
            if kind=='native':validate_native(v)
            else:
                if not isinstance(v,dict):raise ValueError('方案格式無效')
                finite(v.get('master'),0,1)
                v['groups']=validate({'groups':v.get('groups'),'settings':s})['groups']
    return result

COLOR_FIELDS=('color_order','gamma','red_gain','green_gain','blue_gain')
def validate_color(profile):
    if not isinstance(profile,dict) or set(profile)!=set(COLOR_FIELDS):raise ValueError('校色設定不完整')
    if profile['color_order'] not in ('RGB','RBG','GRB','GBR','BRG','BGR'):raise ValueError('色序無效')
    finite(profile['gamma'],.15,3)
    for key in ('red_gain','green_gain','blue_gain'):finite(profile[key],0,2)
    return copy.deepcopy(profile)

def key_color_settings(settings,key):
    if key in HEART and 'heart' in settings.get('color_profiles',{}):
        return dict(settings,**settings['color_profiles']['heart'])
    return settings

def heartbeat(t,bpm=72):
    p=t*bpm/60%1
    def pulse(center,width):
        u=abs(p-center)/width
        return (1-u*u)**3 if u<1 else 0.
    return min(1,pulse(.16,.12)+.62*pulse(.36,.13))

def dataflow_level(x,y,t,speed):
    # 每條垂直光流使用無左右排序的固定相位，絕不沿 X 依序掃描。
    lane=math.floor(x)
    phase=((lane*73+lane*lane*19)%101)/101*8.2
    head=(t*speed*2+phase)%8.2-1.2
    behind=head-y
    if behind<-.35 or behind>1.5:return 0.
    u=1+behind/.35 if behind<0 else 1-behind/1.5
    return (u*u*(3-2*u))**(1 if behind<0 else 2)

def render(cfg,t,events=(),identify=None):
    colors={k:(0,0,0) for k in ORDER}
    if identify is not None:return {k:((70,170,215) if k in identify else (0,0,0)) for k in ORDER}
    for g in cfg['groups']:
        rgb=tuple(int(g['color'][i:i+2],16) for i in (1,3,5));effect=g['effect']
        for index,k in enumerate(g['keys']):
            phase=t*g['speed'];level=g['brightness']*cfg['settings']['master'];color=rgb
            if effect=='呼吸':level*=.5-.5*math.cos(phase*math.tau)
            elif effect=='彩虹':color=tuple(int(x*255+.5) for x in colorsys.hsv_to_rgb((phase*.2+index/max(1,len(g['keys'])))%1,1,1))
            elif effect=='波浪':level*=(.5+.5*math.sin(phase*math.tau-POSITIONS[k][0]*.55))**2
            elif effect=='閃爍':level*=int(phase*2)%2
            elif effect=='心跳':level*=heartbeat(t,g['bpm'])
            elif effect=='核心資料流':
                level*=dataflow_level(*POSITIONS[k],t,g['speed'])
            elif effect in ('觸鍵星光','星光漣漪'):
                energy=0
                for key,when in events:
                    age=t-when
                    if not 0<=age<=2:continue
                    if effect=='觸鍵星光':v=math.exp(-age*5*g['speed']) if key==k else 0
                    else:
                        x,y=POSITIONS[k];a,b=POSITIONS[key];dist=math.hypot(x-a,y-b)
                        v=math.exp(-((dist-age*8*g['speed'])/.7)**2)*math.exp(-age*1.8)
                    energy=max(energy,v)
                level*=energy
            elif effect=='熄滅':level=0
            # 色彩與亮度分開運算，保留小數直到協議輸出，避免兩次取整。
            bg=g.get('background_color','#000000');floor=g.get('background_brightness',0)*cfg['settings']['master'] if effect not in ('心跳','熄滅') else 0
            background=tuple(int(bg[i:i+2],16)*floor for i in (1,3,5))
            colors[k]=tuple(max(0,min(255,max(v*level,background[i]))) for i,v in enumerate(color))
    return colors

def color_faithful(target,actual):
    total=sum(target);out=sum(actual)
    if total==0:return out==0
    if out==0:return False
    return sum(abs(a/total-b/out) for a,b in zip(target,actual))<=.08+1e-12

def raw_quantize(rgb,wireless):
    steps=(8,4,8) if wireless else (1,1,1)
    limits=(31,63,31) if wireless else (255,255,255)
    return tuple(min(limit,int(n/step+.5))*step for n,step,limit in zip(rgb,steps,limits))

def output_faithful(rgb,q,wireless):
    return color_faithful(rgb,q) and (wireless or color_faithful(tuple(n/d for n,d in zip(rgb,(3,2,2))),tuple(n//d for n,d in zip(q,(3,2,2)))))

@lru_cache(maxsize=2048)
def dark_floor(chroma,wireless):
    """取最後一段無法保色區間的上界，避免漸暗時反覆亮滅。"""
    steps=(8,4,8) if wireless else (1,1,1)
    limits=(31,63,31) if wireless else (255,255,255)
    # 超過此界後，最壞的取整誤差也不會超出色彩比例容許值。
    error=sum(e for n,e in zip(chroma,(7,3,7) if wireless else (.5,.5,.5)) if n)
    upper=26*error/sum(chroma)
    if not wireless:upper=max(upper,26*sum(e for n,e in zip(chroma,(5/6,.75,.75)) if n)/sum(n/d for n,d in zip(chroma,(3,2,2))))
    upper=min(255,upper)
    edges={0.,upper}
    for ratio,step,limit in zip(chroma,steps,limits):
        if ratio:
            edges.update((j+.5)*step/ratio for j in range(limit) if (j+.5)*step/ratio<upper)
    edges=sorted(edges)
    for low,high in zip(reversed(edges[:-1]),reversed(edges[1:])):
        sample=tuple(n*(low+high)/2 for n in chroma)
        if not output_faithful(sample,raw_quantize(sample,wireless),wireless):return high
    return 0.

def quantize_color(rgb,mode='USB'):
    """最後一道量化；無法保留色彩比例的暗值整顆熄滅，不抬高亮度或時間閃爍。"""
    wireless=mode.startswith('2.4G')
    peak=max(rgb)
    if peak==0:return (0,0,0)
    chroma=tuple(round(n/peak,8) for n in rgb)
    if peak+1e-7<dark_floor(chroma,wireless):return (0,0,0)
    quantized=raw_quantize(rgb,wireless)
    # 比較各通道占總能量的比例；L1 <= .08，最多 4% 能量轉往其他通道。
    # 已分析的 v1.26 USB 接收路徑還會 R // 3、G/B // 2。
    # 不反向增益補償，只防止此步驟留下單色暗尾。
    if not output_faithful(rgb,quantized,wireless):return (0,0,0)
    return quantized

def transform_output(colors,settings,mode='USB'):
    result={}
    for k,rgb in colors.items():
        profile=key_color_settings(settings,k)
        gains=[profile.get('red_gain',1),profile.get('green_gain',1),profile.get('blue_gain',1)]
        order=profile.get('color_order','RGB');gamma=profile.get('gamma',1)
        peak=max(rgb)
        if peak==0:result[k]=(0,0,0);continue
        # gamma 只改 HSV 的 V（單一亮度值），不分別對 R/G/B 做冪次。
        chroma=[n/peak*g for n,g in zip(rgb,gains)]
        scale=255*(peak/255)**gamma/max(1,max(chroma))
        v=[n*scale for n in chroma]
        result[k]=quantize_color(tuple(v['RGB'.index(c)]for c in order),mode)
    return result

class Engine:
    def __init__(self,data=DATA):
        self.data=Path(data);self.data.mkdir(parents=True,exist_ok=True);self.lock=threading.RLock();self.config=copy.deepcopy(DEFAULT)
        self.status='待命';self.error='';self.connected='';self.battery=None;self.fps=0;self.sent=0;self.acks=0
        self.hardware_brightness=None;self.hardware_brightness_backup=None
        self.events=[];self.identify=None;self.calibration=None;self.maintenance_lock=threading.Lock();self.stop_event=threading.Event();self.thread=None;self.started=time.monotonic();self.desired=False
        try:
            p=self.data/'config.json'
            if p.exists():
                cfg=json.loads(p.read_text(encoding='utf-8'))
                if 'settings' not in cfg:cfg['settings']=copy.deepcopy(SETTINGS)
                self.config=validate(cfg)
        except Exception as e:self.error='舊設定讀取失敗，改用預設：'+str(e)
    def save(self,cfg):
        valid=validate(cfg)
        with self.lock:
            tmp=self.data/'config.tmp';tmp.write_text(json.dumps(valid,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(self.data/'config.json');self.config=valid
        return valid
    def press(self,key):
        if type(key)is not int or key not in IDS:raise ValueError('未知按鍵')
        with self.lock:
            now=time.monotonic()-self.started
            self.events=[x for x in self.events if now-x[1]<2][-63:]+[(key,now)]
    def set_calibration(self,body):
        rgb=body.get('rgb')
        if rgb is None:
            with self.lock:self.calibration=None
            return
        ids=body.get('keys')
        if not isinstance(rgb,list) or len(rgb)!=3 or any(type(v)is not int or not 0<=v<=255 for v in rgb):raise ValueError('校準顏色無效')
        if not isinstance(ids,list) or not ids or len(ids)>90 or len(ids)!=len(set(ids)) or any(type(v)is not int or v not in IDS for v in ids):raise ValueError('校準選區無效')
        profile=validate_color(body.get('settings'))
        brightness=finite(body.get('brightness',.6),0,1)
        with self.lock:self.calibration=dict(rgb=rgb,keys=ids,settings=profile,brightness=brightness,until=time.monotonic()+60)
    def snapshot(self):
        with self.lock:
            if self.calibration and time.monotonic()>self.calibration['until']:self.calibration=None
            calibration=copy.deepcopy(self.calibration)
            if calibration:calibration['remaining']=max(0,calibration['until']-time.monotonic())
            now=time.monotonic()-self.started;self.events=[x for x in self.events if now-x[1]<2]
            return dict(config=copy.deepcopy(self.config),status=self.status,error=self.error,connection=self.connected,battery=self.battery,
                running=bool(self.thread and self.thread.is_alive()),desired=self.desired,fps=round(self.fps,1),sent=self.sent,acks=self.acks,t=now,
                events=list(self.events),identify=self.identify,calibration=calibration,platform=os.name,
                hardware_brightness=self.hardware_brightness,hardware_brightness_backup=self.hardware_brightness_backup,hardware_brightness_effect_verified=False)
    def start(self):
        with self.lock:
            if self.config.get('active_mode')=='native':
                self.status='低延遲模式 · 請從首頁套用內建燈效';return
            self.desired=True
            if self.thread and self.thread.is_alive():return
            self.stop_event.clear();self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def stop(self):
        self.desired=False;self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            # 等已開始的設定寫入及讀回完成，避免改鍵頁接著開啟同一 HID。
            self.thread.join(timeout=8)
            if self.thread.is_alive():raise OSError('燈光設定操作尚未停止；為避免同時存取鍵盤，請稍後重試')
        with self.lock:self.identify=None;self.calibration=None;self.status='已停止 · 保留最後畫面，可用鍵盤快捷鍵切回內建燈效'
    def run(self):
        from native_hid import enumerate_devices,Device
        device=None;fail=False
        try:
            while not self.stop_event.is_set():
                try:
                    with self.lock:self.status='尋找鍵盤…';self.error='';wanted=self.config['settings']['connection']
                    choices=enumerate_devices()
                    if wanted=='USB':choices=[d for d in choices if d['mode']=='USB']
                    elif wanted=='2.4G':choices=[d for d in choices if d['mode'].startswith('2.4G')]
                    choices.sort(key=lambda d: {'USB':0,'2.4G':1,'2.4G-v1':2}[d['mode']])
                    if not choices:raise OSError('找不到 AEMEATH 控制介面；請確認 USB 或 2.4G 接收器已連接')
                    mode=choices[0]['mode'];same=[d for d in choices if d['mode']==mode]
                    if len(same)!=1:raise OSError('找到多個同類 AEMEATH 接收器，請先保留一組連線')
                    device=Device(same[0]);info=device.info()
                    if mode=='2.4G-v1' and info['lightingVersion']!=1:raise OSError('此 64-byte 接收器不是 lightingVersion=1；請使用 FF80 的 32-byte 控制介面')
                    with self.lock:self.hardware_brightness=None;self.hardware_brightness_backup=None
                    with self.lock:
                        self.connected=mode;self.battery=info['battery'] if mode!='USB' else None
                        self.status='USB 已連線' if mode=='USB' else '2.4G 即時傳送中 · 此路徑尚待你的實機確認'
                    while not self.stop_event.is_set():
                        tick=time.monotonic()
                        with self.lock:cfg=copy.deepcopy(self.config);events=list(self.events);identify=copy.deepcopy(self.identify)
                        idle=cfg['settings'].get('idle_minutes',0)
                        if idle:
                            from windows_tools import idle_seconds
                            if idle_seconds()>=idle*60:
                                self.status='閒置暫停輸出，允許鍵盤休眠';self.stop_event.wait(.5);continue
                            if self.status.startswith('閒置'):self.status=mode+' 已恢復輸出'
                        hardware=False
                        now=tick-self.started;colors=render(cfg,now,events,identify)
                        with self.lock:
                            if self.calibration and time.monotonic()>self.calibration['until']:self.calibration=None
                            cal=copy.deepcopy(self.calibration)
                        output_settings=cfg['settings']
                        if cal:
                            colors={k:tuple(v*cal['brightness'] for v in cal['rgb']) if k in cal['keys'] else (0,0,0) for k in ORDER}
                            output_settings=dict(cfg['settings'],**cal['settings']);output_settings['color_profiles']={}
                        colors=transform_output(colors,output_settings,mode)
                        if hardware and cfg['settings']['master']==0:colors={k:(0,0,0)for k in ORDER}
                        for report in frames(colors,ORDER,mode):
                            if self.stop_event.is_set():break
                            if mode=='USB':reply=device.exchange(report,250,True)
                            else:reply=device.exchange(report,8,False)
                            with self.lock:self.sent+=1;self.acks+=int(reply is not None)
                        target=min(cfg['settings']['fps'],12 if mode!='USB' else 20)
                        self.stop_event.wait(max(0,1/target-(time.monotonic()-tick)))
                        with self.lock:self.fps=1/max(.001,time.monotonic()-tick)
                    break
                except Exception as e:
                    logging.exception('HID 控制中止')
                    if device:device.close();device=None
                    with self.lock:
                        self.error=str(e);self.connected='';self.fps=0;self.status='連線中止';retry=self.config['settings']['reconnect']
                    if not retry:fail=True;break
                    self.status='等待重新連線 · 3 秒後僅重新查詢已知介面'
                    if self.stop_event.wait(3):break
        finally:
            if device:device.close()
            with self.lock:
                self.connected='';self.fps=0
                if fail:self.desired=False
