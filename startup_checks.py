"""啟動前環境檢查；不接觸鍵盤、不變更系統安全設定。"""
from pathlib import Path
import sys
import tempfile


def check_environment(base):
    base = Path(base)
    if sys.platform != 'win32':
        raise RuntimeError('目前僅支援 Windows 10／11。')
    if sys.version_info < (3, 11):
        raise RuntimeError('需要 Python 3.11 或更新版本，請參考「環境與故障排除.md」。')
    required = ('server.py', 'engine.py', 'driver.py', 'windows_tools.py',
                'native_hid.py', 'native_lighting.py', 'protocol.py', 'audit_trail.py',
                'version.py', 'keys.json', '使用聲明.md', 'web/index.html',
                'web/style.css', 'web/ui.js', 'web/data.js', 'web/driver-ui.js',
                'web/workspace-ui.js', 'web/color-ui.js', 'web/visual.js')
    missing = [name for name in required if not (base / name).is_file()]
    if missing:
        raise RuntimeError('程式檔案不完整，請完整解壓縮後啟動。缺少：' + '、'.join(missing))
    try:
        import tkinter
    except ImportError as error:
        raise RuntimeError('Python 缺少 tkinter，請修復 Python 安裝並包含 Tcl/Tk 元件。') from error
    try:
        for folder in (base / 'data', base / 'data/logs'):
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=folder) as probe:
                probe.write(b'check')
                probe.flush()
    except OSError as error:
        raise RuntimeError('無法寫入程式旁的 data 資料夾。請將整個程式移至可寫入的本機資料夾，並確認磁碟空間。原始錯誤：' + str(error)) from error

