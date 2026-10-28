"""
Plex 직접 조회 - Tautulli가 꺼져 있을 때(결계 조절로 내렸거나 죽었을 때) 대신 Plex 서버에 바로 묻는다.

plex MCP(config/mcp_servers.yaml "plex")와 같은 서버/토큰(PLEX_URL, PLEX_TOKEN)을 쓰지만, MCP는 부를 때마다 node 프로세스를
띄워서 1분마다 묻기엔 무겁다 - 그래서 Plex HTTP API를 바로 부른다 (읽기만).
- sessions(): /status/sessions -> Tautulli get_activity 세션과 같은 모양 (plex_watch.describe가 그대로 쓴다).
  + 변환 속도(transcode_speed)와 struggling(): 버퍼링 중이거나 변환이 실시간을 못 따라가는 재생 (결계 조절이 본다).
  시청자 계정(User)은 가져오지 않는다.
- plays_by_date(days): /status/sessions/history/all -> {날짜: 재생 수} (metrics 판매 실적 대체용).
  Plex 기록은 "다 본 것" 위주라 Tautulli 재생 수보다 조금 적게 나온다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

PLEX_URL = os.getenv("PLEX_URL", "http://192.168.0.31:32400").rstrip("/")
TIMEOUT = 8
KST = timezone(timedelta(hours=9))


def enabled() -> bool:
    return bool(os.getenv("PLEX_TOKEN", ""))


def _get(path: str, params: dict | None = None) -> dict | None:
    token = os.getenv("PLEX_TOKEN", "")
    if not token:
        return None
    try:
        res = requests.get(f"{PLEX_URL}{path}", params=params or {}, timeout=TIMEOUT,
                           headers={"X-Plex-Token": token, "Accept": "application/json"})
        if res.status_code != 200:
            return None
        body = res.json()
    except (requests.RequestException, ValueError):
        return None
    mc = body.get("MediaContainer") if isinstance(body, dict) else None
    return mc if isinstance(mc, dict) else None


def to_session(m: dict) -> dict:
    """Plex 세션 하나 -> Tautulli 세션 모양 (계정 정보는 버린다)."""
    player = m.get("Player") if isinstance(m.get("Player"), dict) else {}
    return {"session_key": str(m.get("sessionKey") or ""),     # Tautulli session_key와 같은 값 (바꿔 물어도 같은 재생)
            "rating_key": str(m.get("ratingKey") or ""), "media_type": m.get("type", ""),
            "title": m.get("title", ""), "grandparent_title": m.get("grandparentTitle", ""),
            "parent_media_index": m.get("parentIndex"), "media_index": m.get("index"), "year": m.get("year"),
            "library_name": m.get("librarySectionTitle", ""), "content_rating": m.get("contentRating", ""),
            "state": player.get("state", ""), **_transcode(m)}


def _transcode(m: dict) -> dict:
    """변환 중이면 속도(1.0 = 실시간, 그보다 낮으면 변환이 재생을 못 따라감)와 throttled(앞서 있어서 쉬는 중)."""
    t = m.get("TranscodeSession") if isinstance(m.get("TranscodeSession"), dict) else None
    if not t:
        return {}
    try:
        speed = float(t.get("speed")) if t.get("speed") is not None else None
    except (TypeError, ValueError):
        speed = None
    return {"transcode_speed": speed, "transcode_throttled": bool(t.get("throttled"))}


def struggling(session: dict) -> bool:
    """이 재생이 지금 버벅이나: 플레이어가 버퍼링 중이거나, 변환이 실시간을 못 따라감(속도 < 1.0, 쉬는 중 아님)."""
    if session.get("state") == "buffering":
        return True
    speed = session.get("transcode_speed")
    return speed is not None and speed < 1.0 and not session.get("transcode_throttled")


def sessions() -> list[dict] | None:
    """지금 재생 중인 것 (못 받으면 None)."""
    mc = _get("/status/sessions")
    if mc is None:
        return None
    return [to_session(m) for m in mc.get("Metadata") or [] if isinstance(m, dict)]


def plays_by_date(days: int = 8, now: datetime | None = None) -> dict[str, int] | None:
    """최근 days일 날짜별 재생 수 (KST, 못 받으면 None)."""
    now = now or datetime.now(KST)
    since = int((now - timedelta(days=days)).timestamp())
    mc = _get("/status/sessions/history/all", {"sort": "viewedAt:desc", "viewedAt>": since,
                                                  "X-Plex-Container-Start": 0, "X-Plex-Container-Size": 2000})
    if mc is None:
        return None
    out: dict[str, int] = {}
    for m in mc.get("Metadata") or []:
        try:
            day = datetime.fromtimestamp(int(m.get("viewedAt")), KST).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        out[day] = out.get(day, 0) + 1
    return out
