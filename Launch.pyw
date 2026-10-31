"""雙擊啟動。需要來自 python.org / Microsoft Store 的 Python 3.11 以上；不需 pip。"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
try:
    from server import main
    main()
except Exception as error:
    try:
        import tkinter as tk
        from tkinter import messagebox
        root=tk.Tk();root.withdraw();messagebox.showerror('AEMEATH 啟動失敗',str(error));root.destroy()
    except Exception:
        Path(__file__).with_name('launch-error.txt').write_text(str(error),encoding='utf-8')
