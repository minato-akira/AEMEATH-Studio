"""雙擊啟動。需要來自 python.org / Microsoft Store 的 Python 3.11 以上；不需 pip。"""
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler
import sys
BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
DATA=BASE/'data'
LOG_DIR=DATA/'logs'
try:
    LOG_DIR.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(handlers=[RotatingFileHandler(LOG_DIR/'studio.log',maxBytes=600000,backupCount=2,encoding='utf-8')],level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s',force=True)
except Exception:
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
try:
    from server import main
    main()
except Exception as error:
    logging.exception('應用程式啟動或主迴圈失敗')
    try:
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk();root.withdraw();messagebox.showerror('AEMEATH 啟動失敗',str(error));root.destroy()
    except Exception:
        try:
            (LOG_DIR/'launch-error.txt').write_text(str(error),encoding='utf-8')
        except OSError:
            pass
