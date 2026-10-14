"""
빌런 - 마을에 진짜 악역이 있게. LLM은 사람을 착하게 그리는 버릇이 있어서, 설정상 갑질 부장/얌체/욕심 많은 건물주도
몇 마디 뒤엔 "사실 속은 따뜻한 사람"이 되고 갈등이 그 시간 안에 화해로 끝났다.

agents.json에서 "villain"이 있는 인물 (지금: 장대희, 권민우, 부상길 - 교체되지 않게 "fixed"):
  {"agenda": 숨은 장기 목표, "methods": [쓰는 수법], "targets": [주로 노리는 사람]}

1) 악역 연기 원칙 (system_block): 판단/대화/모임 발언 모두에 붙는다 - 착해지지 말 것, 쉽게 사과/화해하지 말 것,
   겉으로 웃어도 속으로 계산, 목표를 끝까지 밀어붙일 것. (LLM 호출 추가 없음)
2) 수작 (tick): 빌런마다 SCHEME_EVERY_DAYS일에 한 번(SCHEME_HOUR시 이후 첫 회차) 수작을 하나 꾸민다 (LLM 1회).
   - 당한 사람은 사건으로 겪고(받은편지함, 깨움), 같은 가게/동네 사람들은 소문으로 듣는다 (누가 했는지는 숨길 수 있다).
   - 연대기에 "진행 중인 일"로 올라가 모두의 판단에 보인다 - 모임을 열어 맞서거나, 따지러 가는 이야기로 이어진다.
   - 숨긴 수작은 다음 날부터 하루 EXPOSE_P 확률로 들통난다: 당사자/같은 곳 사람들이 알게 되고, 빌런 평판이 떨어진다.
   - 빌런 자신은 판단할 때 [나의 속셈]과 지금까지 꾸민 일/평판을 본다 (prompt_block).
안전선: 드라마 수위 (갑질, 뒷담, 성과 가로채기, 월세 압박, 이간질). 실제 범죄 묘사/폭력/혐오/실존 인물 금지.
저장: storage/poring_food/villains.json {이름: {"last", "reputation", "schemes": [...]}}
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta

from . import chronicle, handover, life, memory, novelty, world, worldcfg
from .clock import now_kst
from .josa import j
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR
from .llm import llm_json

VILLAINS_PATH = os.path.join(STATE_DIR, "villains.json")
ENABLED = os.getenv("PORING_VILLAINS", "1") not in ("0", "false", "False", "")
SCHEME_EVERY_DAYS = int(os.getenv("PORING_VILLAIN_EVERY_DAYS", "2"))
SCHEME_HOUR = 10
EXPOSE_P = 0.3
MAX_SCHEMES_KEPT = 20


def is_villain(agent: dict) -> bool:
    return ENABLED and isinstance(agent.get("villain"), dict) and bool(agent["villain"].get("agenda"))


def system_block(agent: dict) -> str:
    """_system(연기 원칙) 뒤에 붙는 악역 연기 원칙 (빌런이 아니면 "")."""
    if not is_villain(agent):
        return ""
    v = agent["villain"]
    return (
        "\n[악역 연기 원칙 - 너는 이 이야기의 빌런이다]\n"
        f"- 숨은 목표: {v['agenda']}. 이걸 위해 움직인다. 남에게 털어놓지 않는다.\n"
        f"- 쓰는 수법: {', '.join(v.get('methods') or [])}.\n"
        "- 착해지지 마라. 몇 마디 만에 '사실 속은 따뜻한 사람'으로 바뀌거나, 쉽게 사과/반성/화해하지 마라. "
        "궁지에 몰려도 변명하고, 말을 돌리고, 남 탓을 하고, 다음 수를 생각한다.\n"
        "- 겉으로는 웃거나 점잖아도 속으로는 계산한다. 상대를 칭찬해도 이유가 있다. 손해 보는 친절은 하지 않는다.\n"
        "- 다만 드라마 수위까지만 - 갑질, 뒷담, 성과 가로채기, 압박, 이간질. 폭력/범죄/혐오는 하지 않는다."
    )


# ===================================================================== 저장
def _load() -> dict:
    try:
        with open(VILLAINS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(VILLAINS_PATH), exist_ok=True)
    tmp = VILLAINS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, VILLAINS_PATH)


def _rec(data: dict, name: str) -> dict:
    return data.setdefault(name, {"last": "", "reputation": 60, "schemes": []})


def prompt_block(name: str, agent: dict | None = None) -> str:
    """빌런 자신의 판단용: 나의 속셈 + 지금까지 꾸민 일 + 평판."""
    if agent is not None and not is_villain(agent):
        return ""
    r = _load().get(name)
    if not r or not r.get("schemes"):
        return ""
    lines = [f"- {s['date'][5:].replace('-', '/')} {s['title']} → {s['target']}"
             + (" (들통남)" if s.get("exposed") else " (아직 아무도 모름)" if s.get("hidden") else "")
             for s in r["schemes"][-5:]]
    return ("[나의 속셈 - 아무에게도 말하지 않는다] 지금까지 꾸민 일:\n" + "\n".join(lines)
            + f"\n동네/가게에서 내 평판: {r.get('reputation', 60)}/100"
            + (" - 다들 나를 의심한다. 더 조심하거나, 더 대놓고 나가거나." if r.get("reputation", 60) < 40 else ""))


# ===================================================================== 수작
def _scheme(agent: dict, targets: list[str], rec: dict, now: datetime) -> dict | None:
    name = agent["name"]
    past = "\n".join(f"- {s['date']} {s['title']} → {s['target']}{' (들통남)' if s.get('exposed') else ''}"
                     for s in rec.get("schemes", [])[-5:]) or "- 없음"
    system = (f"너는 '{name}'이다. {agent.get('profile', '')}" + system_block(agent)
              + "\n지금은 혼자 다음 수를 꾸미는 시간이다. 실제로 오늘 할 일을 하나 정한다.")
    user = (
        f"오늘: {now:%Y-%m-%d} ({'월화수목금토일'[now.weekday()]}요일)\n\n"
        f"{life.prompt_block(name)}\n\n{memory.prompt_block(name, targets, k=5)}\n\n"
        f"[지금까지 꾸민 일 - 같은 수를 반복하지 말고, 지난 일의 다음 단계로]\n{past}\n"
        f"[내 평판] {rec.get('reputation', 60)}/100\n\n"
        f"오늘 노릴 사람 (이 중 하나): {', '.join(targets)}\n"
        "숨은 목표에 한 걸음 다가가는 수작을 하나 꾸며라. 수법 중에서 고르되 상황에 맞게 구체적으로. "
        "드라마 수위까지만(폭력/범죄/혐오 금지). 누가 했는지 숨길지(hidden)도 정해라 - 대놓고 하는 갑질/통보는 false.\n"
        "반드시 JSON으로만 응답:\n"
        '{"target": "노린 사람 이름", "title": "짧은 제목(누가 했는지 안 드러나게)", '
        '"action": "당한 사람이 겪는 일 1~2문장(3인칭, 빌런 이름은 hidden이면 빼고)", '
        '"rumor": "같은 가게/동네 사람들이 듣는 흐릿한 소문 한 문장", "hidden": true/false, '
        '"truth": "들통날 때 드러나는 진실 한 문장(누가 왜 했는지)", '
        '"effects": {"stress|anger|happiness|confidence|job_satisfaction": -0.2~0.2}, '
        '"inner": "빌런의 속마음 한 줄"}'
    )
    out = llm_json(system, user, temperature=0.9, timeout=90, tag=f"빌런 {name}", agent=name)
    if not out or out.get("target") not in targets or not str(out.get("action") or "").strip():
        return None
    return out


def _company_or_town(agent: dict) -> str:
    return agent.get("company") if agent.get("company") in worldcfg.OFFICES else world.TOWN


def tick(agents: list[dict], roster: list[dict], now: datetime | None = None) -> list[tuple[str, str, str]]:
    """[매시 회차] 차례가 된 빌런의 수작 + 숨긴 수작 들통. 반환: 받은편지함에 넣을 [(이름, 글, 종류)]."""
    now = now or now_kst()
    if not ENABLED or now.hour < SCHEME_HOUR:
        return []
    today = now.strftime("%Y-%m-%d")
    names = {a["name"] for a in agents}
    data = _load()
    out: list[tuple[str, str, str]] = []
    for agent in (a for a in agents if is_villain(a)):
        name = agent["name"]
        rec = _rec(data, name)
        out += _maybe_expose(agent, rec, agents, roster, now)
        last = rec.get("last")
        if last and (now.date() - datetime.strptime(last, "%Y-%m-%d").date()).days < SCHEME_EVERY_DAYS:
            continue
        # 노리던 사람이 교체로 쉬러 갔으면 그 자리를 이어받은 사람을 노린다 (handover.successor)
        targets = list(dict.fromkeys(handover.successor(t, names) for t in agent["villain"].get("targets") or []))
        targets = [t for t in targets if t and t != name]
        if not targets:
            continue
        rec["last"] = today  # 실패해도 오늘은 다시 안 꾸민다
        _save(data)
        s = _scheme(agent, targets, rec, now)
        if not s:
            continue
        target = s["target"]
        hidden = bool(s.get("hidden"))
        title = str(s.get("title") or "이상한 일").strip()[:50]
        action = str(s["action"]).strip()[:300]
        world_title = f"{target} - {title}"
        # 당한 사람: 사건으로 (깨운다) / 같은 가게(동네) 사람들: 소문
        effects = s.get("effects") if isinstance(s.get("effects"), dict) else {}
        life.apply_changes(target, effects, reason=title)
        out.append((target, action if hidden else f"{name}: {action}", "사건"))
        rumor = str(s.get("rumor") or "").strip()[:200]
        if rumor:
            for who, _, _ in world.inject(world_title, rumor, [_company_or_town(agent)], agents, roster, kind="빌런"):
                if who not in (name, target):
                    out.append((who, rumor, "소문"))
        # 가게 안 수작은 그 가게 사람만 안다 (동네 빌런의 수작은 동네 일)
        scope = _company_or_town(agent)
        chronicle.open_thread(f"{target}을(를) 둘러싼 일: {title}" if hidden else f"{name}의 수작: {title}",
                              rumor or action, scope="" if scope == world.TOWN else scope)
        memory.remember(name, f"{j(target, '을')} 노리고 {title} - {s.get('inner') or action}", 7, [target], "속셈")
        novelty.note("사건", f"{title} - {action}", [name, target])
        rec["schemes"] = (rec.get("schemes") or []) + [{
            "date": today, "title": title, "target": target, "hidden": hidden, "exposed": not hidden,
            "truth": str(s.get("truth") or f"{name}이 꾸민 일이었다").strip()[:200], "action": action}]
        rec["schemes"] = rec["schemes"][-MAX_SCHEMES_KEPT:]
        if not hidden:
            rec["reputation"] = max(0, rec.get("reputation", 60) - 3)
        print(f"[빌런] {name} -> {target}: {title}{' (숨김)' if hidden else ''} / {s.get('inner', '')}")
    _save(data)
    return out


def _maybe_expose(agent: dict, rec: dict, agents: list[dict], roster: list[dict], now: datetime) -> list[tuple[str, str, str]]:
    """숨긴 수작이 다음 날부터 하루 EXPOSE_P 확률로 들통난다 (하루 한 번만 굴린다)."""
    today = now.strftime("%Y-%m-%d")
    if rec.get("expose_rolled") == today:
        return []
    rec["expose_rolled"] = today
    rnd = random.Random(f"expose-{agent['name']}-{today}")
    out = []
    for s in rec.get("schemes") or []:
        if not s.get("hidden") or s.get("exposed") or s.get("date") >= today:
            continue
        if (now - timedelta(days=14)).strftime("%Y-%m-%d") > s["date"] or rnd.random() >= EXPOSE_P:
            continue
        s["exposed"] = True
        rec["reputation"] = max(0, rec.get("reputation", 60) - 12)
        truth = f"(들통남) {s['truth']}"
        life.apply_changes(s["target"], {"anger": 0.15}, reason=f"{s['title']}의 진실을 알게 됨")
        life.apply_changes(agent["name"], {"stress": 0.1, "confidence": -0.05}, reason="꾸민 일이 들통남")
        out.append((s["target"], truth, "사건"))
        out.append((agent["name"], f"내가 {s['title']} 꾸민 게 들통났다 - {s['truth']}", "사건"))
        for who, _, _ in world.inject(f"{s['title']} - 알고 보니", s["truth"], [_company_or_town(agent)], agents, roster,
                                      kind="빌런"):
            if who not in (agent["name"], s["target"]):
                out.append((who, truth, "소문"))
        chronicle.update_thread(f"{s['target']}을(를) 둘러싼 일: {s['title']}", f"진실: {s['truth']}")
        memory.remember(s["target"], f"{s['title']} - 알고 보니 {s['truth']}", 8, [agent["name"]], "빌런")
        print(f"[빌런] 들통: {agent['name']} - {s['title']}")
        break  # 하루에 하나만
    return out
