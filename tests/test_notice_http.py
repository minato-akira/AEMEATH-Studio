"""HTTP integration tests with a temporary data folder and mocked hardware."""
import http.client
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import native_lighting
from http.server import ThreadingHTTPServer

class NoticeHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        (self.root / 'web').mkdir()
        self.notice = self.root / '\u4f7f\u7528\u8072\u660e.md'
        self.notice.write_text('HTTP test notice', encoding='utf-8')
        self.engine = SimpleNamespace(
            config={'settings': {'theme': 'test', 'auto_effects': True, 'start_hidden': False}},
            maintenance_lock=threading.RLock(), start=Mock(), stop=Mock(), status='',
            set_calibration=Mock())
        self.driver = Mock()
        self.driver.change.return_value = {'ok': True, 'verified': True, 'simulated': True}
        self.driver.restore.return_value = {'ok': True, 'verified': True, 'simulated': True}
        tray = SimpleNamespace(start=lambda: None, stop=lambda: None, ready=True, error='')
        stack = ExitStack()
        self.addCleanup(stack.close)
        class EphemeralServer(ThreadingHTTPServer):
            def __init__(self, address, handler):
                super().__init__(('127.0.0.1', 0), handler)
                server.PORT = self.server_address[1]
        replacements = {
            'BASE': self.root, 'DATA': self.root / 'data', 'PORT': 0,
            'Engine': lambda: self.engine, 'Driver': lambda *_: self.driver,
            'Tray': lambda *_: tray, 'ThreadingHTTPServer': EphemeralServer,
            'migrate_legacy_data': lambda *_: None, 'migrate_legacy_logs': lambda *_: None,
            'cleanup_legacy_data': lambda *_: None, 'cleanup_legacy_logs': lambda *_: None,
            'global_key_loop': lambda *_: None,
            'RotatingFileHandler': lambda *a, **k: logging.NullHandler(),
        }
        for name, value in replacements.items():
            stack.enter_context(patch.object(server, name, value))
        self.native = stack.enter_context(patch.object(native_lighting, 'apply_native', return_value={'ok': True, 'color_verified': False}))
        self.errors = []
        def run():
            try: server.serve(no_browser=True, startup=True, preview=True)
            except Exception as error: self.errors.append(error)
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        runtime = self.root / 'data/runtime.json'
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            if self.errors: raise self.errors[0]
            if runtime.exists():
                try:
                    self.token = json.loads(runtime.read_text(encoding='utf-8'))['token']
                    break
                except (ValueError, KeyError): pass
            time.sleep(.02)
        else: self.fail('Test server did not start')
        self.port = server.PORT

    def stop_server(self):
        if hasattr(self, 'port') and self.thread.is_alive():
            try: self.request('POST', 'quit', {})
            except OSError: pass
            self.thread.join(4)
        self.assertFalse(self.thread.is_alive())

    def request(self, method, endpoint, body=None, token=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        headers = {'X-Aemeath-Token': self.token if token is None else token,
                   'Content-Type': 'application/json'}
        payload = None if body is None else json.dumps(body)
        try:
            conn.request(method, '/api/' + endpoint, body=payload, headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally: conn.close()

    def acknowledge(self):
        status, notice = self.request('GET', 'notice')
        self.assertEqual(status, 200)
        status, result = self.request('POST', 'notice/decision',
                                      {'decision': 'acknowledged', 'notice_sha256': notice['notice_sha256']})
        self.assertEqual(status, 200)
        return notice

    def test_all_device_paths_block_before_confirmation_and_cancel(self):
        self.engine.start.assert_not_called()  # Autostart must also be gated.
        for endpoint, body in [('driver/write', {}), ('driver/restore', {'name': 'test.json'}),
                               ('native-lighting', {}), ('start', {}), ('calibrate', {'rgb': [1, 2, 3]})]:
            status, result = self.request('POST', endpoint, body)
            self.assertEqual(status, 428, endpoint)
            self.assertEqual(result['code'], 'notice_required')
        self.driver.change.assert_not_called()
        self.driver.restore.assert_not_called()
        self.native.assert_not_called()
        self.engine.set_calibration.assert_not_called()
        _, notice = self.request('GET', 'notice')
        self.request('POST', 'notice/decision', {'decision': 'cancelled', 'notice_sha256': notice['notice_sha256']})
        self.assertEqual(self.request('POST', 'driver/write', {})[0], 428)
        # Safety stops remain accessible without acknowledgement.
        self.assertEqual(self.request('POST', 'stop', {})[0], 200)
        self.assertEqual(self.request('POST', 'calibrate', {'rgb': None})[0], 200)

    def test_confirmation_allows_operation_and_update_invalidates_it(self):
        self.acknowledge()
        status, result = self.request('POST', 'driver/write', {'operation': 'binding'})
        self.assertEqual(status, 200)
        self.assertTrue(result['verified'])
        self.assertIn('request_id', result)
        self.driver.change.assert_called_once()
        self.notice.write_text('New notice', encoding='utf-8')
        self.assertEqual(self.request('POST', 'driver/write', {})[0], 428)
        self.assertEqual(self.driver.change.call_count, 1)

    def test_authentication_and_failed_operation(self):
        self.assertEqual(self.request('GET', 'notice', token='wrong')[0], 403)
        self.assertEqual(self.request('POST', 'notice/decision', token='wrong')[0], 403)
        self.acknowledge()
        self.driver.change.side_effect = OSError('Simulated device failure')
        self.assertEqual(self.request('POST', 'driver/write', {})[0], 500)
        rows = [json.loads(line) for p in (self.root / 'data/logs/audit').glob('events-*.jsonl')
                for line in p.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(rows[-1]['event'], 'operation_failed')

if __name__ == '__main__':
    unittest.main()

