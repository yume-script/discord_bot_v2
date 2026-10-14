"""
Plex 상영 알림 - 실제 Plex에서 영상 재생이 시작되면 마을(동네 단톡방)에 알린다.

이야기 속 설정: Plex 재생 = 별빛극장 상영관에서 상영이 시작된 것 (kpi.py "지금 관객" = 지금 시청 중인 스트림 수와 같은 설정).
- 봇(cogs/poring_food.py)이 1분마다 Tautulli get_activity(HTTP API)를 본다. LLM/MCP 없이 가볍게.
- 새로 "playing"이 된 재생(같은 영상의 일시정지→재개는 새로 치지 않는다)이 있으면 ANNOUNCER(기본 소라)가
  자기 말투로 동네 단톡방에 한마디 올린다 (LLM 1회, 실패하면 정해진 문장).
- 실제 시청자 계정 이름은 이야기에 넣지 않는다 ("손님"). 숨길 라이브러리(PORING_PLEX_WATCH_HIDE_LIBRARIES)나
  성인 등급 영상은 제목 없이 "영상 하나"로만.
- 같은 작품(시리즈는 시리즈 단위)은 SAME_TITLE_HOURS 안에 다시 알리지 않는다 (몰아보기로 단톡방이 도배되지 않게).
  한 시간에 MAX_PER_HOUR개까지. 알리는 사람이 자는 시간엔 올리지 않고 기록만 한다.
- 인물들은 판단 때 [요즘 별빛극장 상영관에서 상영한 작품]을 본다 (얘깃거리로, 추가 LLM 없음).
저장: storage/poring_food/plex_watch.json {"seen": {재생키: 시각}, "announced": [{"at", "work", "title"}]}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import requests

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

ENABLED = os.getenv("PORING_PLEX_WATCH", "1") not in ("0", "false", "False", "")
TAUTULLI_URL = os.getenv("TAUTULLI_URL", "http://192.168.0.31:8181").rstrip("/")
ANNOUNCER = os.getenv("PORING_PLEX_WATCH_ANNOUNCER", "소라")
PLACE = os.getenv("PORING_PLEX_WATCH_PLACE", "별빛극장 상영관")
HIDE_LIBRARIES = [s.strip() for s in os.getenv("PORING_PLEX_WATCH_HIDE_LIBRARIES", "").split(",") if s.strip()]
ADULT_RATINGS = ("청소년관람불가", "청불", "R18", "NC-17", "X", "XXX", "18+", "19")
MAX_PER_HOUR = int(os.getenv("PORING_PLEX_WATCH_MAX_PER_HOUR", "3"))
SAME_TITLE_HOURS = 3
SHOW_HOURS = 3            # 판단 때 "지금 틀어 놓은 영상"으로 보여 주는 시간
TIMEOUT = 8
WATCH_PATH = os.path.join(STATE_DIR, "plex_watch.json")
_KIND = {"movie": "영화", "episode": "드라마/시리즈", "track": "오디오북", "clip": "영상"}


def _load() -> dict:
    try:
        with open(WATCH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(WATCH_PATH), exist_ok=True)
    tmp = WATCH_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, WATCH_PATH)


# ===================================================================== 받아오기
_last_status = ""


def _status(msg: str) -> None:
    """받아오기 상태가 바뀔 때만 로그 (1분마다 같은 경고가 쌓이지 않게)."""
    global _last_status
    if msg != _last_status:
        _last_status = msg
        print(f"[상영] {msg}")


def fetch() -> list[dict] | None:
    """Tautulli get_activity의 재생 목록 (못 받으면 None - 키가 없거나 서버가 안 받음)."""
    key = os.getenv("TAUTULLI_API_KEY", "")
    if not ENABLED:
        return None
    if not key:
        _status("TAUTULLI_API_KEY가 없어서 재생 확인을 못 함 (.env 확인)")
        return None
    try:
        res = requests.get(f"{TAUTULLI_URL}/api/v2", params={"apikey": key, "cmd": "get_activity"}, timeout=TIMEOUT)
    except requests.RequestException as e:
        _status(f"Tautulli({TAUTULLI_URL}) 연결 실패: {type(e).__name__}")
        return None
    try:
        body = res.json()["response"]
    except (ValueError, KeyError, TypeError):
        _status(f"Tautulli 응답을 못 읽음 (HTTP {res.status_code})")
        return None
    if body.get("result") != "success":
        _status(f"Tautulli 오류: {str(body.get('message') or body.get('result'))[:100]}")
        return None
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    sessions = data.get("sessions") if isinstance(data.get("sessions"), list) else []
    _status(f"Tautulli 재생 확인 중 ({TAUTULLI_URL})")
    return sessions


def describe(s: dict) -> dict:
    """재생 하나 -> {"key", "work"(같은 작품 판단용), "title"(이야기용, 숨기면 ""), "kind", "state"}."""
    media = s.get("media_type", "")
    if media == "episode":
        work = s.get("grandparent_title") or s.get("title") or ""
        try:
            ep = f" {int(s.get('parent_media_index') or 0)}시즌 {int(s.get('media_index') or 0)}화"
        except (TypeError, ValueError):
            ep = ""
        title = f"{work}{ep}"
    elif media == "track":
        work = s.get("grandparent_title") or s.get("title") or ""
        title = work
    else:
        work = s.get("title") or s.get("full_title") or ""
        title = work + (f" ({s['year']})" if s.get("year") else "")
    hidden = (str(s.get("library_name", "")) in HIDE_LIBRARIES
              or str(s.get("content_rating", "")).strip() in ADULT_RATINGS)
    key = f"{s.get('session_key', '')}|{s.get('rating_key', '')}"
    return {"key": key, "work": work, "title": "" if hidden else title.strip(), "kind": _KIND.get(media, "영상"),
            "state": s.get("state", "")}


# ===================================================================== 알리기
def _line(agent: dict, item: dict) -> str:
    from .llm import llm_json
    what = f"'{item['title']}' ({item['kind']})" if item["title"] else f"{item['kind']} 하나 (제목은 말하지 않는다)"
    out = llm_json(
        f"너는 '{agent['name']}'이다. {agent.get('profile', '')}\n[말투] {agent.get('speech', '')}",
        f"지금 {PLACE}에서 관객이 들어와 {what} 상영이 시작됐다. 나는 극장 1층 로비 카페 사장이다. 동네 단톡방에 이걸 알리는 한마디를 내 말투로 1~2문장 써라. "
        "작품 내용/줄거리/출연자를 지어내지 마라. 관객 이름은 모른다. 반드시 JSON으로만 응답: {\"text\": \"한마디\"}",
        temperature=0.9, timeout=30, tag=f"상영 알림 {agent['name']}", agent=agent["name"])
    text = str((out or {}).get("text") or "").strip()
    if text:
        return text[:200]
    return (f"지금 {PLACE}에서 '{item['title']}' 상영 시작했어요~ 보실 분 오세요!" if item["title"]
            else f"지금 {PLACE}에서 {item['kind']} 하나 상영 중이에요~")


def has_new(sessions: list[dict]) -> bool:
    """새로 시작된 재생이 있는지 (잠금 없이 가볍게 - 있을 때만 잠금을 잡고 process)."""
    seen = _load().get("seen") or {}
    return any(i["state"] == "playing" and i["key"] not in seen for i in map(describe, sessions))


def process(sessions: list[dict], now: datetime | None = None) -> list[dict]:
    """[봇, 잠금 안에서] 새로 시작된 재생을 찾아 알린다. 반환: 알린 것 [{"work", "title"}]."""
    from . import agents as ag  # agents가 이 모듈을 쓴다 - 순환 import 피하려고 여기서
    now = now or now_kst()
    data = _load()
    seen = data.get("seen") or {}
    announced = [a for a in data.get("announced") or [] if a.get("at", "") >= (now - timedelta(days=1)).isoformat()]
    items = [describe(s) for s in sessions]
    fresh = [i for i in items if i["state"] == "playing" and i["key"] not in seen]
    for i in items:  # 일시정지 중인 것도 본 것으로 (재개될 때 새로 알리지 않게)
        seen.setdefault(i["key"], now.isoformat(timespec="minutes"))
    cutoff = (now - timedelta(days=2)).isoformat()
    data["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    done = []
    if fresh:
        by_name = {a["name"]: a for a in ag.load_agents()}
        agent = by_name.get(ANNOUNCER)
        hour_ago = (now - timedelta(hours=1)).isoformat()
        recent = (now - timedelta(hours=SAME_TITLE_HOURS)).isoformat()
        for item in fresh:
            if any(a.get("work") == item["work"] and a["at"] >= recent for a in announced):
                continue  # 같은 작품을 방금 알렸다 (몰아보기)
            rec = {"at": now.isoformat(timespec="minutes"), "work": item["work"], "title": item["title"],
                   "kind": item["kind"], "posted": False}
            if (agent and sum(1 for a in announced if a.get("posted") and a["at"] >= hour_ago) < MAX_PER_HOUR
                    and not ag._asleep(agent, now.hour) and ag.TOWN_GROUP in ag._groups_of(agent)):
                text = _line(agent, item)
                ag._post_group(agent, ag.TOWN_GROUP, f"📺 {text}", {}, list(by_name.values()), at=now)
                rec["posted"] = True
                done.append({"work": item["work"], "title": item["title"]})
                print(f"[상영] {PLACE}: {item['title'] or '(제목 숨김)'} - {agent['name']}이 동네 단톡방에 알림")
            else:
                print(f"[상영] {PLACE}: {item['title'] or '(제목 숨김)'} - 기록만 (알릴 사람이 없거나 자는 중/상한)")
            announced.append(rec)
    data["announced"] = announced
    _save(data)
    return done


def prompt_block(now: datetime | None = None) -> str:
    """판단용: 최근 SHOW_HOURS시간 안에 카페 스크린에서 튼 영상 (제목 숨긴 것은 '영상 하나')."""
    now = now or now_kst()
    since = (now - timedelta(hours=SHOW_HOURS)).isoformat()
    recent = [a for a in _load().get("announced") or [] if a.get("at", "") >= since][-3:]
    if not recent:
        return ""
    return (f"[요즘 {PLACE}에서 상영한 작품 - 얘깃거리로 (내용은 지어내지 마라)]\n" + "\n".join(
        f"- {a['at'][11:16]} " + (f"'{a['title']}' ({a.get('kind', '영상')})" if a.get("title") else f"{a.get('kind', '영상')} 하나")
        for a in recent))


# ===================================================================== 웹훅 (Tautulli -> 봇, 재생 시작 순간)
# 1분마다 묻는 대신 Tautulli 알림(Webhook, 트리거 Playback Start)이 재생 시작 순간 봇으로 보낸다 (cogs/poring_food.py가 받음).
# 웹훅이 켜져 있으면 1분 확인은 FALLBACK_POLL_SEC(10분)마다만 - 웹훅이 빠뜨린 것 줍기용.
WEBHOOK_PORT = int(os.getenv("PORING_WEBHOOK_PORT", "0"))           # 0이면 웹훅 수신 안 함
WEBHOOK_TOKEN = os.getenv("PORING_WEBHOOK_TOKEN", "")               # Tautulli 헤더 X-Poring-Token 값 (비면 안 받음)
FALLBACK_POLL_SEC = int(os.getenv("PORING_PLEX_WATCH_FALLBACK_SEC", "600"))
# Tautulli Webhook 설정의 JSON Data (Playback Start) - 시청자 계정 이름은 넣지 않는다
TAUTULLI_BODY = ('{"action": "{action}", "session_key": "{session_key}", "rating_key": "{rating_key}", '
                 '"media_type": "{media_type}", "title": "{title}", "grandparent_title": "{show_name}", '
                 '"parent_media_index": "{season_num}", "media_index": "{episode_num}", "year": "{year}", '
                 '"library_name": "{library_name}", "content_rating": "{content_rating}"}')


def webhook_enabled() -> bool:
    return ENABLED and WEBHOOK_PORT > 0 and bool(WEBHOOK_TOKEN)


def parse_tautulli(body: dict) -> dict | None:
    """Tautulli 웹훅 JSON -> get_activity 세션과 같은 모양 (process/describe가 그대로 쓴다). 재생 시작/재개가 아니면 None."""
    if not isinstance(body, dict) or str(body.get("action", "play")).lower() not in ("play", "resume", "playback start"):
        return None
    if not (body.get("title") or body.get("grandparent_title")):
        return None
    s = {k: str(body.get(k) or "").strip() for k in ("session_key", "rating_key", "media_type", "title", "grandparent_title",
                                                       "parent_media_index", "media_index", "year", "library_name",
                                                       "content_rating")}
    s["state"] = "playing"
    if s["media_type"] == "episode" and s["grandparent_title"] and s["title"].startswith(s["grandparent_title"]):
        s["title"] = s["title"][len(s["grandparent_title"]):].lstrip(" -")  # Tautulli {title}은 '작품 - 회차명' 전체
    return s
