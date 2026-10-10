"""
마을 프로젝트 - 모임/투표로 정해진 일을 에이전트들이 실제로 해 나간다.

예) 모임 결론 "골목 화단을 만들자" -> 프로젝트 "골목 화단 만들기"
    단계: 장소 정하기 -> 흙과 모종 구하기 -> 심기 -> 이름표 달기 (담당 여화정, 도움 길라임/서달미)

- 만들기(create): 모임이 결정을 내리면 agents.run_meeting이 실천할 일이 있는지 LLM에게 물어서 만든다
  (단계 3~5개, 담당자/도움, 범위 동네/회사). 동시에 진행하는 프로젝트는 MAX_ACTIVE개.
- 참여(contribute): 에이전트가 판단할 때 project_work {"project", "did"}를 쓰면 그 시간에 그 일을 한 것.
  담당자 > 도움 > 그 밖의 사람 순으로 진행도가 오르고, 하루에 오를 수 있는 진행도에 상한이 있어서 며칠에 걸쳐 된다.
  단계를 넘을 때마다 채널에 진행 소식, 끝나면 완료 소식.
- 완료: "우리 마을이 바뀐 것"으로 영구히 모두의 지각에 남고(연대기), 범위 사람 전원의 영구 기억이 된다.
- 멈춤: 이틀 넘게 아무도 손대지 않으면 담당자를 깨워 챙기게 한다.

저장: storage/poring_food/projects.json (지우지 않는다 - 완료된 프로젝트도 그대로 남는다)
이 모듈은 agents.py를 import하지 않는다 (순환 방지) - 게시/받은편지함은 agents가 한다.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

PROJECTS_PATH = os.path.join(STATE_DIR, "projects.json")
MAX_ACTIVE = 3             # 동시에 진행하는 프로젝트 수
DAILY_CAP = 40             # 한 프로젝트가 하루에 오를 수 있는 진행도 (며칠에 걸쳐 되도록)
POINTS = {"owner": 20, "helper": 15, "other": 10}
STALL_DAYS = 2             # 이만큼 손대지 않으면 담당자를 깨운다


def _load() -> list[dict]:
    try:
        with open(PROJECTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    tmp = PROJECTS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PROJECTS_PATH)


def _same(a: str, b: str) -> bool:
    a, b = (a or "").replace(" ", ""), (b or "").replace(" ", "")
    return bool(a and b) and (a in b or b in a)


def active() -> list[dict]:
    return [p for p in _load() if p["stage"] != "완료"]


def done() -> list[dict]:
    return [p for p in _load() if p["stage"] == "완료"]


def all_projects() -> list[dict]:
    return _load()


def create(title: str, goal: str, steps: list[str], owner: str, helpers: list[str], scope: str,
           members: list[str], source: str) -> dict | None:
    """새 프로젝트. 같은 이름이 진행 중이거나 진행 중인 게 너무 많으면 None."""
    items = _load()
    if any(p["stage"] != "완료" and _same(p["title"], title) for p in items):
        return None
    if sum(1 for p in items if p["stage"] != "완료") >= MAX_ACTIVE:
        return None
    steps = [s.strip()[:60] for s in steps if str(s).strip()][:5] or ["준비", "실행", "마무리"]
    now = now_kst().isoformat(timespec="minutes")
    p = {"id": uuid.uuid4().hex[:8], "title": title.strip()[:60], "goal": goal.strip()[:200], "steps": steps,
         "step": 0, "progress": 0, "stage": "준비", "owner": owner, "helpers": [h for h in helpers if h != owner][:5],
         "scope": scope, "members": members, "source": source, "started": now, "last_work": now,
         "log": [], "today": {"date": now[:10], "points": 0}, "nudged": ""}
    items.append(p)
    _save(items)
    print(f"[프로젝트] 시작: {p['title']} (담당 {owner}, 단계 {len(steps)}개)")
    return p


def _step_of(progress: int, n: int) -> int:
    return min(n - 1, progress * n // 100)


def contribute(title: str, name: str, did: str) -> dict | None:
    """
    한 사람이 이번 시간에 그 프로젝트 일을 했다. 반환: 진행 결과
    {"project", "gain", "step_done": 끝낸 단계 이름 또는 "", "completed": bool} (해당 없으면 None).
    """
    items = _load()
    now = now_kst()
    for p in items:
        if p["stage"] == "완료" or not _same(p["title"], title):
            continue
        if p.get("members") and name not in p["members"]:
            return None
        today = now.strftime("%Y-%m-%d")
        if p.get("today", {}).get("date") != today:
            p["today"] = {"date": today, "points": 0}
        room = DAILY_CAP - p["today"]["points"]
        if room <= 0:
            return {"project": p, "gain": 0, "step_done": "", "completed": False}
        role = "owner" if name == p["owner"] else ("helper" if name in p["helpers"] else "other")
        gain = min(room, POINTS[role])
        before = _step_of(p["progress"], len(p["steps"]))
        p["progress"] = min(100, p["progress"] + gain)
        p["today"]["points"] += gain
        p["last_work"] = now.isoformat(timespec="minutes")
        p["nudged"] = ""
        p["stage"] = "진행"
        if role == "other" and name not in p["helpers"]:
            p["helpers"].append(name)  # 한 번 손을 보탠 사람은 도움 명단에 든다
        p["log"].append({"at": p["last_work"], "name": name, "did": did[:150], "gain": gain})
        step_done, completed = "", False
        after = _step_of(p["progress"], len(p["steps"]))
        if p["progress"] >= 100:
            completed = True
            step_done = p["steps"][-1]
            p["stage"] = "완료"
            p["done_at"] = p["last_work"]
            p["step"] = len(p["steps"])
            print(f"[프로젝트] 완료: {p['title']}")
        elif after > before:
            step_done = p["steps"][before]
            p["step"] = after
        _save(items)
        return {"project": p, "gain": gain, "step_done": step_done, "completed": completed}
    return None


def stalled(now: datetime) -> list[dict]:
    """한동안 아무도 손대지 않은 프로젝트 (하루에 한 번만 알린다)."""
    items = _load()
    cutoff = (now - timedelta(days=STALL_DAYS)).isoformat()
    today = now.strftime("%Y-%m-%d")
    out = []
    for p in items:
        if p["stage"] != "완료" and p.get("last_work", "") < cutoff and p.get("nudged") != today:
            p["nudged"] = today
            out.append(p)
    if out:
        _save(items)
    return out


def current_step(p: dict) -> str:
    return p["steps"][min(p["step"], len(p["steps"]) - 1)]


def prompt_block(name: str) -> str:
    """에이전트 지각용: 진행 중인 마을 프로젝트(내가 맡은 일 표시) + 이미 바뀐 것."""
    lines = []
    for p in active():
        if p.get("members") and name not in p["members"]:
            continue
        role = "내가 담당" if name == p["owner"] else ("내가 돕는 중" if name in p["helpers"] else "")
        lines.append(f"- {p['title']} ({p['progress']}%, 지금 단계: {current_step(p)}) 담당 {p['owner']}"
                     + (f" / 도움 {', '.join(p['helpers'][:4])}" if p["helpers"] else "") + (f" ← {role}" if role else ""))
    out = []
    if lines:
        out.append("[진행 중인 마을 프로젝트 - 손을 보태면 project_work에 그 시간에 실제로 한 일을 써라]\n" + "\n".join(lines))
    fin = [p for p in done() if not p.get("members") or name in p["members"]][-10:]
    if fin:
        out.append("[우리 마을이 바뀐 것 - 다 같이 해낸 일]\n" + "\n".join(
            f"- {p['title']}: {p['goal']} ({p.get('done_at', '')[:10]} 완료)" for p in fin))
    return "\n\n".join(out)


def public_block() -> str:
    """세계 엔진/연대기용: 진행 중/완료된 프로젝트 요약."""
    act = active()
    fin = done()[-10:]
    out = []
    if act:
        out.append("[마을 프로젝트 진행 중]\n" + "\n".join(f"- {p['title']} {p['progress']}% (담당 {p['owner']})" for p in act))
    if fin:
        out.append("[마을이 바뀐 것]\n" + "\n".join(f"- {p['title']}: {p['goal']}" for p in fin))
    return "\n\n".join(out)
