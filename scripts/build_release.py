"""Build the v1.0.0 user archive from an explicit allowlist; never copy user data."""
import argparse
import hashlib
from pathlib import Path
import runpy
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES=(
    'Launch.pyw','server.py','engine.py','driver.py','native_lighting.py',
    'native_hid.py','protocol.py','windows_tools.py','audit_trail.py','version.py',
    'keys.json','app.ico','使用聲明.md','README.md','先讀我.txt',
    'web/index.html','web/style.css','web/ui.js','web/workspace-ui.js',
    'web/driver-ui.js','web/color-ui.js','web/visual.js','web/data.js',
    'web/keyboard.png','web/heart.svg','web/fonts/snow-sans.woff',
    'web/fonts/snow-serif.woff','web/fonts/LICENSE.txt',
)
TEXT_SUFFIXES={'.py','.pyw','.json','.md','.txt','.html','.css','.js','.svg'}

def build(output):
    version=runpy.run_path(str(ROOT/'version.py'))
    if version['APP_VERSION']!='1.0.0' or version['APP_TEST_BUILD'] is not None:
        raise ValueError('This release builder only accepts stable v1.0.0')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    archive=output/'AEMEATH-Studio-v1.0.0.zip'
    prefix='AEMEATH-Studio-v1.0.0/'
    expected={}
    for name in FILES:
        source=ROOT/name
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT) or not source.is_file():
            raise ValueError('Missing or unsafe release file: '+name)
        raw=source.read_bytes()
        if source.suffix in TEXT_SUFFIXES:
            raw=raw.decode('utf-8-sig').replace('\r\n','\n').encode('utf-8')
        expected[prefix+name]=raw
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name,raw in sorted(expected.items()):
            info=zipfile.ZipInfo(name,date_time=(2026,11,2,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            z.writestr(info,raw)
    with zipfile.ZipFile(archive) as z:
        if set(z.namelist())!=set(expected) or z.testzip() is not None:
            raise ValueError('Archive member verification failed')
        for name,raw in expected.items():
            if z.read(name)!=raw:raise ValueError('Archive content mismatch: '+name)
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (output/'SHA256SUMS.txt').write_text(digest+'  '+archive.name+'\n',encoding='utf-8')
    print(f'Verified {len(expected)} files: {archive.name}; SHA256 {digest}')
    return archive

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'dist'))
    build(parser.parse_args().output)
