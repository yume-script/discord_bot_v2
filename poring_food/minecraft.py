"""마인크래프트 마을 - 인물(LLM 에이전트)들이 실제 마인크래프트(Java) 서버에 봇으로 들어가 나무를 모으고, 마을을 짓고,
밤에는 몬스터를 잡는다. 서버와 봇은 scripts/minecraft/docker-compose.yml (mc-village + mc-agents).

- 봇(cogs/poring_food.py minecraft_tick)이 TICK_MIN분마다 tick():
  1) 지난 일 결과(이벤트)를 마을 장부에 반영 - 모은 통나무는 마을 창고로, 지은 건물/잡은 몬스터/쓰러짐 수
  2) 결계 경보 2단계 이상이거나 영상 재생이 버벅이면 모두 접속을 끊는다 (서버는 비면 스스로 멈춤 - 시청·독서가 먼저)
  3) 깨어 있는 인물 중 MAX_BOTS명을 고른다 (이미 들어가 있던 사람 먼저, 밤이면 싸움꾼 먼저)
  4) 할 일이 없는 사람들에게 LLM 한 번으로 일을 정한다 (gather / build 건물 / patrol / rest + 게임 안에서 할 말)
     - 짓기는 마을 창고 통나무가 모자라면 나무 모으기로 바꾼다, 밤엔 싸움꾼 한 명은 꼭 순찰
  5) 채널(⛏️ 마인크래프트 마을)에 그 인물 이름으로 한마디 + 끝난 일 소식, 기억/연대기에 남긴다
- 마인크래프트 이름은 영문이어야 해서 MC_NAMES (없으면 V+해시)
- 켜기: .env PORING_MINECRAFT=1 (서버를 띄운 뒤). API 주소 PORING_MC_API (기본 http://127.0.0.1:3010).
저장: storage/poring_food/minecraft.json
"""
from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import datetime

from ._log import pf_print as print  # print()를 봇 로그로
from .clock import now_kst
from .config import STATE_DIR

ENABLED = os.getenv("PORING_MINECRAFT", "0") not in ("0", "false", "False", "")
API = os.getenv("PORING_MC_API", "http://127.0.0.1:3010").rstrip("/")
MAX_BOTS = int(os.getenv("PORING_MC_MAX_BOTS", "3"))
TICK_MIN = 10
STATE_PATH = os.path.join(STATE_DIR, "minecraft.json")
NAME = "⛏️ 마인크래프트 마을"
MAX_STAGE = 1          # 이 단계를 넘으면 모두 나간다 (2단계부터)

# 건물: (통나무 값, 이름)
BLUEPRINTS = {"hut": (20, "오두막"), "farm": (8, "밭"), "tower": (24, "감시탑"), "wall": (12, "성벽 한 칸")}
TASKS = {"gather": "나무 모으기", "build": "짓기", "patrol": "몬스터 순찰", "rest": "광장에서 쉬기"}

MC_NAMES = {
    "애순이": "Aesun", "소라": "Sora", "위벨": "Ubel", "슈타르크": "Stark", "페른": "Fern", "일레이나": "Elaina",
    "덴켄": "Denken", "크리스": "Chris", "메구밍": "Megumin", "베니마루": "Benimaru", "바닐": "Vanir", "칸네": "Kanne",
    "세나": "Sena", "라오프": "Laufen", "프랑": "Fran", "고브타": "Gobta", "소우에이": "Souei", "슈나": "Shuna",
    "리리나": "Ririna", "카즈마": "Kazuma", "아쿠아": "Aqua", "프리렌": "Frieren", "팔린": "Falin", "리그루드": "Rigurd",
    "다크니스": "Darkness", "가비루": "Gabiru", "자인": "Sein", "하쿠로": "Hakuro", "센시": "Senshi", "라이오스": "Laios",
    "카이진": "Kaijin", "나마리": "Namari", "이즈츠미": "Izutsumi", "게르도": "Geld", "알다프": "Aldaf",
    "쿠로베": "Kurobe", "루나": "Luna", "마르실": "Marcille", "칠착": "Chilchuck", "라비네": "Lawine",
    "아이젠": "Eisen", "카브루": "Kabru", "김선영": "SeonYoung", "강단이": "Dani",
}


def mc_name(name: str) -> str:
    return MC_NAMES.get(name) or "V" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


# ===================================================================== HTTP (mc-agents)
class Api:
    def __init__(self, base: str = API):
        self.base = base

    def get(self, path: str) -> dict:
        import requests
        r = requests.get(self.base + path, timeout=5)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        import requests
        r = requests.post(self.base + path, json=body, timeout=75 if path == "/task" else 5)
        if r.status_code >= 500:
            r.raise_for_status()
        return r.json() if r.content else {}


# ===================================================================== 판단 재료
def busy() -> str | None:
    """지금 마인크래프트를 쉬어야 하는 이유 (시청·독서가 먼저)"""
    from . import loadshed
    if loadshed.stage() > MAX_STAGE:
        return f"결계 경보 {loadshed.stage()}단계"
    st = (loadshed._load().get("hosts") or {}).get("plex01") or {}
    if st.get("playing") and st.get("struggling"):
        return "환영극(영상)이 버벅이는 중"
    return None


def apply_events(st: dict, events: list[dict], by_mc: dict[str, str]) -> list[tuple[str, str]]:
    """이벤트 -> 장부. 반환 [(인물, 소식 한 줄)] (알릴 만한 것만)"""
    out = []
    pend = st.setdefault("pending", {})
    built = st.setdefault("built", {})
    for e in events:
        who = by_mc.get(e.get("name", ""), e.get("name", ""))
        kind = e.get("kind")
        res = e.get("result") or {}
        if kind == "done" and e.get("task") == "gather":
            n = int(res.get("logs") or 0)
            st["stock"] = st.get("stock", 0) + n
            if n:
                out.append((who, f"통나무 {n}개를 마을 창고에 쌓았다 (창고 {st['stock']}개)"))
        elif kind == "done" and e.get("task") == "build":
            bp = res.get("blueprint") or (pend.get(e.get("name")) or {}).get("blueprint")
            if bp in BLUEPRINTS:
                built[bp] = built.get(bp, 0) + 1
                out.append((who, f"마을에 {BLUEPRINTS[bp][1]}을(를) 새로 지었다 (모두 {built[bp]}채)"))
        elif kind == "failed" and e.get("task") == "build":
            p = pend.get(e.get("name")) or {}
            st["stock"] = st.get("stock", 0) + int(p.get("cost") or 0)     # 못 지었으면 통나무를 되돌린다
        elif kind == "done" and e.get("task") == "patrol":
            k = int(res.get("kills") or 0)
            st["kills"] = st.get("kills", 0) + k
            if k:
                out.append((who, f"마을 둘레를 돌며 몬스터 {k}마리를 쓰러뜨렸다 (모두 {st['kills']}마리)"))
        elif kind == "died":
            st["deaths"] = st.get("deaths", 0) + 1
            out.append((who, "몬스터에게 당해 쓰러졌다가 마을 광장에서 다시 일어났다"))
        if kind in ("done", "failed", "left"):
            pend.pop(e.get("name"), None)
        st["last_event"] = max(st.get("last_event", 0), int(e.get("id") or 0))
    return out


def pick(agents: list[dict], online: list[str], day: bool | None, k: int = MAX_BOTS, rnd=None) -> list[dict]:
    """접속할 사람 (깨어 있는 사람 중 - 이미 들어가 있던 사람 먼저, 밤이면 싸움꾼 먼저)"""
    from . import raids
    from .agents import _asleep
    rnd = rnd or random.Random()
    hour = now_kst().hour
    awake = [a for a in agents if not _asleep(a, hour)]
    rnd.shuffle(awake)

    def rank(a):
        fighter = a["name"] in raids.DEFENDERS
        return (0 if mc_name(a["name"]) in online else 1, 0 if (fighter and day is False) else 1)
    return sorted(awake, key=rank)[:k]


DECIDE_SYSTEM = """너는 마법 판타지 마을 '오아시스 마을' 사람들이 실제로 들어가 있는 마인크래프트 세계의 하루를 정한다.
사람마다 성격대로 할 일을 하나씩 고른다: gather(숲에서 나무 모으기) / build(마을에 건물 짓기 - hut 오두막, farm 밭,
tower 감시탑, wall 성벽 한 칸) / patrol(마을 둘레를 돌며 몬스터 잡기) / rest(광장에서 쉬기).
밤에는 몬스터가 나오니 싸울 줄 아는 사람은 순찰을, 낮에는 나무를 모으고 짓는다. 창고 통나무가 모자란 건물은 고르지 않는다.
say는 그 사람이 게임 채팅에 칠 짧은 한마디(그 사람 말투, 20자 안팎, 실제 서버/사람 이름 금지).
JSON으로만: {"tasks": [{"name": "이름", "task": "gather|build|patrol|rest", "blueprint": "hut|farm|tower|wall 또는 빈칸", "say": "한마디"}]}"""


def decide(people: list[dict], st: dict, day: bool | None, llm=None) -> list[dict]:
    """할 일 정하기 (LLM 한 번) + 규칙으로 바로잡기"""
    from . import raids
    if not people:
        return []
    stock = st.get("stock", 0)
    facts = (f"지금: {'낮' if day else '밤' if day is False else '모름'} / 마을 창고 통나무 {stock}개 / "
             f"지은 것: " + (", ".join(f"{BLUEPRINTS[b][1]} {n}" for b, n in (st.get('built') or {}).items()) or "아직 없음")
             + " / 건물 값(통나무): " + ", ".join(f"{v[1]} {v[0]}" for v in BLUEPRINTS.values())
             + "\n사람들:\n" + "\n".join(f"- {p['name']}: {str(p.get('profile', ''))[:90]}" for p in people))
    if llm is None:
        from .llm import llm_json
        llm = lambda s, u: llm_json(s, u, temperature=0.8, timeout=60, tag="마인크래프트")  # noqa: E731
    out = (llm(DECIDE_SYSTEM, facts) or {}).get("tasks") or []
    by = {str(t.get("name")): t for t in out if isinstance(t, dict)}
    tasks = []
    for p in people:
        t = by.get(p["name"]) or {}
        task = t.get("task") if t.get("task") in TASKS else "gather"
        bp = t.get("blueprint") if t.get("blueprint") in BLUEPRINTS else ""
        if task == "build":
            bp = bp or min(BLUEPRINTS, key=lambda b: (st.get("built") or {}).get(b, 0))
            if BLUEPRINTS[bp][0] > stock:
                task, bp = "gather", ""
            else:
                stock -= BLUEPRINTS[bp][0]
        tasks.append({"name": p["name"], "task": task, "blueprint": bp if task == "build" else "",
                      "say": str(t.get("say") or "").strip()[:60]})
    if day is False and not any(t["task"] == "patrol" for t in tasks):
        fighter = max(tasks, key=lambda t: raids.DEFENDERS.get(t["name"], 0))   # 가장 센 사람이 순찰
        fighter.update(task="patrol", blueprint="")
    return tasks


# ===================================================================== 한 바퀴
def _note(st: dict, why: str) -> None:
    """쉬거나 못 붙는 이유가 바뀔 때만 로그 한 줄 (매번 찍지 않게)"""
    if why != st.get("why", ""):
        print(f"[마인크래프트] {why or '다시 움직임'}")
        st["why"] = why
        _save(st)



def tick(now: datetime | None = None, api: Api | None = None, llm=None, post=None, agents: list[dict] | None = None) -> dict:
    """[봇, TICK_MIN분마다, 스레드에서] 반환 {"busy"|"down"|"assigned": ...}"""
    if not ENABLED:
        return {}
    from . import agents as ag, chronicle, memory
    now = now or now_kst()
    api = api or Api()
    st = _load()
    try:
        health = api.get("/health")
    except Exception as e:  # noqa: BLE001
        _note(st, f"봇 서비스(mc-agents)에 연결 못 함: {str(e)[:100]}")
        return {"down": str(e)[:120]}
    agents = agents if agents is not None else ag.load_agents()
    by_name = {a["name"]: a for a in agents}
    by_mc = {mc_name(a["name"]): a["name"] for a in agents}
    if post is None:
        def post(name, text):
            (ag._post(by_name[name], text) if name in by_name else ag._post_as(name, text))
    try:
        evs = api.get(f"/events?after={st.get('last_event', 0)}").get("events") or []
    except Exception:  # noqa: BLE001
        evs = []
    for who, line in apply_events(st, evs, by_mc):
        post(who, f"-# {NAME} · {now:%H:%M}\n{line}")
        try:
            memory.remember(who, f"마인크래프트 마을에서 {line}", 4, [], "마인크래프트")
            if "지었다" in line:
                chronicle.record_event("마인크래프트", f"{who}: {line}", line, [who])
        except Exception as e:  # noqa: BLE001
            print(f"[마인크래프트] 기록 실패: {e}")
    online = list(health.get("bots") or [])
    why = busy()
    _note(st, f"쉬는 중 - {why}" if why else "")
    if why:
        if online:
            api.post("/leave", {"name": "*"})
            post(NAME, f"-# {NAME} · {now:%H:%M}\n{why} - 모두 마을 일을 멈추고 돌아왔다")
        st["online"] = []
        _save(st)
        return {"busy": why}
    try:
        day = api.get("/state").get("day") if online else None
    except Exception:  # noqa: BLE001
        day = None
    chosen = pick(agents, online, day)
    want = {mc_name(a["name"]) for a in chosen}
    for n in online:
        if n not in want:
            api.post("/leave", {"name": n})
    for n in want - set(online):
        api.post("/join", {"name": n})
    try:
        working = {b["name"] for b in (api.get("/state").get("bots") or []) if b.get("task")}
    except Exception:  # noqa: BLE001
        working = set()
    idle = [a for a in chosen if mc_name(a["name"]) not in working]
    plan = decide(idle, st, day, llm)
    slots = st.setdefault("slots", {})
    pend = st.setdefault("pending", {})
    for t in plan:
        mc = mc_name(t["name"])
        args = {}
        if t["task"] == "gather":
            args = {"count": 12}
        elif t["task"] == "build":
            cost = BLUEPRINTS[t["blueprint"]][0]
            st["stock"] = st.get("stock", 0) - cost
            args = {"blueprint": t["blueprint"], "slot": slots.get(t["blueprint"], 0)}
            slots[t["blueprint"]] = slots.get(t["blueprint"], 0) + 1
            pend[mc] = {"task": "build", "blueprint": t["blueprint"], "cost": cost}
        elif t["task"] == "patrol":
            args = {"seconds": TICK_MIN * 60 - 30}
        api.post("/task", {"name": mc, "kind": t["task"], "args": args, "say": t["say"]})
        what = TASKS[t["task"]] + (f" ({BLUEPRINTS[t['blueprint']][1]})" if t["blueprint"] else "")
        post(t["name"], f"-# {NAME} · {now:%H:%M} · {what}\n{t['say'] or '…'}")
    st["online"] = sorted(want)
    _save(st)
    print(f"[마인크래프트] 접속 {', '.join(sorted(want)) or '없음'} · 일 {[(t['name'], t['task']) for t in plan]}")
    return {"assigned": plan}


def summary() -> str:
    st = _load()
    built = ", ".join(f"{BLUEPRINTS[b][1]} {n}" for b, n in (st.get("built") or {}).items()) or "아직 없음"
    return (f"{NAME}: 창고 통나무 {st.get('stock', 0)}개 · 지은 것 {built} · 잡은 몬스터 {st.get('kills', 0)} · "
            f"쓰러짐 {st.get('deaths', 0)} · 지금 접속 {', '.join(st.get('online') or []) or '없음'}")
