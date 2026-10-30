# 영문 공급원의 캐시를 DeepL로 사전 번역하고 사용량·경합·저장 상태를 검증합니다.
import re
import sqlite3
import threading

from .quote_http import RemoteError
from .quotes import _lock, number, text

_batch_lock = threading.Lock()
MESSAGES = {
    'auth': 'DeepL API 키와 계정 이용 권한을 확인해 주세요.',
    'quota': 'DeepL의 남은 번역 문자 한도가 부족합니다.',
    'rate-limit': 'DeepL 요청이 많아 중단했습니다. 잠시 후 다시 실행해 주세요.',
    'network': 'DeepL에 연결하지 못했습니다. 잠시 후 다시 실행해 주세요.',
    'response': 'DeepL 응답 형식이 올바르지 않아 중단했습니다.',
    'storage': '번역을 저장하지 못했습니다. 저장소를 확인한 뒤 다시 실행해 주세요.',
    'busy': '다른 번역 묶음을 처리하고 있습니다. 완료 후 다시 실행해 주세요.',
    'input': '한 번에 글귀 ID를 최대 10개까지 선택해 주세요.',
    'not-configured': 'DeepL API 키를 먼저 저장해 주세요.',
}


def reason(error):
    if isinstance(error, (sqlite3.Error, OSError)):
        return 'storage'
    return {403: 'auth', 456: 'quota', 429: 'rate-limit', 502: 'response', 507: 'storage'}.get(getattr(error, 'status', 502), 'network')


class DeepLTranslation:
    def __init__(self, service):
        self.service = service

    @staticmethod
    def eligible(row):
        return (isinstance(row, dict) and row.get('provider') in ('zenquotes', 'positivequotation')
                and row.get('review_status') in ('pending', 'approved') and not row.get('text_ko')
                and isinstance(row.get('id'), str) and isinstance(row.get('fingerprint'), str)
                and isinstance(row.get('text_en'), str) and bool(row['text_en'].strip()))

    def pending(self):
        with _lock:
            try:
                return [dict(row) for row in self.service.load().get('quotes', []) if self.eligible(row)]
            except (ValueError, OSError, TypeError, sqlite3.Error):
                raise RemoteError(MESSAGES['storage'], 507) from None

    def request(self, path, body=None):
        key = self.service.config.get('deepl_key')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,200}(?::fx)?', key):
            raise RemoteError(MESSAGES['auth'], 403)
        host = 'api-free.deepl.com' if key.endswith(':fx') else 'api.deepl.com'
        options = {'hosts': {host}, 'headers': {'Authorization': 'DeepL-Auth-Key ' + key}, 'timeout': 10}
        if body is not None:
            options['body'] = body
        try:
            return self.service.fetch(f'https://{host}/v2/{path}', **options)
        except (ValueError, OSError, TypeError) as error:
            status = getattr(error, 'status', 0)
            code = {403: 'auth', 456: 'quota', 429: 'rate-limit'}.get(status, 'network')
            raise RemoteError(MESSAGES[code], status, number(getattr(error, 'retry_after', 0), 0, 0, 86400)) from None

    def usage(self):
        data = self.request('usage')
        if not isinstance(data, dict) or any(type(data.get(key)) is not int or data[key] < 0
                                             for key in ('character_count', 'character_limit')):
            raise RemoteError(MESSAGES['response'], 502)
        used, limit = data['character_count'], data['character_limit']
        remaining = max(0, limit - used)
        if 'api_key_character_count' in data or 'api_key_character_limit' in data:
            if any(type(data.get(key)) is not int or data[key] < 0
                   for key in ('api_key_character_count', 'api_key_character_limit')):
                raise RemoteError(MESSAGES['response'], 502)
            remaining = min(remaining, max(0, data['api_key_character_limit'] - data['api_key_character_count']))
        return {'used': used, 'limit': limit, 'remaining': remaining}

    def summary(self):
        rows = self.pending()
        configured = self.service.config.get('deepl_key') not in ('', None)
        return {'pending_ids': [row['id'] for row in rows], 'pending_count': len(rows),
                'characters': sum(len(row['text_en']) for row in rows), 'configured': configured,
                'usage': self.usage() if configured else None}

    def translate(self, ids):
        result = {'translated': 0, 'skipped': 0, 'failed': 0, 'remaining': 0,
                  'stopped': False, 'reason': '', 'retry_after': 0}
        batch = []
        acquired = False
        try:
            acquired = _batch_lock.acquire(blocking=False)
            pending = self.pending()
            result['remaining'] = len(pending)
            if not acquired:
                result.update(stopped=True, reason='busy')
                return result
            if not isinstance(ids, list) or len(ids) > 10 or any(not isinstance(value, str) or len(value) > 200 for value in ids):
                result.update(stopped=True, reason='input')
                return result
            by_id = {row['id']: row for row in pending}
            batch = [by_id[value] for value in dict.fromkeys(ids) if value in by_id]
            result['skipped'] = len(ids) - len(batch)
            if not batch:
                return result
            if self.service.config.get('deepl_key') in ('', None):
                result.update(stopped=True, reason='not-configured')
                return result
            usage = self.usage()
            if sum(len(row['text_en']) for row in batch) > usage['remaining']:
                result.update(stopped=True, reason='quota')
                return result
            response = self.request('translate', {'text': [row['text_en'] for row in batch],
                                                  'source_lang': 'EN', 'target_lang': 'KO'})
            values = response.get('translations') if isinstance(response, dict) else None
            if not isinstance(values, list) or len(values) != len(batch):
                raise RemoteError(MESSAGES['response'], 502)
            translations = [text(value.get('text') if isinstance(value, dict) else None, 1000) for value in values]
            saved = 0
            with _lock:
                data = self.service.load()
                current = {row['id']: row for row in data.get('quotes', []) if self.eligible(row)}
                for old, korean in zip(batch, translations):
                    row = current.get(old['id'])
                    if row is None or any(row.get(key) != old[key] for key in ('fingerprint', 'text_en')):
                        result['skipped'] += 1
                        continue
                    row.update(text_ko=korean, translation_status='automatic', translation_provider='deepl',
                               translated_at=self.service.clock())
                    saved += 1
                if saved:
                    try:
                        self.service.save(data)
                    except (ValueError, OSError, TypeError, sqlite3.Error):
                        raise RemoteError(MESSAGES['storage'], 507) from None
                result['translated'] = saved
                result['remaining'] = sum(self.eligible(row) for row in data.get('quotes', []))
        except (ValueError, OSError, TypeError, sqlite3.Error) as error:
            result.update(stopped=True, reason=reason(error), failed=len(batch),
                          retry_after=number(getattr(error, 'retry_after', 0), 0, 0, 86400))
        finally:
            if acquired:
                _batch_lock.release()
        return result
