"""Plex 직접 조회: Tautulli가 꺼져 있으면 Plex에 바로 물어서 상영 알림/관객 실적을 이어 간다 (계정 정보는 안 가져옴)."""
from datetime import datetime

from poring_food import plex_api, plex_watch

SESSION = {"sessionKey": "12", "ratingKey": "345", "type": "episode", "title": "3화", "grandparentTitle": "무빙",
           "parentIndex": 1, "index": 3, "librarySectionTitle": "드라마", "contentRating": "15",
           "Player": {"state": "playing"}, "User": {"title": "실제계정"}}


class _Res:
    status_code = 200

    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


def test_to_session_matches_tautulli_shape_without_account():
    s = plex_api.to_session(SESSION)
    assert s["session_key"] == "12" and s["rating_key"] == "345" and s["state"] == "playing"
    assert "실제계정" not in str(s)
    d = plex_watch.describe(s)
    assert d["title"] == "무빙 1시즌 3화" and d["kind"] == "드라마/시리즈"


def test_fetch_falls_back_to_plex(monkeypatch):
    monkeypatch.setenv("PLEX_TOKEN", "t")
    monkeypatch.setattr(plex_watch, "_fetch_tautulli", lambda: None)
    monkeypatch.setattr(plex_api.requests, "get", lambda url, **kw: _Res({"MediaContainer": {"Metadata": [SESSION]}}))
    got = plex_watch.fetch()
    assert got and got[0]["grandparent_title"] == "무빙" and plex_watch.using_fallback()
    monkeypatch.setattr(plex_watch, "_fetch_tautulli", lambda: [])
    assert plex_watch.fetch() == [] and not plex_watch.using_fallback()
    monkeypatch.delenv("PLEX_TOKEN")
    monkeypatch.setattr(plex_watch, "_fetch_tautulli", lambda: None)
    assert plex_watch.fetch() is None                                   # 토큰이 없으면 대체도 못 함


def test_plays_by_date(monkeypatch):
    monkeypatch.setenv("PLEX_TOKEN", "t")
    now = datetime(2026, 10, 25, 20, 0, tzinfo=plex_api.KST)
    t1 = int(datetime(2026, 10, 25, 9, 0, tzinfo=plex_api.KST).timestamp())
    t2 = int(datetime(2026, 10, 24, 23, 0, tzinfo=plex_api.KST).timestamp())
    seen = {}

    def get(url, params=None, **kw):
        seen.update(params or {})
        return _Res({"MediaContainer": {"Metadata": [{"viewedAt": t1}, {"viewedAt": t1}, {"viewedAt": t2}, {"x": 1}]}})
    monkeypatch.setattr(plex_api.requests, "get", get)
    assert plex_api.plays_by_date(8, now) == {"2026-10-25": 2, "2026-10-24": 1}
    assert seen["sort"] == "viewedAt:desc"
