import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_trail import AuditTrail, NoticeRequired

class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.notice = self.root / 'notice.md'
        self.notice.write_text('Test notice', encoding='utf-8')
        self.audit = AuditTrail(self.root / 'data', self.notice, 'test', 'test-build', True)

    def acknowledge(self):
        info = self.audit.info()
        return self.audit.decision(info['notice_sha256'], 'acknowledged')

    def events(self):
        return [json.loads(line) for path in self.audit.folder.glob('events-*.jsonl')
                for line in path.read_text(encoding='utf-8').splitlines()]

    def test_no_confirmation_and_cancel_never_execute(self):
        callback = Mock()
        with self.assertRaises(NoticeRequired):
            self.audit.run('driver/write', {}, callback)
        self.audit.decision(self.audit.info()['notice_sha256'], 'cancelled')
        with self.assertRaises(NoticeRequired):
            self.audit.run('driver/write', {}, callback)
        callback.assert_not_called()
        self.assertFalse(self.audit.info()['acknowledged'])

    def test_receipt_persists_and_snapshot_is_verified(self):
        self.acknowledge()
        other = AuditTrail(self.root / 'data', self.notice, 'test2', 'build2')
        self.assertTrue(other.info()['acknowledged'])
        snapshot = next(self.audit.folder.glob('notice-*.md'))
        snapshot.write_text('changed', encoding='utf-8')
        self.assertFalse(other.info()['acknowledged'])
        with self.assertRaises(NoticeRequired):
            other.run('driver/write', {}, Mock())

    def test_stale_confirmation_and_notice_change(self):
        old = self.audit.info()['notice_sha256']
        self.acknowledge()
        self.notice.write_text('Updated notice', encoding='utf-8')
        self.assertFalse(self.audit.info()['acknowledged'])
        with self.assertRaises(NoticeRequired):
            self.audit.decision(old, 'acknowledged')
        with self.assertRaises(NoticeRequired):
            self.audit.run('driver/write', {}, Mock())

    def test_corrupt_receipt_requires_confirmation(self):
        self.acknowledge()
        (self.audit.folder / 'confirmation.json').write_text('{}', encoding='utf-8')
        self.assertFalse(self.audit.info()['acknowledged'])

    def test_result_pairing_and_no_raw_sensitive_payload(self):
        self.acknowledge()
        secret = 'PRIVATE_MACRO_EXAMPLE'
        result = self.audit.run('driver/write', {'macro': secret},
                                lambda: {'ok': True, 'verified': True, 'simulated': True, 'backup': 'test.json'})
        rows = self.events()
        start = next(r for r in rows if r['event'] == 'operation_started')
        end = next(r for r in rows if r['event'] == 'operation_completed')
        self.assertEqual(start['request_id'], result['request_id'])
        self.assertEqual(start['request_id'], end['request_id'])
        self.assertTrue(end['verified'])
        self.assertTrue(end['simulated'])
        self.assertEqual(end['backup'], 'test.json')
        self.assertNotIn(secret, json.dumps(rows))

    def test_operation_error_is_logged_and_reraised(self):
        self.acknowledge()
        error = ValueError('device validation failed')
        with self.assertRaises(ValueError):
            self.audit.run('driver/write', {}, Mock(side_effect=error))
        self.assertEqual(self.events()[-1]['event'], 'operation_failed')

    def test_record_failure_before_operation_prevents_write(self):
        self.acknowledge()
        callback = Mock()
        with patch.object(self.audit, '_append', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.audit.run('driver/write', {}, callback)
        callback.assert_not_called()

    def test_record_failure_after_operation_does_not_retry(self):
        self.acknowledge()
        callback = Mock(return_value={'ok': True})
        with patch.object(self.audit, 'finish', side_effect=OSError('disk full')):
            result = self.audit.run('driver/write', {}, callback)
        callback.assert_called_once()
        self.assertTrue(result['ok'])
        self.assertIn('audit_warning', result)

    def test_concurrent_operations_produce_parseable_complete_pairs(self):
        self.acknowledge()
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda i: self.audit.run('driver/write', {'i': i}, lambda: {'ok': True}), range(20)))
        ids = {row['request_id'] for row in results}
        rows = self.events()
        self.assertEqual(ids, {r['request_id'] for r in rows if r['event']=='operation_started'})
        self.assertEqual(ids, {r['request_id'] for r in rows if r['event']=='operation_completed'})

if __name__ == '__main__':
    unittest.main()

