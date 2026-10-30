# 글귀 공급원에 대한 HTTPS 요청과 응답 크기·리다이렉트·주소 경계를 제한합니다.
import ipaddress
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from email.utils import parsedate_to_datetime
import time


class RemoteError(ValueError):
    def __init__(self, message, status=0, retry_after=0):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def public_url(value, hosts=None):
    if not isinstance(value, str) or not value or len(value) > 3000:
        return ''
    try:
        part = urlsplit(value)
        if part.scheme != 'https' or not part.hostname or part.username or part.password or part.port not in (None, 443):
            return ''
        host = part.hostname.lower()
        if hosts and host not in hosts:
            return ''
        if host == 'localhost' or host.endswith(('.local', '.internal')) or '.' not in host:
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            pass
        return value if not any(ord(c) < 32 for c in value) else ''
    except ValueError:
        return ''


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(url, *, hosts, headers=None, body=None, limit=1024 * 1024, timeout=4):
    part = urlsplit(url)
    if part.hostname not in hosts or part.username or part.password or part.fragment:
        raise RemoteError('허용되지 않은 외부 주소입니다.')
    if not public_url(url, hosts):
        raise RemoteError('HTTPS 주소만 사용할 수 있습니다.')
    try:
        for address in socket.getaddrinfo(part.hostname, part.port or (443 if part.scheme == 'https' else 80), type=socket.SOCK_STREAM):
            ip = ipaddress.ip_address(address[4][0])
            if not ip.is_global:
                raise RemoteError('허용되지 않은 서버 주소입니다.')
        request_headers = {'User-Agent': 'BookOasis-GoodQuotes/1.0.0', 'Accept': 'application/json'}
        request_headers.update(headers or {})
        payload = None if body is None else json.dumps(body).encode('utf-8')
        if payload is not None:
            request_headers['Content-Type'] = 'application/json'
        req = Request(url, data=payload, headers=request_headers)
        with build_opener(NoRedirect).open(req, timeout=timeout) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise RemoteError('외부 응답 크기 제한을 초과했습니다.')
            return json.loads(raw)
    except HTTPError as error:
        retry = error.headers.get('Retry-After', '')
        try:
            delay = float(retry) if retry.isdigit() else parsedate_to_datetime(retry).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            delay = 0
        error.close()
        raise RemoteError('외부 서비스 응답 오류', error.code, max(0, min(86400, delay))) from None
    except (URLError, OSError, ValueError) as error:
        if isinstance(error, RemoteError):
            raise
        raise RemoteError('외부 서비스에 연결하거나 JSON을 해석하지 못했습니다.') from None
