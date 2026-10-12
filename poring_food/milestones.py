"""
인생 전환점 - 인물이 몇 주가 지나도 그대로이지 않게, 가끔 삶에 큰 변화가 온다.

- 매일 MILESTONE_HOUR시 이후 첫 회차에, 마지막 전환점에서 MIN_GAP_DAYS일 넘게 지난 에이전트 중 몇 명(PER_DAY,
  기본: 에이전트 수에 맞춰 한 사람당 한 달에 한두 번꼴)을 골라 전환점을 하나씩 일으킨다 (LLM 1회/명).
- 전환점: 승진 심사/이직 제안/이사/연애 시작/이별/새 취미/건강 문제/가족 소식/큰돈/자격증/반려동물/목표 달성 ...
  그 사람의 성격·처지·목표·기억·지난 전환점에 맞게 LLM이 고르고, 지난번과 같은 종류는 피한다.
- 반영: 감정 변화, 목표 갱신(이룬 목표는 빼고 새 목표), 영구 기억, 연대기. 당사자는 받은편지함으로 바로 알게 되고(깨움),
  공개되는 일(승진 소식, 결혼 발표 등)이면 같은 회사/동네에 사건으로 퍼진다.
- 직급/회사 같은 조직 정보는 바꾸지 않는다 (실적 자리 배정이 흔들리지 않게) - 그런 일은 제안/심사/소문 단계로만.
저장: storage/poring_food/milestones.json {"date": 마지막으로 돌린 날, "people": {이름: {"last", "history": [...]}}}
"""
from __future__ import annotations

import json
import math
import os
import random
from datetime import datetime

from . import chronicle, life, memory, novelty, world
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR
from .llm import llm_json

MILESTONES_PATH = os.path.join(STATE_DIR, "milestones.json")
ENABLED = os.getenv("PORING_MILESTONES", "1") not in ("0", "false", "False", "")
PER_DAY = int(os.getenv("PORING_MILESTONES_PER_DAY", "0"))   # 0이면 에이전트 수에 맞춰 (한 사람당 한 달 1.5번꼴)
MILESTONE_HOUR = 10
MIN_GAP_DAYS = 14
KINDS = ["승진 심사/인정", "이직 제안/스카우트", "이사/집 문제", "연애 시작", "이별/관계 정리", "새 취미 시작",
         "건강 문제/회복", "가족 소식(결혼/출산/병환/재회)", "큰돈(보너스/빚/투자)", "자격증/공부 성과",
         "반려동물", "목표 달성", "오랜 친구/옛 인연 재회", "큰 실수/위기", "새로운 꿈/진로 고민"]


def _load() -> dict:
    try:
        with open(MILESTONES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(MILESTONES_PATH), exist_ok=True)
    tmp = MILESTONES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MILESTONES_PATH)


def _per_day(n_agents: int) -> int:
    return PER_DAY if PER_DAY > 0 else max(1, math.ceil(n_agents * 1.5 / 30))


def _pick(agents: list[dict], people: dict, now: datetime, k: int) -> list[dict]:
    """마지막 전환점에서 오래된 사람일수록 뽑힐 확률이 높다 (처음이면 가장 오래된 것으로 친다)."""
    rnd = random.Random(f"milestone-{now:%Y-%m-%d}")
    pool = []
    for a in agents:
        last = (people.get(a["name"]) or {}).get("last")
        gap = (now.date() - datetime.strptime(last, "%Y-%m-%d").date()).days if last else 60
        if gap >= MIN_GAP_DAYS:
            pool.append((a, gap))
    picked = []
    while pool and len(picked) < k:
        total = sum(g for _, g in pool)
        x, acc = rnd.uniform(0, total), 0.0
        for i, (a, g) in enumerate(pool):
            acc += g
            if x <= acc:
                picked.append(a)
                pool.pop(i)
                break
    return picked


def _generate(agent: dict, history: list[dict], now: datetime) -> dict | None:
    name = agent["name"]
    st = life.get(name)
    past = "\n".join(f"- {h['date']} [{h['kind']}] {h['title']}" for h in history[-5:]) or "- 없음"
    system = ("너는 가상 세계 인물들의 삶을 굴리는 '운명'이다. 사람의 삶에는 가끔 방향을 바꾸는 큰일이 생긴다. "
              "그 사람의 성격과 처지에 맞게, 그러나 본인도 예상 못 한 일을 하나 일으켜라. 어떻게 풀릴지는 정하지 마라.")
    user = (
        f"오늘: {now:%Y-%m-%d}\n[인물] {name} ({agent.get('company', '')} {agent.get('dept', '')} {agent.get('rank', '')})\n"
        f"{agent.get('profile', '')}\n평소: {agent.get('routine', '')}\n\n"
        f"{life.prompt_block(name)}\n\n{memory.prompt_block(name, k=5)}\n\n"
        f"[지난 전환점 - 같은 종류는 피해라]\n{past}\n\n"
        f"[전환점 종류] {', '.join(KINDS)}\n\n"
        "규칙:\n"
        f"- 요즘 목표({', '.join(st.get('goals') or []) or '없음'})와 이어지면 좋다 - 목표를 이루거나, 크게 막히거나, 목표가 바뀔 계기.\n"
        "- 직급/소속 회사가 실제로 바뀌는 일(발령/퇴사 확정)은 하지 마라 - 제안/심사/면접/소문 단계까지만.\n"
        "- 좋은 일과 힘든 일이 고르게. 너무 비극적이거나 자극적인 일, 실존 인물/정치/혐오 소재는 금지.\n"
        "- public: 회사나 동네 사람들이 알게 될 일이면 true (승진 심사 통과, 결혼 발표 등), 혼자만 아는 일이면 false.\n"
        "반드시 JSON으로만 응답:\n"
        '{"kind": "전환점 종류(위 목록 중 하나)", "title": "짧은 제목", "detail": "실제로 일어난 일 1~2문장(3인칭)", '
        '"public": true/false, "effects": {"happiness|stress|loneliness|anger|romance|confidence|energy|job_satisfaction": -0.2~0.2}, '
        '"goal_done": "이번에 이룬 목표(요즘 목표 중 하나 그대로, 없으면 빈 문자열)", '
        '"new_goals": ["이 일로 새로 생긴 목표(0~2개)"], "memory": "당사자가 오래 기억할 한 줄"}'
    )
    out = llm_json(system, user, temperature=0.95, timeout=90, tag=f"인생 전환점 {name}")
    if not out or not str(out.get("title") or "").strip() or not str(out.get("detail") or "").strip():
        return None
    return out


def tick(agents: list[dict], roster: list[dict], now: datetime | None = None) -> list[tuple[str, str, str]]:
    """[매시 회차] 하루 한 번 몇 명에게 인생 전환점. 반환: 받은편지함에 넣을 [(이름, 글, 종류)] (당사자는 깨운다)."""
    now = now or now_kst()
    today = now.strftime("%Y-%m-%d")
    if not ENABLED or now.hour < MILESTONE_HOUR or not agents:
        return []
    data = _load()
    if data.get("date") == today:
        return []
    data["date"] = today
    people = data.setdefault("people", {})
    _save(data)  # 실패해도 오늘은 다시 안 돌린다 (LLM이 계속 실패할 때 매시 재시도하지 않게)
    deliveries: list[tuple[str, str, str]] = []
    for agent in _pick(agents, people, now, _per_day(len(agents))):
        name = agent["name"]
        rec = people.setdefault(name, {"history": []})
        out = _generate(agent, rec["history"], now)
        if not out:
            continue
        kind = str(out.get("kind") or "").strip()
        kind = kind if kind in KINDS else "전환점"
        title, detail = str(out["title"]).strip()[:60], str(out["detail"]).strip()[:300]
        public = bool(out.get("public"))
        life.apply_changes(name, out.get("effects") if isinstance(out.get("effects"), dict) else {}, reason=title)
        goals = list(life.get(name).get("goals") or [])
        done = str(out.get("goal_done") or "").strip()
        if done in goals:
            goals.remove(done)
        new = [str(g).strip() for g in (out.get("new_goals") or []) if str(g or "").strip()]
        if new or done:
            life.set_goals(name, (new + goals)[:3] or goals)
        memory.remember(name, str(out.get("memory") or f"{title} - {detail}"), 9, [], "인생", permanent=True)
        chronicle.record_event("인생", f"{name}: {title}", detail, [name], private=not public)
        novelty.note("사건", f"{title} - {detail}", [name])
        rec["last"] = today
        rec["history"] = (rec.get("history") or []) + [{"date": today, "kind": kind, "title": title}]
        rec["history"] = rec["history"][-12:]
        deliveries.append((name, f"[내 인생에 큰일이 생겼다] {detail}" + (f" (이룬 목표: {done})" if done else ""), "사건"))
        if public and agent.get("company"):
            for who, text, k in world.inject(f"{name} 소식: {title}", f"{name} - {detail}", [agent["company"]],
                                             agents, roster, kind="인생"):
                if who != name:
                    deliveries.append((who, text, "소문" if k == "소문" else "소식"))
        print(f"[인생] {name}: [{kind}] {title}{' (공개)' if public else ''}")
    _save(data)
    return deliveries
