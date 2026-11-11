"""
서버실 잔해 치우기/우편함 - 메일 관리 Apps Script가 📣│알림방(PORING_MAIL_CHANNEL_ID)에 올리는 알림을 오아시스 거리의 일로 바꾼다.

봇(아메하나 계정, cogs/poring_food.py on_message)이 그 채널의 웹훅 메시지 중 정해진 보낸 이름/문구만 알아본다.
메일 제목/미리보기/인증 코드는 절대 이야기에 넣지 않는다 - 종류와 개수만 쓴다.

Apps Script(scripts/gas/mail_manage.gs)의 NOTICE_MODE:
- "01" 기존 방식: 📣│알림방에 사람이 보는 알림 그대로 (봇은 그 글을 읽어 이야기로도 쓴다).
- "02" 숨김: 사람이 보는 알림은 안 올리고, 봇만 보는 비공개 채널(PORING_MAIL_HIDDEN_CHANNEL_ID)에
  `poring-mail kind=<종류> count=<개수>` 한 줄만 보낸다(제목/미리보기는 아예 안 보냄). 봇은 읽은 뒤 그 글을 지우고
  (PORING_MAIL_DELETE=0이면 남김) 거리 청소/우편함 이야기로만 남긴다.

| 알림방 메시지                                   | 오아시스 거리                                                    |
|------------------------------------------------|-----------------------------------------------------------------|
| ...메일 정리.GAS: 🗑️ N개의 메일을 휴지통으로     | 🧹 카즈마가 서버실 아래 폐기 박스 N수레를 치움 (품삯, 많으면 "서버실 대청소") |
| 알림대기: 일일 기본 제공량을 모두 사용            | 시설관리팀: 통신망 리소스가 바닥남 (거리 사건)                     |
| 알림대기: 예약 작업이 정상적으로 완료             | 지하 물류창고 정리 끝 (시설관리팀 소우에이에게)                          |
| 알림대기: Google Takeout                       | 택배 영업소에 큰 짐 상자 도착                                    |
| 알림대기: 서버 다운 알림                         | 이웃 거리 서버가 무너졌다는 소식 (거리 사건)                         |
| 알림대기: 이메일 인증 코드 / 새 메일 도착          | 거리 우편함에 등기 우편/편지 한 통 (개수만 센다)                 |

- 같은 날 같은 종류 사건은 SAME_KIND_HOURS 안에 한 번만 (트래픽 알림이 여러 번 와도).
- 인물들은 판단 때 [오늘 거리 소식]으로 오늘 치운 잔해/우편함 편지 수를 본다.
저장: storage/poring_food/street_mail.json {"days": {날짜: {"trash", "bags", "mail", "registered", ...}}, "last": {종류: 시각}}
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from . import worldcfg
from .config import STATE_DIR

ENABLED = os.getenv("PORING_STREET_MAIL", "1") not in ("0", "false", "False", "")
CHANNEL_ID = int(os.getenv("PORING_MAIL_CHANNEL_ID", "591180628842774554") or 0)
CLEANER = os.getenv("PORING_STREET_CLEANER", "카즈마")
WAGE_PER_BAG = 2000          # 잔해 한 수레 품삯 (이야기 속 돈)
BAG_SIZE = 5                 # 메일 5개 = 잔해 한 수레
BIG_CLEANUP = 30             # 이만큼 넘게 치우면 "서버실 대청소" 거리 사건
SAME_KIND_HOURS = 3
KEEP_DAYS = 14
MAIL_PATH = os.path.join(STATE_DIR, "street_mail.json")

HIDDEN_CHANNEL_ID = int(os.getenv("PORING_MAIL_HIDDEN_CHANNEL_ID", "0") or 0)
DELETE_HIDDEN = os.getenv("PORING_MAIL_DELETE", "1") not in ("0", "false", "False", "")
KINDS = ("trash", "traffic", "nas", "takeout", "server_down", "registered", "mail")

_TRASH = re.compile(r"(\d+)\s*개의 메일을 휴지통")
_SIGNAL = re.compile(r"^\s*poring-mail\s+kind=([a-z_]+)\s+count=(\d+)\s*$", re.M)


def channels() -> set[int]:
    return {c for c in (CHANNEL_ID, HIDDEN_CHANNEL_ID) if c}


def _load() -> dict:
    try:
        with open(MAIL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(MAIL_PATH), exist_ok=True)
    tmp = MAIL_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MAIL_PATH)


def parse(author: str, content: str) -> dict | None:
    """알림방 웹훅 메시지 하나 -> {"kind", "count"} (모르는 메시지면 None). 제목/본문은 돌려주지 않는다."""
    author, content = author or "", content or ""
    sig = _SIGNAL.search(content)                     # 02 숨김 방식: 정해진 한 줄 신호
    if sig:
        kind, count = sig.group(1), int(sig.group(2))
        return {"kind": kind, "count": max(1, min(count, 10000))} if kind in KINDS else None
    if "메일 정리" in author:
        m = _TRASH.search(content)
        return {"kind": "trash", "count": int(m.group(1))} if m else None
    if "알림대기" not in author:
        return None
    head = content.strip().splitlines()[0] if content.strip() else ""   # 첫 줄(종류)만 본다 - 제목/미리보기는 안 봄
    for key, kind in (("기본 제공량을 모두 사용", "traffic"), ("예약 작업이 정상적으로 완료", "nas"),
                      ("Google Takeout", "takeout"), ("서버 다운", "server_down"), ("인증 코드", "registered"),
                      ("새 메일 도착", "mail")):
        if key in head:
            return {"kind": kind, "count": 1}
    return None


def _recent(data: dict, kind: str, now: datetime) -> bool:
    last = (data.get("last") or {}).get(kind, "")
    return bool(last) and last >= (now - timedelta(hours=SAME_KIND_HOURS)).isoformat()


def handle(ev: dict, now: datetime | None = None) -> str:
    """[봇, 잠금 안에서] 알림 하나를 거리의 일로. 반환: 로그용 한 줄 ("" = 기록만)."""
    from . import agents as ag, life, memory  # agents가 이 모듈을 쓴다 - 순환 import 피하려고 여기서
    now = now or now_kst()
    data = _load()
    day = now.strftime("%Y-%m-%d")
    days = data.setdefault("days", {})
    today = days.setdefault(day, {})
    kind, count = ev["kind"], int(ev.get("count") or 1)
    today[kind] = today.get(kind, 0) + count
    cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    data["days"] = {d: v for d, v in days.items() if d >= cutoff}
    out = ""
    if kind == "trash":
        bags = max(1, round(count / BAG_SIZE))
        today["bags"] = today.get("bags", 0) + bags
        wage = bags * WAGE_PER_BAG
        life.give_money(CLEANER, wage, f"서버실 잔해 치우기 품삯 ({bags}수레)")
        memory.remember(CLEANER, f"서버실 아래 폐기 박스 {bags}수레를 치우고 품삯 {wage:,}원을 받았다", 4 if count < BIG_CLEANUP else 6,
                        [], "일")
        if count >= BIG_CLEANUP and not _recent(data, "big_cleanup", now):
            ag.inject_event([ag.world.TOWN], f"{CLEANER}이(가) 서버실 아래 오래 쌓인 폐기 박스를 {bags}수레나 치웠다 - 서버실 앞이 훤해졌다.",
                            "서버실 대청소")
            data.setdefault("last", {})["big_cleanup"] = now.isoformat(timespec="minutes")
        cleaner = next((a for a in ag.load_agents() if a["name"] == CLEANER), None)
        if cleaner and not ag._asleep(cleaner, now.hour) and ag.TOWN_GROUP in ag._groups_of(cleaner):
            ag._post_group(cleaner, ag.TOWN_GROUP, f"🧹 {_line(cleaner, bags, wage)}", {}, ag.load_agents(), at=now)
        out = f"{CLEANER} 서버실 잔해 {bags}수레 (메일 {count}개)"
    elif kind in ("mail", "registered"):
        out = ""  # 우편함 편지/등기 우편은 개수만 센다 (판단 때 [오늘 거리 소식])
    elif not _recent(data, kind, now):
        data.setdefault("last", {})[kind] = now.isoformat(timespec="minutes")
        if kind == "traffic":
            ag.inject_event([ag.world.TOWN], "시설관리팀 공지 - 이번 달 통신망 리소스가 바닥났다. 오늘은 메신저가 잘 안 터질 수 있다.",
                            "통신망 리소스 바닥")
            out = "통신망 리소스 바닥"
        elif kind == "server_down":
            ag.inject_event([ag.world.TOWN], "이웃 거리 서버가 무너졌다는 소식이 거리에 돌았다.", "이웃 거리 서버 붕괴")
            out = "이웃 거리 서버 붕괴"
        elif kind == "nas":
            ag.deliver("소우에이", "지하 물류창고 정리 작업이 예정대로 끝났다.", "업무", quiet=True)
            out = "지하 창고 정리 끝 (소우에이)"
        elif kind == "takeout":
            ag.deliver(CLEANER, "택배 영업소에 큰 짐 상자가 하나 도착했다 - 누가 날라 줘야 한다.", "소식", quiet=True)
            out = "택배 영업소 큰 짐 (카즈마)"
    _save(data)
    if out:
        print(f"[서버실 청소] {out}")
    return out


def _line(agent: dict, bags: int, wage: int) -> str:
    from .llm import llm_json
    got = llm_json(
        f"너는 '{agent['name']}'이다. {agent.get('profile', '')}\n[말투] {agent.get('speech', '')}\n{worldcfg.WORLD_RULE}",
        f"방금 오아시스 거리 서버실 아래 쌓인 폐기 박스를 {bags}수레 치우고 품삯 {wage:,}원을 받았다. 사내 단톡방에 내 말투로 "
        "1~2문장 한마디. 반드시 JSON으로만 응답: {\"text\": \"한마디\"}",
        temperature=0.9, timeout=30, tag=f"서버실 청소 {agent['name']}", agent=agent["name"])
    text = str((got or {}).get("text") or "").strip()
    return text[:200] if text else f"서버실 아래 잔해 {bags}수레 치웠어요~ 품삯은 받아요!"


def prompt_block(now: datetime | None = None) -> str:
    """판단용: 오늘 거리 소식 (치운 잔해, 우편함 편지 수 - 서버실/거리 일이라 모두가 안다)."""
    now = now or now_kst()
    t = (_load().get("days") or {}).get(now.strftime("%Y-%m-%d")) or {}
    parts = []
    if t.get("bags"):
        parts.append(f"{CLEANER}이(가) 서버실 아래 잔해 {t['bags']}수레를 치움")
    if t.get("mail") or t.get("registered"):
        parts.append(f"우편함에 편지 {t.get('mail', 0)}통" + (f", 등기 우편 {t['registered']}통" if t.get("registered") else ""))
    for k, label in (("traffic", "통신망 리소스 바닥"), ("server_down", "이웃 회사 서버 장애 소식"), ("takeout", "택배 영업소에 큰 짐")):
        if t.get(k):
            parts.append(label)
    return ("[오늘 거리 소식]\n" + "\n".join(f"- {p}" for p in parts)) if parts else ""
