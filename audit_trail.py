"""Local audit records; not a trusted timestamp or tamper-proof identity record."""
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import uuid

class NoticeRequired(ValueError):
    pass

class AuditTrail:
    def __init__(self, data, notice, version, build, preview=False):
        self.folder = Path(data) / 'logs' / 'audit'
        self.notice = Path(notice)
        self.version, self.build, self.preview = version, build, preview
        self.session_id = uuid.uuid4().hex
        self.lock = threading.RLock()

    def _document(self):
        # Normalize line endings so a packaging change alone does not invalidate confirmation.
        text = self.notice.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
        return text, hashlib.sha256(text.encode('utf-8')).hexdigest()

    def _receipt(self, digest):
        try:
            value = json.loads((self.folder / 'confirmation.json').read_text(encoding='utf-8'))
            if value.get('notice_sha256') == digest and value.get('event') == 'notice_acknowledged' and isinstance(value.get('event_id'), str) and value['event_id']:
                snapshot = (self.folder / ('notice-' + digest + '.md')).read_text(encoding='utf-8')
                if hashlib.sha256(snapshot.encode('utf-8')).hexdigest() == digest:
                    return value
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def info(self):
        with self.lock:
            text, digest = self._document()
            return dict(text=text, notice_sha256=digest, acknowledged=self._receipt(digest) is not None)

    def _append(self, event, **fields):
        self.folder.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        row = dict(schema=1, event=event, event_id=uuid.uuid4().hex,
                   timestamp_utc=now.isoformat(), time_source='local_system_clock',
                   session_id=self.session_id, app_version=self.version,
                   build_id=self.build, simulated=self.preview, **fields)
        path = self.folder / ('events-' + now.strftime('%Y-%m-%d') + '.jsonl')
        with path.open('a', encoding='utf-8', newline='\n') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        return row

    def decision(self, digest, decision):
        if decision not in ('acknowledged', 'cancelled'):
            raise ValueError('無效的聲明確認動作')
        with self.lock:
            text, current = self._document()
            if digest != current:
                raise NoticeRequired('聲明已更新，請重新閱讀確認')
            self.folder.mkdir(parents=True, exist_ok=True)
            snapshot = self.folder / ('notice-' + current + '.md')
            # Verify existing snapshots rather than silently trusting changed files.
            if not snapshot.exists() or snapshot.read_text(encoding='utf-8') != text:
                with snapshot.open('w', encoding='utf-8', newline='\n') as stream:
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
            row = self._append('notice_' + decision, notice_sha256=current,
                               confirmation_label='已閱讀，繼續操作' if decision == 'acknowledged' else '取消',
                               scope='this_data_folder', source='local_ui_request')
            if decision == 'acknowledged':
                temp = self.folder / ('confirmation-' + uuid.uuid4().hex + '.tmp')
                try:
                    with temp.open('w', encoding='utf-8') as stream:
                        json.dump(row, stream, ensure_ascii=False)
                        stream.flush()
                        os.fsync(stream.fileno())
                    temp.replace(self.folder / 'confirmation.json')
                finally:
                    temp.unlink(missing_ok=True)
            return dict(ok=True, notice_sha256=current, acknowledged=decision == 'acknowledged')

    def begin(self, operation, body, source='ui'):
        with self.lock:
            _, digest = self._document()
            receipt = self._receipt(digest)
            if receipt is None:
                self._append('operation_blocked', operation=operation, notice_sha256=digest,
                             reason='notice_confirmation_required', source=source)
                raise NoticeRequired('請先閱讀操作風險與資料說明')
            payload = json.dumps(body, sort_keys=True, ensure_ascii=False, allow_nan=False)
            request_id = uuid.uuid4().hex
            self._append('operation_started', request_id=request_id, operation=operation,
                         notice_sha256=digest, confirmation_id=receipt['event_id'],
                         payload_sha256=hashlib.sha256(payload.encode('utf-8')).hexdigest(), source=source)
            return request_id

    def finish(self, request_id, result=None, error=None):
        with self.lock:
            fields = dict(request_id=request_id)
            if error is not None:
                fields['error_type'] = type(error).__name__
                return self._append('operation_failed', **fields)
            if isinstance(result, dict):
                for key in ('ok', 'verified', 'color_verified'):
                    if type(result.get(key)) is bool: fields[key] = result[key]
                backup = result.get('backup')
                if isinstance(backup, str) and Path(backup).name == backup:
                    fields['backup'] = backup
            # Completion of a start request is not confirmation of physical lighting output.
            return self._append('operation_completed', **fields)

    def run(self, operation, body, callback, source='ui'):
        request_id = self.begin(operation, body, source)
        log = logging.getLogger('aemeath.audit')
        log.info('操作開始 request_id=%s operation=%s', request_id, operation)
        try:
            result = callback()
        except Exception as error:
            try:
                self.finish(request_id, error=error)
            except OSError:
                log.exception('無法保存失敗結果 request_id=%s', request_id)
            log.warning('操作失敗 request_id=%s error_type=%s', request_id, type(error).__name__)
            raise
        result = dict(result) if isinstance(result, dict) else {'ok': True}
        try:
            self.finish(request_id, result=result)
        except OSError:
            log.exception('操作已完成但無法保存結果 request_id=%s', request_id)
            result['audit_warning'] = '操作已完成，但結果日誌未能保存；請勿因此重複寫入。'
        result['request_id'] = request_id
        log.info('操作完成 request_id=%s', request_id)
        return result

