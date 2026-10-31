"""原廠設定協議：讀取後保留未知欄位，備份後寫入，再讀回驗證。"""
import contextlib
import hashlib
import json
from pathlib import Path
import struct
import time
import uuid
from engine import KEYS,IDS
from protocol import query_table,write_table

HID={k['id']:k['hid']for k in KEYS};KEY_BY_ID={k['id']:k for k in KEYS}
MODS={224,225,226,227,228,229,230,231}

def integer(value,lo,hi,name='數值'):
    if type(value)is not int or not lo<=value<=hi:raise ValueError(name+'超出範圍')
    return value

def binding(value):
    t=value.get('type');p=value.get('target',0)
    def key(v):return integer(v,1,255,'HID 按鍵')
    if t=='default':return bytes(4)
    if t=='key':return bytes((2,0,key(p),0))
    if t=='media':
        p=integer(p,1,65535,'多媒體碼');return bytes((3,p&255,p>>8,0))
    if t=='toggle':return bytes((10,key(p),0,0))
    if t=='mt':
        hold=key(value.get('hold'));delay=integer(value.get('delay',400),10,2550,'MT 門檻')
        if delay%10:raise ValueError('MT 門檻以 10ms 為單位')
        return bytes((9,key(p),hold,delay//10))
    if t=='shortcut':
        modifiers=value.get('modifiers',[])
        if not isinstance(modifiers,list)or not 1<=len(modifiers)<=2 or len(set(modifiers))!=len(modifiers)or any(x not in MODS for x in modifiers):raise ValueError('組合鍵需 1～2 個不同修飾鍵')
        return bytes((7,modifiers[0],modifiers[1]if len(modifiers)>1 else 0,key(p)))
    if t=='macro':return bytes((6,integer(value.get('macro'),0,99,'宏編號'),integer(value.get('mode',0),0,2,'宏模式'),integer(value.get('repeat',1),1,255,'循環次數')if value.get('mode')==1 else 0))
    raise ValueError('不支援的改鍵類型')

def decode_binding(raw):
    t,a,b,c=raw
    return {'type':{0:'default',2:'key',3:'media',6:'macro',7:'shortcut',9:'mt',10:'toggle',11:'socd',14:'end'}.get(t,'unknown'),'raw':list(raw),'label':{0:'原廠',2:'改鍵',3:'多媒體',6:'宏',7:'組合鍵',9:'MT 雙重功能',10:'TGL 切換保持',11:'SOCD',14:'END'}.get(t,f'原始類型 {t}')}

def encode_macros(macros,capacity):
    if not isinstance(macros,list)or len(macros)>100:raise ValueError('宏數量需為 0～100')
    directory=bytearray(400);records=bytearray();seen=set()
    for macro in sorted(macros,key=lambda x:x['id']):
        mid=integer(macro.get('id'),0,99,'宏編號')
        if mid in seen:raise ValueError('重複宏編號')
        seen.add(mid);actions=macro.get('actions')
        if not isinstance(actions,list):raise ValueError('宏動作格式無效')
        if not actions:continue
        if len(actions)>1024:raise ValueError('單宏最多 1024 個動作')
        record=bytearray(struct.pack('<HBB',len(actions)*2,0,0));down=set()
        for action in actions:
            delay=integer(action.get('delay',5),0,65535,'動作延遲');code=integer(action.get('keyCode'),1,255,'宏按鍵')
            if type(action.get('isPress'))is not bool:raise ValueError('宏按下／放開格式無效')
            kind=action.get('actionType',3)
            if kind!=3:raise ValueError('此版宏編輯器僅寫入鍵盤動作；未知原始宏請保留')
            token=(kind,code)
            if action['isPress']:
                if token in down:raise ValueError('宏重複按下同一鍵，請先放開')
                down.add(token)
            else:
                if token not in down:raise ValueError('宏先放開未按下的鍵')
                down.remove(token)
            record.extend(struct.pack('<HBB',delay,code,0xb0 if action['isPress']else 0x30))
        if down:raise ValueError('宏結束前必須放開所有按鍵')
        offset=400+len(records);struct.pack_into('<I',directory,mid*4,offset);records.extend(record)
    raw=bytes(directory+records)
    if len(raw)>capacity:raise ValueError(f'宏容量不足：需要 {len(raw)} bytes，可用 {capacity} bytes')
    return raw

def decode_macros(raw):
    result=[]
    if len(raw)<400:raise ValueError('宏索引不完整')
    for mid in range(100):
        offset=struct.unpack_from('<I',raw,mid*4)[0]
        if offset in (0,0xffffffff):continue
        if not 400<=offset<=len(raw)-4:raise ValueError(f'宏 {mid} 索引超出可用容量，停止修改')
        count=struct.unpack_from('<H',raw,offset)[0]//2
        if count>1024 or offset+4+count*4>len(raw):raise ValueError('宏資料長度異常，停止修改')
        actions=[];editable=True
        for i in range(count):
            delay,keyCode,flags=struct.unpack_from('<HBB',raw,offset+4+i*4)
            actionType=(flags>>4)&7;editable&=actionType==3
            actions.append(dict(delay=delay,keyCode=keyCode,isPress=bool(flags&128),actionType=actionType))
        result.append(dict(id=mid,name=f'宏 {mid+1}',actions=actions,editable=editable))
    return result

class DemoDevice:
    mode='USB';length=64
    def __init__(self):self.tables={0x13:bytearray([1,255,255,255,0,0,0,0,0,5,3,0,0,0,0,0]),0x11:bytearray(56),0x12:bytearray(512),0x16:bytearray(512),0x15:bytearray(4608)}
    def info(self):return {'macro_capacity':4608,'battery':100,'lightingVersion':0}
    def exchange(self,report,timeout=600,strict=True):
        cmd=report[1];offset=int.from_bytes(report[3:5],'little');n=report[2]
        if cmd in self.tables:
            payload=bytes(self.tables[cmd][offset:offset+n]);return b'\x55'+report[1:8]+payload+bytes(self.length-8-n)
        get={0x23:0x13,0x21:0x11,0x22:0x12,0x25:0x15,0x26:0x16}[cmd];self.tables[get][offset:offset+n]=report[8:8+n]
        if cmd==0x23:self.tables[get][4]=0;self.tables[get][14:16]=bytes(2)
        return b'\x55'+report[1:]
    def close(self):pass

class Driver:
    def __init__(self,engine,preview=False):self.engine=engine;self.preview=preview;self.demo=DemoDevice();self.folder=engine.data/'backups';self.folder.mkdir(exist_ok=True)
    @contextlib.contextmanager
    def device(self):
        from native_hid import enumerate_devices,Device
        with self.engine.maintenance_lock:
            resume=self.engine.desired;self.engine.stop();device=None;ok=False
            try:
                if self.preview:device=self.demo
                else:
                    wanted=self.engine.config['settings']['connection'];choices=enumerate_devices()
                    choices=[d for d in choices if d['mode'] in ('USB','2.4G') and(wanted=='自動'or d['mode']==wanted)]
                    choices.sort(key=lambda d:d['mode']!='USB')
                    if not choices:raise OSError('找不到一般控制介面；請連接 USB 或 2.4G 並喚醒鍵盤')
                    if sum(d['mode']==choices[0]['mode']for d in choices)>1:raise OSError('請保留一組 AEMEATH 連線')
                    device=Device(choices[0])
                info=device.info();yield device,info;ok=True
            finally:
                if device:device.close()
                if resume and ok:self.engine.start()
    @staticmethod
    def read(d,cmd,length):
        raw=bytearray();n=d.length-8
        for offset in range(0,length,n):
            count=min(n,length-offset);request=query_table(cmd,d.length,offset,count,offset+count==length)
            r=d.exchange(request,1000,True)
            if len(r)<8+count:raise OSError('裝置讀回資料長度不足')
            raw.extend(r[8:8+count])
        return bytes(raw)
    @staticmethod
    def write(d,cmd,raw):
        if cmd==0x25:
            reports=write_table(cmd,raw[:400],d.length,0,False)+write_table(cmd,raw[400:],d.length,400,True)
        else:reports=write_table(cmd,raw,d.length)
        for r in reports:d.exchange(r,1200,True)
    def backup(self,cmd,raw,desc):
        name=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.json'
        record=dict(command=cmd,before=raw.hex(),description=desc,created=time.time(),simulated=self.preview)
        (self.folder/name).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8');return name
    def backups(self):
        out=[]
        for p in sorted(self.folder.glob('*.json'),reverse=True)[:20]:
            try:
                r=json.loads(p.read_text());out.append(dict(name=p.name,description=r['description'],created=r['created'],simulated=r.get('simulated',False)))
            except Exception:pass
        return out
    def load(self):
        with self.device()as(d,info):
            mode=self.read(d,0x11,56);normal=self.read(d,0x12,512);fn=self.read(d,0x16,512)
            capacity=min(5120,info.get('macro_capacity',4608))
            if capacity<400:raise ValueError('裝置宏容量資訊異常')
            macroRaw=self.read(d,0x15,capacity);macros=decode_macros(macroRaw)
        return dict(simulated=self.preview,mode=d.mode,sleep=mode[3],debounce=mode[4],reportRate=mode[5],systemMode=mode[6],macroCapacity=capacity,
            normal={k:decode_binding(normal[k*4:k*4+4])for k in IDS},fn={k:decode_binding(fn[k*4:k*4+4])for k in IDS},macros=macros,backups=self.backups())
    def change(self,action):
        op=action.get('operation')
        with self.device()as(d,info):
            if op in ('binding','socd'):
                layer=action.get('layer','normal')
                if layer not in ('normal','fn'):raise ValueError('未知鍵層')
                get,cmd=(0x12,0x22)if layer=='normal'else(0x16,0x26)
                before=self.read(d,get,512);raw=bytearray(before);source=action.get('source')
                if type(source)is not int or source not in IDS or source==85:raise ValueError('來源按鍵無效，Fn 鍵保留為復原入口')
                if layer=='fn'and 1<=source<=12:raise ValueError('依官方配置保留 Fn+F1～F12 功能')
                # 解除來源原有的成對 SOCD，避免留下孤立關聯。
                previous=raw[source*4:source*4+4]
                if previous[0]==11:
                    for k in IDS:
                        if raw[k*4:k*4+4]==previous:raw[k*4:k*4+4]=bytes(4)
                if op=='socd':
                    peer=action.get('peer');mode=integer(action.get('mode'),1,4,'SOCD 模式')
                    if type(peer)is not int or peer not in IDS or peer in (85,source) or (layer=='fn'and 1<=peer<=12):raise ValueError('SOCD 第二鍵無效')
                    prior=raw[peer*4:peer*4+4]
                    if prior[0]==11:
                        for k in IDS:
                            if raw[k*4:k*4+4]==prior:raw[k*4:k*4+4]=bytes(4)
                    value=bytes((11,mode,HID[source],HID[peer]));raw[source*4:source*4+4]=value;raw[peer*4:peer*4+4]=value
                else:
                    value=action.get('binding',{})
                    if value.get('type')=='macro':
                        capacity=min(5120,info.get('macro_capacity',4608));stored=decode_macros(self.read(d,0x15,capacity))
                        if not any(m['id']==value.get('macro')and m['actions']for m in stored):raise ValueError('請先把此宏儲存到鍵盤，再綁定按鍵')
                    raw[source*4:source*4+4]=binding(value)
                desc=f'{layer} 改鍵 LED {source}'
            elif op=='sleep':
                get,cmd=0x11,0x21;before=self.read(d,get,56);raw=bytearray(before);raw[3]=integer(action.get('minutes'),0,30,'睡眠分鐘');desc=f'睡眠 {raw[3]} 分鐘'
            elif op=='device_settings':
                get,cmd=0x11,0x21;before=self.read(d,get,56);raw=bytearray(before)
                raw[4]=integer(action.get('debounce'),1,5,'按鍵延遲等級')
                rate=action.get('reportRate')
                if rate is not None:
                    if d.mode!='USB'or rate not in (3,5,6):raise ValueError('輪詢率設定只支援 USB 的 1000／4000／8000 Hz')
                    raw[5]=rate
                desc='更新按鍵延遲與輪詢率'
            elif op=='macros':
                get,cmd=0x15,0x25;capacity=min(5120,info.get('macro_capacity',4608));before=self.read(d,get,capacity)
                existing=decode_macros(before)
                if any(not m['editable']for m in existing):raise ValueError('鍵盤含此版不支援的原始宏動作；先匯出備份，不覆寫這些宏')
                raw=encode_macros(action.get('macros'),capacity);desc='更新宏列表'
            else:raise ValueError('未知裝置操作')
            backup=self.backup(cmd,before,desc)
            self.write(d,cmd,raw);after=self.read(d,get,len(raw))
            if after!=bytes(raw):raise OSError('寫入後讀回不一致，已停止；請使用備份還原：'+backup)
        return dict(ok=True,verified=True,backup=backup,simulated=self.preview)
    def restore(self,name):
        if not isinstance(name,str)or Path(name).name!=name:raise ValueError('備份名稱無效')
        r=json.loads((self.folder/name).read_text());cmd=r['command'];raw=bytes.fromhex(r['before'])
        if cmd not in (0x21,0x22,0x25,0x26):raise ValueError('未知備份命令')
        if bool(r.get('simulated'))!=self.preview:raise ValueError('預覽備份不能寫入實體鍵盤，實體備份也不寫入預覽裝置')
        with self.device()as(d,info):
            get={0x23:0x13,0x21:0x11,0x22:0x12,0x25:0x15,0x26:0x16}[cmd]
            cap={0x21:56,0x22:512,0x26:512,0x25:min(5120,info.get('macro_capacity',4608))}[cmd]
            if len(raw)!=cap:raise ValueError('備份容量與此裝置不符')
            current=self.read(d,get,cap);self.backup(cmd,current,'還原前備份');self.write(d,cmd,raw)
            if self.read(d,get,cap)!=raw:raise OSError('還原後讀回驗證不一致')
        return dict(ok=True,verified=True,simulated=self.preview)
