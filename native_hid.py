"""Windows 系統 HID API；只依賴 Python 標準函式庫，沒有額外 .pyd / .dll。"""
import ctypes as C
import sys
import time
from protocol import check_report,query_info
U8=C.c_uint8;U16=C.c_uint16;U32=C.c_uint32;UP=C.c_size_t;PTR=C.c_void_p
class GUID(C.Structure):_fields_=[('a',U32),('b',U16),('c',U16),('d',U8*8)]
class INTERFACE(C.Structure):_fields_=[('size',U32),('guid',GUID),('flags',U32),('reserved',UP)]
class ATTRS(C.Structure):_fields_=[('size',U32),('vid',U16),('pid',U16),('version',U16)]
class CAPS(C.Structure):_fields_=[('usage',U16),('page',U16),('input',U16),('output',U16),('feature',U16),('reserved',U16*17),('tail',U16*10)]
class OVERLAPPED(C.Structure):_fields_=[('internal',UP),('internal_high',UP),('offset',U32),('offset_high',U32),('event',PTR)]
INVALID=C.c_void_p(-1).value

def api():
    if sys.platform!='win32':raise OSError('實體 HID 控制需使用 Windows 10／11；此環境可使用預覽')
    kernel=C.WinDLL('kernel32',use_last_error=True);hid=C.WinDLL('hid',use_last_error=True);setup=C.WinDLL('setupapi',use_last_error=True)
    def f(d,n,args,ret):
        fn=getattr(d,n);fn.argtypes=args;fn.restype=ret;return fn
    f(kernel,'CreateFileW',[C.c_wchar_p,U32,U32,PTR,U32,U32,PTR],PTR)
    f(kernel,'CloseHandle',[PTR],C.c_int)
    f(kernel,'CreateEventW',[PTR,C.c_int,C.c_int,C.c_wchar_p],PTR)
    f(kernel,'WaitForSingleObject',[PTR,U32],U32)
    f(kernel,'CancelIoEx',[PTR,C.POINTER(OVERLAPPED)],C.c_int)
    f(kernel,'GetOverlappedResult',[PTR,C.POINTER(OVERLAPPED),C.POINTER(U32),C.c_int],C.c_int)
    for name in ('ReadFile','WriteFile'):f(kernel,name,[PTR,PTR,U32,C.POINTER(U32),C.POINTER(OVERLAPPED)],C.c_int)
    f(hid,'HidD_GetHidGuid',[C.POINTER(GUID)],None)
    f(hid,'HidD_GetAttributes',[PTR,C.POINTER(ATTRS)],U8)
    f(hid,'HidD_GetPreparsedData',[PTR,C.POINTER(PTR)],U8)
    f(hid,'HidD_FreePreparsedData',[PTR],U8)
    f(hid,'HidP_GetCaps',[PTR,C.POINTER(CAPS)],C.c_int32)
    f(setup,'SetupDiGetClassDevsW',[C.POINTER(GUID),C.c_wchar_p,PTR,U32],PTR)
    f(setup,'SetupDiEnumDeviceInterfaces',[PTR,PTR,C.POINTER(GUID),U32,C.POINTER(INTERFACE)],C.c_int)
    f(setup,'SetupDiGetDeviceInterfaceDetailW',[PTR,C.POINTER(INTERFACE),PTR,U32,C.POINTER(U32),PTR],C.c_int)
    f(setup,'SetupDiDestroyDeviceInfoList',[PTR],C.c_int)
    return kernel,hid,setup

def enumerate_devices():
    k,h,s=api();guid=GUID();h.HidD_GetHidGuid(C.byref(guid))
    info=s.SetupDiGetClassDevsW(C.byref(guid),None,None,0x12)
    if info==INVALID:raise C.WinError(C.get_last_error())
    devices=[]
    try:
        idx=0
        while True:
            interface=INTERFACE();interface.size=C.sizeof(interface)
            if not s.SetupDiEnumDeviceInterfaces(info,None,C.byref(guid),idx,C.byref(interface)):
                error=C.get_last_error()
                if error==259:break
                raise C.WinError(error)
            idx+=1;length=U32()
            s.SetupDiGetDeviceInterfaceDetailW(info,C.byref(interface),None,0,C.byref(length),None)
            if length.value<8:continue
            detail=C.create_string_buffer(length.value);C.cast(detail,C.POINTER(U32))[0]=8 if C.sizeof(PTR)==8 else 6
            if not s.SetupDiGetDeviceInterfaceDetailW(info,C.byref(interface),detail,length,C.byref(length),None):continue
            path=C.wstring_at(C.addressof(detail)+4);lower=path.lower()
            # 在開啟任何裝置前先按 VID/PID 過濾。
            if 'vid_0c45' not in lower or not any(p in lower for p in ('pid_271b','pid_fefc')):continue
            handle=k.CreateFileW(path,0,3,None,3,0,None)
            if handle==INVALID:continue
            try:
                attrs=ATTRS();attrs.size=C.sizeof(attrs);caps=CAPS();pp=PTR()
                if not h.HidD_GetAttributes(handle,C.byref(attrs)):continue
                if not h.HidD_GetPreparsedData(handle,C.byref(pp)):continue
                try:status=h.HidP_GetCaps(pp,C.byref(caps))
                finally:h.HidD_FreePreparsedData(pp)
                if status<0:continue
                expected={(0x271b,0xff68,97,65):'USB',(0xfefc,0xff80,1,33):'2.4G',(0xfefc,0xff70,113,65):'2.4G-v1'}
                mode=expected.get((attrs.pid,caps.page,caps.usage,caps.output))
                if mode and attrs.vid==0x0c45:
                    devices.append(dict(path=path,mode=mode,input=caps.input,output=caps.output,page=caps.page,pid=attrs.pid,version=attrs.version))
            finally:k.CloseHandle(handle)
    finally:s.SetupDiDestroyDeviceInfoList(info)
    return devices

class Device:
    def __init__(self,description):
        self.k,self.h,self.s=api();self.desc=description;self.mode=description['mode'];self.length=description['output']-1
        self.handle=self.k.CreateFileW(description['path'],0xc0000000,3,None,3,0x40000000,None)
        if self.handle==INVALID:raise C.WinError(C.get_last_error())
    def io(self,data=None,timeout=250):
        read=data is None
        size=self.desc['input'] if read else len(data)
        if not 1<=size<=65:raise ValueError('不允許大型介面')
        buf=C.create_string_buffer(size) if read else C.create_string_buffer(data,size)
        ov=OVERLAPPED();ov.event=self.k.CreateEventW(None,True,False,None)
        if not ov.event:raise C.WinError(C.get_last_error())
        count=U32()
        try:
            fn=self.k.ReadFile if read else self.k.WriteFile
            ok=fn(self.handle,buf,size,C.byref(count),C.byref(ov))
            if not ok:
                error=C.get_last_error()
                if error!=997:raise C.WinError(error)
                wait=self.k.WaitForSingleObject(ov.event,timeout)
                if wait!=0:
                    self.k.CancelIoEx(self.handle,C.byref(ov))
                    # OVERLAPPED 與 buffer 必須保留至取消完成；不可提早釋放。
                    self.k.GetOverlappedResult(self.handle,C.byref(ov),C.byref(count),True)
                    if wait==258:raise TimeoutError('HID 等待逾時')
                    raise C.WinError(C.get_last_error())
                if not self.k.GetOverlappedResult(self.handle,C.byref(ov),C.byref(count),False):raise C.WinError(C.get_last_error())
            return bytes(buf.raw[:count.value]) if read else count.value
        finally:self.k.CloseHandle(ov.event)
    def write(self,report):
        check_report(report)
        if len(report)!=self.length:raise ValueError('封包與介面大小不符')
        count=self.io(b'\0'+report)
        if count!=len(report)+1:raise OSError('HID 寫入長度不符')
    def read(self,timeout=250):
        data=self.io(timeout=timeout)
        if len(data)==self.desc['input'] and data[0]==0:data=data[1:]
        return data
    def exchange(self,report,timeout=250,strict=True):
        self.write(report);deadline=time.monotonic()+timeout/1000
        while time.monotonic()<deadline:
            try:reply=self.read(max(1,int((deadline-time.monotonic())*1000)))
            except TimeoutError:break
            if len(reply)>=2 and reply[:2]==bytes((0x55,report[1])):
                if not strict or reply[2:8]==report[2:8]:return reply
        if strict:raise TimeoutError('鍵盤未回覆，已停止；請確認連線並關閉官方驅動')
        return None
    def info(self):
        raw=bytearray()
        for offset in range(0,48,self.length-8):
            req=query_info(self.length,offset)
            reply=self.exchange(req,600,True)
            raw.extend(reply[8:8+req[2]])
        if len(raw)<33:raise OSError('裝置資訊不完整')
        return {'macro_capacity':int.from_bytes(raw[2:4],'little') or 4608,'lightingVersion':raw[31],'battery':raw[17],'raw':raw.hex()}
    def close(self):
        if self.handle not in (None,INVALID):self.k.CloseHandle(self.handle);self.handle=None
