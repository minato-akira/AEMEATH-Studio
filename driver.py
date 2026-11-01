"""原廠設定協議：讀取後保留未知欄位，備份後寫入，再讀回驗證。"""
import contextlib
import hashlib
import json
import logging
from pathlib import Path
import struct
import time
import uuid
from engine import KEYS,IDS
from protocol import query_table,write_table

HID={k['id']:k['hid']for k in KEYS};KEY_BY_ID={k['id']:k for k in KEYS}
MODS={224,225,226,227,228,229,230,231}
FUNCTION_KEYS={
    0x01:'恢復出廠設定',0x02:'藍牙通道 1',0x03:'藍牙通道 2',0x04:'藍牙通道 3',0x05:'無線回連',
    0x07:'查詢電量',0x08:'Windows 模式',0x09:'Mac 模式',0x0B:'燈光效果切換',0x0C:'燈光顏色切換',
    0x0D:'燈光亮度＋',0x0E:'燈光亮度－',0x0F:'燈光速度＋',0x10:'燈光速度－',0x11:'關閉燈光',
    0x16:'鎖定 Win',0x17:'側燈效果切換',0x18:'側燈顏色切換',0x65:'8K 數據測試',0xB0:'側燈開關',
}
log=logging.getLogger('aemeath.driver')

def describe_action(action):
    op=action.get('operation','unknown');layer=action.get('layer','')
    if op in ('binding','socd'):
        source=action.get('source');source_name=KEY_BY_ID.get(source,{}).get('name',str(source))
        prefix=f'{layer} 層來源鍵={source_name}(LED {source})'
        if op=='socd':
            peer=action.get('peer');peer_name=KEY_BY_ID.get(peer,{}).get('name',str(peer))
            return f'{prefix} SOCD 配對={peer_name}(LED {peer}) 模式={action.get("mode")}'
        value=action.get('binding',{});kind=value.get('type','unknown');target=value.get('target')
        target_name=next((k['name'] for k in KEYS if k['hid']==target),str(target))
        if kind=='default':detail='恢復單鍵原廠映射'
        elif kind in ('key','toggle'):detail=f'{kind} 目標={target_name}(HID {target})'
        elif kind=='shortcut':detail=f'組合鍵 修飾={value.get("modifiers",[])} 目標={target_name}(HID {target})'
        elif kind=='mt':detail=f'MT 輕觸={target_name}(HID {target}) 長按HID={value.get("hold")} 門檻={value.get("delay")}ms'
        elif kind=='end':detail=f'END 按下HID={target} 放開HID={value.get("release")}'
        elif kind=='media':detail=f'多媒體碼={target}'
        elif kind=='macro':detail=f'宏ID={value.get("macro")} 模式={value.get("mode")} 次數={value.get("repeat")}'
        elif kind=='function':
            code=target if type(target)is int else -1
            detail=f'官方功能={FUNCTION_KEYS.get(code,"未知功能")} 代碼={code}'
        else:detail=f'功能={kind}'
        return f'{prefix} {detail}'
    if op=='macros':
        macros=action.get('macros',[])
        return f'寫入宏列表 數量={len(macros)} 宏統計='+','.join(f"M{m.get('id')}:{len(m.get('actions',[]))}動作" for m in macros)
    if op=='device_settings':return f"裝置參數 debounce={action.get('debounce')} reportRate={action.get('reportRate')}"
    if op=='sleep':return f"睡眠設定 minutes={action.get('minutes')}"
    return f'操作={op}'

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
    if t=='function':
        code=integer(p,1,255,'官方 Fn 功能碼')
        if code not in FUNCTION_KEYS:raise ValueError('未知官方 Fn 功能碼，保留原始設定')
        return bytes((13,0,0,code))
    if t=='toggle':return bytes((10,key(p),0,0))
    if t=='mt':
        hold=key(value.get('hold'));delay=integer(value.get('delay',400),10,2550,'MT 門檻')
        if delay%10:raise ValueError('MT 門檻以 10ms 為單位')
        # HFD 協議：Byte[1] 長按鍵，Byte[2] 輕觸鍵，Byte[3] 門檻（10 ms）。
        return bytes((9,hold,key(p),delay//10))
    if t=='end':
        return bytes((14,key(p),key(value.get('release')),0))
    if t=='shortcut':
        modifiers=value.get('modifiers',[])
        if not isinstance(modifiers,list)or not 1<=len(modifiers)<=2 or len(set(modifiers))!=len(modifiers)or any(x not in MODS for x in modifiers):raise ValueError('組合鍵需 1～2 個不同修飾鍵')
        return bytes((7,modifiers[0],modifiers[1]if len(modifiers)>1 else 0,key(p)))
    if t=='macro':return bytes((6,integer(value.get('macro'),0,99,'宏編號'),integer(value.get('mode',0),0,2,'宏模式'),integer(value.get('repeat',1),1,255,'循環次數')if value.get('mode')==1 else 0))
    raise ValueError('不支援的改鍵類型')

def decode_binding(raw):
    t,a,b,c=raw
    if t==13:
        if a==0 and b==0 and c in FUNCTION_KEYS:
            return {'type':'function','raw':list(raw),'label':FUNCTION_KEYS[c]}
        return {'type':'unknown','raw':list(raw),'label':f'未知官方功能碼 0x{c:02X}'}
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
        for index,action in enumerate(actions,1):
            delay=integer(action.get('delay',5),0,65535,'動作延遲')
            if type(action.get('isPress'))is not bool:raise ValueError('宏按下／放開格式無效')
            kind=action.get('actionType',3)
            if kind==5:
                code=integer(action.get('keyCode',0),0,0,'宏延遲鍵碼')
                if action['isPress']:raise ValueError('宏延遲不可設定為按下事件')
                record.extend(struct.pack('<HBB',delay,code,0x50))
                continue
            if kind not in (1,3):raise ValueError('宏只支援已確認的鍵盤、滑鼠按鍵與延遲動作；未知原始宏請保留')
            code=action.get('keyCode')
            if type(code)is not int or not 1<=code<=255:raise ValueError(f'M{mid+1} 第{index}步宏按鍵碼無效：{code!r}；允許 1～255')
            if kind==1 and code not in (1,2,4):raise ValueError(f'M{mid+1} 第{index}步滑鼠宏按鍵無效')
            token=(kind,code)
            if action['isPress']:
                if token in down:raise ValueError('宏重複按下同一鍵，請先放開')
                down.add(token)
            else:
                if token not in down:raise ValueError('宏先放開未按下的鍵')
                down.remove(token)
            flags=(0x80 if action['isPress'] else 0)|(kind<<4)
            record.extend(struct.pack('<HBB',delay,code,flags))
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
        actions=[];editable=True;issues=[];down=set()
        for i in range(count):
            delay,keyCode,flags=struct.unpack_from('<HBB',raw,offset+4+i*4)
            actionType=(flags>>4)&7
            event_index=i+1;isPress=bool(flags&128);valid_event=False
            if actionType==1:
                valid_event=keyCode in (1,2,4)and flags in (0x90,0x10)
                if not valid_event:issues.append(f'第{event_index}步滑鼠事件資料無效')
            elif actionType==3:
                valid_event=1<=keyCode<=255 and flags in (0xb0,0x30)
                if not valid_event:issues.append(f'第{event_index}步鍵盤鍵碼／事件旗標無效（鍵碼 {keyCode}）')
            elif actionType==5:
                valid_event=keyCode==0 and flags==0x50 and not isPress
                if not valid_event:issues.append(f'第{event_index}步延遲資料無效')
            else:issues.append(f'第{event_index}步動作類型 {actionType} 不支援')
            editable &= valid_event
            if actionType in (1,3)and valid_event:
                token=(actionType,keyCode)
                if isPress:
                    if token in down:
                        editable=False;issues.append(f'第{event_index}步重複按下未放開的按鍵')
                    else:down.add(token)
                elif token not in down:
                    editable=False;issues.append(f'第{event_index}步放開了尚未按下的按鍵')
                else:down.remove(token)
            actions.append(dict(delay=delay,keyCode=keyCode,isPress=bool(flags&128),actionType=actionType))
        if down:editable=False;issues.append('宏結束時仍有按鍵未放開')
        result.append(dict(id=mid,name=f'宏 {mid+1}',actions=actions,editable=editable,validationIssue='；'.join(dict.fromkeys(issues))))
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
                r=json.loads(p.read_text(encoding='utf-8'));out.append(dict(name=p.name,description=r['description'],created=r['created'],simulated=r.get('simulated',False)))
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
        summary=describe_action(action);log.info('裝置操作開始：%s preview=%s',summary,self.preview)
        with self.device()as(d,info):
            if op in ('binding','socd'):
                layer=action.get('layer','normal')
                if layer not in ('normal','fn'):raise ValueError('未知鍵層')
                if layer=='fn'and op=='socd':raise ValueError('Fn 層僅支援官方 Fn 功能與單鍵原廠還原')
                get,cmd=(0x12,0x22)if layer=='normal'else(0x16,0x26)
                before=self.read(d,get,512);raw=bytearray(before);source=action.get('source')
                if type(source)is not int or source not in IDS or source==85:raise ValueError('來源按鍵無效，Fn 鍵保留為復原入口')
                if layer=='fn'and 1<=source<=12:raise ValueError('依官方配置保留 Fn+F1～F12 功能')
                # 解除來源原有的成對 SOCD，避免留下孤立關聯。
                previous=raw[source*4:source*4+4]
                if previous[0]==11:
                    for k in IDS:
                        if raw[k*4:k*4+4]==previous:raw[k*4:k*4+4]=bytes((2,0,HID[k],0))
                if op=='socd':
                    peer=action.get('peer');mode=integer(action.get('mode'),1,4,'SOCD 模式')
                    if type(peer)is not int or peer not in IDS or peer in (85,source) or (layer=='fn'and 1<=peer<=12):raise ValueError('SOCD 第二鍵無效')
                    prior=raw[peer*4:peer*4+4]
                    if prior[0]==11:
                        for k in IDS:
                            if raw[k*4:k*4+4]==prior:raw[k*4:k*4+4]=bytes((2,0,HID[k],0))
                    value=bytes((11,mode,HID[source],HID[peer]));raw[source*4:source*4+4]=value;raw[peer*4:peer*4+4]=value
                else:
                    value=action.get('binding',{})
                    if layer=='fn'and value.get('type')not in ('default','function'):raise ValueError('Fn 層僅支援官方 Fn 功能與單鍵原廠還原')
                    if value.get('type')=='function'and layer!='fn':raise ValueError('官方 Fn 特殊功能只能設定在 Fn 鍵層')
                    if value.get('type')=='macro':
                        capacity=min(5120,info.get('macro_capacity',4608));stored=decode_macros(self.read(d,0x15,capacity))
                        if not any(m['id']==value.get('macro')and m['actions']for m in stored):raise ValueError('請先把此宏儲存到鍵盤，再綁定按鍵')
                    # 韌體以「映射至自身 HID」表示單鍵原廠功能；Fn 與一般層都不可用四個零，否則讀回會被正規化而驗證失敗。
                    encoded=bytes((2,0,HID[source],0))if value.get('type')=='default'else binding(value)
                    raw[source*4:source*4+4]=encoded
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
                requested=action.get('macros')
                if not isinstance(requested,list):raise ValueError('宏列表格式無效')
                requested_ids={item.get('id')for item in requested if isinstance(item,dict)and type(item.get('id'))is int}
                invalid=[macro for macro in existing if not macro['editable']]
                retained=[macro for macro in invalid if macro['id']in requested_ids]
                if retained:
                    detail='；'.join(f"M{macro['id']+1}：{macro.get('validationIssue')or'含不支援的原始動作'}"for macro in retained)
                    raise ValueError('以下宏有無效動作，先從宏清單刪除並重建，才可寫入：'+detail)
                removed={macro['id']for macro in invalid if macro['id']not in requested_ids}
                if removed:
                    bindings=[]
                    for layer,table in (('一般',self.read(d,0x12,512)),('Fn',self.read(d,0x16,512))):
                        for key_id in IDS:
                            bind=table[key_id*4:key_id*4+4]
                            if bind[0]==6 and bind[1]in removed:
                                key_name=KEY_BY_ID.get(key_id,{}).get('name',str(key_id))
                                bindings.append(f'{layer}層 {key_name}(LED {key_id})→M{bind[1]+1}')
                    if bindings:raise ValueError('異常宏仍被按鍵使用，為避免留下失效綁定，請先解除：'+'；'.join(bindings))
                    for macro in invalid:
                        if macro['id']in removed:log.warning('使用者要求移除未綁定的無效宏 M%s：%s',macro['id']+1,macro.get('validationIssue')or'不支援的原始動作')
                raw=encode_macros(requested,capacity);desc='更新宏列表'
            else:raise ValueError('未知裝置操作')
            backup=self.backup(cmd,before,desc)
            self.write(d,cmd,raw);after=self.read(d,get,len(raw))
            if after!=bytes(raw):
                expected=bytes(raw);size=max(len(after),len(expected));diffs=[(i,expected[i]if i<len(expected)else None,after[i]if i<len(after)else None)for i in range(size)if i>=len(expected)or i>=len(after)or expected[i]!=after[i]]
                log.error('裝置讀回驗證失敗：%s command=0x%02X expected_len=%d actual_len=%d mismatch_count=%d first_diffs=%s backup=%s',summary,cmd,len(expected),len(after),len(diffs),diffs[:24],backup)
                raise OSError('寫入後讀回不一致，已停止；請使用備份還原：'+backup)
        log.info('裝置操作完成並驗證：%s mode=%s backup=%s preview=%s',summary,d.mode,backup,self.preview)
        return dict(ok=True,verified=True,backup=backup,simulated=self.preview)
    def restore(self,name):
        if not isinstance(name,str)or Path(name).name!=name:raise ValueError('備份名稱無效')
        log.info('設定還原開始：backup=%s preview=%s',name,self.preview)
        r=json.loads((self.folder/name).read_text(encoding='utf-8'));cmd=r['command'];raw=bytes.fromhex(r['before'])
        if cmd not in (0x21,0x22,0x25,0x26):raise ValueError('未知備份命令')
        if bool(r.get('simulated'))!=self.preview:raise ValueError('預覽備份不能寫入實體鍵盤，實體備份也不寫入預覽裝置')
        with self.device()as(d,info):
            get={0x23:0x13,0x21:0x11,0x22:0x12,0x25:0x15,0x26:0x16}[cmd]
            cap={0x21:56,0x22:512,0x26:512,0x25:min(5120,info.get('macro_capacity',4608))}[cmd]
            if len(raw)!=cap:raise ValueError('備份容量與此裝置不符')
            current=self.read(d,get,cap);self.backup(cmd,current,'還原前備份');self.write(d,cmd,raw)
            if self.read(d,get,cap)!=raw:raise OSError('還原後讀回驗證不一致')
        log.info('設定還原完成並驗證：backup=%s command=0x%02X preview=%s',name,cmd,self.preview)
        return dict(ok=True,verified=True,simulated=self.preview)
