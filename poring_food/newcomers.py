"""
새 이웃 이사 오기 - 주 1회, 드라마 하나를 골라 그 인물 3~5명이 동네 배경 인물로 이사 온다.

- 고르기: data/drama_pool.json에서 아직 안 쓴 드라마를 위에서부터 (다 쓰면 LLM이 동네극 하나를 직접 고른다).
- 만들기: LLM이 그 드라마에서 우리 동네(광주 동네 상가)에 어울리는 인물 3~5명을 드라마 성향대로 써 준다
  (동네에서의 직업/겉모습/속마음/행동). 실존 배우와 이름이 같은 인물, 이미 있는 이름은 뺀다.
- 반영: characters.load_roster()가 이 파일의 주민을 "광주 동네 상가 / 새 이웃" 배경 인물로 읽는다 ->
  작가 장면/일지에 나오고, 하루 교체(rotation) 때 동네 에이전트로 나설 수 있다.
- 상한: 동네 배경 인물이 MAX_TOWN_NPC명을 넘으면, 이사 온 사람 중 가장 먼저 온 사람(지금 에이전트가 아닌)이 이사 간다
  (조직도 파일에 적힌 원래 주민은 이사 가지 않는다). 기록/기억은 지우지 않는다.
- 관리자: /포링푸드이웃 [드라마] 로 바로 이사 오게 할 수 있다.

저장: storage/poring_food/newcomers.json {"week", "used": [드라마], "residents": [...], "moved_out": [...]}
이 모듈은 characters/agents를 import하지 않는다 (characters가 이 모듈을 읽는다).
"""
from __future__ import annotations

import json
import os
import re

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import DATA_DIR, STATE_DIR
from .llm import llm_json

NEWCOMERS_PATH = os.path.join(STATE_DIR, "newcomers.json")
POOL_PATH = os.path.join(DATA_DIR, "drama_pool.json")
COMPANY = "광주 동네 상가"
DEPT = "새 이웃"
ENABLED = os.getenv("PORING_NEWCOMERS", "1") not in ("0", "false", "False", "")
MOVE_IN_WEEKDAY = 0        # 월요일
MOVE_IN_FROM_HOUR = 6
MAX_TOWN_NPC = int(os.getenv("PORING_MAX_TOWN_NPC", "60"))
TIMEOUT_SEC = 90
_NAME = re.compile(r"^[가-힣]{2,4}$")


def _load() -> dict:
    try:
        with open(NEWCOMERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    tmp = NEWCOMERS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, NEWCOMERS_PATH)


def residents() -> list[dict]:
    """characters.load_roster()용: 지금 동네에 사는 새 이웃들 (조직도 member 형식 + dept/drama)."""
    return [r for r in _load().get("residents", []) if r.get("name")]


def _pool() -> list[dict]:
    try:
        with open(POOL_PATH, "r", encoding="utf-8") as f:
            return [d for d in json.load(f).get("dramas", []) if isinstance(d, dict) and d.get("title")]
    except (OSError, json.JSONDecodeError):
        return []


def _pick_drama(used: list[str]) -> dict:
    for d in _pool():
        if d["title"] not in used:
            return d
    return {"title": "", "note": ""}  # 다 썼으면 LLM이 고른다


def move_in(existing: set[str], drama: str = "", now=None) -> dict:
    """
    드라마 인물들을 이사 오게 한다. existing: 이미 쓰는 이름 전부(로스터/에이전트/reserve).
    반환: {"drama", "added": [주민...], "moved_out": [이름...], "reason"}
    """
    now = now or now_kst()
    data = _load()
    used = data.setdefault("used", [])
    pick = {"title": drama.strip(), "note": ""} if drama.strip() else _pick_drama(used)
    taken = set(existing) | {r["name"] for r in data.get("residents", [])} | {m["name"] for m in data.get("moved_out", [])}
    out = llm_json(
        "너는 가상 도시(광주의 한 동네, 포링푸드 공장 근처 골목)의 인물 설정 담당이다. 한국 드라마 인물을 이 동네로 "
        "이사 온 이웃으로 옮겨 온다. 드라마 속 성격/관계/말버릇을 살리되 직업과 사는 곳은 이 동네에 맞게 바꾼다.",
        (f"[드라마] {pick['title']}" + (f" ({pick['note']})" if pick.get("note") else "") if pick["title"] else
         "[드라마] 동네 사람들이 나오는 현대 한국 드라마 하나를 직접 골라라 (아래 '이미 쓴 드라마'는 빼고)")
        + f"\n[이미 쓴 드라마] {', '.join(used) or '없음'}"
        + f"\n[이미 있는 이름 - 겹치면 안 됨] {', '.join(sorted(taken))[:3000]}\n\n"
        "이 드라마에서 동네 이웃으로 어울리는 인물 3~5명을 골라라. 가족이나 친구 사이면 함께 이사 와도 좋다.\n"
        "- 드라마 속 인물 이름 그대로(성+이름). 실존 배우 이름과 같은 인물, 위 목록에 있는 이름은 빼라.\n"
        "- 실존 인물/정치/혐오 소재 금지.\n"
        "반드시 JSON으로만 응답:\n"
        '{"drama": "드라마 제목", "people": [{"name": "이름", "rank": "동네에서의 직업/처지(짧게)", '
        '"outer_persona": "겉으로 보이는 모습 한 줄", "inner_truth": "속마음/사연 한 줄", '
        '"key_behavior": "자주 하는 행동 2~3개 쉼표로"}]}',
        temperature=0.6, timeout=TIMEOUT_SEC, tag="새 이웃")
    if not out:
        return {"drama": pick["title"], "added": [], "moved_out": [], "reason": "LLM 응답 없음"}
    title = str(out.get("drama") or pick["title"]).strip()[:40]
    added = []
    for p in out.get("people") or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not _NAME.match(name) or name in taken:
            continue
        taken.add(name)
        added.append({
            "name": name, "rank": str(p.get("rank") or "주민").strip()[:20],
            "outer_persona": str(p.get("outer_persona") or "").strip()[:150],
            "inner_truth": str(p.get("inner_truth") or "").strip()[:150],
            "key_behavior": str(p.get("key_behavior") or "").strip()[:100],
            "drama": title, "arrived": now.strftime("%Y-%m-%d"),
        })
        if len(added) >= 5:
            break
    if title and title not in used:
        used.append(title)
    data.setdefault("residents", []).extend(added)
    _save(data)
    if added:
        print(f"[새 이웃] {title}: {', '.join(r['name'] for r in added)} 이사 옴")
    return {"drama": title, "added": added, "moved_out": [], "reason": "" if added else "쓸 만한 인물이 없음"}


def trim(town_npc_count: int, active_agents: set[str], now=None) -> list[str]:
    """동네 배경 인물이 상한을 넘으면 먼저 온 새 이웃부터(지금 에이전트가 아닌) 이사 보낸다. 반환: 이사 간 이름."""
    over = town_npc_count - MAX_TOWN_NPC
    if over <= 0:
        return []
    now = now or now_kst()
    data = _load()
    res = data.get("residents", [])
    gone = []
    for r in sorted(res, key=lambda x: x.get("arrived", "")):
        if len(gone) >= over:
            break
        if r["name"] in active_agents:
            continue
        gone.append(r)
    if not gone:
        return []
    names = {g["name"] for g in gone}
    data["residents"] = [r for r in res if r["name"] not in names]
    data.setdefault("moved_out", []).extend({**g, "left": now.strftime("%Y-%m-%d")} for g in gone)
    _save(data)
    print(f"[새 이웃] 이사 감: {', '.join(names)}")
    return sorted(names)


def due(now=None) -> bool:
    """이번 주 이사가 아직이고, 월요일 아침이 지났는지."""
    if not ENABLED:
        return False
    now = now or now_kst()
    y, w, _ = now.isocalendar()
    week = f"{y}-W{w:02d}"
    return _load().get("week") != week and (now.weekday() > MOVE_IN_WEEKDAY or now.hour >= MOVE_IN_FROM_HOUR)


def mark_week(now=None) -> None:
    now = now or now_kst()
    y, w, _ = now.isocalendar()
    data = _load()
    data["week"] = f"{y}-W{w:02d}"
    _save(data)
