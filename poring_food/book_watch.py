"""
도서관 손님 알림 - 실제 북오아시스에서 누가 책을 읽기 시작하면 마을(마을 통신방)에 알린다.

이야기 속 설정: 북오아시스에서 책을 읽음 = 오아시스 마법도서관에 방문객이 와서 그 책(마법서/그림 두루마리)을 펼친 것.
- 봇(cogs/poring_food.py)이 POLL_SEC(기본 10분)마다 북오아시스 MCP run_readonly_query로 서재별 user_progress
  (책별 마지막으로 읽은 시각/읽은 쪽수)의 최근 기록을 본다. 북오아시스엔 웹훅이 없어서 가끔 묻는 방식.
  (MCP가 매번 SSH + docker exec라 1분마다는 무겁다)
- 새로 읽은 기록(마지막으로 읽은 시각이나 쪽수가 지난번과 다른 책)이 있으면 ANNOUNCER(기본 애순이)가 자기 말투로
  마을 통신방에 한마디 (LLM 1회, 실패하면 정해진 문장). 처음 조회는 기준만 기록한다.
- 실제 읽은 사람 계정은 이야기에 넣지 않는다 ("손님"). 성인 서재(BOOKOASIS_ADULT_DB_TYPES)는 제목 없이
  "금서고에 손님"으로만 - 금서고 담당 위벨이 마을이 아니라 도서관 통신방에 알린다.
- 같은 작품(시리즈 단위)은 SAME_TITLE_HOURS 안에 다시 알리지 않는다 (한 권 읽는 동안 쪽수가 계속 바뀌어도 한 번).
  한 시간에 MAX_PER_HOUR개까지, 알리는 사람이 자는 시간엔 기록만.
- 인물들은 판단 때 [요즘 오아시스 마법도서관 손님이 읽은 책]을 본다 (얘깃거리로, 추가 LLM 없음).
저장: storage/poring_food/book_watch.json {"seen": {서재|책: 기록}, "announced": [...]}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from . import worldcfg
from .config import BOOKOASIS_ADULT_DB_TYPES, BOOKOASIS_ENABLED, BOOKOASIS_MCP_SERVER, BOOKOASIS_STORY_DB_TYPES, STATE_DIR

ENABLED = BOOKOASIS_ENABLED and os.getenv("PORING_BOOK_WATCH", "1") not in ("0", "false", "False", "")
POLL_SEC = int(os.getenv("PORING_BOOK_WATCH_SEC", "600"))
ANNOUNCER = os.getenv("PORING_BOOK_WATCH_ANNOUNCER", "애순이")
ADULT_ANNOUNCER = os.getenv("PORING_BOOK_WATCH_ADULT_ANNOUNCER", "위벨")
PLACE = "오아시스 마법도서관"
MAX_PER_HOUR = int(os.getenv("PORING_BOOK_WATCH_MAX_PER_HOUR", "3"))
SAME_TITLE_HOURS = 3
SHOW_HOURS = 3
ROWS = 20
WATCH_PATH = os.path.join(STATE_DIR, "book_watch.json")
# 서재별 최근 읽은 기록 (계정 정보는 안 가져온다)
SQL = ("SELECT p.book_id, p.pages_read, p.is_completed, p.last_read_at, b.title, b.series_name, b.total_pages, "
       "l.name AS library_name FROM user_progress p JOIN books b ON b.id = p.book_id "
       "LEFT JOIN libraries l ON l.id = b.library_id WHERE p.last_read_at IS NOT NULL "
       f"ORDER BY p.last_read_at DESC LIMIT {ROWS}")


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
def _db_types() -> list[str]:
    return list(dict.fromkeys(BOOKOASIS_STORY_DB_TYPES + BOOKOASIS_ADULT_DB_TYPES))


async def fetch_async() -> list[dict] | None:
    """[봇 이벤트 루프] 서재별 최근 읽은 기록 [{..., "db_type"}]. 한 서재도 못 받으면 None."""
    from ai.mcp_manager import server_session
    from .bookoasis import _parse_tool_result
    out, ok = [], False
    async with server_session(BOOKOASIS_MCP_SERVER) as session:
        for db in _db_types():
            res = await session.call_tool("run_readonly_query", {"db_type": db, "sql": SQL, "max_rows": ROWS})
            if getattr(res, "isError", False):
                continue
            rows = _parse_tool_result(res).get("rows")
            if isinstance(rows, list):
                ok = True
                out += [{**r, "db_type": db} for r in rows if isinstance(r, dict)]
    return out if ok else None


def describe(row: dict) -> dict:
    """기록 하나 -> {"key"(서재|책), "stamp"(마지막 읽은 시각|쪽수), "work"(같은 작품 판단용), "title"(숨기면 ""), "kind", "adult"}."""
    db = str(row.get("db_type", ""))
    adult = db in BOOKOASIS_ADULT_DB_TYPES
    lib = str(row.get("library_name") or "")
    title = str(row.get("title") or "").strip()
    series = str(row.get("series_name") or "").strip()
    work = series or title
    kind = "그림 두루마리" if "웹툰" in lib else ("그림책" if "만화" in lib else "책")
    return {"key": f"{db}|{row.get('book_id')}", "stamp": f"{row.get('last_read_at')}|{row.get('pages_read')}",
            "work": f"{db}|{work}", "title": "" if adult else (title if not series or series in title else f"{series} - {title}"),
            "kind": "금서고 책" if adult else kind, "adult": adult,
            "done": bool(row.get("is_completed"))}


def has_new(rows: list[dict]) -> bool:
    """처음이 아니고, 지난번과 다른 읽은 기록이 있는지 (잠금 없이 가볍게)."""
    seen = _load().get("seen")
    if not seen:
        return bool(rows)  # 처음 - 기준을 적으러 process로
    return any(seen.get(d["key"]) != d["stamp"] for d in map(describe, rows))


# ===================================================================== 알리기
def _line(agent: dict, item: dict) -> str:
    from .llm import llm_json
    if item["adult"]:
        what = "금서고(성인 서가)의 책 한 권 - 제목/내용은 절대 말하지 않는다, '금서고에 손님'까지만"
    else:
        what = f"'{item['title']}' ({item['kind']})" + (" - 끝까지 다 읽었다" if item["done"] else "")
    out = llm_json(
        f"너는 '{agent['name']}'이다. {agent.get('profile', '')}\n[말투] {agent.get('speech', '')}\n{worldcfg.WORLD_RULE}",
        f"지금 {PLACE}에 손님이 와서 {what}을(를) 읽고 있다. 통신방에 이걸 알리는 한마디를 내 말투로 1~2문장 써라. "
        "책 내용/줄거리를 지어내지 마라 (제목은 그대로 말한다). 손님 이름은 모른다. 반드시 JSON으로만 응답: {\"text\": \"한마디\"}",
        temperature=0.9, timeout=30, tag=f"도서관 손님 {agent['name']}", agent=agent["name"])
    text = str((out or {}).get("text") or "").strip()
    if text:
        return text[:200]
    if item["adult"]:
        return "금서고에 손님 한 분 오셨네요. 조용히들 해 주세요."
    return f"지금 {PLACE}에 손님이 '{item['title']}' 읽고 계세요~"


def process(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """[봇, 잠금 안에서] 새로 읽은 기록을 찾아 알린다. 처음은 기준만. 반환: 알린 것 [{"work", "title"}]."""
    from . import agents as ag  # agents가 이 모듈을 쓴다 - 순환 import 피하려고 여기서
    now = now or now_kst()
    data = _load()
    first = not data.get("seen")
    seen = dict(data.get("seen") or {})
    announced = [a for a in data.get("announced") or [] if a.get("at", "") >= (now - timedelta(days=1)).isoformat()]
    items = [describe(r) for r in rows]
    fresh = [] if first else [i for i in items if seen.get(i["key"]) != i["stamp"]]
    for i in items:
        seen[i["key"]] = i["stamp"]
    data["seen"] = dict(list(seen.items())[-500:])
    done = []
    if first:
        print(f"[도서관 손님] 처음 조회 - 기준만 기록 ({len(items)}건)")
    if fresh:
        by_name = {a["name"]: a for a in ag.load_agents()}
        hour_ago = (now - timedelta(hours=1)).isoformat()
        recent = (now - timedelta(hours=SAME_TITLE_HOURS)).isoformat()
        for item in fresh:
            if any(a.get("work") == item["work"] and a["at"] >= recent for a in announced) or \
                    any(d["work"] == item["work"] for d in done):
                continue  # 같은 작품을 방금 알렸다 (읽는 동안 쪽수가 계속 바뀐다)
            # 일반 서가는 애순이가 마을 통신방에, 금서고는 위벨이 도서관 통신방에 (마을에 떠들 일은 아니라서)
            agent = by_name.get(ADULT_ANNOUNCER if item["adult"] else ANNOUNCER) or by_name.get(ANNOUNCER)
            group = ag._shop_group(agent.get("company", "")) if agent and item["adult"] else ag.TOWN_GROUP
            rec = {"at": now.isoformat(timespec="minutes"), "work": item["work"], "title": item["title"],
                   "kind": item["kind"], "adult": item["adult"], "posted": False}
            if (agent and sum(1 for a in announced if a.get("posted") and a["at"] >= hour_ago) < MAX_PER_HOUR
                    and not ag._asleep(agent, now.hour) and group and group in ag._groups_of(agent)):
                ag._post_group(agent, group, f"📚 {_line(agent, item)}", {}, list(by_name.values()), at=now)
                rec["posted"] = True
                done.append({"work": item["work"], "title": item["title"]})
                print(f"[도서관 손님] {item['title'] or '(제목 숨김)'} - {agent['name']}이 {group}에 알림")
            else:
                done.append({"work": item["work"], "title": item["title"], "posted": False})
                print(f"[도서관 손님] {item['title'] or '(제목 숨김)'} - 기록만 (알릴 사람이 없거나 자는 중/상한)")
            announced.append(rec)
    data["announced"] = announced
    _save(data)
    return [d for d in done if d.get("posted", True)]


def prompt_block(company: str = "", now: datetime | None = None) -> str:
    """판단용: 최근 SHOW_HOURS시간 안에 도서관 손님이 읽은 책. 금서고 손님은 도서관 사람만 안다 (그것도 제목 없이)."""
    from .worldcfg import BOOK
    now = now or now_kst()
    since = (now - timedelta(hours=SHOW_HOURS)).isoformat()
    recent = [a for a in _load().get("announced") or [] if a.get("at", "") >= since
              and (not a.get("adult") or company == BOOK)][-3:]
    if not recent:
        return ""
    return (f"[요즘 {PLACE} 손님이 읽은 책 - 얘깃거리로 (내용은 지어내지 마라)]\n" + "\n".join(
        f"- {a['at'][11:16]} " + (f"'{a['title']}' ({a.get('kind', '책')})" if a.get("title") else f"{a.get('kind', '책')} 한 권")
        for a in recent))
