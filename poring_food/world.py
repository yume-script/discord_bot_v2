"""
[3단계] 세계 엔진 - 에이전트(애순이/소라...)가 미리 알 수 없는 사건을 세계가 일으킨다.

작가 회의는 줄거리를 미리 짜고 인물이 그걸 연기하는 방식이었다. 에이전트는 그 반대로, 세계가 사건만
던지고 각자 자기 상태/기억으로 반응하면서 이야기가 생긴다. 그래서 사건 계획은 에이전트에게 절대
보여주지 않고, 시각이 되면 그때 당사자에게만 "지각"으로 전달한다.

- 하루 계획(plan_day): 그날 처음 돌 때 LLM이 오늘 일어날 사건 4~8개를 시각별로 정해 둔다
  (회사 전체/동네 사건으로 여러 명에게 닿게, 좋은 일/나쁜 일/소소한 일 골고루).
  실제 바깥 신호(날씨/화제/날짜)와 어긋나지 않게 한다.
- 발생(release): 시각이 된 사건을 터뜨린다.
  · 당사자 에이전트에게 전달할 목록을 돌려준다 (agents.py가 받은편지함에 넣음)
  · 소문(rumor) 사건은 먼저 흐릿한 소문으로, 2시간 뒤 확인된 사실로 전달한다
  · 회사 전체 사건은 그 회사 배경 인물(LLM 없는 25명)의 상태에도 바로 반영한다
- 오늘 생긴 일(today_lines): 배경 인물 장면/일지가 읽는 "바깥 세상 변화"에 들어간다 (signals.py).

이 모듈은 agents.py/signals.py를 import하지 않는다 (순환 방지) - 필요한 건 인자로 받는다.
"""
from __future__ import annotations

import json
import os
import uuid

from . import chronicle, life, memory
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR
from .llm import llm_json

WORLD_PATH = os.path.join(STATE_DIR, "world_state.json")
PLAN_FROM_HOUR = 6          # 이 시각 이후 그날 처음 돌 때 계획을 세운다
LAST_EVENT_HOUR = 23
RUMOR_CONFIRM_AFTER_H = 2
MAX_LOG = 60
PLAN_TIMEOUT_SEC = 120
DETAILED_AGENTS = 12        # 계획 프롬프트에 루틴/기억까지 자세히 넣는 에이전트 수 (agents.json 앞에서부터)
TOWN = "동네"


def _load() -> dict:
    try:
        with open(WORLD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _save(data: dict) -> None:
    tmp = WORLD_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, WORLD_PATH)


# ===================================================================== 하루 계획
def _plan_day(now, agents: list[dict], roster: list[dict], signals_block: str, recent_log: list[dict]) -> list[dict]:
    first_hour = max(now.hour + 1, 7)
    if first_hour > LAST_EVENT_HOUR:
        return []
    companies = sorted({c["company"] for c in roster})
    agent_lines = []
    for i, a in enumerate(agents):
        st = life.get(a["name"])
        if i < DETAILED_AGENTS:
            mems = memory.recall(a["name"], k=3)
            agent_lines.append(
                f"- {a['name']} ({a.get('company', '')}): {a.get('profile', '')}\n"
                f"  평소 루틴: {a.get('routine', '')}\n"
                f"  요즘 목표: {', '.join(st.get('goals') or [])}\n"
                f"  최근 기억: {' / '.join(m['text'] for m in mems) or '없음'}"
            )
        else:
            # 인원이 많으면 프롬프트가 너무 길어진다 - 뒤쪽 사람은 한 줄 요약만
            goals = ", ".join((st.get("goals") or [])[:2])
            agent_lines.append(f"- {a['name']} ({a.get('company', '')}): {a.get('profile', '')[:70]}"
                               + (f" / 목표: {goals}" if goals else ""))
    npc_sample = [f"{c['name']}({c['company'].split(' ')[0]} {c['dept']} {c['rank']})"
                  for c in roster if c["name"] not in {a["name"] for a in agents}][:30]
    system = (
        "너는 가상 세계의 '세계 엔진'이다. 이 세계에는 스스로 판단하며 사는 인물(에이전트)들이 있고, "
        "너는 그들이 미리 알 수 없는 사건을 일으킨다. 줄거리를 짜는 작가가 아니라 날씨처럼 세상을 굴리는 쪽이다. "
        "사건이 어떻게 풀릴지는 정하지 마라 - 인물들이 알아서 반응한다."
    )
    user = (
        f"오늘: {now.strftime('%Y-%m-%d')} (지금 {now.hour}시). 사건은 {first_hour}~{LAST_EVENT_HOUR}시 사이에만.\n\n"
        "[에이전트 - 이들은 오늘 무슨 일이 생길지 모른다]\n" + "\n".join(agent_lines) + "\n\n"
        f"[회사/장소] {', '.join(companies)}, {TOWN}(광주 동네 전체)\n"
        f"[배경 인물 일부] {', '.join(npc_sample)}\n\n"
        f"{signals_block}\n\n"
        + (chronicle.prompt_block() + "\n\n" if chronicle.prompt_block() else "") +
        "[최근에 세상에서 있었던 일]\n" + ("\n".join(f"- {e.get('date', '')} {e.get('hour', '')}시 {e.get('title', '')}" for e in recent_log[-8:]) or "- 없음") + "\n\n"
        "오늘 일어날 사건 4~8개를 정해라:\n"
        "- 에이전트가 많으니 전원에게 줄 필요는 없다 - 회사 전체/동네 사건으로 여러 명에게 닿게 하고,"
        " 개인 사건은 최근에 사건이 없던 사람 위주로.\n"
        "- 좋은 일, 나쁜 일, 소소한 일을 골고루. 매일 큰 사건이 터지면 안 된다(큰 사건은 가끔).\n"
        "- 실제 날씨/날짜/화제와 어긋나지 않게. 최근 사건과 똑같은 걸 반복하지 마라.\n"
        "- 회사 내부의 불확실한 일(구조조정, 인사, 감사 등)은 visibility를 rumor로 - 먼저 소문으로 돈다.\n"
        "- 실존 인물/정치/혐오 소재 금지.\n"
        "반드시 JSON으로만 응답:\n"
        '{"events": [{"hour": 정수, "title": "사건 제목", "detail": "실제로 일어난 일(당사자가 알게 되는 내용) 1~2문장", '
        '"rumor": "visibility가 rumor일 때 먼저 도는 흐릿한 소문 한 문장(아니면 빈 문자열)", '
        '"targets": ["에이전트 이름 또는 회사 이름 또는 동네"], "visibility": "direct 또는 rumor", '
        '"effects": {"stress|happiness|loneliness|anger|romance|confidence|energy|job_satisfaction": -0.2~0.2}}]}'
    )
    out = llm_json(system, user, temperature=0.9, timeout=PLAN_TIMEOUT_SEC, tag="세계 엔진")
    if not out:
        return []
    valid_targets = {a["name"] for a in agents} | set(companies) | {TOWN}
    events = []
    for e in out.get("events") or []:
        if not isinstance(e, dict):
            continue
        try:
            hour = int(e.get("hour"))
        except (TypeError, ValueError):
            continue
        targets = [t for t in (e.get("targets") or []) if t in valid_targets]
        title = str(e.get("title") or "").strip()[:80]
        if not (first_hour <= hour <= LAST_EVENT_HOUR) or not targets or not title:
            continue
        rumor = e.get("visibility") == "rumor" and bool(str(e.get("rumor") or "").strip())
        events.append({
            "id": uuid.uuid4().hex[:8], "hour": hour, "title": title,
            "detail": str(e.get("detail") or title).strip()[:300],
            "rumor": str(e.get("rumor") or "").strip()[:200] if rumor else "",
            "visibility": "rumor" if rumor else "direct",
            "targets": targets,
            "effects": e.get("effects") if isinstance(e.get("effects"), dict) else {},
            "released": False, "confirmed": not rumor,
            # 에이전트 개인에게만 일어난 일 - 다른 사람(다른 에이전트/배경 인물)은 모른다
            "private": all(t in {a["name"] for a in agents} for t in targets),
        })
    events.sort(key=lambda x: x["hour"])
    return events[:8]


# ===================================================================== 발생
def _recipients(event: dict, agents: list[dict]) -> list[str]:
    names = []
    for a in agents:
        t = event["targets"]
        if a["name"] in t or a.get("company") in t or TOWN in t:
            names.append(a["name"])
    return names


def _apply_npc_effects(event: dict, roster: list[dict], agent_names: set[str]) -> None:
    """회사 전체/동네 사건은 그곳 배경 인물들의 상태에도 바로 반영한다 (에이전트는 스스로 반응하니 제외)."""
    if not event.get("effects"):
        return
    for c in roster:
        if c["name"] in agent_names:
            continue
        if c["company"] in event["targets"] or TOWN in event["targets"] or c["name"] in event["targets"]:
            life.apply_changes(c["name"], event["effects"], reason=event["title"], rank=c.get("rank", ""))


def tick(agents: list[dict], roster: list[dict], signals_block: str = "") -> list[tuple[str, str, str]]:
    """
    [매시 회차] 필요하면 하루 계획을 세우고, 시각이 된 사건을 터뜨린다.
    반환: 에이전트에게 전달할 지각 [(이름, 글, 종류), ...] - 종류는 "사건" 또는 "소문"
    """
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    data = _load()
    if data.get("date") != today:
        if now.hour < PLAN_FROM_HOUR:
            return []
        log = data.get("log", [])
        plan = _plan_day(now, agents, roster, signals_block, log)
        data = {"date": today, "plan": plan, "log": log, "manual": data.get("manual", {})}
        _save(data)
        print(f"[세계] 오늘 사건 {len(plan)}개 계획 (에이전트에게는 비밀): {[(e['hour'], e['title']) for e in plan]}")

    agent_names = {a["name"] for a in agents}
    deliveries: list[tuple[str, str, str]] = []
    for e in data.get("plan", []):
        if not e.get("released") and e["hour"] <= now.hour:
            e["released"] = True
            data.setdefault("log", []).append({"date": today, "hour": e["hour"], "title": e["title"],
                                               "targets": e["targets"]})
            _apply_npc_effects(e, roster, agent_names)
            kind, text = ("소문", e["rumor"]) if e["visibility"] == "rumor" else ("사건", e["detail"])
            for name in _recipients(e, agents):
                deliveries.append((name, text, kind))
            chronicle.record_event("소문" if kind == "소문" else "세계", e["title"], text, e["targets"],
                                   private=e.get("private", False))
            print(f"[세계] 사건 발생 {e['hour']}시: {e['title']} ({kind})")
        elif e.get("released") and not e.get("confirmed") and now.hour >= e["hour"] + RUMOR_CONFIRM_AFTER_H:
            e["confirmed"] = True
            for name in _recipients(e, agents):
                deliveries.append((name, f"(소문이 사실로 확인됨) {e['detail']}", "사건"))
            chronicle.record_event("세계", f"{e['title']} (소문 확인)", e["detail"], e["targets"])
            print(f"[세계] 소문 확인: {e['title']}")
    data["log"] = data.get("log", [])[-MAX_LOG:]
    _save(data)
    return deliveries


def targets(agents: list[dict], roster: list[dict]) -> list[str]:
    """사건 대상으로 쓸 수 있는 이름들: 에이전트, 회사, 동네."""
    return [a["name"] for a in agents] + sorted({c["company"] for c in roster}) + [TOWN]


def inject(title: str, detail: str, target_list: list[str], agents: list[dict], roster: list[dict],
           effects: dict | None = None, kind: str = "관리자") -> list[tuple[str, str, str]]:
    """
    [관리자 사건] 지금 바로 사건 하나를 일으킨다. 하루 계획과 같은 방식으로 기록/전달된다
    (에이전트에게는 그냥 세상에서 생긴 일로 보인다). 반환: 전달할 지각 [(이름, 글, "사건"), ...]
    대상이 하나도 맞지 않으면 빈 목록.
    """
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    valid = set(targets(agents, roster))
    target_list = [t for t in target_list if t in valid]
    if not target_list:
        return []
    agent_names = {a["name"] for a in agents}
    e = {
        "id": uuid.uuid4().hex[:8], "hour": now.hour, "title": title.strip()[:80] or detail.strip()[:40],
        "detail": detail.strip()[:300], "rumor": "", "visibility": "direct", "targets": target_list,
        "effects": effects or {}, "released": True, "confirmed": True, "manual": True,
        "private": all(t in agent_names for t in target_list),
    }
    data = _load()
    manual = data.get("manual") if isinstance(data.get("manual"), dict) else {}
    if manual.get("date") != today:
        manual = {"date": today, "events": []}
    manual["events"].append(e)
    data["manual"] = manual  # 하루 계획(plan)과 따로 둔다 - 계획 전(새벽)에 넣어도 그날 계획이 막히지 않게
    data.setdefault("log", []).append({"date": today, "hour": e["hour"], "title": e["title"], "targets": target_list})
    data["log"] = data["log"][-MAX_LOG:]
    _save(data)
    _apply_npc_effects(e, roster, agent_names)
    chronicle.record_event(kind, e["title"], e["detail"], target_list, private=e["private"])
    print(f"[세계] {kind} 사건 {e['hour']}시: {e['title']} -> {target_list}")
    return [(name, e["detail"], "사건") for name in _recipients(e, agents)]


def _today_events(data: dict, today: str) -> list[dict]:
    events = list(data.get("plan", [])) if data.get("date") == today else []
    manual = data.get("manual") if isinstance(data.get("manual"), dict) else {}
    if manual.get("date") == today:
        events += manual.get("events", [])
    return sorted(events, key=lambda x: x.get("hour", 0))


def today_lines(company: str | None = None) -> list[str]:
    """오늘 이미 일어난 공개 사건 (배경 인물 장면/일지, 에이전트의 바깥 세상 신호용).
    에이전트 개인 사건은 빼고(당사자 받은편지함으로만 간다), 아직 확인 안 된 건 소문으로만."""
    data = _load()
    lines = []
    for e in _today_events(data, now_kst().strftime("%Y-%m-%d")):
        if not e.get("released") or e.get("private"):
            continue
        # company가 주어지면(에이전트 지각) 그 회사 사건과 동네 사건만 - 다른 회사 내부 일은 모른다
        if company and company not in e["targets"] and TOWN not in e["targets"]:
            continue
        where = ", ".join(e["targets"])
        if e.get("confirmed"):
            lines.append(f"{e['hour']}시 [{where}] {e['title']} - {e['detail']}")
        else:
            lines.append(f"{e['hour']}시 [{where}] (소문) {e['rumor']}")
    return lines
