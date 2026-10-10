"""
에이전트 <-> 배경 인물 교체 - 하루에 한 번, 소속별로 한 명씩 스스로 사는 사람(LLM 에이전트)과 배경 인물이 자리를 바꾼다.

같은 사람들만 계속 주인공이면 이야기가 좁아진다. 그래서 매일 새벽(ROTATE_FROM_HOUR 이후 첫 회차) 포링푸드/
에린 로지스틱스/동네(광주 동네 상가)마다 오래 활동한 에이전트 한 명이 배경 인물로 쉬러 가고, 오래 쉰 배경 인물
한 명이 에이전트로 나선다. 소속별 인원은 그대로다.

- 고정(교체 안 함): 자기 디스코드 계정이 있는 사람(애순이/소라), 담당 업무(duty)가 있는 사람, 역할(role, 예: 통장)이
  있는 사람, agents.json에서 "fixed": true인 사람. 지금 맡은 마을 프로젝트 담당자, 진행 중인 모임 제안자도 이번엔 안 뺀다.
- 처음 에이전트가 되는 배경 인물은 조직도의 겉모습/속마음/행동을 바탕으로 LLM이 한 번 프로필/루틴/말투/수면/단골을
  써 준다(실패하면 조직도로 기본 프로필). 써 둔 프로필은 저장해 두고 다음에 다시 나설 때 그대로 쓴다.
- 기억/감정/관계는 이름으로 이어지므로 에이전트 <-> 배경 인물을 오가도 그대로 남는다.

저장: storage/poring_food/rotation.json {"date", "benched": [쉬는 기본 에이전트], "promoted": {이름: 프로필},
"since": {이름: 마지막으로 바뀐 날}, "history": [...]}. 지우지 않는다 (history는 최근 200개).
이 모듈은 agents.py를 import하지 않는다 (agents.load_agents가 이 모듈로 명단을 고친다).
"""
from __future__ import annotations

import json
import os

from . import characters, chronicle, projects
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import DATA_DIR, STATE_DIR
from .llm import llm_json

ROTATION_PATH = os.path.join(STATE_DIR, "rotation.json")
ENABLED = os.getenv("PORING_AGENT_ROTATION", "1") not in ("0", "false", "False", "")
ROTATE_FROM_HOUR = 5
SWAPS_PER_COMPANY = int(os.getenv("PORING_AGENT_ROTATION_SWAPS", "1"))
COMPANIES = ("포링푸드 (Poring Food)", "에린 로지스틱스 (Erinn Logistics)", "광주 동네 상가")
TOWN_COMPANY = "광주 동네 상가"
TOWN_GROUP = "동네 단톡방"
DEFAULT_EVERY = 3
TIMEOUT_SEC = 60


def _load() -> dict:
    try:
        with open(ROTATION_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    tmp = ROTATION_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ROTATION_PATH)


def apply(base: list[dict]) -> list[dict]:
    """agents.json 명단에 교체를 반영한 지금 명단 (쉬는 사람은 빼고, 나선 배경 인물은 더한다)."""
    data = _load()
    benched = set(data.get("benched", []))
    out = [a for a in base if a["name"] not in benched]
    have = {a["name"] for a in out}
    for name, entry in (data.get("promoted") or {}).items():
        if name not in have and entry.get("active"):
            out.append(entry)
    return out


def _fixed(a: dict) -> bool:
    return bool(a.get("account") or a.get("duty") or a.get("role") or a.get("fixed"))


def _busy() -> set[str]:
    """지금 빼면 이야기가 끊기는 사람: 진행 중인 프로젝트 담당자, 진행 중인 모임 제안자."""
    busy = {p["owner"] for p in projects.active()}
    busy |= {t["proposal"]["by"] for t in chronicle.pending_proposals()}
    return busy


def _reserve() -> dict[str, dict]:
    """agents.json "reserve" - 예전에 에이전트였던 사람들의 손으로 쓴 프로필 (다시 나설 때 그대로 쓴다)."""
    try:
        with open(os.path.join(DATA_DIR, "agents.json"), "r", encoding="utf-8") as f:
            return {a["name"]: a for a in json.load(f).get("reserve", []) if isinstance(a, dict) and a.get("name")}
    except (OSError, json.JSONDecodeError):
        return {}


def _profile_for(member: dict, data: dict) -> dict:
    """배경 인물 -> 에이전트 프로필 (reserve/전에 써 둔 게 있으면 그대로, 없으면 LLM이 한 번 쓴다)."""
    saved = _reserve().get(member["name"]) or (data.get("profiles") or {}).get(member["name"])
    if saved:
        return dict(saved)
    persona = (f"{member.get('company', '')} {member.get('dept', '')} {member.get('rank', '')}. "
               f"겉모습: {member.get('outer_persona', '')} / 속마음: {member.get('inner_truth', '')} / "
               f"행동: {member.get('key_behavior', '')}")
    out = llm_json(
        "너는 가상 도시의 인물 설정 담당이다. 배경 인물을 스스로 판단하며 사는 인물로 만들 프로필을 쓴다. "
        "주어진 설정에서 벗어나지 말고, 실존 인물/혐오 소재는 쓰지 마라.",
        f"[인물] {member['name']}\n[설정] {persona}\n\n"
        "이 사람의 프로필을 써라. 반드시 JSON으로만 응답:\n"
        '{"profile": "나이대, 하는 일, 겉과 속, 요즘 고민 2~3문장", "routine": "평일/주말 하루 일과 한두 문장", '
        '"speech": "말투 한 문장 (존댓말/반말, 말버릇)", "sleep": [잠드는 시각 0~23, 일어나는 시각 0~23], '
        '"hangouts": ["카페 오후세시/포장마차 단밤/동네 헬스장/동네 편의점 중 0~2곳"]}',
        temperature=0.7, timeout=TIMEOUT_SEC, tag=f"교체 프로필 {member['name']}")
    out = out if isinstance(out, dict) else {}
    sleep = out.get("sleep") if isinstance(out.get("sleep"), list) and len(out.get("sleep")) == 2 else [0, 7]
    try:
        sleep = [int(sleep[0]) % 24, int(sleep[1]) % 24]
    except (TypeError, ValueError):
        sleep = [0, 7]
    places = {"카페 오후세시", "포장마차 단밤", "동네 헬스장", "동네 편의점"}
    entry = {
        "name": member["name"], "company": member.get("company", ""), "sleep": sleep,
        "avatar_url": f"https://api.dicebear.com/9.x/notionists/png?seed=rot{sum(map(ord, member['name']))}",
        "profile": str(out.get("profile") or persona).strip()[:400],
        "routine": str(out.get("routine") or "평일 낮엔 일하고 저녁엔 쉰다.").strip()[:300],
        "speech": str(out.get("speech") or "평범한 해요체.").strip()[:200],
        "account": "", "decide_every": DEFAULT_EVERY,
        "hangouts": [h for h in (out.get("hangouts") or []) if h in places][:2],
    }
    if member.get("company") == TOWN_COMPANY:
        entry["groups"] = [TOWN_GROUP]
    data.setdefault("profiles", {})[member["name"]] = entry
    return dict(entry)


def tick(base: list[dict], max_agents: int) -> list[tuple[str, str, str]]:
    """
    [매시 회차] 오늘 아직 안 바꿨고 새벽이 지났으면 소속별로 교체한다. 반환: [(소속, 쉬는 사람, 나선 사람)].
    """
    if not ENABLED:
        return []
    now = now_kst()
    today = now.strftime("%Y-%m-%d")
    data = _load()
    if data.get("date") == today or now.hour < ROTATE_FROM_HOUR:
        return []
    data["date"] = today
    current = apply(base)
    active_names = {a["name"] for a in current}
    roster = characters.load_roster()
    since = data.setdefault("since", {})
    busy = _busy()
    swaps = []
    for company in COMPANIES:
        for _ in range(SWAPS_PER_COMPANY):
            outs = [a for a in current if a.get("company") == company and not _fixed(a) and a["name"] not in busy]
            ins = [c for c in roster if c.get("company") == company and c["name"] not in active_names]
            if not outs or not ins:
                break
            # 가장 오래 에이전트였던 사람이 쉬고, 가장 오래 쉰(한 번도 안 나선) 배경 인물이 나선다
            leaving = min(outs, key=lambda a: since.get(a["name"], "0000"))
            coming = min(ins, key=lambda c: since.get(c["name"], "0000"))
            base_entry = next((a for a in base if a["name"] == coming["name"]), None)
            if base_entry:
                # 원래 agents.json 사람(쉬러 갔던 사람)이 돌아온다 - 원래 프로필 그대로
                entry = base_entry
                data["benched"] = [n for n in data.get("benched", []) if n != coming["name"]]
            else:
                entry = _profile_for(coming, data)
                entry["active"] = True
                data.setdefault("promoted", {})[coming["name"]] = entry
            if leaving["name"] in (data.get("promoted") or {}):
                data["promoted"][leaving["name"]]["active"] = False
            else:
                data.setdefault("benched", []).append(leaving["name"])
            since[leaving["name"]] = since[coming["name"]] = today
            current = [a for a in current if a["name"] != leaving["name"]] + [entry]
            active_names = {a["name"] for a in current}
            swaps.append((company, leaving["name"], coming["name"]))
    if len(current) > max_agents:
        print(f"[경고] 교체 후 에이전트 {len(current)}명 - PORING_AGENT_MAX({max_agents})를 넘는다")
    data.setdefault("history", []).extend({"date": today, "company": c, "out": o, "in": i} for c, o, i in swaps)
    data["history"] = data["history"][-200:]
    _save(data)
    for c, o, i in swaps:
        print(f"[교체] {c.split(' ')[0]}: {o} -> 배경 인물, {i} -> 에이전트")
    return swaps


def is_promoted(name: str) -> bool:
    entry = (_load().get("promoted") or {}).get(name)
    return bool(entry and entry.get("active"))
