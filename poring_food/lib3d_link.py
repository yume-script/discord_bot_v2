"""
[연결] BookOasis 3D 서재 (bookoasis_assistant) - 오아시스 거리의 서점 건물과 거리 인물을 잇는다.

3D 서재에는 서재 쪽 AI 직원(사서 등)이 따로 있고, 이 봇의 인물들은 서재 바깥 사람이다. 둘은 서로의
속을 모르고 "전화"와 "배달"로만 주고받는다.

1) 전화 받기 - POST /lib3d/call (포링푸드 웹 서버, PORING_WEBHOOK_PORT · Authorization: Bearer LIB3D_CALL_TOKEN)
   3D 서재 [AI 직원]에서 대표(등)의 '바깥 AI 주소'를 http://<봇 서버>:<포트>/lib3d/call 로 넣으면
   그 직원에게 오는 질문(ask)과 부하 직원의 보고(report)가 여기로 온다. LIB3D_CALL_AGENT(기본 애순이)가
   지금 자기 상태·기억을 보고 자기 말투로 답한다(LLM 1회). 받은 일은 그 인물의 받은편지함에도 넣어서
   다음 판단 때 "3D 서재에서 이런 연락이 왔었지"를 안다(조용히 - 이것 때문에 깨우지는 않음).
2) 배달 보내기 - POST <LIB3D_URL>/api/lib3d/events (Authorization: Bearer LIB3D_EVENT_TOKEN)
   LIB3D_DELIVERY_SEC(기본 10분)마다 북오아시스 신간(LIB3D_DELIVERY_TYPES, 성인 서재 제외)과 Plex 새 영상을
   확인해서, 새로 들어온 게 있으면 배달꾼(LIB3D_COURIERS 중 깨어 있는 사람)이 상자를 들고 서재에 간다.
   처음 확인할 때는 기준점만 잡는다(있던 장서가 한꺼번에 배달되지 않게). 배달꾼의 한마디는 그 인물 말투로(LLM 1회,
   실패하면 정해 둔 문장).

[안전] 조회 도구(search_books, get_recently_added)만 이름으로 부른다. 성인 서재·숨길 Plex 라이브러리·성인 등급은
제목을 보내지 않는다(배달하지 않음). 토큰은 로그에 남기지 않는다.
"""
from __future__ import annotations

import hmac
import json
import os
import random
from datetime import datetime

import requests

from . import agents, memory, runtime
from ._log import pf_print as print  # print()를 봇 로그로
from .bookoasis import _parse_tool_result, _root_cause
from .config import BOOKOASIS_MCP_SERVER, STATE_DIR
from .clock import now_kst
from .llm import llm_json
from .plex_watch import ADULT_RATINGS, HIDE_LIBRARIES

LIB3D_URL = os.getenv("LIB3D_URL", "").strip().rstrip("/")
EVENT_TOKEN = os.getenv("LIB3D_EVENT_TOKEN", "").strip()
CALL_TOKEN = os.getenv("LIB3D_CALL_TOKEN", "").strip()
CALL_AGENT = os.getenv("LIB3D_CALL_AGENT", "애순이").strip() or "애순이"
COURIERS = [s.strip() for s in os.getenv("LIB3D_COURIERS", "").split(",") if s.strip()]
DELIVERY_SEC = max(120, int(os.getenv("LIB3D_DELIVERY_SEC", "600") or 600))
DELIVERY_TYPES = [t.strip() for t in os.getenv("LIB3D_DELIVERY_TYPES", "general,audiobook").split(",")
                  if t.strip() and t.strip() != "adult"]   # 성인 서재는 넣어도 뺀다
DELIVERY_VIDEO = os.getenv("LIB3D_DELIVERY_VIDEO", "1") not in ("0", "false", "False", "")
PLEX_SERVER = os.getenv("PORING_PLEX_MCP_SERVER", "plex")
STATE_PATH = os.path.join(STATE_DIR, "lib3d_delivery.json")
CALL_TIMEOUT = 40          # 3D 서재는 45초 기다린다
FETCH_TIMEOUT = 90
MAX_ITEMS = 12             # 배달 한 번에 담는 제목 수
KIND_OF_DB = {"general": "book", "audiobook": "audio"}


def call_enabled() -> bool:
    return bool(CALL_TOKEN)


def delivery_enabled() -> bool:
    return bool(LIB3D_URL and EVENT_TOKEN)


def call_token_ok(header: str) -> bool:
    given = str(header or "").strip()
    given = given[7:].strip() if given.lower().startswith("bearer ") else given
    return bool(CALL_TOKEN) and bool(given) and hmac.compare_digest(given.encode(), CALL_TOKEN.encode())


# ===================================================================== 전화 받기
def _who(d) -> str:
    d = d if isinstance(d, dict) else {}
    name, job = str(d.get("name") or "").strip()[:20], str(d.get("job") or "").strip()[:20]
    return f"{name}({job})" if name and job else name or job or "서재 직원"


def answer_call(body: dict) -> tuple[dict, str]:
    """[스레드] 3D 서재에서 온 전화 하나에 답한다. 반환: (돌려줄 JSON, 받은편지함에 넣을 한 줄 - 없으면 "")"""
    kind = body.get("type")
    if kind not in ("ask", "report"):
        raise ValueError("type은 ask·report 중 하나입니다.")
    me = next((a for a in agents.load_agents() if a["name"] == CALL_AGENT), None)
    if not me:
        raise ValueError(f"LIB3D_CALL_AGENT '{CALL_AGENT}'가 에이전트 목록에 없습니다.")
    my_post = _who(body.get("agent"))   # 3D 서재에서 내가 맡은 자리 (예: 대표)
    if kind == "ask":
        asker = str(body.get("user") or "손님").strip()[:30]
        text = " ".join(str(body.get("question") or "").split())[:400]
        if not text:
            raise ValueError("question이 비었습니다.")
        situation = (f"오아시스 거리의 서점 건물 'BookOasis 3D 서재'에서 전화가 왔다. 서재에 온 "
                     f"{'손님 ' + asker if body.get('guest') else asker + '님'}이 서재 쪽 사람들이 '{my_post}'로 부르는 나를 찾아 말을 건다.\n"
                     f"[들은 말] {text}")
        rule = ("서재에 온 사람에게 하는 말이라 짧게(2~3문장, 200자 안). 서재 안 책 찾기는 서재 사서가 더 잘 아니 "
                "모르는 책 제목을 지어내지 마라. 거리 안의 비밀(다른 기관 다툼, 장부 숫자, 누군가의 사생활)은 말하지 않는다.")
        query = text
    else:
        sender = _who(body.get("from"))
        text = " ".join(str(body.get("text") or "").split())[:600]
        if not text:
            raise ValueError("text가 비었습니다.")
        situation = (f"오아시스 거리의 서점 건물 'BookOasis 3D 서재'에서 일하는 부하 직원 {sender}이(가) 메신저로 보고를 올렸다. "
                     f"나는 그 서재의 '{my_post}'이다.\n[보고] {text}")
        rule = "상사로서 한두 문장(150자 안)으로 답한다 - 칭찬, 걱정, 다음에 챙길 것 중 내 성격대로."
        query = text
    act = agents.active(me["name"]) or {}
    now_line = (f"[지금 나] {act.get('location')}에서 {act.get('activity')} ({act.get('state')}). 속마음: {act.get('thought', '')}"
                if act else "[지금 나] 평소 하던 일을 하는 중")
    try:
        mems = memory.recall(me["name"], query=query, k=3)
    except Exception:  # noqa: BLE001 - 기억을 못 꺼내도 답은 한다
        mems = []
    mem_line = ("\n[떠오르는 기억] " + " / ".join(m.get("text", "")[:80] for m in mems)) if mems else ""
    user = (f"{situation}\n{now_line}{mem_line}\n\n{rule}\n"
            "자다가 깼거나 바쁘면 그 티가 나도 된다. " + agents._voice(me) + "\n"
            '반드시 JSON으로만: {"text": "내가 실제로 하는 말", "say": "서재 안에 들릴 혼잣말 한마디(없으면 빈 문자열)"}')
    out = llm_json(agents._system(me), user, temperature=0.8, timeout=CALL_TIMEOUT - 5, tag=f"3D 서재 전화 {me['name']}", agent=me["name"])
    reply = " ".join(str((out or {}).get("text") or "").split())[:400]
    if not reply:
        return {"text": ""}, ""   # 전화를 못 받음 - 3D 서재는 "답이 없었다"로 처리
    say = " ".join(str((out or {}).get("say") or "").split())[:80]
    if kind == "ask":
        note = f"[3D 서재 전화] {asker}: {text[:120]} → 내 답: {reply[:120]}"
    else:
        note = f"[3D 서재 보고] {sender}: {text[:160]} → 내 답: {reply[:100]}"
    return {"text": reply, "say": say}, note


def remember_call(note: str) -> None:
    """[스레드, 포링푸드 잠금 안에서] 받은 전화를 그 인물의 받은편지함에 (다음 판단 때 읽음, 깨우지는 않음)"""
    if note:
        agents.deliver(CALL_AGENT, note, kind="통신", quiet=True)


# ===================================================================== 배달 보내기
def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(data: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_PATH)


async def _fetch_async() -> dict:
    """[봇 루프] 북오아시스 서재별 최신 작품 + Plex 최근 추가 (조회 도구만)"""
    from ai.mcp_manager import server_session
    out = {"books": {}, "plex": None}
    if DELIVERY_TYPES:
        async with server_session(BOOKOASIS_MCP_SERVER) as session:
            for db_type in DELIVERY_TYPES:
                res = await session.call_tool("search_books", {"db_type": db_type, "sort": "date_desc", "limit": 20})
                if not getattr(res, "isError", False):
                    out["books"][db_type] = _parse_tool_result(res).get("series", []) or []
    if DELIVERY_VIDEO:
        try:
            async with server_session(PLEX_SERVER) as session:
                res = await session.call_tool("get_recently_added", {"limit": 30})
            out["plex"] = _parse_tool_result(res).get("recentlyAdded", []) or []
        except Exception as e:  # noqa: BLE001 - Plex가 없거나 잠깐 안 돼도 책 배달은 한다
            print(f"[3D 서재 배달] Plex 확인 실패: {_root_cause(e)!r:.120}")
    return out


def _plex_title(it: dict) -> str:
    if it.get("type") == "episode" and it.get("grandparentTitle"):
        return str(it["grandparentTitle"])
    if it.get("type") == "season" and it.get("parentTitle"):
        return str(it["parentTitle"])
    return str(it.get("title") or "")


def new_items(data: dict, state: dict) -> tuple[list[dict], dict]:
    """지난번 이후 새로 들어온 것만 [{title, kind}]. 처음이면 기준점만. 반환: (새 것, 바뀐 상태)"""
    seen = dict(state.get("books") or {})
    items: list[dict] = []
    for db_type, series in (data.get("books") or {}).items():
        last = str(seen.get(db_type, ""))
        newest = last
        for s in series:
            added = str(s.get("latest_added") or "")
            if not added:
                continue
            newest = max(newest, added)
            if last and added > last:
                items.append({"title": str(s.get("display_name") or s.get("series_name") or "제목 없음")[:80],
                              "kind": KIND_OF_DB.get(db_type, "book")})
        seen[db_type] = newest
    plex_last = int(state.get("plex") or 0)
    plex_new = plex_last
    if data.get("plex") is not None:
        for it in data["plex"]:
            try:
                ts = int(it.get("addedAt") or 0)
            except (TypeError, ValueError):
                continue
            plex_new = max(plex_new, ts)
            hidden = (str(it.get("librarySectionTitle") or "") in HIDE_LIBRARIES
                      or str(it.get("contentRating") or "").strip() in ADULT_RATINGS)
            if plex_last and ts > plex_last and not hidden and _plex_title(it):
                items.append({"title": _plex_title(it)[:80], "kind": "video"})
    uniq, have = [], set()
    for it in items:   # 같은 작품 여러 회차는 한 번만
        key = (it["title"], it["kind"])
        if key not in have:
            have.add(key)
            uniq.append(it)
    return uniq[:MAX_ITEMS], {"books": seen, "plex": plex_new, "checked": now_kst().isoformat(timespec="seconds")}


def pick_courier() -> dict | None:
    roster = agents.load_agents()
    pool = [a for a in roster if a["name"] in COURIERS] if COURIERS else \
        [a for a in roster if a.get("company", "").startswith("ZEEPS 편집국") and a["name"] != CALL_AGENT]
    awake = [a for a in pool if not agents.is_asleep_now(a["name"])]
    return random.choice(awake or pool) if (awake or pool) else None


def _courier_line(courier: dict | None, items: list[dict]) -> str:
    titles = ", ".join(it["title"] for it in items[:4]) + (" 외" if len(items) > 4 else "")
    fallback = f"{titles} 배달 왔어요! 데스크에 둘게요."
    if not courier:
        return fallback
    user = ("거리 일로 오아시스 거리의 서점 건물 'BookOasis 3D 서재'에 새로 들어온 것을 상자에 담아 배달하러 왔다. "
            f"[상자 안] {titles}\n안내 데스크에 상자를 내려놓으며 하는 한마디(60자 안). " + agents._voice(courier) + "\n"
            '반드시 JSON으로만: {"text": "한마디"}')
    out = llm_json(agents._system(courier), user, temperature=0.9, timeout=30, tag=f"3D 서재 배달 {courier['name']}", agent=courier["name"])
    line = " ".join(str((out or {}).get("text") or "").split())[:120]
    return line or fallback


def send_event(body: dict) -> dict:
    res = requests.post(LIB3D_URL + "/api/lib3d/events", json=body, timeout=15,
                        headers={"Authorization": "Bearer " + EVENT_TOKEN, "User-Agent": "discord_bot_v2-poring_food"})
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code != 200 or not data.get("success"):
        raise RuntimeError(f"3D 서재가 받지 않음: HTTP {res.status_code} {str(data.get('error') or '')[:120]}")
    return data


def check_and_deliver() -> str:
    """[스레드, 포링푸드 잠금 안에서] 새로 들어온 게 있으면 배달. 반환: 로그 한 줄"""
    try:
        data = runtime.run_on_bot_loop(_fetch_async(), timeout=FETCH_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return f"확인 실패: {_root_cause(e)!r:.160}"
    state = _load_state()
    first = not state.get("checked")
    items, new_state = new_items(data, state)
    if first:
        _save_state(new_state)
        return "첫 확인 - 기준점만 저장"
    if not items:
        _save_state(new_state)
        return ""
    courier = pick_courier()
    note = _courier_line(courier, items)
    send_event({"type": "delivery", "from": courier["name"] if courier else "", "items": items, "note": note})
    _save_state(new_state)   # 보낸 뒤에 기준점을 옮긴다 (3D 서재가 꺼져 있었으면 다음에 다시)
    if courier:
        agents.deliver(courier["name"], f"[3D 서재 배달] 서점 건물에 {len(items)}건 배달함 ({', '.join(i['title'] for i in items[:3])})",
                       kind="일", quiet=True)
        memory.remember(courier["name"], f"BookOasis 3D 서재에 새로 들어온 {items[0]['title']} 등 {len(items)}건을 배달했다",
                        importance=3, source="lib3d")
    return f"{courier['name'] if courier else '배달원'} - {len(items)}건 배달"


def say_in_library(agent_id: str, text: str, place: str = "") -> None:
    """거리 쪽에서 3D 서재의 그 직원(예: boss)에게 말을 시킨다 (지금은 관리자 명령 등에서 쓸 수 있게 열어 둠)"""
    send_event({"type": "say", "agent": agent_id, "text": text[:160], "place": place})


def status() -> dict:
    st = _load_state()
    return {"call": call_enabled(), "call_agent": CALL_AGENT, "delivery": delivery_enabled(), "url": LIB3D_URL,
            "checked": st.get("checked"), "every_sec": DELIVERY_SEC, "at": datetime.now().isoformat(timespec="seconds")}
