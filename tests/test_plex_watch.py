"""Plex 상영 알림: 새로 시작된 재생만 동네 단톡방에 알리고, 일시정지/재개·몰아보기·숨길 영상은 걸러낸다."""
import json
from datetime import datetime

from poring_food import agents, plex_watch


def _sess(key, rating, title, state="playing", media="movie", **kw):
    return {"session_key": key, "rating_key": rating, "title": title, "state": state, "media_type": media,
            "friendly_name": "실제계정", **kw}


def _posts():
    try:
        with open(agents.GROUP_PATH, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


def test_new_playback_is_announced_once(fake_llm, monkeypatch):
    monkeypatch.setattr(agents, "_group_channel", lambda g: 123)
    fake_llm(lambda system, user: {"text": "지금 카페 스크린에서 영화 틀었어요~"})
    now = datetime(2026, 10, 14, 15, 0)
    done = plex_watch.process([_sess(1, 10, "기생충", year=2019)], now)
    assert done == [{"work": "기생충", "title": "기생충 (2019)"}]
    posts = _posts()
    assert posts[-1]["name"] == "소라" and posts[-1]["group"] == agents.TOWN_GROUP and "📺" in posts[-1]["text"]
    assert "실제계정" not in posts[-1]["text"]
    # 같은 재생(일시정지 -> 재개)은 다시 안 알린다
    assert plex_watch.process([_sess(1, 10, "기생충", state="paused")], now) == []
    assert plex_watch.process([_sess(1, 10, "기생충")], now) == []
    assert "기생충" in plex_watch.prompt_block(now)


def test_binge_hidden_and_asleep(fake_llm, monkeypatch):
    monkeypatch.setattr(agents, "_group_channel", lambda g: 123)
    fake_llm(lambda system, user: {})
    now = datetime(2026, 10, 14, 20, 0)
    ep = {"media_type": "episode", "grandparent_title": "무빙", "parent_media_index": 1}
    done = plex_watch.process([_sess(2, 21, "1화", media_index=1) | ep], now)
    assert done and done[0]["title"] == "무빙 1시즌 1화"
    # 다음 화는 몰아보기 - 같은 작품이라 안 알린다
    assert plex_watch.process([_sess(3, 22, "2화", media_index=2) | ep], now) == []
    # 성인 등급은 제목 없이
    done = plex_watch.process([_sess(4, 30, "비밀영화", content_rating="청소년관람불가")], now)
    assert done == [{"work": "비밀영화", "title": ""}] and "비밀영화" not in _posts()[-1]["text"]
    # 소라가 자는 시간(1~7시)엔 기록만
    assert plex_watch.process([_sess(5, 40, "새벽영화")], datetime(2026, 10, 15, 3, 0)) == []
    assert "새벽영화" in plex_watch.prompt_block(datetime(2026, 10, 15, 3, 10))


def test_fetch_without_key_is_none(monkeypatch):
    monkeypatch.delenv("TAUTULLI_API_KEY", raising=False)
    assert plex_watch.fetch() is None


def test_has_new_and_fetch_status(monkeypatch):
    s = [_sess(7, 70, "새영화")]
    assert plex_watch.has_new(s)
    plex_watch.process([_sess(7, 70, "새영화", state="paused")], datetime(2026, 10, 14, 15, 0))
    assert not plex_watch.has_new(s)   # 이미 본 재생

    class Res:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    monkeypatch.setenv("TAUTULLI_API_KEY", "k")
    monkeypatch.setattr(plex_watch.requests, "get", lambda *a, **k: Res(
        {"response": {"result": "success", "data": {"stream_count": "1", "sessions": s}}}))
    assert plex_watch.fetch() == s
    monkeypatch.setattr(plex_watch.requests, "get", lambda *a, **k: Res(
        {"response": {"result": "error", "message": "Invalid apikey", "data": {}}}))
    assert plex_watch.fetch() is None
    assert "Invalid apikey" in plex_watch._last_status


def test_tautulli_webhook_payload(fake_llm, monkeypatch):
    monkeypatch.setattr(agents, "_group_channel", lambda g: 123)
    fake_llm(lambda system, user: {"text": "상영 시작!"})
    body = json.loads(plex_watch.TAUTULLI_BODY.replace("{action}", "play").replace("{session_key}", "9")
                      .replace("{rating_key}", "77").replace("{media_type}", "episode").replace("{title}", "무빙 - 첫 만남")
                      .replace("{show_name}", "무빙").replace("{season_num}", "1").replace("{episode_num}", "3")
                      .replace("{year}", "2023").replace("{library_name}", "드라마").replace("{content_rating}", "15"))
    s = plex_watch.parse_tautulli(body)
    assert s["state"] == "playing" and s["title"] == "첫 만남" and "friendly_name" not in s
    assert plex_watch.describe(s)["title"] == "무빙 1시즌 3화"
    assert plex_watch.parse_tautulli({**body, "action": "stop"}) is None
    now = datetime(2026, 10, 14, 15, 0)
    assert plex_watch.process([s], now)                                  # 웹훅으로 알림
    poll = {"session_key": "9", "rating_key": "77", "state": "playing", "media_type": "episode",
            "grandparent_title": "무빙", "parent_media_index": 1, "media_index": 3}
    assert not plex_watch.has_new([poll])                                # 1분 확인이 같은 재생을 또 알리지 않는다
