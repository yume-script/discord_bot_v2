"""
골목 청소/우체통 - 메일 관리 Apps Script가 📣│알림방(PORING_MAIL_CHANNEL_ID)에 올리는 알림을 오아시스 거리의 일로 바꾼다.

봇(아메하나 계정, cogs/poring_food.py on_message)이 그 채널의 웹훅 메시지 중 정해진 보낸 이름/문구만 알아본다.
Apps Script는 고치지 않는다. 메일 제목/미리보기/인증 코드는 절대 이야기에 넣지 않는다 - 종류와 개수만 쓴다.

| 알림방 메시지                                   | 오아시스 거리                                              |
|------------------------------------------------|-----------------------------------------------------------|
| ...메일 정리.GAS: 🗑️ N개의 메일을 휴지통으로     | 🧹 홍두식이 골목 쓰레기 N봉지를 치움 (일당, 많으면 "골목 대청소") |
| 알림대기: 일일 기본 제공량을 모두 사용            | 관리사무소: 건물 인터넷 데이터가 바닥남 (동네 사건)            |
| 알림대기: 예약 작업이 정상적으로 완료             | 지하 공용 창고 정리 끝 (관리사무소 황용식에게)                 |
| 알림대기: Google Takeout                       | 골목 우체국에 큰 택배 상자 도착                              |
| 알림대기: 서버 다운 알림                         | 옆 건물 정전 소식 (동네 사건)                                 |
| 알림대기: 이메일 인증 코드 / 새 메일 도착          | 골목 우체통에 등기/편지 한 통 (개수만 센다)                     |

- 같은 날 같은 종류 사건은 SAME_KIND_HOURS 안에 한 번만 (트래픽 알림이 여러 번 와도).
- 인물들은 판단 때 [오늘 골목 소식]으로 오늘 치운 쓰레기/우체통 편지 수를 본다.
저장: storage/poring_food/street_mail.json {"days": {날짜: {"trash", "bags", "mail", "registered", ...}}, "last": {종류: 시각}}
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

ENABLED = os.getenv("PORING_STREET_MAIL", "1") not in ("0", "false", "False", "")
CHANNEL_ID = int(os.getenv("PORING_MAIL_CHANNEL_ID", "591180628842774554") or 0)
CLEANER = os.getenv("PORING_STREET_CLEANER", "홍두식")
WAGE_PER_BAG = 2000          # 쓰레기 한 봉지 일당 (이야기 속 돈)
BAG_SIZE = 5                 # 메일 5개 = 쓰레기 한 봉지
BIG_CLEANUP = 30             # 이만큼 넘게 치우면 "골목 대청소" 동네 사건
SAME_KIND_HOURS = 3
KEEP_DAYS = 14
MAIL_PATH = os.path.join(STATE_DIR, "street_mail.json")

_TRASH = re.compile(r"(\d+)\s*개의 메일을 휴지통")


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
    """[봇, 잠금 안에서] 알림 하나를 골목의 일로. 반환: 로그용 한 줄 ("" = 기록만)."""
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
        life.give_money(CLEANER, wage, f"골목 청소 일당 ({bags}봉지)")
        memory.remember(CLEANER, f"골목 쓰레기 {bags}봉지를 치우고 일당 {wage:,}원을 받았다", 4 if count < BIG_CLEANUP else 6,
                        [], "일")
        if count >= BIG_CLEANUP and not _recent(data, "big_cleanup", now):
            ag.inject_event([ag.world.TOWN], f"{CLEANER}이(가) 오래 쌓인 골목 쓰레기를 {bags}봉지나 치웠다 - 골목이 훤해졌다.",
                            "골목 대청소")
            data.setdefault("last", {})["big_cleanup"] = now.isoformat(timespec="minutes")
        cleaner = next((a for a in ag.load_agents() if a["name"] == CLEANER), None)
        if cleaner and not ag._asleep(cleaner, now.hour) and ag.TOWN_GROUP in ag._groups_of(cleaner):
            ag._post_group(cleaner, ag.TOWN_GROUP, f"🧹 {_line(cleaner, bags, wage)}", {}, ag.load_agents(), at=now)
        out = f"{CLEANER} 골목 청소 {bags}봉지 (메일 {count}개)"
    elif kind in ("mail", "registered"):
        out = ""  # 우체통 편지/등기는 개수만 센다 (판단 때 [오늘 골목 소식])
    elif not _recent(data, kind, now):
        data.setdefault("last", {})[kind] = now.isoformat(timespec="minutes")
        if kind == "traffic":
            ag.inject_event([ag.world.TOWN], "관리사무소 공지 - 이번 달 건물 인터넷 데이터가 바닥났다. 오늘은 인터넷이 느릴 수 있다.",
                            "건물 인터넷 데이터 바닥")
            out = "건물 인터넷 데이터 바닥"
        elif kind == "server_down":
            ag.inject_event([ag.world.TOWN], "옆 건물에 정전이 났다는 소식이 골목에 돌았다.", "옆 건물 정전")
            out = "옆 건물 정전"
        elif kind == "nas":
            ag.deliver("황용식", "지하 공용 창고 정리 작업이 예정대로 끝났다.", "업무", quiet=True)
            out = "지하 창고 정리 끝 (황용식)"
        elif kind == "takeout":
            ag.deliver(CLEANER, "골목 우체국에 큰 택배 상자가 하나 도착했다 - 누가 날라 줘야 한다.", "소식", quiet=True)
            out = "우체국 큰 택배 (홍두식)"
    _save(data)
    if out:
        print(f"[골목] {out}")
    return out


def _line(agent: dict, bags: int, wage: int) -> str:
    from .llm import llm_json
    got = llm_json(
        f"너는 '{agent['name']}'이다. {agent.get('profile', '')}\n[말투] {agent.get('speech', '')}",
        f"방금 오아시스 거리 골목에 쌓인 쓰레기를 {bags}봉지 치우고 일당 {wage:,}원을 받았다. 동네 단톡방에 내 말투로 "
        "1~2문장 한마디. 반드시 JSON으로만 응답: {\"text\": \"한마디\"}",
        temperature=0.9, timeout=30, tag=f"골목 청소 {agent['name']}", agent=agent["name"])
    text = str((got or {}).get("text") or "").strip()
    return text[:200] if text else f"골목 쓰레기 {bags}봉지 치웠어요~ 일당은 받아요!"


def prompt_block(now: datetime | None = None) -> str:
    """판단용: 오늘 골목 소식 (청소한 쓰레기, 우체통 편지 수 - 건물/골목 일이라 모두가 안다)."""
    now = now or now_kst()
    t = (_load().get("days") or {}).get(now.strftime("%Y-%m-%d")) or {}
    parts = []
    if t.get("bags"):
        parts.append(f"{CLEANER}이(가) 골목 쓰레기 {t['bags']}봉지를 치움")
    if t.get("mail") or t.get("registered"):
        parts.append(f"골목 우체통에 편지 {t.get('mail', 0)}통" + (f", 등기 {t['registered']}통" if t.get("registered") else ""))
    for k, label in (("traffic", "건물 인터넷 데이터 바닥"), ("server_down", "옆 건물 정전 소식"), ("takeout", "우체국에 큰 택배")):
        if t.get(k):
            parts.append(label)
    return ("[오늘 골목 소식]\n" + "\n".join(f"- {p}" for p in parts)) if parts else ""
