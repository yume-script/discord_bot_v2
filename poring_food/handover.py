"""
자리 인수인계 - 교체(rotation.py)로 사람이 바뀌어도 그 자리(소속+부서)의 이야기는 끊기지 않게.

고정 인물(디스코드 계정이 있는 사람, 역할이 있는 사람, "fixed" - 애순이/소라/통장/빌런 등)은 바뀌지 않는다.
나머지는 하루 한 번 소속별로 교체되는데, 예전엔 실적 자리(kpi.py)만 넘어가고 전임자가 하던 일/약속/다툼/받은 소식은
전임자 이름에 붙은 채 묻혔다. 이제 교체될 때:

1) 인수인계서: 전임자의 최근 중요한 기억, 아직 안 읽은 받은편지함, 걸려 있는 진행 중인 일, 담당 실적을 보고 LLM이
   "이 자리에서 이어서 할 일" 3~6줄을 쓴다 (교체 한 번에 LLM 1회, 실패하면 모은 것을 그대로). 후임자는 7일 동안 판단 때
   [내 자리 인수인계]로 보고, 영구 기억으로도 남는다.
2) 받은편지함: 전임자에게 와 있던 (읽지 않은) 소식은 "전임 OO 앞으로 온 것"으로 후임자에게 넘긴다.
3) 동료: 같은 소속 에이전트들은 "OO 자리는 이제 XX가" 소식을 받고, 판단 때 [요즘 바뀐 사람 - 자리는 그대로]를 본다.
4) 진행 중인 일: 전임자 이름이 걸린 일에 "XX가 이어받음"을 남긴다.
5) 승계: successor(이름)로 지금 그 자리에 있는 사람을 찾는다 (빌런이 노리던 사람이 쉬러 가면 후임자를 노린다 등).
저장: storage/poring_food/handover.json {"notes": {후임: {...}}, "successor": {전임: 후임}, "log": [...]}
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from . import characters, chronicle, memory
from .clock import now_kst
from .josa import j
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR
from .llm import llm_json

HANDOVER_PATH = os.path.join(STATE_DIR, "handover.json")
SHOW_DAYS = 7
KEEP_LOG = 100


def _load() -> dict:
    try:
        with open(HANDOVER_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(HANDOVER_PATH), exist_ok=True)
    tmp = HANDOVER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, HANDOVER_PATH)


def seat(name: str, roster: list[dict] | None = None) -> str:
    """그 사람의 자리 이름: '포링푸드 생산부 과장' 같은 (조직도 기준, 없으면 소속만)."""
    c = next((r for r in (roster or characters.load_roster()) if r["name"] == name), None)
    if not c:
        return ""
    company = (c.get("company") or "").split(" (")[0]
    return " ".join(x for x in (company, c.get("dept", ""), c.get("rank", "")) if x)


def successor(name: str, active: set[str]) -> str:
    """지금 그 사람 자리에 있는 사람 (본인이 활동 중이면 본인, 쉬러 갔으면 후임 - 후임도 쉬면 그다음). 없으면 ""."""
    succ = _load().get("successor") or {}
    seen = set()
    while name and name not in active and name not in seen:
        seen.add(name)
        name = succ.get(name, "")
    return name if name in active else ""


def _note(out_name: str, in_name: str, seat_name: str, inbox: list[dict], threads: list[str], posts: list[str]) -> str:
    mems = [m["text"] for m in memory.recall(out_name, k=8) if int(m.get("importance", 0)) >= 4]
    raw = (["- 기억: " + t for t in mems[:6]] + ["- 받은 소식: " + i["text"] for i in inbox[-5:]]
           + ["- 걸린 일: " + t for t in threads[:4]] + ["- 담당 실적: " + p for p in posts])
    if not raw:
        return ""
    out = llm_json(
        "너는 회사/동네의 업무 인수인계를 정리하는 사람이다. 전임자가 남긴 것에서 '이 자리를 이어받은 사람이 계속 이어가야 할 일'만"
        " 골라 짧게 쓴다. 전임자 개인의 사생활(연애/가족/사적인 감정)은 빼고, 일/약속/진행 중인 다툼/맡은 사람과의 관계만.",
        f"[자리] {seat_name or '같은 소속'}\n[전임] {out_name} → [후임] {in_name}\n[전임자가 남긴 것]\n" + "\n".join(raw) + "\n\n"
        "후임이 이어갈 일 3~6줄을 써라 (누구와 무슨 일이 어디까지 됐는지). 반드시 JSON으로만 응답:\n"
        '{"items": ["이어갈 일 한 줄", ...]}',
        temperature=0.4, timeout=60, tag=f"인수인계 {out_name}->{in_name}")
    items = [str(x).strip()[:120] for x in ((out or {}).get("items") or []) if str(x).strip()]
    return "\n".join(f"- {x}" for x in items[:6]) if items else "\n".join(raw[:6])


def run(out_name: str, in_name: str, company: str, data: dict, agents: list[dict], push, now: datetime | None = None,
        kpi_posts: list[str] | None = None) -> str:
    """교체 한 건의 인수인계. data는 에이전트 상태(받은편지함), push(data, 이름, 글, 종류, quiet=)는 agents._push_inbox.
    반환: 인수인계서 (없으면 "")."""
    now = now or now_kst()
    roster = characters.load_roster()
    seat_name = seat(out_name, roster) or seat(in_name, roster)
    st_out = data.get(out_name) or {}
    inbox = [m for m in st_out.get("inbox", []) if not m.get("quiet")]
    threads = [t["title"] for t in chronicle.open_threads(company or None)
               if out_name in t["title"] or any(out_name in (u.get("note") or "") for u in t.get("updates", [])[-3:])]
    note = _note(out_name, in_name, seat_name, inbox, threads, kpi_posts or [])
    h = _load()
    h.setdefault("successor", {})[out_name] = in_name
    h.setdefault("notes", {})[in_name] = {"from": out_name, "seat": seat_name, "date": now.strftime("%Y-%m-%d"), "note": note}
    h.setdefault("log", []).append({"date": now.strftime("%Y-%m-%d"), "company": company, "seat": seat_name,
                                    "out": out_name, "in": in_name})
    h["log"] = h["log"][-KEEP_LOG:]
    _save(h)
    # 후임: 인수인계서 + 전임에게 와 있던 소식
    if note:
        memory.remember(in_name, f"{out_name}에게서 {seat_name or '자리'}를 이어받음 - " + note.replace("\n", " ")[:180],
                        7, [out_name], "인수인계", permanent=True)
        push(data, in_name, f"인수인계: 오늘부터 내가 {seat_name or out_name + '의 자리'}를 맡는다. 전임 {out_name}이 하던 일:\n{note}",
             "업무")
    for m in inbox[-5:]:
        push(data, in_name, f"(전임 {out_name} 앞으로 와 있던 것) {m['text']}", m.get("kind", "소식"), quiet=True)
    if out_name in data:
        data[out_name]["inbox"] = []
    # 동료: 누가 이어받았는지
    for a in agents:
        if a["name"] not in (out_name, in_name) and a.get("company") == company:
            push(data, a["name"], f"{seat_name or out_name + '의 자리'}는 오늘부터 {in_name} 담당 - {j(out_name, '은')} 한동안 쉰다. "
                                  f"{out_name}과 하던 일은 이제 {in_name}에게.", "소식", quiet=True)
    for title in threads:
        chronicle.update_thread(title, f"{j(out_name, '이')} 쉬러 가서 {j(in_name, '이')} 이어받음")
    print(f"[인수인계] {seat_name or company}: {out_name} -> {in_name}" + (f" ({note.count(chr(10)) + 1}줄)" if note else ""))
    return note


def prompt_block(name: str, company: str, now: datetime | None = None) -> str:
    """판단용: 내가 받은 인수인계(7일) + 같은 소속에서 요즘 바뀐 사람."""
    now = now or now_kst()
    h = _load()
    since = (now - timedelta(days=SHOW_DAYS)).strftime("%Y-%m-%d")
    out = []
    n = (h.get("notes") or {}).get(name)
    if n and n.get("date", "") >= since and n.get("note"):
        out.append(f"[내 자리 인수인계 - {n['date'][5:].replace('-', '/')} 전임 {n['from']}에게서 {n.get('seat') or '이 자리'}를 "
                   f"이어받았다. 이어서 챙길 일]\n{n['note']}")
    changes = [x for x in h.get("log") or [] if x.get("date", "") >= since and x.get("company") == company
               and name not in (x.get("in"), x.get("out"))]
    if changes:
        out.append("[요즘 바뀐 사람 - 자리는 그대로, 그 일은 이제 새 사람과]\n" + "\n".join(
            f"- {x['date'][5:].replace('-', '/')} {x.get('seat') or '자리'}: {x['out']} → {x['in']}" for x in changes[-5:]))
    return "\n\n".join(out)
