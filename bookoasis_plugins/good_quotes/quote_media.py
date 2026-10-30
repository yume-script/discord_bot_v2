# Met 공개 작품·Pexels·Unsplash 무료 배경 사진을 연결합니다.
from math import ceil
import threading
from urllib.parse import parse_qsl, urlencode, urlsplit

from .quote_http import public_url
from .quotes import _lock, enabled, number

GRADIENT = {'provider': 'gradient', 'url': '', 'creator': '', 'source_url': '', 'license': '', 'license_url': ''}


class MediaService:
    def __init__(self, service):
        self.service, self.config = service, service.config

    @staticmethod
    def query(tags):
        values = ' '.join(str(value).casefold() for value in tags)
        for words, query in [(('hope', 'courage', 'inspir'), 'sunrise mountains'),
                             (('love', 'friend', 'kind'), 'flowers soft light'),
                             (('wisdom', 'book', 'learn'), 'old books sunlight'),
                             (('life', 'time', 'change'), 'autumn forest'),
                             (('nature', 'peace', 'calm'), 'misty lake')]:
            if any(word in values for word in words):
                return query
        return 'soft landscape'

    def met_background(self):
        host = 'collectionapi.metmuseum.org'
        params = urlencode({'hasImages': 'true', 'q': 'landscape', 'limit': 50, 'offset': 0})
        deadline = self.service.clock() + 6
        result = self.service.cached('met:search:landscape', 3600, lambda: self.service.fetch(
            'https://' + host + '/public/collection/v1.1/search?' + params, hosts={host}))
        ids = list(dict.fromkeys(value for value in (result.get('objectIDs') or [])[:500]
                                if type(value) is int and value > 0))
        # 작품마다 요청이 4초 걸릴 수 있으므로 후보 수와 다음 요청의 시작 시간을 제한한다.
        for object_id in self.service.rng.sample(ids, min(3, len(ids))):
            if self.service.clock() >= deadline:
                break
            photo = self.service.cached('met:object:' + str(object_id), 3600, lambda: self.service.fetch(
                f'https://{host}/public/collection/v1/objects/{object_id}', hosts={host}))
            if not isinstance(photo, dict) or photo.get('isPublicDomain') is not True:
                continue
            image = public_url(photo.get('primaryImage'), {'images.metmuseum.org'})
            source = public_url(photo.get('objectURL'), {'metmuseum.org', 'www.metmuseum.org'})
            if image and source:
                return {'provider': 'met', 'url': image, 'source_url': source,
                        'title': str(photo.get('title') or '')[:500],
                        'creator': str(photo.get('artistDisplayName') or '작가 미상')[:200],
                        'credit_line': str(photo.get('creditLine') or '')[:1000],
                        'license': 'CC0', 'license_url': 'https://creativecommons.org/publicdomain/zero/1.0/'}
        self.service.state('met')['until'] = self.service.clock() + 60
        return {**GRADIENT, 'reason': 'no-public-domain-art'}

    @staticmethod
    def unsplash_image(value):
        value = public_url(value, {'images.unsplash.com'})
        if not value:
            return ''
        part = urlsplit(value)
        params = dict(parse_qsl(part.query, keep_blank_values=True))
        for key in ('dpr', 'h'):
            params.pop(key, None)
        params.update(w='2200', q='80', auto='format', fit='max')
        return part._replace(query=urlencode(params)).geturl()

    @staticmethod
    def unsplash_link(value):
        value = public_url(value, {'unsplash.com'})
        return value + ('&' if '?' in value else '?') + 'utm_source=bookoasis_good_quotes&utm_medium=referral' if value else ''

    def background(self, tags, *, force=False):
        provider = self.config.get('photo_provider', 'gradient')
        if provider not in ('met', 'unsplash', 'pexels'):
            return {**GRADIENT, 'reason': 'selected-gradient', 'refresh_after': 0}
        with _lock:
            selection_lock = self.service.runtime['cache_locks'].setdefault('photo-selection', threading.RLock())
        with selection_lock:
            now = self.service.clock()
            selected = self.service.runtime.get('selected_background')
            if selected and selected['provider'] != provider:
                selected = None
            previous = selected['photo'] if selected else GRADIENT
            state = self.service.state(provider)
            if state['until'] > now:
                return {**previous, 'reason': 'cooldown', 'refresh_after': max(60, ceil(state['until'] - now))}
            if not force and selected and selected['expires'] > now:
                return {**previous, 'refresh_after': max(1, ceil(selected['expires'] - now))}
            if force or selected:
                with _lock:
                    for key in list(self.service.runtime['cache']):
                        if key.startswith(provider + ':'):
                            self.service.runtime['cache'].pop(key)
            photo = self._select_background(tags)
            now = self.service.clock()
            if photo.get('url'):
                expires = now + number(self.config.get('photo_refresh_hours'), 3, 1, 24) * 3600
            else:
                if photo.get('reason') in ('terms-pending', 'key-required'):
                    return {**photo, 'refresh_after': 60}
                expires = max(now + 60, state['until'])
                state['until'] = expires
                photo = {**previous, 'reason': photo.get('reason', 'request-failed')}
            self.service.runtime['selected_background'] = {'provider': provider, 'photo': dict(photo), 'expires': expires}
            return {**photo, 'refresh_after': max(1, ceil(expires - now))}

    def _select_background(self, tags):
        provider = self.config.get('photo_provider', 'gradient')
        try:
            if provider not in ('met', 'unsplash', 'pexels'):
                return {**GRADIENT, 'reason': 'selected-gradient'}
            state = self.service.state(provider)
            if state['until'] > self.service.clock():
                return {**GRADIENT, 'reason': 'cooldown'}
            if provider == 'met':
                return self.met_background()
            if not enabled(self.config, provider + '_terms'):
                return {**GRADIENT, 'reason': 'terms-pending'}
            key = self.config.get(provider + '_key', '').strip()
            if not key:
                return {**GRADIENT, 'reason': 'key-required'}
            query = self.query(tags)
            headers = {'Authorization': 'Client-ID ' + key if provider == 'unsplash' else key}
            if provider == 'unsplash':
                url = 'https://api.unsplash.com/photos/random?' + urlencode({'query': query, 'orientation': 'landscape', 'content_filter': 'high', 'count': 10})
                response = self.service.cached(provider + ':' + query, 1800, lambda: self.service.fetch(url, hosts={'api.unsplash.com'}, headers=headers))
                pool = response if isinstance(response, list) else [response]
                pool = [item for item in pool if isinstance(item, dict) and not item.get('premium') and not item.get('plus')]
                photo = self.service.rng.choice(pool)
                images = photo.get('urls', {})
                image = self.unsplash_image(images.get('raw') or images.get('full') or images.get('regular'))
                tracking = public_url(photo.get('links', {}).get('download_location'), {'api.unsplash.com'})
                source = self.unsplash_link(photo.get('links', {}).get('html'))
                author = photo.get('user', {})
                if not image or not source or not tracking or not urlsplit(tracking).path.endswith('/download'):
                    return {**GRADIENT, 'reason': 'invalid-photo'}
                # 배경에 실제 선택할 때마다 공식 download_location을 호출하고 이미지는 hotlink한다.
                self.service.fetch(tracking, hosts={'api.unsplash.com'}, headers=headers)
                return {'provider': provider, 'url': image, 'source_url': source,
                        'creator': str(author.get('name') or 'Unsplash 작가')[:200],
                        'creator_url': self.unsplash_link(author.get('links', {}).get('html')),
                        'license': 'Unsplash License', 'license_url': 'https://unsplash.com/license'}
            url = 'https://api.pexels.com/v1/search?' + urlencode({'query': query, 'orientation': 'landscape', 'per_page': 15})
            response = self.service.cached(provider + ':' + query, 1800, lambda: self.service.fetch(url, hosts={'api.pexels.com'}, headers=headers))
            photo = self.service.rng.choice(response.get('photos', []))
            image = public_url(photo.get('src', {}).get('landscape'), {'images.pexels.com'})
            source = public_url(photo.get('url'), {'www.pexels.com', 'pexels.com'})
            if not image or not source:
                return {**GRADIENT, 'reason': 'invalid-photo'}
            return {'provider': provider, 'url': image, 'source_url': source,
                    'creator': str(photo.get('photographer') or 'Pexels 작가')[:200],
                    'license': 'Pexels License', 'license_url': 'https://www.pexels.com/license/'}
        except (ValueError, OSError, TypeError, AttributeError, KeyError, IndexError) as error:
            state = self.service.state(provider)
            state['until'] = self.service.clock() + max(60, getattr(error, 'retry_after', 0))
            state['last_code'] = getattr(error, 'status', 0)
            return {**GRADIENT, 'reason': 'request-failed'}
