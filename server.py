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
from engine import Engine,DATA,BASE,KEYS,DEFAULT,EFFECTS,validate
from driver import Driver
from windows_tools import Tray,autostart_enabled,set_autostart,diagnostics,global_key_loop

PORT=47635

def serve(no_browser=False,startup=False,preview=False):
    DATA.mkdir(parents=True,exist_ok=True)
    log_dir=BASE/'logs';log_dir.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(handlers=[RotatingFileHandler(log_dir/'studio.log',maxBytes=600000,backupCount=2,encoding='utf-8')],level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    log=logging.getLogger('aemeath.server')
    token=secrets.token_urlsafe(32);engine=Engine();closing=threading.Event();url=f'http://127.0.0.1:{PORT}/'
    runtime=DATA/'runtime.json';web=BASE/'web'
    if preview:
        def run_preview():
            engine.status='預覽執行中 · 未接觸硬體';engine.error=''
            while not engine.stop_event.wait(.1):pass
        engine.run=run_preview
    def show():webbrowser.open(url)
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
        def authorized(self):return self.valid_host() and secrets.compare_digest(self.headers.get('X-Aemeath-Token',''),token)
        def do_GET(self):
            if not self.valid_host():self.send(403,{'error':'Host denied'});return
            path=urllib.parse.urlsplit(self.path).path
            if path=='/api/state':
                if not self.authorized():self.send(403,{'error':'Token denied'});return
                state=engine.snapshot();state.update(tray=tray.ready,tray_error=tray.error,autostart_actual=autostart_enabled(),preview=preview)
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
            if not self.authorized():self.send(403,{'error':'Token denied'});return
            origin=self.headers.get('Origin')
            if origin and origin!=url.rstrip('/'):self.send(403,{'error':'Origin denied'});return
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':self.send(415,{});return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=2000000:raise ValueError('請求大小無效')
                body=json.loads(self.rfile.read(size))
                if path=='/api/native-diagnostic':
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
                    self.send(200,apply_native(driver,body))
                elif path=='/api/start':
                    with engine.maintenance_lock:engine.start()
                    self.send(200,{'ok':True})
                elif path=='/api/stop':engine.stop();self.send(200,{'ok':True})
                elif path=='/api/key':engine.press(body.get('id'));self.send(200,{'ok':True})
                elif path=='/api/identify':
                    ids=body.get('keys')
                    if ids is not None and (not isinstance(ids,list) or len(ids)>90 or any(type(x)is not int or x not in set(k['id'] for k in KEYS) for x in ids)):raise ValueError('選區無效')
                    with engine.lock:engine.identify=ids
                    self.send(200,{'ok':True})
                elif path=='/api/driver/read':self.send(200,driver.load())
                elif path=='/api/driver/write':self.send(200,driver.change(body))
                elif path=='/api/driver/restore':self.send(200,driver.restore(body.get('name')))
                elif path=='/api/calibrate':
                    engine.set_calibration(body)
                    self.send(200,{'ok':True})
                elif path=='/api/diagnostics':
                    report=diagnostics();state=engine.snapshot();report['lighting']={key:state[key] for key in ('status','error','connection','fps','sent','acks')};self.send(200,report)
                elif path=='/api/quit':self.send(200,{'ok':True});closing.set()
                else:self.send(404,{})
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
    try:server=Server(('127.0.0.1',PORT),Handler)
    except OSError:
        try:
            old=json.loads(runtime.read_text(encoding='utf-8'))
            req=urllib.request.Request(url+'api/state',headers={'X-Aemeath-Token':old['token']})
            with urllib.request.urlopen(req,timeout=2) as r:json.load(r)
            if not no_browser:webbrowser.open(url)
            return
        except Exception:raise OSError(f'連接埠 {PORT} 被其他程式佔用；請先結束舊版或稍後再開啟')
    runtime.write_text(json.dumps({'token':token,'pid':os.getpid()}),encoding='utf-8')
    log.info('應用程式啟動 preview=%s startup=%s port=%s',preview,startup,PORT)
    threading.Thread(target=server.serve_forever,daemon=True).start();tray.start()
    threading.Thread(target=global_key_loop,args=(engine,KEYS,closing),daemon=True).start()
    if startup and engine.config['settings']['auto_effects']:engine.start()
    # 沒有系統匣時不可完全隱藏，保留可以開啟/結束程式的視窗入口。
    if startup:
        for _ in range(30):
            if tray.ready or tray.error:break
            time.sleep(.05)
    hidden=startup and engine.config['settings']['start_hidden'] and tray.ready
    if not no_browser and not hidden:show()
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
