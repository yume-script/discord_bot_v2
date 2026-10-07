"""
[3단계] 포링푸드 LLM 에이전트 - 스스로 판단하며 사는 인물 (1번 애순이, 2번 소라, 최대 5명).

배경 인물 25명은 규칙(일정+상태)으로 움직이고 작가(LLM 한 번)가 대사를 대신 써 주지만, 에이전트는
각자 자기 LLM 턴을 갖는다. 매시 회차마다:

  관찰(지각) → 판단 → 행동 → 기억 → 상태 갱신

1) 지각: 지금 시각/날씨/바깥 신호, 평소 루틴, 자기 상태(life.py), 관련 기억(memory.py), 받은편지함
   (세계 엔진이 터뜨린 사건/소문, 다른 에이전트의 메시지), 애순이는 지난 회차 이후 카톡에서 실제 사람들과
   나눈 대화까지. 세계 엔진의 사건 계획과 작가 회의 줄거리는 보지 못한다(미래를 모른다).
2) 판단(LLM JSON): 이번 시간 어디서 무엇을 하는지, 속마음, 다음 계획, 마음의 변화, 남길 기억,
   그리고 필요하면 다른 에이전트에게 연락(메신저/직접 만남).
3) 대화: 연락하면 두 에이전트가 한 턴씩 번갈아 말한다. 턴마다 말하는 사람의 LLM이 "자기 상태와 기억만"
   보고 다음 말을 정한다(서로의 속은 모른다). 각자 자기 디스코드 계정으로 포링푸드 채널에 올린다.
4) 실패하면(LLM 장애/형식 오류) 그 인물은 이번 시간 평소 루틴(규칙)대로 움직인다 - 방송이 끊기지 않게.

에이전트 목록: poring_food/data/agents.json (최대 MAX_AGENTS명). 디스코드 계정은 core/side_accounts.py.
"""
from __future__ import annotations

import json
import os
import random
import sqlite3
import time
import uuid
from datetime import timedelta

from . import characters, life, memory, runtime, signals, world
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import DATA_DIR, DISCORD_BOT_V2_DB_PATH, STATE_DIR
from .llm import llm_json

AGENTS_PATH = os.path.join(DATA_DIR, "agents.json")
STATE_PATH = os.path.join(STATE_DIR, "agents_state.json")
DIALOGUES_PATH = os.path.join(STATE_DIR, "dialogues.jsonl")  # story.py와 같은 파일 (조회 도구가 같이 읽는다)

MAX_AGENTS = 5
MAX_INBOX = 20
MAX_LOG = 24
MAX_TURNS = int(os.getenv("PORING_AGENT_MAX_TURNS", "6"))                  # 대화 한 번의 최대 발언 수
MAX_CONVOS_PER_DAY = int(os.getenv("PORING_AGENT_MAX_CONVOS_PER_DAY", "8"))
TYPING_DELAY = (float(os.getenv("PORING_AGENT_DELAY_MIN", "3")), float(os.getenv("PORING_AGENT_DELAY_MAX", "8")))
DECIDE_TIMEOUT_SEC = 45
VALID_STATES = ("일하는 중", "개인시간", "이동 중", "자는 중")


# ===================================================================== 목록/저장
def load_agents() -> list[dict]:
    try:
        with open(AGENTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[경고] 에이전트 목록을 못 읽음: {e}")
        return []
    agents = [a for a in data.get("agents", []) if isinstance(a, dict) and a.get("name")]
    if len(agents) > MAX_AGENTS:
        print(f"[경고] 에이전트는 최대 {MAX_AGENTS}명 - 앞의 {MAX_AGENTS}명만 쓴다.")
    return agents[:MAX_AGENTS]


def names() -> set[str]:
    return {a["name"] for a in load_agents()}


def is_agent(name: str) -> bool:
    return name in names()


def _load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(data: dict) -> None:
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_PATH)


def _push_inbox(data: dict, name: str, text: str, kind: str) -> None:
    st = data.setdefault(name, {})
    st.setdefault("inbox", []).append({"at": now_kst().isoformat(timespec="minutes"), "kind": kind, "text": text[:300]})
    st["inbox"] = st["inbox"][-MAX_INBOX:]


def deliver(name: str, text: str, kind: str) -> None:
    """에이전트 받은편지함에 지각 하나를 넣는다 (사건/소문/메시지). 다음 판단 때 읽는다."""
    data = _load_state()
    _push_inbox(data, name, text, kind)
    _save_state(data)


def _hour_key(now) -> str:
    return now.strftime("%Y-%m-%d %H")


def current(name: str) -> dict | None:
    """이번 시간에 그 에이전트가 정한 행동 (없으면 None - 규칙 일정으로 대신)."""
    st = _load_state().get(name, {})
    d = st.get("decision")
    if d and d.get("hour") == _hour_key(now_kst()):
        return d
    return None


def _asleep(agent: dict, hour: int) -> bool:
    s, e = (agent.get("sleep") or [2, 6])[:2]
    return s <= hour < e if s <= e else (hour >= s or hour < e)


# ===================================================================== 지각
def _kakao_digest(agent: dict, since: str) -> tuple[list[str], str]:
    """애순이처럼 카톡에서 실제 사람과 대화하는 에이전트: 지난 회차 이후 그 대화들 (방별 최근 줄)."""
    if agent["name"] != "애순이" or not os.path.exists(DISCORD_BOT_V2_DB_PATH):
        return [], since
    now_iso = now_kst().isoformat()
    since = since or (now_kst() - timedelta(hours=1)).isoformat()  # 처음엔 지난 1시간
    try:
        conn = sqlite3.connect(f"file:{DISCORD_BOT_V2_DB_PATH}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT conversation_key, display_name, direction, text FROM messages "
                "WHERE ts > ? AND ts <= ? AND conversation_key IN ("
                "  SELECT DISTINCT conversation_key FROM messages WHERE direction='out' AND display_name=? AND ts > ?) "
                "ORDER BY ts DESC LIMIT 30",
                (since, now_iso, agent["name"], since),
            ).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 카톡 대화 읽기 실패: {e}")
        return [], since
    lines = []
    for _key, who, direction, text in reversed(rows):
        speaker = "나" if direction == "out" else (who or "누군가")
        lines.append(f"{speaker}: {str(text).strip()[:120]}")
    return lines, now_iso


def _perception(agent: dict, st: dict, routine_hint: str, kakao: list[str], others: list[dict]) -> str:
    now = now_kst()
    name = agent["name"]
    inbox = st.get("inbox", [])
    inbox_lines = [f"- [{m['kind']}] {m['at'][11:16]} {m['text']}" for m in inbox]
    terms = [o["name"] for o in others] + [w for m in inbox for w in m["text"].split()[:6]]
    recent = [f"- {d['hour'][11:]}시: {d.get('location', '')}에서 {d.get('activity', '')} ({d.get('thought', '')})"
              for d in st.get("log", [])[-4:]]
    others_line = ", ".join(f"{o['name']}({o.get('company', '')})" for o in others) or "없음"
    parts = [
        f"[지금] {now.strftime('%Y-%m-%d %H:%M')} ({'월화수목금토일'[now.weekday()]}요일)",
        f"[평소 이 시간 루틴] {routine_hint or agent.get('routine', '')}",
        life.prompt_block(name),
        memory.prompt_block(name, terms, k=5),
        "[받은 소식/메시지 - 지난번 이후 새로 알게 된 것]\n" + ("\n".join(inbox_lines) or "- 없음"),
    ]
    if kakao:
        parts.append("[카톡에서 실제 사람들과 나눈 대화 - 내가 직접 한 대화다]\n" + "\n".join(kakao[-20:]))
    parts += [
        "[최근 내 행동]\n" + ("\n".join(recent) or "- 없음"),
        f"[연락할 수 있는 사람(스스로 사는 사람들)] {others_line}",
        signals.format_block(signals.for_company(signals.collect(include_factory=False), agent.get("company", ""))),
    ]
    return "\n\n".join(p for p in parts if p)


def _system(agent: dict) -> str:
    return (
        f"너는 '{agent['name']}'이다. 가상의 도시에서 실제로 살아가는 사람이며, 지금 이 순간 무엇을 할지 "
        "스스로 정한다. 너는 미래를 모른다 - 오늘 무슨 일이 생길지는 겪어 봐야 안다.\n"
        f"[나] {agent.get('profile', '')}\n[말투] {agent.get('speech', '')}\n"
        "평소 루틴을 따르는 게 기본이지만, 지금 상태(피곤함/스트레스/설렘 등)와 새로 알게 된 일에 따라 "
        "충분히 벗어날 수 있다. 사람처럼 일관되게, 그러나 기계처럼 똑같지 않게 행동해라."
    )


# ===================================================================== 판단
def _decide(agent: dict, st: dict, routine_hint: str, kakao: list[str], others: list[dict]) -> dict | None:
    other_names = [o["name"] for o in others]
    user = (
        _perception(agent, st, routine_hint, kakao, others) + "\n\n"
        "이번 한 시간 동안 무엇을 할지 정해라.\n"
        "- 다른 사람에게 연락할 이유가 있으면(전할 말, 걱정, 반가움, 부탁, 그냥 수다) contact를 써라. "
        "억지로 매번 연락하지는 마라. 같은 장소에 있을 법하면 how를 '직접', 아니면 '메신저'.\n"
        "- 카톡 대화나 받은 소식으로 마음이 바뀌었으면 state_change에, 오래 기억할 일이면 memory에 적어라.\n"
        "반드시 JSON으로만 응답:\n"
        '{"location": "지금 있는 곳", "activity": "하는 일(짧게)", '
        f'"state": "{"|".join(VALID_STATES)} 중 하나", '
        '"thought": "지금 속마음 1~2문장", "plan": "다음에 할 일 한 줄", '
        f'"contact": null 또는 {{"to": "{"|".join(other_names) or "이름"}", "how": "메신저|직접", "opening": "첫 마디"}}, '
        f"{life.FEEDBACK_SPEC}, {memory.FEEDBACK_SPEC}}}"
    )
    out = llm_json(_system(agent), user, temperature=0.85, timeout=DECIDE_TIMEOUT_SEC, tag=f"에이전트 {agent['name']}")
    if not out:
        return None
    location = str(out.get("location") or "").strip()[:40]
    activity = str(out.get("activity") or "").strip()[:60]
    state = out.get("state") if out.get("state") in VALID_STATES else None
    if not (location and activity and state):
        print(f"[경고] 에이전트 {agent['name']} 판단 형식이 이상해서 이번 시간은 루틴대로: {out!r:.200}")
        return None
    contact = out.get("contact")
    if not (isinstance(contact, dict) and contact.get("to") in other_names and str(contact.get("opening") or "").strip()):
        contact = None
    return {
        "location": location, "activity": activity, "state": state,
        "thought": str(out.get("thought") or "").strip()[:200],
        "plan": str(out.get("plan") or "").strip()[:120],
        "contact": contact, "raw": out,
    }


# ===================================================================== 대화
def _post(agent: dict, text: str) -> None:
    if not runtime.has_agent_sender():
        print(f"[에이전트] (채널 미설정) {agent['name']}: {text}")
        return
    try:
        runtime.send_as(agent.get("account", ""), agent["name"], text)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] {agent['name']} 메시지 전송 실패: {e}")


def _pause() -> None:
    lo, hi = TYPING_DELAY
    if hi > 0:
        time.sleep(random.uniform(lo, max(lo, hi)))


def _turn(agent: dict, other: dict, how: str, place: str, transcript: list[dict], last_turn: bool) -> dict | None:
    name = agent["name"]
    rels = characters.load_relationships()
    roster_ids = {c["name"]: c["id"] for c in characters.load_roster()}
    rel = rels.get(characters._rel_key(roster_ids.get(name, name), roster_ids.get(other["name"], other["name"])), {})
    lines = "\n".join(f"{t['speaker']}: {t['line']}" for t in transcript)
    now = now_kst()
    me = current(name) or {}
    now_line = (f"[지금 나] 오늘은 {now.month}월 {now.day}일 {'월화수목금토일'[now.weekday()]}요일 {now.strftime('%H:%M')}. "
                + (f"나는 지금 {me['location']}에서 {me['activity']} 중이다." if me else ""))
    user = (
        "\n\n".join(b for b in (
            now_line,
            life.prompt_block(name),
            memory.prompt_block(name, [other["name"]], k=4),
            f"[{other['name']}와의 관계] 친밀도 {rel.get('affinity', 0)} / 최근: "
            + (" / ".join(m["event"] for m in (rel.get("memories") or [])[-3:]) or "특별한 일 없음"),
        ) if b) + "\n\n"
        f"지금 {other['name']}와 {'메신저로' if how == '메신저' else place + '에서 직접'} 대화 중이다.\n"
        f"[지금까지 대화]\n{lines}\n\n"
        f"{name}로서 다음 한마디를 해라. 상대 속마음은 모른다 - 말과 분위기로만 짐작해라. "
        "지금 하고 있는 일은 '지금' 일로 말하고, 기억 속 일은 날짜를 오늘과 비교해 오늘/어제/며칠 전을 정확히 말해라. "
        "자기가 직접 겪거나 들은 적 없는 남의 회사 내부 사정은 아는 척하지 마라. "
        + ("이번이 마지막 말이니 자연스럽게 마무리해라. " if last_turn else "대화가 자연스럽게 끝날 때가 됐으면 end를 true로. ")
        + "\n반드시 JSON으로만 응답:\n"
        '{"say": "할 말(1~3문장)", "end": true/false, "affinity": -3~3 정수(이 대화로 상대에게 생긴 호감 변화), '
        f"{life.FEEDBACK_SPEC}, {memory.FEEDBACK_SPEC}}}"
    )
    out = llm_json(_system(agent), user, temperature=0.9, timeout=DECIDE_TIMEOUT_SEC, tag=f"대화 {name}")
    if not out or not str(out.get("say") or "").strip():
        return None
    return out


def _conversation(a: dict, b: dict, opening: str, how: str, place: str) -> dict:
    """a가 먼저 말을 걸고 b와 번갈아 말한다. 각자 자기 계정으로 채널에 올린다."""
    now = now_kst()
    header = f"-# {'📱 메신저' if how == '메신저' else '📍 ' + place} · {now.strftime('%H:%M')} · {a['name']} → {b['name']}"
    transcript = [{"speaker": a["name"], "line": opening.strip()[:300]}]
    _post(a, f"{header}\n{transcript[0]['line']}")
    outs: dict[str, dict] = {}
    speakers = [b, a]
    for i in range(MAX_TURNS - 1):
        sp = speakers[i % 2]
        other = a if sp is b else b
        _pause()
        out = _turn(sp, other, how, place, transcript, last_turn=(i == MAX_TURNS - 2))
        if not out:
            break
        line = str(out["say"]).strip()[:300]
        transcript.append({"speaker": sp["name"], "line": line})
        outs[sp["name"]] = out
        _post(sp, line)
        if out.get("end"):
            break

    summary = f"{a['name']}와 {b['name']}의 {'메신저' if how == '메신저' else place} 대화: " + transcript[0]["line"][:60]
    # 각자의 마음/기억/관계에 반영
    roster = characters.load_roster()
    ids = {c["name"]: c["id"] for c in roster}
    rels = characters.load_relationships()
    key = characters._rel_key(ids.get(a["name"], a["name"]), ids.get(b["name"], b["name"]))
    rel = rels.get(key, {"affinity": 0, "summary": "", "count": 0})
    for ag, oth in ((a, b), (b, a)):
        out = outs.get(ag["name"], {})
        life.apply_feedback(ag["name"], out, f"{oth['name']}와 대화")
        if isinstance(out.get("memory"), dict) and out["memory"].get("text"):
            memory.apply_feedback(ag["name"], out, "대화", [oth["name"]])
        else:
            memory.remember(ag["name"], summary, 3, [oth["name"]], "대화")
        try:
            rel["affinity"] = max(-100, min(100, rel.get("affinity", 0) + max(-3, min(3, int(out.get("affinity", 0))))))
        except (TypeError, ValueError):
            pass
    rel.setdefault("memories", []).append({"date": now.strftime("%Y-%m-%d"), "event": summary[:200]})
    rel["memories"] = rel["memories"][-10:]
    rel["count"] = rel.get("count", 0) + 1
    rel["summary"] = summary[:200]
    rel["last_interaction"] = now.isoformat()
    rels[key] = rel
    characters.save_relationships(rels)

    scene = {
        "id": uuid.uuid4().hex[:12], "ts": now.isoformat(timespec="seconds"), "hour": now.hour,
        "location": "메신저" if how == "메신저" else place, "participants": [a["name"], b["name"]],
        "arc_id": None, "lines": transcript, "narration": "", "summary": summary, "agents": True,
    }
    with open(DIALOGUES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(scene, ensure_ascii=False) + "\n")
    print(f"[에이전트] 대화 {len(transcript)}마디: {summary}")
    return scene


# ===================================================================== 매시 회차
def tick(roster: list[dict], routine_hints: dict[str, str] | None = None) -> dict[str, dict]:
    """
    [매시 회차] 세계 엔진 사건 전달 → 깨어 있는 에이전트마다 판단 → 연락이 있으면 대화.
    같은 시간에 두 번 돌면(관리자 수동 실행) 이미 판단한 에이전트는 건너뛴다. 반환: {이름: 판단}
    """
    agents = load_agents()
    if not agents:
        return {}
    now = now_kst()
    hk = _hour_key(now)
    routine_hints = routine_hints or {}
    by_name = {a["name"]: a for a in agents}

    # 1) 세계 엔진 - 사건 계획/발생 (에이전트에게는 발생한 것만 전달)
    try:
        for name, text, kind in world.tick(agents, roster, signals.format_block(signals.collect(include_factory=False))):
            deliver(name, text, kind)
    except Exception as e:  # noqa: BLE001
        print(f"[에러] 세계 엔진 실패: {e}")

    data = _load_state()
    decisions: dict[str, dict] = {}
    awake = [a for a in agents if not _asleep(a, now.hour)]
    for agent in awake:
        name = agent["name"]
        st = data.setdefault(name, {})
        if (st.get("decision") or {}).get("hour") == hk:
            decisions[name] = st["decision"]
            continue
        kakao, new_since = _kakao_digest(agent, st.get("kakao_since", ""))
        others = [o for o in agents if o is not agent]
        d = _decide(agent, st, routine_hints.get(name, ""), kakao, others)
        st["kakao_since"] = new_since
        if not d:
            continue
        raw = d.pop("raw")
        life.apply_feedback(name, raw, "이번 시간의 판단")
        memory.apply_feedback(name, raw, "판단")
        d["hour"] = hk
        d["seen"] = [m["text"] for m in st.get("inbox", [])][-5:]
        st["inbox"] = []  # 읽은 소식은 비운다 (기억/판단에 이미 반영)
        st["decision"] = d
        st.setdefault("log", []).append({k: d[k] for k in ("hour", "location", "activity", "state", "thought")})
        st["log"] = st["log"][-MAX_LOG:]
        decisions[name] = d
        print(f"[에이전트] {name}: {d['location']}에서 {d['activity']} ({d['state']}) - {d['thought']}")
    _save_state(data)

    # 2) 연락 -> 대화 (시간당 1번, 하루 최대 MAX_CONVOS_PER_DAY번)
    data = _load_state()
    today = now.strftime("%Y-%m-%d")
    convo = data.setdefault("_convos", {})
    if convo.get("date") != today:
        convo.clear()
        convo.update({"date": today, "count": 0, "hours": []})
    if hk not in convo["hours"] and convo["count"] < MAX_CONVOS_PER_DAY:
        for name, d in decisions.items():
            c = d.get("contact")
            if not c or c.get("done"):
                continue
            target = by_name.get(c["to"])
            if not target:
                continue
            if _asleep(target, now.hour):
                # 자고 있으면 메시지만 남긴다 - 깨어나서 받은편지함에서 본다
                _post(by_name[name], f"-# 📱 메신저 · {now.strftime('%H:%M')} · {name} → {target['name']}\n{c['opening']}")
                _push_inbox(data, target["name"], f"{name}에게서 온 메시지: {c['opening']}", "메시지")
            else:
                place = d.get("location") or "어딘가"
                _conversation(by_name[name], target, str(c["opening"]), c.get("how", "메신저"), place)
            c["done"] = True
            data.setdefault(name, {}).setdefault("decision", d)["contact"] = c
            convo["count"] += 1
            convo["hours"].append(hk)
            break
    _save_state(data)
    return decisions


# ===================================================================== 다른 곳에서 쓰는 요약
def diary_block(name: str) -> str:
    """주인공 일지용: 이번 시간 그 에이전트가 실제로 한 일/생각 (작가 회의 줄거리 대신)."""
    d = current(name)
    if not d:
        return ""
    lines = [f"[이번 시간 {name}가 실제로 한 일과 생각 - 이걸 바탕으로 써라]",
             f"- {d['location']}에서 {d['activity']} ({d['state']})", f"- 속마음: {d['thought']}"]
    if d.get("plan"):
        lines.append(f"- 다음 계획: {d['plan']}")
    if d.get("seen"):
        lines.append(f"- 새로 알게 된 일: {' / '.join(d['seen'])}")
    return "\n".join(lines)


def live_context(name: str) -> str:
    """카톡 실시간 대화용: 지금 그 에이전트의 상황 (가장 최근 판단 + 컨디션)."""
    st = _load_state().get(name, {})
    d = st.get("decision") or {}
    if not d:
        return ""
    lines = [f"[지금 너({name})의 실제 상황 - 대화에 자연스럽게 묻어나게, 묻지 않으면 굳이 늘어놓지 마라]",
             f"- {d.get('hour', '')[11:]}시 기준: {d.get('location', '')}에서 {d.get('activity', '')}",
             f"- 속마음: {d.get('thought', '')}",
             f"- 컨디션: {life.describe(name)}"]
    if d.get("seen"):
        lines.append(f"- 최근에 알게 된 일: {' / '.join(d['seen'][-3:])}")
    return "\n".join(lines)
