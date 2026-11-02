import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine import Engine, DEFAULT, validate
from driver import Driver
import server


class ReviewRegressions(unittest.TestCase):
    def test_bound_macros_cannot_be_deleted_or_emptied_in_either_layer(self):
        for layer in (0x12,0x16):
            with self.subTest(layer=layer),tempfile.TemporaryDirectory() as folder:
                d=Driver(Engine(folder),True)
                macro=dict(id=0,actions=[dict(delay=0,keyCode=4,isPress=True,actionType=3),dict(delay=0,keyCode=4,isPress=False,actionType=3)])
                d.change(dict(operation='macros',macros=[macro]))
                d.demo.tables[layer][196:200]=bytes([6,0,0,0])
                before=bytes(d.demo.tables[0x15]);backups=len(d.backups())
                for requested in ([],[dict(id=0,actions=[])]):
                    with self.assertRaisesRegex(ValueError,'不能刪除或清空'):
                        d.change(dict(operation='macros',macros=requested))
                    self.assertEqual(bytes(d.demo.tables[0x15]),before)
                    self.assertEqual(len(d.backups()),backups)
                d.demo.tables[layer][196:200]=bytes([2,0,4,0])
                self.assertTrue(d.change(dict(operation='macros',macros=[]))['verified'])

    def test_defaults_and_existing_configuration(self):
        root=Path(__file__).resolve().parents[1]
        ui=json.loads((root/'web/data.js').read_text(encoding='utf-8').split('=',1)[1].strip().rstrip(';'))
        self.assertEqual(validate(DEFAULT),ui['config'])
        with tempfile.TemporaryDirectory() as folder:
            e=Engine(folder)
            self.assertEqual(e.config['settings']['theme'],'星海')
            self.assertFalse(e.config['settings']['autostart'])
            cfg=copy.deepcopy(DEFAULT);cfg['settings']['theme']='晨霧'
            e.save(cfg)
            self.assertEqual(Engine(folder).config['settings']['theme'],'晨霧')

    def test_cross_folder_identity_requires_local_receipt_and_authenticated_match(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)/'other-install';(base/'data').mkdir(parents=True)
            identity=dict(app='AEMEATH-Studio',pid=123,version='test',build_id='test',base_path=str(base))
            receipt=dict(identity,token='local-test-token');receipt.pop('app')
            (base/'data/runtime.json').write_text(json.dumps(receipt),encoding='utf-8')
            requests=[]
            def response(request,**kwargs):
                requests.append(request)
                if isinstance(request,str):value=identity
                else:
                    self.assertEqual(request.get_header('X-aemeath-token'),'local-test-token')
                    value=dict(instance=identity,ui_active=True)
                return io.BytesIO(json.dumps(value).encode())
            with patch.object(server.urllib.request,'urlopen',side_effect=response):
                found=server._running_instance(Path(folder)/'missing.json','http://127.0.0.1:47635/')
                self.assertEqual(found['pid'],123);self.assertTrue(found['_ui_active'])
                receipt['pid']=124
                (base/'data/runtime.json').write_text(json.dumps(receipt),encoding='utf-8')
                self.assertIsNone(server._running_instance(Path(folder)/'missing.json','http://127.0.0.1:47635/'))
                self.assertEqual(len(requests),3,'mismatched identity must not send token')

    def test_identity_without_token_is_not_authorization(self):
        with tempfile.TemporaryDirectory() as folder:
            identity=dict(app='AEMEATH-Studio',pid=123,version='test',build_id='test',base_path=folder)
            with patch.object(server.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(identity).encode())):
                self.assertIsNone(server._running_instance(Path(folder)/'missing.json','http://127.0.0.1:47635/'))

if __name__=='__main__':unittest.main()
