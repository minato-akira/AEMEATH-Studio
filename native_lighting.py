"""內建燈效單次寫入；讀回等待收斂，永不自動重送設定。"""
import copy,json,time,uuid
import logging
from protocol import header

def validate_native(v):
    if not isinstance(v,dict):raise ValueError('內建燈效格式錯誤')
    for key,lo,hi in [('mode',0,19),('brightness',1,5),('speed',1,5),('direction',0,3),('colorMode',0,1)]:
        if type(v.get(key))is not int or not lo<=v[key]<=hi:raise ValueError('內建燈效參數無效：'+key)
    if v['mode']==10 and v['direction'] not in (2,3):raise ValueError('多彩縱橫僅支援上下方向')
    if v['mode'] in (11,12,16,18) and v['direction'] not in (0,1):raise ValueError('此模式僅支援左右方向')
    c=v.get('color','')
    if not isinstance(c,str) or len(c)!=7 or c[0]!='#':raise ValueError('顏色格式無效')
    int(c[1:],16)
    return v

# v1.26 GET13 組裝的實際欄位；其餘位元組不是完整儲存區的鏡像。
FIELDS={0:'mode',1:'red',2:'green',3:'blue',8:'colorMode',9:'brightness',10:'speed',11:'direction'}
LABELS={'mode':'燈效','red':'紅色','green':'綠色','blue':'藍色','colorMode':'色彩模式','brightness':'亮度','speed':'速度','direction':'方向'}
class NativeLightingError(OSError):
    def __init__(self,message,diagnostic):super().__init__(message);self.diagnostic=diagnostic

def apply_native(driver,value):
    v=copy.deepcopy(validate_native(value));driver.engine.stop()
    log=logging.getLogger('aemeath.native_lighting')
    log.info('開始套用內建燈效 preview=%s mode=%s brightness=%s',driver.preview,v['mode'],v['brightness'])
    folder=driver.engine.data/'lighting-backups';folder.mkdir(exist_ok=True)
    name=time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.json'
    record=dict(kind='native-lighting',simulated=driver.preview,requested=v,stage='read-before',reads=[],writes=0)
    def save():
        text=json.dumps(record,ensure_ascii=False,indent=2)
        (folder/name).write_text(text,encoding='utf-8')
        # 供介面下載最近一次操作，不暴露任意檔案路徑。
        tmp=folder/'latest.tmp';tmp.write_text(text,encoding='utf-8');tmp.replace(folder/'latest-native.json')
    try:
        with driver.device() as(d,info):
            record['connection']=d.mode
            def read():
                req=header(0x13,bytes(16),size=d.length);r=d.exchange(req,2000,True)
                if not r or len(r)<24 or r[:8]!=b'\x55'+req[1:8]:raise OSError('讀取內建燈效回覆不符')
                return bytes(r[8:24])
            old=read();payload=bytearray(old)
            payload[0]=v['mode'];payload[1:4]=bytes.fromhex(v['color'][1:]);payload[4]=255
            payload[8]=v['colorMode'];payload[9]=v['brightness'];payload[10]=v['speed'];payload[11]=v['direction'];payload[14:16]=b'\xaa\x55'
            record.update(original=old.hex(),payload=payload.hex(),stage='write');save()
            req=header(0x23,bytes(payload),size=d.length);record['writes']=1
            r=d.exchange(req,2000,True);record['ack']=r.hex() if r else None
            if not r or r[:24]!=b'\x55'+req[1:24]:raise OSError('寫入回覆不符；未自動重試')
            record['stage']='readback'
            # 僅重讀，不重送 0x23；避免非同步載入時把舊狀態當成最終狀態。
            for delay in (.12,.2,.35,.6):
                time.sleep(delay);actual=read();record['reads'].append(actual.hex())
                # GET13 的 RGB 是執行狀態回報，裝置會回傳 FF，不能驗證寫入顏色。
                # 顏色差異留在診斷記錄中，其餘可靠欄位仍須完全符合。
                diffs=[dict(field=FIELDS[i],requested=payload[i],actual=actual[i]) for i in FIELDS if i not in (1,2,3) and actual[i]!=payload[i]]
                record['color_readback']={'requested':list(payload[1:4]),'actual':list(actual[1:4]),'verified':False}
                record['differences']=diffs;record['actual']={name:actual[i] for i,name in FIELDS.items()}
                save()
                if not diffs:break
            if diffs:
                detail='；'.join(f"{LABELS[x['field']]}：要求 {x['requested']}、讀回 {x['actual']}" for x in diffs)
                raise OSError('內建燈效讀回未通過：'+detail+'。未自動重寫，請下載診斷記錄。')
            cfg=driver.engine.snapshot()['config'];cfg['active_mode']='native';cfg['native_lighting']=dict(v);driver.engine.save(cfg)
            record['stage']='verified';record['ok']=True;save()
        driver.engine.status=f"內建燈效 · 亮度 {v['brightness']}/5 已確認；顏色讀回不提供驗證，停止持續傳送"
        return dict(ok=True,simulated=driver.preview,actual=record['actual'],color_verified=False)
    except Exception as exc:
        log.exception('內建燈效套用失敗 stage=%s writes=%s',record.get('stage'),record.get('writes'))
        record.update(ok=False,error=str(exc));save()
        raise NativeLightingError(str(exc),record) from exc
