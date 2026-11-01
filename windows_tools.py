import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

RUN_KEY=r'Software\Microsoft\Windows\CurrentVersion\Run';RUN_NAME='AEMEATHResonanceStudio'

def autostart_enabled():
    if sys.platform!='win32':return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:return bool(winreg.QueryValueEx(key,RUN_NAME)[0])
    except OSError:return False

def set_autostart(enabled):
    if sys.platform!='win32':
        if enabled:raise OSError('開機自啟動只支援 Windows')
        return
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN_KEY) as key:
        if enabled:
            exe=Path(sys.executable).with_name('pythonw.exe')
            if not exe.exists():exe=Path(sys.executable)
            script=Path(__file__).with_name('Launch.pyw').resolve()
            # 僅修改本使用者、本程式的單一 Run 值，不需要管理員權限。
            winreg.SetValueEx(key,RUN_NAME,0,winreg.REG_SZ,subprocess.list2cmdline([str(exe),str(script),'--startup']))
        else:
            try:winreg.DeleteValue(key,RUN_NAME)
            except FileNotFoundError:pass

def diagnostics():
    result={'python':sys.version,'executable':sys.executable,'platform':sys.platform,'autostart':autostart_enabled(),
        'dependencies':'僅 Python 標準函式庫與 Windows 系統 HID DLL；無 pip、.bat 或第三方原生擴充',
        'message':'本程式不修改智慧型應用程式控制、安全原則或防毒設定。'}
    if sys.platform=='win32':
        # 固定唯讀指令；不執行下載腳本，不改 ExecutionPolicy。
        command="[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-CodeIntegrity/Operational'; Id=3077,3033; StartTime=(Get-Date).AddDays(-2)} -MaxEvents 8 -ErrorAction Stop | Select-Object TimeCreated,Id,Message | ConvertTo-Json -Depth 3"
        try:
            p=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,timeout=12,creationflags=0x08000000)
            result['code_integrity']=p.stdout.decode('utf-8-sig',errors='replace')[:20000] if p.returncode==0 else '無法讀取或沒有近期事件：'+p.stderr.decode('utf-8',errors='replace')[:1500]
        except Exception as e:result['code_integrity']=str(e)
    return result

def virtual_key(code):
    if code.startswith('Key') and len(code)==4:return ord(code[-1])
    if code.startswith('Digit') and len(code)==6:return ord(code[-1])
    if code.startswith('Numpad') and code[-1:].isdigit():return 0x60+int(code[-1])
    if code.startswith('F') and code[1:].isdigit():return 0x70+int(code[1:])-1
    return {'Escape':27,'Backquote':0xc0,'Minus':0xbd,'Equal':0xbb,'Backspace':8,'Tab':9,'BracketLeft':0xdb,'BracketRight':0xdd,
        'Backslash':0xdc,'CapsLock':20,'Semicolon':0xba,'Quote':0xde,'Enter':13,'ShiftLeft':0xa0,'ShiftRight':0xa1,
        'Comma':0xbc,'Period':0xbe,'Slash':0xbf,'ControlLeft':0xa2,'ControlRight':0xa3,'MetaLeft':0x5b,'AltLeft':0xa4,
        'AltRight':0xa5,'Space':32,'ContextMenu':0x5d,'ArrowLeft':37,'ArrowUp':38,'ArrowRight':39,'ArrowDown':40,
        'Delete':46,'NumpadDecimal':0x6e}.get(code)

def global_key_loop(engine,keys,closing):
    if sys.platform!='win32':return
    user=C.WinDLL('user32',use_last_error=True);user.GetAsyncKeyState.argtypes=[C.c_int];user.GetAsyncKeyState.restype=C.c_int16
    mapping={virtual_key(k['code']):k['id'] for k in keys if virtual_key(k['code']) is not None};previous=set()
    while not closing.wait(.02):
        with engine.lock:enabled=engine.config['settings']['global_keys']
        if not enabled:previous=set();continue
        current={v for v in mapping if user.GetAsyncKeyState(v)&0x8000}
        for v in current-previous:engine.press(mapping[v])
        previous=current

class Tray:
    """原生 Windows 系統匣。UI 執行緒只處理選單，不直接存取 HID。"""
    def __init__(self,show,pause,quit_app,icon):
        self.show=show;self.pause=pause;self.quit_app=quit_app;self.icon_path=str(icon);self.ready=False;self.error='';self.hwnd=None
    def start(self):
        if sys.platform=='win32':threading.Thread(target=self.run,daemon=True).start()
    def stop(self):
        if self.hwnd:
            u=C.WinDLL('user32');u.PostMessageW.argtypes=[C.c_void_p,C.c_uint32,C.c_size_t,C.c_ssize_t];u.PostMessageW(self.hwnd,0x10,0,0)
    def run(self):
        from ctypes import wintypes as W
        try:
            U=C.WinDLL('user32',use_last_error=True);S=C.WinDLL('shell32',use_last_error=True);K=C.WinDLL('kernel32',use_last_error=True)
            CALLBACK=C.WINFUNCTYPE(C.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
            class WNDCLASS(C.Structure):
                _fields_=[('style',W.UINT),('proc',CALLBACK),('class_extra',C.c_int),('window_extra',C.c_int),('instance',W.HINSTANCE),('icon',W.HICON),('cursor',W.HANDLE),('background',W.HBRUSH),('menu',W.LPCWSTR),('name',W.LPCWSTR)]
            class NID(C.Structure):
                _fields_=[('size',W.DWORD),('hwnd',W.HWND),('id',W.UINT),('flags',W.UINT),('message',W.UINT),('icon',W.HICON),('tip',W.WCHAR*128),('state',W.DWORD),('state_mask',W.DWORD),('info',W.WCHAR*256),('timeout',W.UINT),('title',W.WCHAR*64),('info_flags',W.DWORD)]
            def f(d,n,args,ret):fn=getattr(d,n);fn.argtypes=args;fn.restype=ret;return fn
            f(K,'GetModuleHandleW',[W.LPCWSTR],W.HMODULE)
            f(U,'DefWindowProcW',[W.HWND,W.UINT,W.WPARAM,W.LPARAM],C.c_ssize_t)
            f(U,'RegisterClassW',[C.POINTER(WNDCLASS)],W.ATOM)
            f(U,'CreateWindowExW',[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID],W.HWND)
            f(U,'CreatePopupMenu',[],W.HMENU);f(U,'AppendMenuW',[W.HMENU,W.UINT,C.c_size_t,W.LPCWSTR],W.BOOL)
            f(U,'TrackPopupMenu',[W.HMENU,W.UINT,C.c_int,C.c_int,C.c_int,W.HWND,W.LPVOID],W.BOOL)
            f(U,'DestroyMenu',[W.HMENU],W.BOOL);f(U,'SetForegroundWindow',[W.HWND],W.BOOL)
            f(U,'DestroyWindow',[W.HWND],W.BOOL);f(U,'GetCursorPos',[C.POINTER(W.POINT)],W.BOOL)
            f(U,'LoadImageW',[W.HINSTANCE,W.LPCWSTR,W.UINT,C.c_int,C.c_int,W.UINT],W.HANDLE)
            f(U,'DestroyIcon',[W.HICON],W.BOOL)
            f(U,'GetMessageW',[C.POINTER(W.MSG),W.HWND,W.UINT,W.UINT],C.c_int)
            f(U,'TranslateMessage',[C.POINTER(W.MSG)],W.BOOL);f(U,'DispatchMessageW',[C.POINTER(W.MSG)],C.c_ssize_t)
            f(U,'PostQuitMessage',[C.c_int],None);f(S,'Shell_NotifyIconW',[W.DWORD,C.POINTER(NID)],W.BOOL)
            self.nid=NID();self.nid.size=C.sizeof(NID)
            @CALLBACK
            def proc(hwnd,msg,wp,lp):
                try:
                    if msg==0x8001:
                        if lp==0x203:self.show();return 0
                        if lp==0x205:
                            menu=U.CreatePopupMenu()
                            for number,text in [(101,'開啟心律光譜'),(102,'停止燈光控制'),(103,'結束程式')]:U.AppendMenuW(menu,0,number,text)
                            point=W.POINT();U.GetCursorPos(C.byref(point));U.SetForegroundWindow(hwnd)
                            U.TrackPopupMenu(menu,0,point.x,point.y,0,hwnd,None);U.DestroyMenu(menu);return 0
                    if msg==0x111:
                        fn={101:self.show,102:self.pause,103:self.quit_app}.get(int(wp)&65535)
                        if fn:threading.Thread(target=fn,daemon=True).start()
                        return 0
                    if msg==0x10:U.DestroyWindow(hwnd);return 0
                    if msg==0x2:S.Shell_NotifyIconW(2,C.byref(self.nid));U.PostQuitMessage(0);return 0
                except Exception:pass
                return U.DefWindowProcW(hwnd,msg,wp,lp)
            instance=K.GetModuleHandleW(None);wc=WNDCLASS();wc.proc=proc;wc.instance=instance;wc.name='AEMEATHResonanceTray'
            if not U.RegisterClassW(C.byref(wc)):raise C.WinError(C.get_last_error())
            hwnd=U.CreateWindowExW(0,wc.name,'AEMEATH',0,0,0,0,0,None,None,instance,None)
            if not hwnd:raise C.WinError(C.get_last_error())
            self.hwnd=hwnd;icon=U.LoadImageW(None,self.icon_path,1,32,32,0x10)
            self.nid.hwnd=hwnd;self.nid.id=1;self.nid.flags=7;self.nid.message=0x8001;self.nid.icon=icon;self.nid.tip='AEMEATH 心律光譜 · 雙擊開啟'
            if not S.Shell_NotifyIconW(0,C.byref(self.nid)):raise OSError('系統匣圖示建立失敗')
            self.ready=True;message=W.MSG()
            while U.GetMessageW(C.byref(message),None,0,0)>0:U.TranslateMessage(C.byref(message));U.DispatchMessageW(C.byref(message))
            if icon:U.DestroyIcon(icon)
        except Exception as e:self.error=str(e)
        finally:self.ready=False;self.hwnd=None


def idle_seconds():
    if sys.platform!='win32':return 0
    class LASTINPUT(C.Structure):_fields_=[('size',C.c_uint32),('tick',C.c_uint32)]
    u=C.WinDLL('user32');k=C.WinDLL('kernel32');u.GetLastInputInfo.argtypes=[C.POINTER(LASTINPUT)];u.GetLastInputInfo.restype=C.c_int;k.GetTickCount.restype=C.c_uint32
    info=LASTINPUT();info.size=C.sizeof(info)
    if not u.GetLastInputInfo(C.byref(info)):return 0
    return ((k.GetTickCount()-info.tick)&0xffffffff)/1000
