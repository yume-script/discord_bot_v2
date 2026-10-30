# 한국어·영어 공급원을 가중치로 선택하고 저장된 번역·캐시·출처를 관리합니다.
from collections import OrderedDict
import hashlib
import json
import random
import re
import sqlite3
import threading
import time

from .quote_http import fetch_json, public_url

PROVIDERS = ('korean_advice', 'zenquotes', 'positivequotation')
WEIGHTS = (60, 35, 5)
LABELS = ('Korean Advice API', 'ZenQuotes API', 'PositiveQuotation')
QUOTE_FIELDS = ('id', 'provider', 'text_en', 'text_ko', 'original_language', 'author', 'author_profile',
                'tags', 'fingerprint', 'provider_url', 'original_source_url', 'original_source_title',
                'translation_status', 'translation_provider', 'translated_at', 'review_status',
                'rights_status', 'rights_evidence_url', 'provider_license', 'fetched_at')
_runtime = OrderedDict()
_lock = threading.RLock()
BUILTIN = (
    ('Leave a little room in your day for a page you have not read.', '오늘 하루에 아직 읽지 않은 한 페이지를 위한 여백을 남겨 보세요.'),
    ('A quiet page can become a small window in a busy day.', '고요한 한 페이지가 바쁜 하루의 작은 창이 될 수 있어요.'),
    ('Let your next page begin at your own pace.', '다음 페이지는 나만의 속도로 시작해 보세요.'),
    ('Keep a sentence you like close to the day you are living.', '마음에 드는 한 문장을 오늘의 곁에 두어 보세요.'),
)


def reset_runtime():
    with _lock:
        _runtime.clear()


def text(value, maximum=500):
    if not isinstance(value, str):
        raise ValueError('문자열 형식을 확인해 주세요.')
    result = ' '.join(value.split())
    if not result or len(result) > maximum or re.search(r'<[^>]*>|[\x00-\x08\x0b\x0c\x0e-\x1f]', result):
        raise ValueError('문구의 길이와 내용을 확인해 주세요.')
    return result


def fingerprint(content, author):
    value = ' '.join(content.casefold().split()) + '\n' + ' '.join(author.casefold().split())
    return hashlib.sha256(value.encode()).hexdigest()


def enabled(config, key, default=False):
    return config.get(key, default) in (True, 1, '1', 'true', 'on')


def number(value, default, low=0, high=100):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


class QuoteService:
    def __init__(self, store, config, *, fetch=fetch_json, clock=time.time, rng=random):
        self.store, self.config = store, config if isinstance(config, dict) else {}
        self.fetch, self.clock, self.rng = fetch, clock, rng
        signature = hashlib.sha256(json.dumps(self.config, sort_keys=True).encode()).hexdigest()
        with _lock:
            self.runtime = _runtime.setdefault(signature, {'cache': {}, 'states': {}, 'cache_locks': {}})
            _runtime.move_to_end(signature)
            while len(_runtime) > 16:
                _runtime.popitem(last=False)

    def load(self):
        value = self.store.load()
        if not isinstance(value, dict) or not all(isinstance(value.get(key, []), list) for key in ('quotes', 'photos', 'audit')):
            raise ValueError('글귀 저장소 형식을 확인해 주세요.')
        return value

    def save(self, data):
        if len(json.dumps(data).encode()) > 8 * 1024 * 1024:
            raise ValueError('글귀 저장소는 8MB 이하여야 합니다.')
        self.store.save(data)

    def state(self, provider):
        return self.runtime['states'].setdefault(provider, {'failures': 0, 'until': 0, 'calls': 0,
                                                           'selected': 0, 'displayed': 0, 'cache_hits': 0, 'last_code': 0})

    def cached(self, key, ttl, loader):
        provider = key.split(':')[0]
        with _lock:
            provider_lock = self.runtime['cache_locks'].setdefault(provider, threading.RLock())
        with provider_lock:
            with _lock:
                record = self.runtime['cache'].get(key)
                if record and record[0] > self.clock():
                    self.state(provider)['cache_hits'] += 1
                    return record[1]
                self.runtime['cache'].pop(key, None)
            value = loader()
            with _lock:
                if len(self.runtime['cache']) >= 80:
                    self.runtime['cache'].pop(next(iter(self.runtime['cache'])))
                self.runtime['cache'][key] = (self.clock() + ttl, value)
            return value

    def available(self):
        result = []
        for index, provider in enumerate(PROVIDERS):
            prefix = ('korean', 'zen', 'positive')[index]
            active = enabled(self.config, prefix + '_enabled', index == 0)
            terms = index == 0 or enabled(self.config, prefix + '_terms')
            value = self.config.get('korean_weight', self.config.get('local_weight')) if index == 0 else self.config.get(prefix + '_weight')
            weight = number(value, WEIGHTS[index])
            if active and terms and weight and self.state(provider)['until'] <= self.clock():
                result.append((provider, weight))
        return result

    def status(self):
        active = dict(self.available())
        total = sum(active.values())
        result = []
        for index, provider in enumerate(PROVIDERS):
            prefix = ('korean', 'zen', 'positive')[index]
            state = self.state(provider)
            if not enabled(self.config, prefix + '_enabled', index == 0):
                status = 'disabled'
            elif index and not enabled(self.config, prefix + '_terms'):
                status = 'terms-pending'
            elif state['until'] > self.clock():
                status = 'cooldown'
            else:
                status = 'healthy' if provider in active else 'no-eligible-content'
            result.append({'id': provider, 'name': LABELS[index], 'status': status,
                           'effective_weight': round(100 * active.get(provider, 0) / total, 2) if total else 0, **state})
        return result

    def remote_quote(self, provider):
        state = self.state(provider)
        if provider == 'korean_advice':
            state['calls'] += 1
            value = self.fetch('https://korean-advice-open-api.vercel.app/api/advice',
                               hosts={'korean-advice-open-api.vercel.app'})
            return [self.normalize_remote(provider, value)]
        if provider == 'zenquotes':
            def load_zen():
                # 무료 IP 한도를 보호하기 위해 실패·다중 클릭에도 즉시 다시 요청하지 않는다.
                state['until'] = self.clock() + 30
                state['calls'] += 1
                values = self.fetch('https://zenquotes.io/api/quotes', hosts={'zenquotes.io'})
                if not isinstance(values, list):
                    raise ValueError('명언 목록 형식이 올바르지 않습니다.')
                valid = []
                for value in values[:50]:
                    try:
                        valid.append(self.normalize_remote(provider, value))
                    except ValueError:
                        continue
                if not valid:
                    raise ValueError('표시할 수 있는 문구가 없습니다.')
                state['until'] = 0
                return valid
            pool = self.cached('zenquotes:pool', 3600, load_zen)
        elif provider == 'positivequotation':
            state['calls'] += 1
            data = self.fetch('https://positivequotation.com/api/v1/quotes/random', hosts={'positivequotation.com'})
            if not isinstance(data, dict):
                raise ValueError('명언 응답 형식을 확인해 주세요.')
            pool = [self.normalize_remote(provider, data.get('data'))]
        else:
            raise ValueError('지원하지 않는 글귀 공급원입니다.')
        # 캐시에는 원문만 두고 매번 최신 저장 번역을 병합한다.
        with _lock:
            data = self.load()
            rows = data.setdefault('quotes', [])
            existing = {row.get('id'): row for row in rows if isinstance(row, dict)}
            result, changed = [], False
            for item in pool:
                old = existing.get(item['id'])
                if old and all(old.get(key) == item[key] for key in ('provider', 'fingerprint', 'text_en')):
                    result.append(dict(old))
                else:
                    if old:
                        rows[rows.index(old)] = dict(item)
                    else:
                        rows.append(dict(item))
                    existing[item['id']] = item
                    result.append(dict(item))
                    changed = True
            if changed:
                self.save(data)
            return result

    def prepare_translation_sources(self):
        errors = []
        for provider, prefix in (('zenquotes', 'zen'), ('positivequotation', 'positive')):
            if not enabled(self.config, prefix + '_enabled') or not enabled(self.config, prefix + '_terms'):
                continue
            state = self.state(provider)
            if state['until'] > self.clock():
                errors.append({'provider': provider, 'message': '공급원 요청 대기 중입니다.',
                               'retry_after': max(1, int(state['until'] - self.clock()))})
                continue
            try:
                self.remote_quote(provider)
                state['failures'] = 0
            except (ValueError, OSError, TypeError, KeyError, IndexError, sqlite3.Error) as error:
                state['failures'] += 1
                state['last_code'] = getattr(error, 'status', 0)
                delay = max(getattr(error, 'retry_after', 0), 300 if state['failures'] >= 3 else 30)
                state['until'] = self.clock() + delay
                errors.append({'provider': provider, 'message': '영문 글귀를 준비하지 못했습니다. 잠시 후 다시 실행해 주세요.',
                               'retry_after': delay})
        return {'errors': errors}

    def normalize_remote(self, provider, value):
        if not isinstance(value, dict):
            raise ValueError('명언 객체가 필요합니다.')
        if provider == 'korean_advice':
            content, author = text(value.get('message'), 1000), text(value.get('author'), 200)
            digest = fingerprint(content, author)
            return {'id': provider + ':' + digest, 'provider': provider, 'text_en': '', 'text_ko': content,
                    'original_language': 'ko', 'translation_status': 'original', 'author': author,
                    'author_profile': text(value['authorProfile'], 300) if value.get('authorProfile') else '',
                    'tags': [], 'fingerprint': digest, 'review_status': 'approved', 'rights_status': 'provider-terms',
                    'provider_url': 'https://github.com/gwongibeom/korean-advice-open-api',
                    'rights_evidence_url': 'https://github.com/gwongibeom/korean-advice-open-api/blob/main/LICENSE',
                    'provider_license': '비상업적 무료 · 출처 표기 · 상업적 이용 별도 문의',
                    'original_source_url': '', 'original_source_title': '', 'fetched_at': self.clock()}
        if provider not in ('zenquotes', 'positivequotation'):
            raise ValueError('지원하지 않는 글귀 공급원입니다.')
        content = text(value.get('q') if provider == 'zenquotes' else value.get('quote'), 180)
        author = text(value.get('a') if provider == 'zenquotes' else value.get('attribution') or '저자 미확인', 200)
        digest = fingerprint(content, author)
        source = value.get('source') if isinstance(value.get('source'), dict) else {}
        return {'id': provider + ':' + digest, 'provider': provider, 'text_en': content, 'text_ko': '',
                'original_language': 'en', 'author': author, 'tags': [], 'fingerprint': digest, 'review_status': 'approved',
                'rights_status': 'operator-confirmed', 'provider_url': 'https://zenquotes.io/' if provider == 'zenquotes' else public_url(value.get('detail_url'), {'positivequotation.com'}),
                'original_source_url': public_url(source.get('url')), 'original_source_title': str(source.get('title') or '')[:300],
                'fetched_at': self.clock()}

    def pick(self, recent):
        recent = set(value for value in recent[-20:] if isinstance(value, str)) if isinstance(recent, list) else set()
        candidates = self.available()
        while candidates:
            provider = self.rng.choices([p for p, _ in candidates], weights=[w for _, w in candidates], k=1)[0]
            state = self.state(provider)
            state['selected'] += 1
            try:
                pool = self.remote_quote(provider)
                unseen = [item for item in pool if item['fingerprint'] not in recent]
                if not unseen and provider == 'positivequotation':
                    pool = self.remote_quote(provider)
                    unseen = [item for item in pool if item['fingerprint'] not in recent]
                selected = self.rng.choice(unseen or pool)
                state['failures'] = 0
                state['displayed'] += 1
                return dict(selected)
            except (ValueError, OSError, TypeError, KeyError, IndexError, sqlite3.Error) as error:
                state['failures'] += 1
                state['last_code'] = getattr(error, 'status', 0)
                delay = max(getattr(error, 'retry_after', 0), 300 if state['failures'] >= 3 else 30)
                state['until'] = self.clock() + delay
                candidates = [(p, w) for p, w in candidates if p != provider]
        return self.builtin_quote(recent)

    def builtin_quote(self, recent):
        pool = [item for item in BUILTIN if fingerprint(item[0], 'Good Quotes') not in recent] or list(BUILTIN)
        original, korean = self.rng.choice(pool)
        return {'id': 'builtin:' + fingerprint(original, 'Good Quotes'), 'provider': 'builtin',
                'text_en': original, 'text_ko': korean, 'author': '좋은 글귀 · 자체 작성', 'tags': ['Books'],
                'fingerprint': fingerprint(original, 'Good Quotes'), 'provider_url': '',
                'original_source_url': '', 'original_source_title': '', 'translation_status': 'original',
                'review_status': 'approved', 'rights_status': 'original'}

    def card(self, recent, background_only=None, *, initial=False, include_background=True):
        from .quote_media import GRADIENT, MediaService
        media = MediaService(self)
        if background_only and not initial:
            return {'background': media.background(background_only)}
        if initial:
            quote = dict(self.runtime.get('last_quote') or self.builtin_quote([]))
            if 'tags' in quote:
                quote['tags'] = list(quote['tags'])
            quote.update(background=dict(GRADIENT), deferred=True)
        else:
            quote = self.pick(recent)
            quote.pop('background', None)
            snapshot = {key: quote[key] for key in QUOTE_FIELDS if key in quote}
            snapshot['tags'] = list(quote.get('tags', []))
            self.runtime['last_quote'] = snapshot
            if include_background:
                quote['background'] = media.background(quote.get('tags', []))
        quote['translation_status'] = quote.get('translation_status', 'reviewed') if quote.get('text_ko') else 'missing'
        quote['language'] = self.config.get('quote_language', 'both')
        font = self.config.get('quote_font', '')
        quote['font'] = font if isinstance(font, str) and len(font) <= 200 else ''
        quote['font_size'] = number(self.config.get('quote_font_size'), 32, 16, 96)
        en_font = self.config.get('quote_en_font', quote['font'])
        quote['en_font'] = en_font if isinstance(en_font, str) and len(en_font) <= 200 else ''
        quote['en_font_size'] = number(self.config.get('quote_en_font_size', quote['font_size']), 32, 16, 96)
        author_font = self.config.get('quote_author_font', '')
        quote['author_font'] = author_font if isinstance(author_font, str) and len(author_font) <= 200 else ''
        quote['author_font_size'] = number(self.config.get('quote_author_font_size'), 15, 8, quote['font_size'] - 1)
        quote['widget_type'] = 'quotes'
        quote['enabled'] = True
        return quote
