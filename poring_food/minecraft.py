"""숲 너머 개척지 (마인크래프트 마을) - 인물(LLM 에이전트)들이 실제 마인크래프트(Java) 서버에 봇으로 들어가 나무를 베고
돌을 캐고, 건물을 지어 개척지의 문명을 한 시대씩 발전시킨다. 서버와 봇은 scripts/minecraft/docker-compose.yml.

세계 모드 (PORING_WORLD_MODE, 기본 minecraft):
- minecraft: 오아시스 마을 매시 회차·실시간 대화·상영/도서관/거리 이야기를 멈추고, 이야기는 이 개척지 중심으로 돈다.
  슈나의 💠 결계 조절 알림(서버 부하 단계)은 그대로 - 결계 사정이 개척지 일에 영향을 준다(높으면 모두 철수).
- oasis: 예전처럼 오아시스 마을 (개척지는 그 옆 이야기).

문명 (ERAS): 개척 캠프 → 통나무 마을 → 돌담 마을 → 성채 도시 → 왕도. 시대마다 지어야 할 건물이 있고, 다 지으면 다음 시대.
건물값은 통나무(logs)와 돌(stone) - 나무 베기(gather)와 돌 캐기(mine)로 마을 창고에 모은다.

- 봇(cogs/poring_food.py minecraft_tick)이 TICK_MIN분마다 tick():
  1) 지난 일 결과(이벤트) → 마을 장부 (창고, 지은 건물, 잡은 몬스터, 쓰러짐) - 시대가 오르면 개척지 회의
  2) 결계 경보 2단계 이상이거나 영상 재생이 버벅이면 모두 철수 (서버는 비면 스스로 멈춤 - 시청·독서가 먼저)
  3) 깨어 있는 인물 MAX_BOTS명 접속 (이미 있던 사람 먼저, 밤이면 싸움꾼 먼저)
  4) 할 일이 없는 사람들의 일을 LLM 한 번으로 (이번 시대 목표, 창고, 서버 사정을 보고) - 규칙으로 바로잡는다:
     못 짓는 건물은 모자란 자원 모으기로, 밤엔 가장 센 사람이 순찰
  5) 채널(⛏️ 개척지)에 그 인물 이름으로 한마디 + 끝난 일 소식, 기억/연대기
- 매시(minecraft 모드의 회차): hourly() - 최근 개척지에서 일한 사람들이 시대 목표·창고·서버 사정을 두고 짧게 회의
  (한 줄에 LLM 한 번, 채널 + 게임 안에 있는 사람은 게임 채팅으로도)
- 몬스터 불시 습격(raids surge - 재생 중 부하 급등)은 개척지 둘레에 실제 몬스터가 나타나는 것으로 (surge_raid)
- 마인크래프트 이름은 영문 (MC_NAMES, 없으면 V+해시)
- 켜기: .env PORING_MINECRAFT=1 (서버를 띄운 뒤). API 주소 PORING_MC_API (기본 http://127.0.0.1:3010).
- 시험: 디스코드 /포링푸드마크 지금:True (봇 안에서 돌아야 채널에 올라간다 - 터미널에서 직접 부르면 로그에만 찍힌다)
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
WORLD_MODE = os.getenv("PORING_WORLD_MODE", "minecraft").strip().lower()
API = os.getenv("PORING_MC_API", "http://127.0.0.1:3010").rstrip("/")
MAX_BOTS = int(os.getenv("PORING_MC_MAX_BOTS", "3"))
TICK_MIN = 10
STATE_PATH = os.path.join(STATE_DIR, "minecraft.json")
NAME = "⛏️ 개척지"
MAX_STAGE = 1          # 이 단계를 넘으면 모두 나간다 (2단계부터)
TALK_LINES = 5         # 매시 회의 발언 수

# 건물: 이름, 값 {자원: 개수}
BLUEPRINTS = {
    "hut": ("오두막", {"logs": 20}),
    "farm": ("밭", {"logs": 8}),
    "well": ("우물", {"stone": 10}),
    "wall": ("성벽 한 칸", {"stone": 12}),
    "tower": ("감시탑", {"stone": 24, "logs": 6}),
    "smithy": ("대장간", {"logs": 16, "stone": 16}),
    "hall": ("마을회관", {"logs": 40, "stone": 40}),
}
RESOURCES = {"logs": "통나무", "stone": "돌"}
TASKS = {"gather": "나무 베기", "mine": "돌 캐기", "build": "짓기", "patrol": "몬스터 순찰", "rest": "광장에서 쉬기"}
# 시대: 이름, 다음 시대로 가려면 지어야 할 것(누적 개수)
ERAS = [
    ("개척 캠프", {"hut": 2, "farm": 1}),
    ("통나무 마을", {"hut": 4, "farm": 2, "well": 1}),
    ("돌담 마을", {"wall": 4, "tower": 1, "smithy": 1}),
    ("성채 도시", {"tower": 3, "wall": 8, "hall": 1}),
    ("왕도", {}),
]

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


def mc_world() -> bool:
    """이야기가 개척지 중심인지 (오아시스 마을 회차/대화를 멈춘다)"""
    return WORLD_MODE == "minecraft"


def mc_name(name: str) -> str:
    return MC_NAMES.get(name) or "V" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


def _load() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
            st = data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        st = {}
    res = st.setdefault("res", {})
    if "stock" in st:                       # 예전 장부(통나무만) -> 자원별
        res["logs"] = res.get("logs", 0) + int(st.pop("stock") or 0)
    return st


def _save(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


# ===================================================================== 문명
def era(st: dict) -> int:
    return min(int(st.get("era", 0)), len(ERAS) - 1)


def era_goal(st: dict) -> dict[str, tuple[int, int]]:
    """이번 시대 목표 {건물: (지은 수, 필요 수)}"""
    built = st.get("built") or {}
    return {b: (built.get(b, 0), n) for b, n in ERAS[era(st)][1].items()}


def allowed(st: dict) -> list[str]:
    """지금까지 시대에서 지을 수 있는 건물 (지금 시대 목표 포함)"""
    out = []
    for _, goal in ERAS[:era(st) + 1]:
        out += [b for b in goal if b not in out]
    return out or ["hut", "farm"]


def needed(st: dict) -> list[str]:
    """이번 시대에 아직 모자란 건물 (먼저 지을 것)"""
    return [b for b, (have, need) in era_goal(st).items() if have < need]


def can_afford(st: dict, bp: str) -> bool:
    res = st.get("res") or {}
    return all(res.get(k, 0) >= v for k, v in BLUEPRINTS[bp][1].items())


def short_of(st: dict, bp: str) -> str:
    """그 건물에 가장 모자란 자원 (logs/stone)"""
    res = st.get("res") or {}
    gaps = {k: v - res.get(k, 0) for k, v in BLUEPRINTS[bp][1].items()}
    return max(gaps, key=gaps.get)


def advance(st: dict) -> str | None:
    """이번 시대 목표를 다 채웠으면 다음 시대로. 반환: 새 시대 이름"""
    if era(st) < len(ERAS) - 1 and not needed(st):
        st["era"] = era(st) + 1
        return ERAS[st["era"]][0]
    return None


def ledger(st: dict) -> str:
    res = st.get("res") or {}
    goal = ", ".join(f"{BLUEPRINTS[b][0]} {h}/{n}" for b, (h, n) in era_goal(st).items()) or "없음 (마지막 시대)"
    built = ", ".join(f"{BLUEPRINTS[b][0]} {n}" for b, n in (st.get("built") or {}).items()) or "아직 없음"
    return (f"시대: {ERAS[era(st)][0]} (다음 시대까지: {goal}) / 창고: "
            + ", ".join(f"{RESOURCES[k]} {res.get(k, 0)}" for k in RESOURCES)
            + f" / 지은 것: {built} / 잡은 몬스터 {st.get('kills', 0)}")


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
        r = requests.post(self.base + path, json=body, timeout=75 if path == "/task" else 15)
        if r.status_code >= 500:
            r.raise_for_status()
        return r.json() if r.content else {}


# ===================================================================== 서버 상황 (이야기에 섞는다)
def server_line() -> str:
    """지금 실제 서버 사정을 마을 말로 한 줄 - 결계 단계, 환영극(영상) 손님, 최근 몬스터 습격"""
    from . import loadshed, raids
    try:
        stage = loadshed.stage()
        p = (loadshed._load().get("hosts") or {}).get("plex01") or {}
        bits = [f"결계 경보 {stage}단계({loadshed.STAGE_NAMES.get(stage, '')})"]
        if p.get("playing"):
            bits.append(f"환영극장에 손님 {p['playing']}명이 상영 중" + (" - 화면이 버벅임" if p.get("struggling") else ""))
        r = raids.last()
        if r and r.get("raid"):
            bits.append(f"최근 몬스터 습격({r['at'][11:16]}): {r['monster']} {r['outcome']}")
        return " / ".join(bits)
    except Exception:  # noqa: BLE001
        return ""


def _remember_line(st: dict, name: str, line: str, now: datetime) -> None:
    rec = st.setdefault("recent", {})
    rec[name] = (rec.get(name) or [])[-4:] + [f"{now:%m/%d %H:%M} {line}"]
    st["log"] = (st.get("log") or [])[-19:] + [f"{now:%H:%M} {name}: {line}"]


def prompt_block(name: str, now: datetime | None = None) -> str:
    """인물 판단 프롬프트용 (oasis 모드) - 개척지 사정 + 내가 거기서 한 일 + 서버 사정과 엮어 말하라는 안내"""
    if not ENABLED:
        return ""
    st = _load()
    if not st.get("built") and not (st.get("res") or {}) and not st.get("recent"):
        return ""
    online = [k for k, v in MC_NAMES.items() if v in (st.get("online") or [])]
    mine = (st.get("recent") or {}).get(name) or []
    lines = [f"[숲 너머 개척지 (마인크래프트 마을)] {ledger(st)} · 지금 나가 있는 사람: {', '.join(online) or '없음'}"
             + (f" · 개척 쉬는 중({st['why'][6:]})" if str(st.get('why', '')).startswith("쉬는 중") else "")]
    if mine:
        lines.append("내가 개척지에서 한 일: " + " / ".join(mine[-3:]))
    lines.append("개척지 일은 결계가 높거나 환영극 손님이 버벅이면 멈춘다 - 서버 사정(결계, 환영극 손님, 몬스터 습격)과 "
                 "개척지 일(나무, 돌, 건물, 밤 순찰, 다음 시대)을 엮어서 얘기해도 된다.")
    return "\n".join(lines)


# ===================================================================== 개척지 회의 (대화)
TALK_SYSTEM_TAIL = """
[지금 너는 숲 너머 개척지(마인크래프트 세계)에서 동료들과 개척지 문명을 키우고 있다.]
개척지 일(나무 베기, 돌 캐기, 건물 짓기, 밤 순찰, 다음 시대로 가기)과 마을 서버 사정(결계 경보 - 높으면 다 같이 철수해야 한다,
환영극장 손님 - 손님이 있으면 조용히 일해야 한다, 몬스터 습격)을 엮어서 말한다. 숫자는 주어진 것만 쓴다."""


def talk(topic: str, people: list[str], st: dict | None = None, now: datetime | None = None, speak=None,
         post=None, api: Api | None = None, lines: int = TALK_LINES) -> list[dict]:
    """개척지 회의 - people이 돌아가며 한 줄씩 (각자 LLM). 채널 + 게임 안에 있으면 게임 채팅으로도. 반환 [{"speaker","line"}]"""
    from . import agents as ag, memory
    now = now or now_kst()
    st = st if st is not None else _load()
    agents = {a["name"]: a for a in ag.load_agents()}
    people = [p for p in dict.fromkeys(people) if p in agents][:4]
    if len(people) < 2:
        return []
    facts = (f"[개척지 장부] {ledger(st)}\n[최근 개척지 소식] " + (" / ".join((st.get("log") or [])[-6:]) or "없음")
             + (f"\n[마을 서버 사정] {s}" if (s := server_line()) else ""))
    if post is None:
        def post(n, t):
            ag._post(agents[n], t)
    online = set(st.get("online") or [])
    out: list[dict] = []
    for i in range(lines):
        who = people[i % len(people)]
        a = agents[who]
        talk_so_far = "\n".join(f"{x['speaker']}: {x['line']}" for x in out) or "(아직 아무도 말하지 않음)"
        user = (f"[개척지 회의 주제] {topic}\n{facts}\n[같이 있는 사람] {', '.join(p for p in people if p != who)}\n"
                f"[지금까지 오간 말]\n{talk_so_far}\n\n{who}로서 다음에 할 말 한두 문장. 다음에 뭘 지을지, 누가 뭘 할지, "
                "자원이 뭐가 모자란지, 서버 사정 때문에 어떡할지 - 내 성격대로 제안하거나 반대하거나 딴지 걸어도 된다.\n"
                + ag._voice(a) + '\nJSON으로만: {"say": "할 말"}')
        if speak is not None:
            say = speak(who, user)
        else:
            from .llm import llm_json
            say = str((llm_json(ag._system(a) + TALK_SYSTEM_TAIL, user, temperature=0.9, timeout=45,
                                tag=f"개척지 {who}", agent=who) or {}).get("say") or "").strip()
        say = str(say or "").strip()[:280]
        if not say:
            continue
        out.append({"speaker": who, "line": say})
        post(who, f"-# {NAME} · {now:%H:%M} · {topic[:30]}\n{say}")
        if api is not None and mc_name(who) in online:
            try:
                api.post("/say", {"name": mc_name(who), "text": say[:180]})
            except Exception:  # noqa: BLE001
                pass
    if out:
        summary = f"개척지 회의 '{topic}': " + " / ".join(f"{x['speaker']}: {x['line'][:40]}" for x in out[-3:])
        for p in people:
            try:
                memory.remember(p, summary, 4, [q for q in people if q != p], "개척지")
            except Exception as e:  # noqa: BLE001
                print(f"[개척지] 기억 실패: {e}")
    return out


def _react(detail: str, title: str, people: list[str], react=None, st: dict | None = None) -> None:
    """사건에 관련 인물이 바로 반응 - 개척지 모드면 개척지 회의 한 판, oasis 모드면 마을 사건(react_now)"""
    try:
        if react is not None:
            react(detail, title, people, kind="개척지")
        elif mc_world():
            talk(f"{title} - {detail}", people, st=st, lines=3)
        else:
            from . import agents as ag
            ag.react_now(detail, title, people, kind="개척지")
    except Exception as e:  # noqa: BLE001
        print(f"[개척지] 반응 실패: {e}")


def hourly(now: datetime | None = None, speak=None, post=None) -> list[dict]:
    """[minecraft 모드의 매시 회차] 최근 개척지에서 일한 사람들(+ 깨어 있는 사람)이 이번 시대 목표를 두고 회의"""
    if not ENABLED:
        return []
    from . import agents as ag
    now = now or now_kst()
    st = _load()
    agents = ag.load_agents()
    awake = [a["name"] for a in agents if not ag._asleep(a, now.hour)]
    recent = [n for n in (st.get("recent") or {}) if n in awake]
    rnd = random.Random(f"{now:%Y%m%d%H}")
    rnd.shuffle(awake)
    people = list(dict.fromkeys(recent[-2:] + awake))[:3]
    need = needed(st)
    topic = (f"{ERAS[era(st)][0]} - 다음 시대로 가려면 " + ", ".join(BLUEPRINTS[b][0] for b in need) if need
             else f"{ERAS[era(st)][0]} - 다음엔 뭘 할까")
    try:
        api = Api() if st.get("online") else None
    except Exception:  # noqa: BLE001
        api = None
    out = talk(topic, people, st=st, now=now, speak=speak, post=post, api=api)
    print(f"[개척지] 매시 회의 {len(out)}마디 ({', '.join(people)})")
    return out


# ===================================================================== 판단 재료
def busy() -> str | None:
    """지금 개척지 일을 쉬어야 하는 이유 (시청·독서가 먼저)"""
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
    res = st.setdefault("res", {})
    for e in events:
        who = by_mc.get(e.get("name", ""), e.get("name", ""))
        kind = e.get("kind")
        r = e.get("result") or {}
        if kind == "done" and e.get("task") in ("gather", "mine"):
            key = "logs" if e.get("task") == "gather" else "stone"
            n = int(r.get(key) or 0)
            res[key] = res.get(key, 0) + n
            if n:
                out.append((who, f"{RESOURCES[key]} {n}개를 마을 창고에 쌓았다 (창고 {RESOURCES[key]} {res[key]}개)"))
        elif kind == "done" and e.get("task") == "build":
            bp = r.get("blueprint") or (pend.get(e.get("name")) or {}).get("blueprint")
            if bp in BLUEPRINTS:
                built[bp] = built.get(bp, 0) + 1
                out.append((who, f"개척지에 {BLUEPRINTS[bp][0]}을(를) 새로 지었다 (모두 {built[bp]}채)"))
        elif kind == "failed" and e.get("task") == "build":
            for k, v in ((pend.get(e.get("name")) or {}).get("cost") or {}).items():   # 못 지었으면 자원을 되돌린다
                res[k] = res.get(k, 0) + int(v)
        elif kind == "done" and e.get("task") == "patrol":
            k = int(r.get("kills") or 0)
            st["kills"] = st.get("kills", 0) + k
            if k:
                out.append((who, f"개척지 둘레를 돌며 몬스터 {k}마리를 쓰러뜨렸다 (모두 {st['kills']}마리)"))
        elif kind == "died":
            st["deaths"] = st.get("deaths", 0) + 1
            out.append((who, "몬스터에게 당해 쓰러졌다가 광장에서 다시 일어났다"))
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


DECIDE_SYSTEM = """너는 숲 너머 개척지(마인크래프트 세계)에서 문명을 키우는 사람들의 하루를 정한다.
사람마다 성격대로 할 일을 하나씩 고른다: gather(숲에서 나무 베기 - 통나무) / mine(돌 캐기 - 돌) / build(건물 짓기 - blueprint) /
patrol(개척지 둘레를 돌며 몬스터 잡기) / rest(광장에서 쉬기). 이번 시대 목표 건물을 먼저 짓고, 창고에 모자란 자원을 모은다.
밤에는 몬스터가 나오니 싸울 줄 아는 사람은 순찰을 한다.
say는 그 사람이 게임 채팅에 칠 짧은 한마디(그 사람 말투, 20자 안팎, 실제 서버/사람 이름 금지). 마을 서버 사정
(결계 경보, 환영극 손님, 몬스터 습격)이 있으면 한마디에 자연스럽게 엮어도 된다 - 예: 극장 손님 많으니 조용히 베자.
JSON으로만: {"tasks": [{"name": "이름", "task": "gather|mine|build|patrol|rest", "blueprint": "건물 키 또는 빈칸", "say": "한마디"}]}"""


def decide(people: list[dict], st: dict, day: bool | None, llm=None) -> list[dict]:
    """할 일 정하기 (LLM 한 번) + 규칙으로 바로잡기 - 자원은 예약한 만큼 st['res']에서 미리 뺀 것처럼 계산"""
    from . import raids
    if not people:
        return []
    ok = allowed(st)
    facts = ((f"[마을 서버 사정] {server}\n" if (server := server_line()) else "")
             + f"지금: {'낮' if day else '밤' if day is False else '모름'}\n[개척지 장부] {ledger(st)}\n"
             + "지을 수 있는 건물(키: 이름 값): " + ", ".join(
                 f"{b}: {BLUEPRINTS[b][0]} " + "+".join(f"{RESOURCES[k]}{v}" for k, v in BLUEPRINTS[b][1].items()) for b in ok)
             + "\n사람들:\n" + "\n".join(f"- {p['name']}: {str(p.get('profile', ''))[:90]}" for p in people))
    if llm is None:
        from .llm import llm_json
        llm = lambda s, u: llm_json(s, u, temperature=0.8, timeout=60, tag="개척지")  # noqa: E731
    out = (llm(DECIDE_SYSTEM, facts) or {}).get("tasks") or []
    by = {str(t.get("name")): t for t in out if isinstance(t, dict)}
    sim = {"res": dict(st.get("res") or {}), "built": dict(st.get("built") or {}), "era": st.get("era", 0)}
    tasks = []
    for p in people:
        t = by.get(p["name"]) or {}
        task = t.get("task") if t.get("task") in TASKS else "gather"
        bp = t.get("blueprint") if t.get("blueprint") in ok else ""
        if task == "build":
            bp = bp or (needed(sim) or ok)[0]
            if not can_afford(sim, bp):
                task, bp = ("gather" if short_of(sim, bp) == "logs" else "mine"), ""
            else:
                for k, v in BLUEPRINTS[bp][1].items():
                    sim["res"][k] -= v
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
        print(f"[개척지] {why or '다시 움직임'}")
        st["why"] = why
        _save(st)


def tick(now: datetime | None = None, api: Api | None = None, llm=None, post=None, agents: list[dict] | None = None,
         force: bool = False, react=None) -> dict:
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
    builders = []
    for who, line in apply_events(st, evs, by_mc):
        post(who, f"-# {NAME} · {now:%H:%M}\n{line}")
        _remember_line(st, who, line, now)
        try:
            memory.remember(who, f"숲 너머 개척지에서 {line}", 4, [], "개척지")
            if "지었다" in line:
                chronicle.record_event("개척지", f"{who}: {line}", line, [who])
                builders.append(who)
        except Exception as e:  # noqa: BLE001
            print(f"[개척지] 기록 실패: {e}")
    new_era = advance(st)
    if new_era:
        _save(st)
        post(NAME, f"-# {NAME} · {now:%H:%M}\n🎉 **개척지가 '{new_era}' 시대로 올라섰다!** 다음 목표: "
             + (", ".join(f"{BLUEPRINTS[b][0]} {n}" for b, n in ERAS[era(st)][1].items()) or "없음"))
        try:
            chronicle.record_event("개척지", f"새 시대: {new_era}", ledger(st), builders)
        except Exception as e:  # noqa: BLE001
            print(f"[개척지] 기록 실패: {e}")
        _react(f"개척지가 '{new_era}' 시대로 올라섰다", "새 시대", (builders + list(st.get("recent") or {}))[-3:], react, st)
    elif builders:
        _react(f"{builders[-1]}이(가) 개척지에 새 건물을 세웠다", "새 건물", [builders[-1], "리그루드"], react, st)
    online = list(health.get("bots") or [])
    why = None if force else busy()      # force: 관리자 시험 (/포링푸드마크 지금)
    _note(st, f"쉬는 중 - {why}" if why else "")
    if why:
        if online:
            api.post("/leave", {"name": "*"})
            post(NAME, f"-# {NAME} · {now:%H:%M}\n{why} - 개척지에 나가 있던 사람들이 일을 멈추고 마을로 돌아왔다")
            back = [by_mc.get(n, n) for n in online]
            from . import stewards
            _react(f"{why} - 개척지에서 일하던 {', '.join(back)}이(가) 일을 멈추고 돌아왔다", "개척지 철수",
                   [stewards.steward("loadshed")] + back[:2], react, st)
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
    res = st.setdefault("res", {})
    for t in plan:
        mc = mc_name(t["name"])
        args = {}
        if t["task"] in ("gather", "mine"):
            args = {"count": 12}
        elif t["task"] == "build":
            cost = BLUEPRINTS[t["blueprint"]][1]
            for k, v in cost.items():
                res[k] = res.get(k, 0) - v
            args = {"blueprint": t["blueprint"], "slot": slots.get(t["blueprint"], 0)}
            slots[t["blueprint"]] = slots.get(t["blueprint"], 0) + 1
            pend[mc] = {"task": "build", "blueprint": t["blueprint"], "cost": cost}
        elif t["task"] == "patrol":
            args = {"seconds": TICK_MIN * 60 - 30}
        api.post("/task", {"name": mc, "kind": t["task"], "args": args, "say": t["say"]})
        what = TASKS[t["task"]] + (f" ({BLUEPRINTS[t['blueprint']][0]})" if t["blueprint"] else "")
        post(t["name"], f"-# {NAME} · {now:%H:%M} · {what}\n{t['say'] or '…'}")
        _remember_line(st, t["name"], f"{what} 하러 감", now)
    st["online"] = sorted(want)
    _save(st)
    print(f"[개척지] 접속 {', '.join(sorted(want)) or '없음'} · 일 {[(t['name'], t['task']) for t in plan]}")
    return {"assigned": plan}


def surge_raid(surge: dict, api: Api | None = None, post=None) -> dict | None:
    """[minecraft 모드] 몬스터 불시 습격 = 개척지 둘레에 실제 몬스터. 개척지에 아무도 없으면 None (raids 이야기로)"""
    if not ENABLED:
        return None
    from . import agents as ag
    api = api or Api()
    st = _load()
    if not st.get("online"):
        return None
    n = max(3, min(10, int(surge.get("rise", 20) // 8) + 2 * int(surge.get("playing") or 1)))
    mob = "husk" if surge.get("peak", 0) >= 90 else "zombie"
    try:
        api.post("/raid", {"count": n, "mob": mob})
    except Exception as e:  # noqa: BLE001
        print(f"[개척지] 습격 소환 실패: {e}")
        return None
    text = (f"-# {NAME} · {now_kst():%H:%M} · ⚔️ 불시 습격\n환영극장에 손님이 몰려 결계 부하가 {surge.get('base', 0):g}%→"
            f"{surge.get('peak', 0):g}%로 치솟자, 얇아진 결계를 뚫고 개척지 둘레에 몬스터 {n}마리가 나타났다!")
    (post or (lambda t: ag._post_as(NAME, t)))(text)
    st.setdefault("raids", 0)
    st["raids"] += 1
    _remember_line(st, "개척지", f"몬스터 {n}마리 습격", now_kst())
    _save(st)
    return {"count": n, "mob": mob}


def summary() -> str:
    st = _load()
    return (f"{NAME}: {ledger(st)} · 쓰러짐 {st.get('deaths', 0)} · "
            f"지금 접속 {', '.join(st.get('online') or []) or '없음'}"
            + (f" · {st['why']}" if st.get("why") else ""))
