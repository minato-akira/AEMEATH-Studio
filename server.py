import argparse
import copy
import json
import logging
from logging.handlers import RotatingFileHandler
import mimetypes
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from engine import Engine,DATA,BASE,KEYS,DEFAULT,EFFECTS,validate,migrate_legacy_data,cleanup_legacy_data,migrate_legacy_logs,cleanup_legacy_logs
from driver import Driver
from audit_trail import AuditTrail,NoticeRequired
from windows_tools import Tray,autostart_enabled,set_autostart,diagnostics,global_key_loop,focus_aemeath_window,refresh_aemeath_window
from version import APP_VERSION,APP_VERSION_LABEL,APP_BUILD_ID

PORT=47635

def _port_available(host,port):
    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    try:
        if os.name=='nt' and hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        sock.bind((host,port))
        return True
    except OSError:return False
    finally:sock.close()

def _running_instance(runtime,url):
    # Discovery exposes identity only; the token stays in the running package.
    # Read that package's receipt locally, then require an authenticated response.
    identity=None
    try:
        with urllib.request.urlopen(url+'api/instance',timeout=1.5) as response:
            candidate=json.load(response)
        if (isinstance(candidate,dict) and candidate.get('app')=='AEMEATH-Studio'
                and type(candidate.get('pid')) is int and candidate['pid']>0
                and isinstance(candidate.get('base_path'),str)
                and Path(candidate['base_path']).is_absolute()):
            identity=candidate
            runtime=Path(candidate['base_path'])/'data'/'runtime.json'
    except (OSError,ValueError):pass
    try:info=json.loads(runtime.read_text(encoding='utf-8'))
    except (OSError,ValueError):return None
    if not isinstance(info,dict):return None
    if identity and any(info.get(k)!=identity.get(k) for k in ('pid','version','build_id','base_path')):return None
    token=info.get('token')
    if not isinstance(token,str) or not token:return None
    try:
        req=urllib.request.Request(url+'api/state',headers={'X-Aemeath-Token':token})
        with urllib.request.urlopen(req,timeout=1.5) as response:state=json.load(response)
        if not isinstance(state,dict):return None
        if identity and state.get('instance')!=identity:return None
        info['_ui_active']=bool(state.get('ui_active',False))
        return info
    except (OSError,ValueError):return None


def _ask_keep_instance(existing,theme='晨霧'):
    old_version=existing.get('version') or '無法識別的舊版'
    old_path=existing.get('base_path') or '未提供安裝路徑'
    same_version=old_version==APP_VERSION
    old_label=f'v{old_version}（舊修訂）' if same_version else old_version
    current_label=APP_VERSION_LABEL + ('（本次修訂）' if same_version else '')
    palettes={
        '晨霧':{'bg':'#f9f6fc','panel':'#ffffff','text':'#333650','muted':'#6d6d86','accent':'#a875a6','accent_text':'#ffffff','border':'#e9e1ee','soft':'#f2eaf6'},
        '星海':{'bg':'#111522','panel':'#1e2033','text':'#e1dfed','muted':'#b4b1cb','accent':'#e5a0c8','accent_text':'#261a27','border':'#444058','soft':'#292b41'},
        '像素':{'bg':'#e6dcf3','panel':'#efe5fb','text':'#564476','muted':'#715c90','accent':'#9a65a6','accent_text':'#ffffff','border':'#bda6d8','soft':'#dfd0ed'},
    }
    colors=palettes.get(theme,palettes['晨霧']);choice={'value':None}
    try:
        import tkinter as tk
        root=tk.Tk();root.title('AEMEATH · 版本選擇');root.resizable(False,False);root.configure(bg=colors['bg']);root.attributes('-topmost',True)
        width,height=540,390;root.update_idletasks();x=(root.winfo_screenwidth()-width)//2;y=(root.winfo_screenheight()-height)//2
        root.geometry(f'{width}x{height}+{x}+{y}')
        font=('Microsoft JhengHei UI',10)
        card=tk.Frame(root,bg=colors['panel'],highlightthickness=1,highlightbackground=colors['border'])
        card.pack(fill='both',expand=True,padx=20,pady=20)
        tk.Label(card,text='版本選擇',bg=colors['panel'],fg=colors['text'],font=('Microsoft JhengHei UI',17,'bold')).pack(anchor='w',padx=22,pady=(20,3))
        explanation=('版本號相同，但程式修訂不同。請選擇要保留的程式。' if same_version else '偵測到另一個程式版本。請選擇要保留的程式。')
        tk.Label(card,text=explanation,bg=colors['panel'],fg=colors['muted'],font=font,wraplength=470,justify='left').pack(anchor='w',padx=22,pady=(0,15))
        info=tk.Frame(card,bg=colors['soft'],highlightthickness=1,highlightbackground=colors['border'])
        info.pack(fill='x',padx=22,pady=(0,13))
        tk.Label(info,text='目前正在執行',bg=colors['soft'],fg=colors['muted'],font=('Microsoft JhengHei UI',9)).pack(anchor='w',padx=13,pady=(10,0))
        tk.Label(info,text=old_label,bg=colors['soft'],fg=colors['text'],font=('Microsoft JhengHei UI',11,'bold')).pack(anchor='w',padx=13,pady=(1,0))
        tk.Label(info,text=old_path,bg=colors['soft'],fg=colors['muted'],font=('Microsoft JhengHei UI',8),wraplength=450,justify='left').pack(anchor='w',padx=13,pady=(1,10))
        tk.Label(info,text='本次要啟動',bg=colors['soft'],fg=colors['muted'],font=('Microsoft JhengHei UI',9)).pack(anchor='w',padx=13,pady=(5,0))
        tk.Label(info,text=current_label,bg=colors['soft'],fg=colors['text'],font=('Microsoft JhengHei UI',11,'bold')).pack(anchor='w',padx=13,pady=(1,10))
        tk.Label(card,text='切換版本會正常結束目前程式，並重整既有設定頁。',bg=colors['panel'],fg=colors['muted'],font=('Microsoft JhengHei UI',9),wraplength=470,justify='left').pack(anchor='w',padx=22,pady=(0,14))
        actions=tk.Frame(card,bg=colors['panel']);actions.pack(fill='x',padx=22,pady=(0,19))
        def finish(value):choice['value']=value;root.grab_release();root.destroy()
        def button(text,command,bg,fg,border,default=False):
            return tk.Button(actions,text=text,command=command,bg=bg,fg=fg,activebackground=colors['soft'],activeforeground=colors['text'],relief='flat',bd=0,highlightthickness=1,highlightbackground=border,highlightcolor=colors['accent'],font=('Microsoft JhengHei UI',9,'bold' if default else 'normal'),padx=12,pady=9,cursor='hand2')
        button('保留執行中版本',lambda:finish(True),colors['soft'],colors['text'],colors['border']).pack(side='left')
        button('改用本次版本',lambda:finish(False),colors['accent'],colors['accent_text'],colors['accent'],True).pack(side='left',padx=(8,0))
        button('取消',lambda:finish(None),colors['panel'],colors['muted'],colors['border']).pack(side='right')
        root.protocol('WM_DELETE_WINDOW',lambda:finish(None));root.bind('<Escape>',lambda _event:finish(None))
        root.grab_set();root.focus_force();root.mainloop()
        return choice['value']
    except Exception as error:
        raise OSError(f'偵測到其他版本，但無法顯示版本選擇視窗：{error}') from error

def _close_running_instance(existing,url):
    token=existing.get('token')
    if not isinstance(token,str) or not token:return False
    request=urllib.request.Request(
        url+'api/quit',data=b'{}',method='POST',
        headers={'Content-Type':'application/json','X-Aemeath-Token':token})
    try:
        with urllib.request.urlopen(request,timeout=2) as response:response.read()
    except Exception:return False
    deadline=time.monotonic()+6
    while time.monotonic()<deadline:
        if _port_available('127.0.0.1',PORT):return True
        time.sleep(.15)
    return False

def _resolve_existing_instance(runtime,url,no_browser,log,theme='晨霧'):
    for _ in range(12):
        existing=_running_instance(runtime,url)
        if existing:
            old_version=existing.get('version')
            if old_version==APP_VERSION and existing.get('build_id')==APP_BUILD_ID:
                ui_active=existing.get('_ui_active',False)
                log.info('偵測到相同修訂既有程序 version=%s build=%s pid=%s ui_active=%s',old_version,APP_BUILD_ID,existing.get('pid'),ui_active)
                focused=focus_aemeath_window('愛彌斯・絨雪輕歌｜鍵盤控制台') if not no_browser else False
                if not no_browser and not focused and not ui_active:webbrowser.open(url)
                return None
            log.info('偵測到不同或未知修訂 old=%s/%s current=%s/%s pid=%s',old_version,existing.get('build_id','unknown'),APP_VERSION,APP_BUILD_ID,existing.get('pid'))
            choice=_ask_keep_instance(existing,theme)
            if choice is True:
                log.info('使用者選擇保留既有版本 version=%s',old_version)
                focused=focus_aemeath_window('愛彌斯・絨雪輕歌｜鍵盤控制台') if not no_browser else False
                if not no_browser and not focused and not existing.get('_ui_active',False):webbrowser.open(url)
                return None
            if choice is None:
                log.info('使用者取消版本切換')
                return None
            if not _close_running_instance(existing,url):
                raise OSError('無法安全關閉執行中的版本。請先從系統匣結束它，再重新啟動本版本。')
            log.info('已正常關閉既有版本，準備啟動 version=%s',APP_VERSION)
            return existing
        if _port_available('127.0.0.1',PORT):return {}
        time.sleep(.25)
    raise OSError(f'連接埠 {PORT} 已被其他程式佔用，且無法驗證其為 AEMEATH 程序；請先關閉佔用程式。')

def serve(no_browser=False,startup=False,preview=False):
    DATA.mkdir(parents=True,exist_ok=True)
    log_dir=DATA/'logs';log_dir.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(handlers=[RotatingFileHandler(log_dir/'studio.log',maxBytes=600000,backupCount=2,encoding='utf-8')],level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    log=logging.getLogger('aemeath.server')
    migrate_legacy_data(log)
    migrate_legacy_logs(log)
    token=secrets.token_urlsafe(32);engine=Engine();closing=threading.Event();url=f'http://127.0.0.1:{PORT}/'
    runtime=DATA/'runtime.json';web=BASE/'web'
    audit=AuditTrail(DATA,BASE/'使用聲明.md',APP_VERSION,APP_BUILD_ID,preview)
    instance=dict(app='AEMEATH-Studio',pid=os.getpid(),version=APP_VERSION,build_id=APP_BUILD_ID,base_path=str(BASE))
    ui_clients={};ui_clients_lock=threading.Lock()
    def mark_ui_client(client_id):
        if not isinstance(client_id,str) or not 0<len(client_id)<=128:return
        with ui_clients_lock:ui_clients[client_id]=time.monotonic()
    def ui_is_active():
        now=time.monotonic()
        with ui_clients_lock:
            for client_id,last_seen in list(ui_clients.items()):
                if now-last_seen>8:ui_clients.pop(client_id,None)
            return bool(ui_clients)
    if preview:
        def run_preview():
            engine.status='預覽執行中 · 未接觸硬體';engine.error=''
            while not engine.stop_event.wait(.1):pass
        engine.run=run_preview
    def show():
        if focus_aemeath_window('愛彌斯・絨雪輕歌｜鍵盤控制台'):
            log.info('系統匣已聚焦既有設定頁')
            return
        if ui_is_active():
            log.info('系統匣開啟請求略過：設定頁已開啟')
            return
        webbrowser.open(url)
    def quit_app():closing.set()
    driver=Driver(engine,preview)
    tray=Tray(show,engine.stop,quit_app,BASE/'app.ico')
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,format,*args):
            log.info('HTTP '+format,*args)
        def valid_host(self):return self.headers.get('Host')==f'127.0.0.1:{PORT}'
        def send(self,status,data,mime='application/json; charset=utf-8'):
            if not isinstance(data,bytes):data=json.dumps(data,ensure_ascii=False).encode('utf-8')
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers();self.wfile.write(data)
        def authorized(self):
            if not self.valid_host():return False
            provided=self.headers.get('X-Aemeath-Token','')
            if secrets.compare_digest(provided,token):return True
            return False
        def do_GET(self):
            if not self.valid_host():self.send(403,{'error':'Host denied'});return
            path=urllib.parse.urlsplit(self.path).path
            if path=='/api/instance':
                self.send(200,instance);return
            if path=='/api/notice':
                if not self.authorized():self.send(403,{'error':'Token denied'});return
                try:self.send(200,audit.info())
                except (OSError,ValueError):self.send(503,{'error':'無法讀取聲明紀錄，請確認程式檔案與資料夾權限'})
                return
            if path=='/api/state':
                if not self.authorized():self.send(403,{'error':'Token denied'});return
                mark_ui_client(self.headers.get('X-Aemeath-Client'))
                state=engine.snapshot();state.update(tray=tray.ready,tray_error=tray.error,autostart_actual=autostart_enabled(),preview=preview,ui_active=ui_is_active(),instance=instance)
                self.send(200,state);return
            if path.startswith('/api/'):self.send(404,{});return
            name='index.html' if path=='/' else urllib.parse.unquote(path.lstrip('/'))
            target=(web/name).resolve()
            if not target.is_relative_to(web.resolve()) or not target.is_file():self.send(404,{'error':'Not found'});return
            data=target.read_bytes()
            if name=='index.html':data=data.replace(b'__APP_TOKEN__',token.encode())
            mime=mimetypes.guess_type(name)[0] or 'application/octet-stream'
            if name.endswith('.js'):mime='application/javascript'
            self.send(200,data,mime+('; charset=utf-8' if mime.startswith('text/') or name.endswith('.js') else ''))
        def do_POST(self):
            path=urllib.parse.urlsplit(self.path).path
            if not self.authorized():
                self.close_connection=True
                self.send(403,{'error':'Token denied'});return
            client_id=self.headers.get('X-Aemeath-Client')
            mark_ui_client(client_id)
            origin=self.headers.get('Origin')
            if origin and origin!=url.rstrip('/'):
                self.close_connection=True
                self.send(403,{'error':'Origin denied'});return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                self.close_connection=True
                self.send(415,{});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=2000000:
                    self.close_connection=True
                    raise ValueError('請求大小無效')
                body=json.loads(self.rfile.read(size))
                if path=='/api/notice/decision':
                    self.send(200,audit.decision(body.get('notice_sha256'),body.get('decision')))
                elif path=='/api/ui-close':
                    with ui_clients_lock:ui_clients.pop(client_id,None)
                    self.send(200,{'ok':True})
                elif path=='/api/native-diagnostic':
                    p=engine.data/'lighting-backups'/'latest-native.json'
                    self.send(200,json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'error':'尚無內建燈效操作記錄'})
                elif path=='/api/config':
                    cfg=validate(body);old=autostart_enabled();new=cfg['settings']['autostart']
                    try:
                        if old!=new:set_autostart(new)
                        engine.save(cfg)
                    except Exception:
                        if old!=new:set_autostart(old)
                        raise
                    self.send(200,{'ok':True,'config':cfg})
                elif path=='/api/native-lighting':
                    from native_lighting import apply_native
                    self.send(200,audit.run('native-lighting',body,lambda:apply_native(driver,body)))
                elif path=='/api/start':
                    def start_lighting():
                        with engine.maintenance_lock:engine.start()
                        return {'ok':True,'simulated':preview}
                    self.send(200,audit.run('start',body,start_lighting))
                elif path=='/api/stop':engine.stop();self.send(200,{'ok':True})
                elif path=='/api/key':engine.press(body.get('id'));self.send(200,{'ok':True})
                elif path=='/api/identify':
                    ids=body.get('keys')
                    if ids is not None and (not isinstance(ids,list) or len(ids)>90 or any(type(x)is not int or x not in set(k['id'] for k in KEYS) for x in ids)):raise ValueError('選區無效')
                    with engine.lock:engine.identify=ids
                    self.send(200,{'ok':True})
                elif path=='/api/driver/read':self.send(200,driver.load())
                elif path=='/api/driver/write':self.send(200,audit.run('driver/write',body,lambda:driver.change(body)))
                elif path=='/api/driver/restore':self.send(200,audit.run('driver/restore',body,lambda:driver.restore(body.get('name'))))
                elif path=='/api/calibrate':
                    if body.get('rgb') is None:
                        engine.set_calibration(body);self.send(200,{'ok':True})
                    else:self.send(200,audit.run('calibrate',body,lambda:engine.set_calibration(body)))
                elif path=='/api/diagnostics':
                    report=diagnostics();state=engine.snapshot();report['lighting']={key:state[key] for key in ('status','error','connection','fps','sent','acks')};self.send(200,report)
                elif path=='/api/quit':self.send(200,{'ok':True});closing.set()
                else:self.send(404,{})
            except NoticeRequired as e:
                self.send(428,{'error':str(e),'code':'notice_required'})
            except (ValueError,KeyError,TypeError) as e:
                log.warning('API 請求拒絕 path=%s reason=%s',path,str(e))
                self.send(400,{'error':str(e)})
            except Exception as e:
                log.exception('API 執行失敗 path=%s',path);result={'error':str(e)}
                if hasattr(e,'diagnostic'):result['diagnostic']=e.diagnostic
                self.send(500,result)
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        allow_reuse_address=False
        def server_bind(self):
            if os.name=='nt' and hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            super().server_bind()
    handoff=None
    try:server=Server(('127.0.0.1',PORT),Handler)
    except OSError:
        handoff=_resolve_existing_instance(runtime,url,no_browser,log,engine.config.get('settings',{}).get('theme','晨霧'))
        if handoff is None:return
        migrate_legacy_data(log)
        migrate_legacy_logs(log)
        engine=Engine()
        if preview:engine.run=run_preview
        driver=Driver(engine,preview)
        tray=Tray(show,engine.stop,quit_app,BASE/'app.ico')
        try:server=Server(('127.0.0.1',PORT),Handler)
        except OSError as error:raise OSError(f'已選擇保留本次版本，但連接埠 {PORT} 尚未釋放；請稍後再啟動。') from error
    cleanup_legacy_data(log)
    cleanup_legacy_logs(log)
    runtime.write_text(json.dumps({'token':token,'pid':os.getpid(),'version':APP_VERSION,'build_id':APP_BUILD_ID,'base_path':str(BASE)}),encoding='utf-8')
    log.info('應用程式啟動 version=%s build=%s preview=%s startup=%s port=%s',APP_VERSION,APP_BUILD_ID,preview,startup,PORT)
    threading.Thread(target=server.serve_forever,daemon=True).start();tray.start()
    threading.Thread(target=global_key_loop,args=(engine,KEYS,closing),daemon=True).start()
    notice_pending=False
    if startup and engine.config['settings']['auto_effects']:
        try:audit.run('start',{},engine.start,source='autostart')
        except NoticeRequired:
            notice_pending=True;engine.status='請先在介面確認使用聲明，再開始燈效'
        except OSError:
            notice_pending=True;engine.status='無法保存操作紀錄，請檢查 data 資料夾權限';log.exception('自動啟動未執行')
    # 沒有系統匣時不可完全隱藏，保留可以開啟/結束程式的視窗入口。
    if startup:
        for _ in range(30):
            if tray.ready or tray.error:break
            time.sleep(.05)
    hidden=startup and engine.config['settings']['start_hidden'] and tray.ready and not notice_pending
    if not no_browser and not hidden:
        if handoff:
            if refresh_aemeath_window('愛彌斯・絨雪輕歌｜鍵盤控制台'):
                log.info('版本交接後已重整並沿用既有設定頁')
            else:show()
        elif not ui_is_active():show()
        else:log.info('啟動時已沿用既有設定頁，略過新增瀏覽器頁面')
    try:
        while not closing.wait(.5):pass
    except KeyboardInterrupt:pass
    finally:
        log.info('應用程式結束')
        engine.stop();tray.stop();server.shutdown();server.server_close()
        try:runtime.unlink()
        except OSError:pass

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--no-browser',action='store_true');parser.add_argument('--startup',action='store_true');parser.add_argument('--preview',action='store_true')
    args=parser.parse_args();serve(args.no_browser,args.startup,args.preview)
if __name__=='__main__':main()
