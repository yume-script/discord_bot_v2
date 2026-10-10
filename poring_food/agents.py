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
from datetime import datetime, timedelta

from . import characters, chronicle, life, memory, metrics, projects, rotation, runtime, signals, world
from .clock import now_kst
from .josa import j
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import DATA_DIR, DISCORD_BOT_V2_DB_PATH, STATE_DIR
from .llm import llm_json
from config import settings as bot_settings

AGENTS_PATH = os.path.join(DATA_DIR, "agents.json")
STATE_PATH = os.path.join(STATE_DIR, "agents_state.json")
DIALOGUES_PATH = os.path.join(STATE_DIR, "dialogues.jsonl")  # story.py와 같은 파일 (조회 도구가 같이 읽는다)

MAX_AGENTS = int(os.getenv("PORING_AGENT_MAX", "50"))
MAX_INBOX = 20
MAX_LOG = 24
MAX_TURNS = int(os.getenv("PORING_AGENT_MAX_TURNS", "6"))                  # 대화 한 번의 최대 발언 수
MAX_CONVOS_PER_DAY = int(os.getenv("PORING_AGENT_MAX_CONVOS_PER_DAY", "12"))
STATUS_DIGEST = os.getenv("PORING_AGENT_STATUS_DIGEST", "1") not in ("0", "false", "False", "")
TYPING_DELAY = (float(os.getenv("PORING_AGENT_DELAY_MIN", "3")), float(os.getenv("PORING_AGENT_DELAY_MAX", "8")))
DECIDE_TIMEOUT_SEC = 45
VALID_STATES = ("일하는 중", "개인시간", "이동 중", "자는 중")

# 단톡방 - 멤버만 보고 쓴다 (모두가 보는 곳이라 비밀 얘기는 안 쓴다)
#  - 포링푸드 단톡방: 포링푸드 소속 에이전트 전원 (기존 포링푸드 이야기 채널)
#  - 동네 단톡방: agents.json에서 "groups"에 넣은 사람 (에이전트 대화 채널에 머리줄을 붙여 같이 올린다)
GROUP_COMPANY = "포링푸드 (Poring Food)"
GROUP_NAME = "포링푸드 단톡방"
TOWN_GROUP = "동네 단톡방"
GROUP_HEADERS = {TOWN_GROUP: "-# 🏘️ 동네 단톡방"}  # 다른 글과 같은 채널을 쓰는 단톡방은 머리줄로 구분
GROUP_PATH = os.path.join(STATE_DIR, "group_chat.jsonl")
GROUP_MAX_POSTS_PER_DAY = int(os.getenv("PORING_GROUP_MAX_POSTS_PER_DAY", "10"))
GROUP_MAX_POSTS_PER_HOUR = 2
GROUP_RECENT = 10

# 엿듣기/소문 - 대화가 끝나면 주변 사람에게 확률적으로 퍼진다 (추가 LLM 호출 없음)
GOSSIP_MAX_RECIPIENTS = 2
GOSSIP_P_SAME_PLACE = 0.6     # 직접 만난 대화를 같은 곳에 있던 사람이 엿들음
GOSSIP_P_COLLEAGUE = 0.25     # 같은 회사 동료에게 말이 돎
GOSSIP_P_REGULAR = 0.3       # 같은 단골 가게(카페/포장마차 등)를 쓰는 사이 - 사장/단골끼리 말이 돈다
GOSSIP_P_OTHER = 0.05
# 담당 업무(agents.json "duty") - 실제 서비스 수치(metrics.part)가 그 사람의 실적이다
DUTY_BIG_RATIO = 2.0      # 오늘 입고가 평소 하루 평균의 이만큼 이상이면 회사 사건 (하루 한 번)
DUTY_BIG_MIN = 10         # 평균이 작을 때 몇 건만으로 "대량"이 되지 않게
DUTY_SLOW_FROM_HOUR = 18  # 이 시각 이후에도 한산하면 담당자가 신경 쓴다 (하루 한 번)
GOSSIP_MESSENGER_FACTOR = 0.4  # 메신저 대화는 잘 안 샌다


# ===================================================================== 목록/저장
def _base_agents() -> list[dict]:
    """agents.json 그대로 (교체 반영 전)."""
    try:
        with open(AGENTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return [a for a in data.get("agents", []) if isinstance(a, dict) and a.get("name")]


def _rotate(now) -> None:
    """[하루 한 번] 소속별로 에이전트 한 명 <-> 배경 인물 한 명 교체, 채널/연대기/당사자 기억에 남긴다."""
    swaps = rotation.tick(_base_agents(), MAX_AGENTS)
    if not swaps:
        return
    lines = [f"-# 🔄 {now.month}/{now.day} 오늘의 얼굴들"]
    for company, out_name, in_name in swaps:
        label = "동네" if company == rotation.TOWN_COMPANY else company.split(" ")[0]
        lines.append(f"• {label}: **{in_name}** 씨가 나서고, {out_name} 씨는 한동안 조용히 지내요")
        chronicle.record_event("교체", f"{in_name} 등장 / {out_name} 휴식", company, [in_name, out_name])
        memory.remember(in_name, "요즘 들어 동네 일과 사람들에게 마음이 더 쓰인다. 내 하루를 내가 정해 보기로 했다.", 4, [], "교체")
    _post_as("동네 소식", "\n".join(lines))


def load_agents() -> list[dict]:
    try:
        with open(AGENTS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[경고] 에이전트 목록을 못 읽음: {e}")
        return []
    agents = [a for a in data.get("agents", []) if isinstance(a, dict) and a.get("name")]
    agents = rotation.apply(agents)  # 하루 한 번 배경 인물과 자리를 바꾼 것 반영
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


def _push_inbox(data: dict, name: str, text: str, kind: str, urgent: bool = False, quiet: bool = False) -> None:
    st = data.setdefault(name, {})
    item = {"at": now_kst().isoformat(timespec="minutes"), "kind": kind, "text": text[:300]}
    if urgent:
        item["urgent"] = True  # 이번 시간에 이미 판단했어도 다시 판단한다 (관리자 사건/메시지)
    if quiet:
        item["quiet"] = True  # 다음 판단 때 읽기만 한다 - 이것 때문에 깨우지는 않는다 (담당 실적 갱신)
    st.setdefault("inbox", []).append(item)
    st["inbox"] = st["inbox"][-MAX_INBOX:]


def deliver(name: str, text: str, kind: str, urgent: bool = False, quiet: bool = False) -> None:
    """에이전트 받은편지함에 지각 하나를 넣는다 (사건/소문/메시지). 다음 판단 때 읽는다."""
    data = _load_state()
    _push_inbox(data, name, text, kind, urgent, quiet)
    _save_state(data)


def send_message(to: str, text: str, sender: str = "") -> bool:
    """[관리자 귓속말] 에이전트 받은편지함에 메시지를 넣는다. 보낸 사람 이름을 비우면 '누군가'.
    다음 판단(관리자가 바로 반영을 고르면 지금)에 읽고 반응한다. 없는 이름이면 False."""
    if not is_agent(to):
        return False
    who = (sender or "").strip() or "누군가"
    deliver(to, f"{who}에게서 온 메시지: {text.strip()}", "메시지", urgent=True)
    print(f"[에이전트] 관리자 메시지: {to} <- {who}: {text.strip()}")
    return True


def inject_event(target_list: list[str], detail: str, title: str = "") -> list[str]:
    """[관리자 사건] 세계 엔진에 지금 사건 하나를 넣고 당사자 받은편지함에 바로 전달한다.
    반환: 전달된 에이전트 이름 (대상이 회사/동네면 소속 에이전트 전부, 배경 인물은 장면/일지로 안다)."""
    out = world.inject(title or detail, detail, target_list, load_agents(), characters.load_roster())
    for name, text, kind in out:
        deliver(name, text, kind, urgent=True)
    if out and len(out) > 1:
        # 여러 사람에게 닿은 일은 끝날 때까지 "아직 끝나지 않은 일"로 모두의 지각에 남긴다 (날짜가 바뀌어도)
        chronicle.open_thread(title or detail[:40], detail)
    return [name for name, _, _ in out]


def target_names() -> list[str]:
    return world.targets(load_agents(), characters.load_roster())


def is_asleep_now(name: str) -> bool:
    agent = next((a for a in load_agents() if a["name"] == name), None)
    return bool(agent) and _asleep(agent, now_kst().hour)


def run_agents_only() -> None:
    """[관리자 바로 반영] 일지/방송 없이 에이전트 회차만 돈다 (급한 소식을 받은 사람만 다시 판단)."""
    from . import processor  # 일지 쪽 모듈 - 에이전트 회차만 돌 때만 필요
    location, activity, _focus, state, _sleeping = processor.get_aesun_detailed_schedule()
    tick(characters.load_roster(), {"애순이": f"{location}에서 {activity} ({state})"})


def _hour_key(now) -> str:
    return now.strftime("%Y-%m-%d %H")


def current(name: str) -> dict | None:
    """이번 시간에 그 에이전트가 정한 행동 (없으면 None - 규칙 일정으로 대신)."""
    st = _load_state().get(name, {})
    d = st.get("decision")
    if d and d.get("hour") == _hour_key(now_kst()):
        return d
    return None


def latest(name: str, max_age_h: int = 1) -> dict | None:
    """가장 최근 판단이 max_age_h시간 이내면 그걸 (몇 시간마다 판단하는 에이전트는 그사이에도 그 행동을 이어간다)."""
    d = _load_state().get(name, {}).get("decision")
    if not d:
        return None
    try:
        age = (now_kst() - datetime.strptime(d["hour"], "%Y-%m-%d %H")).total_seconds() / 3600
    except (KeyError, ValueError):
        return None
    return d if age < max_age_h else None


def active(name: str) -> dict | None:
    """지금 유효한 그 에이전트의 행동 (이번 시간 판단, 또는 판단 주기 안의 최근 판단)."""
    agent = next((a for a in load_agents() if a["name"] == name), None)
    return latest(name, _every(agent)) if agent else None


def _every(agent: dict) -> int:
    try:
        return max(1, int(agent.get("decide_every") or 1))
    except (TypeError, ValueError):
        return 1


def _due(agent: dict, hour: int) -> bool:
    """이번 시간이 판단할 차례인가. 사람마다 시각을 흩어 놓는다(모두 같은 시간에 몰리지 않게)."""
    every = _every(agent)
    return every == 1 or (hour + sum(map(ord, agent["name"]))) % every == 0


def _asleep(agent: dict, hour: int) -> bool:
    s, e = (agent.get("sleep") or [2, 6])[:2]
    return s <= hour < e if s <= e else (hour >= s or hour < e)


# ===================================================================== 단톡방 (회사 / 동네)
def _group_channel(group: str) -> int:
    if group == GROUP_NAME:
        return bot_settings.PORING_GROUP_CHAT_CHANNEL_ID
    if group == TOWN_GROUP:
        return bot_settings.PORING_TOWN_CHAT_CHANNEL_ID or bot_settings.PORING_AGENT_CHANNEL_ID
    return 0


def _groups_of(agent: dict) -> list[str]:
    groups = [GROUP_NAME] if agent.get("company") == GROUP_COMPANY else []
    groups += [g for g in (agent.get("groups") or []) if g not in groups]
    return [g for g in groups if _group_channel(g)]


def _has_group(agent: dict) -> bool:
    return bool(_groups_of(agent))


def _group_recent(group: str, hours: int = 24, limit: int = GROUP_RECENT) -> list[dict]:
    if not os.path.exists(GROUP_PATH):
        return []
    since = (now_kst() - timedelta(hours=hours)).isoformat()
    out = []
    with open(GROUP_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if m.get("at", "") >= since and m.get("group", GROUP_NAME) == group:
                out.append(m)
    return out[-limit:]


def _group_block(agent: dict, st: dict) -> str:
    last_seen = (st.get("decision") or {}).get("hour", "")
    blocks = []
    for group in _groups_of(agent):
        who = "회사 사람들" if group == GROUP_NAME else "동네 사람들"
        lines = []
        for m in _group_recent(group):
            new = " (새 글)" if m["at"][:13].replace("T", " ") >= last_seen and m["name"] != agent["name"] else ""
            lines.append(f"- {m['at'][5:16].replace('T', ' ')} {m['name']}: {m['text']}{new}")
        blocks.append(f"[{group} 최근 글 - {who}이 다 보는 곳]\n" + ("\n".join(lines) or "- 조용함"))
    return "\n\n".join(blocks)


def _post_group(agent: dict, group: str, text: str, data: dict, agents: list[dict]) -> None:
    """단톡방에 올리고 기록한다. 이름이 불린 멤버는 받은편지함으로 깨운다."""
    now = now_kst()
    header = GROUP_HEADERS.get(group)
    try:
        runtime.send_as(agent.get("account", ""), agent["name"], f"{header}\n{text}" if header else text,
                        agent.get("avatar_url", ""), channel_id=_group_channel(group))
    except Exception as e:  # noqa: BLE001
        print(f"[경고] {agent['name']} {group} 전송 실패: {e}")
        return
    with open(GROUP_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now.isoformat(timespec="minutes"), "group": group, "name": agent["name"],
                            "text": text}, ensure_ascii=False) + "\n")
    for other in agents:
        if other is not agent and group in _groups_of(other) and other["name"] in text:
            _push_inbox(data, other["name"], f"{group}에서 {j(agent['name'], '가')} 나를 언급함: {text}", "단톡방")
    print(f"[에이전트] {group} {agent['name']}: {text}")


# ===================================================================== 담당 업무 (실제 서비스 수치)
def _duty_text(x: dict) -> str:
    line = f"오늘 입고 {x['today']}건{'+' if x.get('saturated') else ''}"
    extra = [f"최근 7일 하루 평균 {x['avg']:g}건" if x.get("avg") is not None else "", x.get("pace", "")]
    extra = [e for e in extra if e]
    return line + (f" ({', '.join(extra)})" if extra else "")


def _duty_block(agent: dict) -> str:
    duty = agent.get("duty") or {}
    if not duty.get("metric"):
        return ""
    x = metrics.part(duty["metric"])
    body = _duty_text(x) if x else "오늘 집계 전"
    return (f"[내 담당 업무 - {duty.get('line', duty['metric'])}] {body}\n"
            "이게 내 실적이다. 회사 사람들도 이 숫자를 안다.")


def _duty_updates(agents: list[dict], data: dict, now) -> None:
    """
    담당 라인 수치가 바뀌면 담당자 받은편지함에 알린다 (깨우지는 않음). 평소의 몇 배가 들어온 날은
    회사 사건으로 터뜨리고(하루 한 번, 동료들도 앎), 저녁까지 한산하면 담당자에게만 알린다(하루 한 번).
    LLM 호출 없음 - 수치는 metrics.refresh()가 회차 맨 앞에서 MCP로 받아 둔 값.
    """
    today = now.strftime("%Y-%m-%d")
    for agent in agents:
        duty = agent.get("duty") or {}
        x = metrics.part(duty.get("metric", "")) if duty.get("metric") else None
        if not x:
            continue
        name, line = agent["name"], duty.get("line", duty["metric"])
        st = data.setdefault(name, {})
        ds = st.get("duty_seen") or {}
        if ds.get("date") != today:
            ds = {"date": today, "seen": 0, "big": False, "slow": False}
        n, avg = int(x["today"]), x.get("avg")
        if n > ds["seen"]:
            _push_inbox(data, name, f"담당 {line}에 새로 {n - ds['seen']}건 입고 - {_duty_text(x)}", "업무", quiet=True)
            ds["seen"] = n
        big = x.get("saturated") or (avg and n >= max(DUTY_BIG_MIN, avg * DUTY_BIG_RATIO))
        if big and not ds["big"]:
            ds["big"] = True
            company = agent.get("company", "")
            detail = (f"{company.split(' ')[0]} {line}에 오늘 {n}건{'+' if x.get('saturated') else ''}이 한꺼번에 들어왔다"
                      + (f" (평소 하루 {avg:g}건)" if avg else "") + f". 담당 {name}의 일이 몰렸다.")
            out = world.inject(f"{line} 대량 입고", detail, [company] if company else [name], agents,
                               characters.load_roster(), effects={"stress": 0.05}, kind="업무")
            if name not in {w for w, _, _ in out}:
                out.append((name, detail, "사건"))
            for who, text, kind in out:
                _push_inbox(data, who, text, kind)
            print(f"[에이전트] 담당 업무 사건: {name} {line} {n}건")
        elif (now.hour >= DUTY_SLOW_FROM_HOUR and not ds["slow"] and x.get("pace") == "평소보다 한산함"):
            ds["slow"] = True
            _push_inbox(data, name, f"담당 {line}가 오늘 한산하다 - {_duty_text(x)}", "업무")
        st["duty_seen"] = ds


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
        _duty_block(agent),
        memory.prompt_block(name, terms, k=5),
        "[받은 소식/메시지 - 지난번 이후 새로 알게 된 것]\n" + ("\n".join(inbox_lines) or "- 없음"),
    ]
    if kakao:
        parts.append("[카톡에서 실제 사람들과 나눈 대화 - 내가 직접 한 대화다]\n" + "\n".join(kakao[-20:]))
    parts.append(_group_block(agent, st))
    parts.append(projects.prompt_block(name))
    props = [t for t in chronicle.pending_proposals() if agent["name"] in t["proposal"]["members"]]
    if props:
        parts.append("[모임 제안 - 내가 낄 수 있는 모임]\n" + "\n".join(
            f"- {t['proposal']['by']}: '{t['title']}' - {t['proposal']['why']} (오늘 {t['proposal']['hour']}시, "
            f"찬성: {', '.join(t['proposal']['supporters'])}"
            + (" - 나도 찬성함" if agent["name"] in t["proposal"]["supporters"] else "") + ")" for t in props))
    parts += [
        chronicle.prompt_block(),
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
def _parse_group_post(raw, groups: list[str]) -> dict | None:
    """{"group", "text"} 또는 그냥 글(첫 단톡방으로). 멤버가 아닌 단톡방이면 버린다."""
    if not groups or not raw:
        return None
    if isinstance(raw, str):
        raw = {"group": groups[0], "text": raw}
    if not isinstance(raw, dict):
        return None
    group = raw.get("group") if raw.get("group") in groups else groups[0]
    text = str(raw.get("text") or "").strip()[:300]
    return {"group": group, "text": text} if text else None


def _decide(agent: dict, st: dict, routine_hint: str, kakao: list[str], others: list[dict]) -> dict | None:
    other_names = [o["name"] for o in others]
    groups = _groups_of(agent)
    group_rule = (
        f"- 단톡방({', '.join(groups)})은 멤버들이 다 보는 곳이다. 공지/질문/가벼운 잡담/누가 부른 것에 대한 답이 있으면 "
        "group_post에 써라. 개인적인 비밀이나 남의 험담은 쓰지 마라(다 본다). 할 말이 없으면 null - 매번 쓰지 마라.\n"
        if groups else ""
    )
    group_spec = (f', "group_post": null 또는 {{"group": "{"|".join(groups)}", "text": "단톡방에 올릴 말(1~2문장)"}}'
                  if groups else "")
    scopes = _meeting_scopes(agent)
    pending = [t for t in chronicle.pending_proposals() if agent["name"] in t["proposal"]["members"]
               and agent["name"] not in t["proposal"]["supporters"]]
    meeting_rule = (
        "- 여러 사람이 모여서 정해야 할 공동의 일(아직 끝나지 않은 일, 의견이 갈리는 문제, 동네/회사에 닥친 일)이 있고 "
        "내가 나설 만한 성격/처지라면 meeting으로 모임을 제안할 수 있다. 정말 필요할 때만 - 대부분은 null.\n"
        + ("- 누가 제안한 모임에 참석/찬성하고 싶으면 meeting_support에 그 주제를 그대로 써라 (내키지 않으면 null).\n"
           if pending else "")
    ) if scopes else ""
    if TOWN_GROUP in (agent.get("groups") or []):
        meeting_rule += (
            "- 나도 이 동네 주민이다. 동네를 더 살기 좋은 곳으로 만들 작은 일(이웃 챙기기, 불편한 점 고치기, 같이 할 행사나 "
            "골목 가꾸기 아이디어, 이미 정해진 일 실천하기)이 떠오르면 행동/단톡방 글/연락/모임 제안으로 옮겨도 된다. 억지로는 말고.\n")
    if agent.get("role") == "통장":
        meeting_rule += (
            "- 나는 통장이다. 아직 끝나지 않은 동네 일이나 주민들 사이에서 나온 불편/아이디어를 챙겨서, 필요하면 모임을 열어 "
            "의견을 모으고, 정해진 일은 실제로 되도록 챙긴다.\n")
    my_projects = [p for p in projects.active() if not p.get("members") or agent["name"] in p["members"]]
    if my_projects:
        meeting_rule += ("- 진행 중인 마을 프로젝트에 이번 시간 실제로 손을 보탰으면(맡은 일, 도울 일, 지나가다 거든 일) "
                         "project_work에 써라. 안 했으면 null - 지어내지 마라.\n")
    meeting_spec = (
        (f', "meeting": null 또는 {{"topic": "모임 주제", "why": "왜 모여야 하는지 한 줄", '
         f'"scope": "{"|".join(scopes)}", "hour": 오늘 모일 시각(정수, 보통 저녁 19~21)}}')
        + (', "meeting_support": null 또는 "찬성하는 모임 주제"' if pending else "")
    ) if scopes else ""
    if my_projects:
        meeting_spec += (f', "project_work": null 또는 {{"project": "{"|".join(p["title"] for p in my_projects)}", '
                         '"did": "이번 시간에 실제로 한 일 한 줄"}')
    user = (
        _perception(agent, st, routine_hint, kakao, others) + "\n\n"
        "이번 한 시간 동안 무엇을 할지 정해라.\n"
        "- 다른 사람에게 연락할 이유가 있으면(전할 말, 걱정, 반가움, 부탁, 그냥 수다) contact를 써라. "
        "억지로 매번 연락하지는 마라. 같은 장소에 있을 법하면 how를 '직접', 아니면 '메신저'.\n"
        "- 카톡 대화나 받은 소식으로 마음이 바뀌었으면 state_change에, 오래 기억할 일이면 memory에 적어라.\n"
        + group_rule + meeting_rule +
        "반드시 JSON으로만 응답:\n"
        '{"location": "지금 있는 곳", "activity": "하는 일(짧게)", '
        f'"state": "{"|".join(VALID_STATES)} 중 하나", '
        '"thought": "지금 속마음 1~2문장", "plan": "다음에 할 일 한 줄", '
        f'"contact": null 또는 {{"to": "{"|".join(other_names) or "이름"}", "how": "메신저|직접", "opening": "첫 마디"}}{group_spec}{meeting_spec}, '
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
        "group_post": _parse_group_post(out.get("group_post"), groups),
        "meeting": _parse_meeting(out.get("meeting"), scopes),
        "meeting_support": str(out.get("meeting_support") or "").strip()[:80] if pending else "",
        "project_work": _parse_project_work(out.get("project_work"), my_projects),
    }


def _parse_project_work(raw, my_projects: list[dict]) -> dict | None:
    if not isinstance(raw, dict) or not my_projects:
        return None
    did = str(raw.get("did") or "").strip()[:150]
    title = str(raw.get("project") or "").strip()
    if not did or not any(projects._same(p["title"], title) for p in my_projects):
        return None
    return {"project": title, "did": did}


def _parse_meeting(raw, scopes: list[str]) -> dict | None:
    if not isinstance(raw, dict) or not scopes:
        return None
    topic = str(raw.get("topic") or "").strip()[:60]
    if not topic:
        return None
    try:
        hour = int(raw.get("hour"))
    except (TypeError, ValueError):
        hour = MEETING_AUTO_HOUR
    scope = raw.get("scope") if raw.get("scope") in scopes else scopes[0]
    return {"topic": topic, "why": str(raw.get("why") or "").strip()[:120], "scope": scope,
            "hour": max(9, min(22, hour))}


# ===================================================================== 대화
def _post(agent: dict, text: str) -> None:
    if not runtime.has_agent_sender():
        print(f"[에이전트] (채널 미설정) {agent['name']}: {text}")
        return
    try:
        runtime.send_as(agent.get("account", ""), agent["name"], text, agent.get("avatar_url", ""))
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
    me = latest(name, _every(agent) + 1) or {}
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
        f"지금 {j(other['name'], '와')} {'메신저로' if how == '메신저' else place + '에서 직접'} 대화 중이다.\n"
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


def _same_place(x: str, y: str) -> bool:
    x, y = (x or "").replace(" ", ""), (y or "").replace(" ", "")
    return bool(x and y) and (x in y or y in x)


def _shared_hangout(other: dict, a: dict, b: dict, place: str) -> str:
    """other와 대화 당사자가 같이 드나드는 단골 가게(또는 대화 장소가 other의 단골)면 그 이름."""
    mine = other.get("hangouts") or []
    for h in mine:
        if _same_place(h, place) or h in (a.get("hangouts") or []) or h in (b.get("hangouts") or []):
            return h
    return ""


def _gossip(a: dict, b: dict, how: str, place: str, transcript: list[dict], outs: dict) -> list[tuple[str, str]]:
    """
    [엿듣기/소문] 대화가 끝나면 주변에 확률적으로 샌다. 직접 만난 대화를 같은 곳에 있던 사람이 엿듣거나,
    같은 회사 동료에게 말이 돈다. 메신저 대화는 잘 안 새고, 중요한 얘기일수록 잘 퍼진다. (추가 LLM 호출 없음)
    반환: [(받는 사람, 소문 글)] - 호출한 쪽이 받은편지함에 넣는다.
    """
    importance = 3
    for o in outs.values():
        try:
            importance = max(importance, int((o.get("memory") or {}).get("importance", 0)))
        except (TypeError, ValueError):
            pass
    weight = max(0.4, min(1.6, importance / 5)) * (1.0 if how != "메신저" else GOSSIP_MESSENGER_FACTOR)
    line = max(transcript, key=lambda t: len(t["line"]))  # 가장 할 말이 많았던 대목
    quote = line["line"][:80]
    rnd = random.Random(f"{now_kst().isoformat()}|{a['name']}|{b['name']}")
    picked = []
    for other in load_agents():
        if other["name"] in (a["name"], b["name"]):
            continue
        od = active(other["name"]) or {}
        if how != "메신저" and _same_place(od.get("location", ""), place):
            p, text = GOSSIP_P_SAME_PLACE, f"{place}에서 {j(a['name'], '와')} {j(b['name'], '가')} 하는 얘기를 우연히 들었다 - {line['speaker']}: \"{quote}\""
        elif other.get("company") in (a.get("company"), b.get("company")):
            p, text = GOSSIP_P_COLLEAGUE, f"{j(a['name'], '랑')} {j(b['name'], '가')} 얘기하던데, \"{quote}\" 이런 말이 나왔다더라"
        elif shared := _shared_hangout(other, a, b, place):
            p, text = GOSSIP_P_REGULAR, f"{shared}에서 들었는데, {j(a['name'], '랑')} {j(b['name'], '가')} \"{quote}\" 이런 얘기를 했다더라"
        else:
            p, text = GOSSIP_P_OTHER, f"동네에서 {j(a['name'], '와')} {b['name']} 얘기를 들었다 - \"{quote}\" 그랬다던데"
        if rnd.random() < min(0.9, p * weight):
            picked.append((other["name"], text))
    rnd.shuffle(picked)
    return picked[:GOSSIP_MAX_RECIPIENTS]


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

    summary = f"{j(a['name'], '와')} {b['name']}의 {'메신저' if how == '메신저' else place} 대화: " + transcript[0]["line"][:60]
    # 각자의 마음/기억/관계에 반영
    roster = characters.load_roster()
    ids = {c["name"]: c["id"] for c in roster}
    rels = characters.load_relationships()
    key = characters._rel_key(ids.get(a["name"], a["name"]), ids.get(b["name"], b["name"]))
    rel = rels.get(key, {"affinity": 0, "summary": "", "count": 0})
    for ag, oth in ((a, b), (b, a)):
        out = outs.get(ag["name"], {})
        life.apply_feedback(ag["name"], out, f"{j(oth['name'], '와')} 대화")
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

    gossip = _gossip(a, b, how, place, transcript, outs)

    scene = {
        "gossip": gossip,
        "id": uuid.uuid4().hex[:12], "ts": now.isoformat(timespec="seconds"), "hour": now.hour,
        "location": "메신저" if how == "메신저" else place, "participants": [a["name"], b["name"]],
        "arc_id": None, "lines": transcript, "narration": "", "summary": summary, "agents": True,
    }
    with open(DIALOGUES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(scene, ensure_ascii=False) + "\n")
    print(f"[에이전트] 대화 {len(transcript)}마디: {summary}")
    return scene


# ===================================================================== 모임 (여러 명이 한자리에서)
MEETING_MAX_PEOPLE = 10
MEETING_DEFAULT_TURNS = 12
MEETING_PLACE = "동네 주민센터 회의실"


def _meeting_people(names: list[str] | None, agents: list[dict], now) -> tuple[list[dict], list[str]]:
    """참가자 (지정 안 하면 깨어 있는 동네 단톡방 멤버). 반환: (참가자, 자는 중이라 빠진 이름)."""
    by_name = {a["name"]: a for a in agents}
    if names:
        picked = [by_name[n] for n in dict.fromkeys(names) if n in by_name]
    else:
        picked = [a for a in agents if TOWN_GROUP in (a.get("groups") or [])]
    asleep = [a["name"] for a in picked if _asleep(a, now.hour)]
    people = [a for a in picked if not _asleep(a, now.hour)][:MEETING_MAX_PEOPLE]
    if not names and len(people) < 3:
        extra = [a for a in agents if a not in people and not _asleep(a, now.hour)]
        random.shuffle(extra)
        people += extra[:3 - len(people)]
    return people, asleep


def _meeting_turn(agent: dict, topic: str, place: str, people: list[dict], transcript: list[dict],
                  last_turn: bool, context: str = "") -> dict | None:
    name = agent["name"]
    lines = "\n".join(f"{t['speaker']}: {t['line']}" for t in transcript[-16:]) or "(아직 아무도 말하지 않음)"
    spoken = {t["speaker"] for t in transcript}
    others = ", ".join(p["name"] for p in people if p is not agent)
    user = (
        "\n\n".join(b for b in (
            f"[지금] {now_kst().strftime('%m월 %d일 %H:%M')} {place}에서 열린 모임에 와 있다.",
            life.prompt_block(name),
            memory.prompt_block(name, [p["name"] for p in people] + topic.split()[:4], k=4),
            chronicle.prompt_block(),
        ) if b) + "\n\n"
        f"[모임 주제] {topic}\n" + (f"{context}\n" if context else "") + f"[같이 온 사람] {others}\n[지금까지 오간 말]\n{lines}\n\n"
        f"{name}로서 이 모임에서 다음 발언을 해라. 내 성격/처지/기억대로 - 구체적인 안을 내거나, 남의 안에 찬성/반대하거나, "
        "질문하거나, 이야기를 정리해도 된다. 다른 사람 이름을 불러 의견을 물어도 된다. "
        + ("아직 말 안 한 사람이 있으면 그 사람 의견도 궁금해할 수 있다. " if len(spoken) < len(people) else "")
        + ("이번이 마지막 발언이니 결론 쪽으로 정리해라. " if last_turn else "")
        + "\n반드시 JSON으로만 응답:\n"
        '{"say": "할 말(1~3문장)", "stance": "제안/찬성/반대/질문/보충/정리 중 하나", '
        '"proposal": "새로 낸 구체적인 안이 있으면 짧게, 없으면 빈 문자열"}'
    )
    out = llm_json(_system(agent), user, temperature=0.9, timeout=DECIDE_TIMEOUT_SEC, tag=f"모임 {name}")
    if not out or not str(out.get("say") or "").strip():
        return None
    return out


def _next_speaker(people: list[dict], transcript: list[dict], rnd: random.Random) -> dict:
    last = transcript[-1] if transcript else None
    if last:
        named = [p for p in people if p["name"] != last["speaker"] and p["name"] in last["line"]]
        if named:
            return named[0]
    counts = {p["name"]: 0 for p in people}
    for t in transcript:
        if t["speaker"] in counts:
            counts[t["speaker"]] += 1
    pool = [p for p in people if not last or p["name"] != last["speaker"]] or people
    least = min(counts[p["name"]] for p in pool)
    return rnd.choice([p for p in pool if counts[p["name"]] == least])


def _post_as(name: str, text: str) -> None:
    if not runtime.has_agent_sender():
        print(f"[에이전트] (채널 미설정) {name}: {text}")
        return
    try:
        runtime.send_as("", name, text, "")
    except Exception as e:  # noqa: BLE001
        print(f"[경고] {name} 메시지 전송 실패: {e}")


MEETING_MAX_ROUNDS = 5     # 결론이 안 나면 이만큼 다시 모이고, 마지막 모임 끝에 투표로 정한다
MEETING_AUTO_HOUR = 20     # 다시 모이는 시각 (다음 날 저녁 - 깨어 있는 사람이 가장 많을 때)


def _next_meeting_at(now) -> str:
    nxt = (now + timedelta(days=1)).replace(hour=MEETING_AUTO_HOUR, minute=0, second=0, microsecond=0)
    return nxt.isoformat(timespec="minutes")


def _match_candidate(vote: str, candidates: list[str]) -> str | None:
    v = (vote or "").strip().strip("'\"")
    for c in candidates:
        if v == c:
            return c
    for c in candidates:
        if _same_place(v, c):  # 공백/따옴표 차이, 부분 일치
            return c
    return None


def _vote_once(people: list[dict], topic: str, candidates: list[str], talk: str, runoff: bool) -> list[tuple[str, str, str]]:
    """참가자 각자가 자기 LLM으로 한 표씩. 반환: [(이름, 후보, 이유)] (무효표는 빠짐)."""
    out = []
    numbered = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(candidates))
    for p in people:
        user = (
            "\n\n".join(b for b in (life.prompt_block(p["name"]),
                                     memory.prompt_block(p["name"], topic.split()[:4], k=3)) if b) + "\n\n"
            f"[주제] {topic}\n[지금까지 모임에서 오간 이야기]\n{talk[-3000:]}\n\n"
            f"{'동률이라 결선 투표다. ' if runoff else ''}여러 차례 모였는데도 결론이 안 나서 투표로 정하기로 했다. "
            f"아래 후보 중 하나에 내 생각대로 한 표를 던져라 (후보 이름을 그대로 써라).\n{numbered}\n"
            '반드시 JSON으로만 응답: {"vote": "후보 이름 그대로", "reason": "고른 이유 한 줄"}'
        )
        res = llm_json(_system(p), user, temperature=0.7, timeout=DECIDE_TIMEOUT_SEC, tag=f"투표 {p['name']}")
        choice = _match_candidate(str((res or {}).get("vote") or ""), candidates)
        if not choice:
            print(f"[에이전트] 투표 무효: {p['name']} ({(res or {}).get('vote')})")
            continue
        reason = str((res or {}).get("reason") or "").strip()[:120]
        out.append((p["name"], choice, reason))
        _post(p, f"🗳️ **{choice}**" + (f" - {reason}" if reason else ""))
        _pause()
    return out


def _vote(people: list[dict], topic: str, candidates: list[str], talk: str) -> tuple[str, dict, str]:
    """투표로 마무리. 동률이면 결선 한 번, 그래도 동률이면 먼저 나온 안. 반환: (결정, {후보: 표}, 설명)."""
    _post_as("🏛️ 동네 모임", f"-# 🗳️ 투표 - 후보: {' / '.join(candidates)}")
    ballots = _vote_once(people, topic, candidates, talk, runoff=False)
    tally = {c: 0 for c in candidates}
    for _, c, _r in ballots:
        tally[c] += 1
    note = ""
    top = max(tally.values()) if tally else 0
    tied = [c for c in candidates if tally[c] == top]
    if len(tied) > 1 and top > 0:
        _post_as("🏛️ 동네 모임", f"-# 🗳️ 동률 ({' / '.join(tied)}) - 결선 투표")
        runoff = _vote_once(people, topic, tied, talk, runoff=True)
        rt = {c: 0 for c in tied}
        for _, c, _r in runoff:
            rt[c] += 1
        rtop = max(rt.values()) if rt else 0
        rtied = [c for c in tied if rt[c] == rtop]
        winner = rtied[0]
        note = "결선 투표 " + ", ".join(f"{c} {n}표" for c, n in rt.items())
        if len(rtied) > 1:
            note += " - 그래도 동률이라 먼저 나온 안으로"
    else:
        winner = tied[0] if tied else candidates[0]
    return winner, tally, note


def run_meeting(topic: str, names: list[str] | None = None, place: str = "", turns: int = MEETING_DEFAULT_TURNS) -> dict:
    """
    [모임] 여러 에이전트가 한자리에 모여 돌아가며 말한다 (각자 자기 LLM, 자기 상태/기억만 본다).
    끝나면 결론을 정리해서 채널/연대기/참가자 기억/나머지 사람 받은편지함에 남긴다.
    결론이 안 나면 다음 날 저녁 같은 주제로 다시 모이고(최대 MEETING_MAX_ROUNDS차), 마지막 모임에서도 안 나면
    참가자 투표로 정한다. 결정된 내용(모임 결론/투표 결과)은 영구 기억 + "이 동네에서 정해진 것"으로 남는다.
    반환: {"participants", "asleep", "lines", "conclusion", "decided", "round", "vote", "next_at"}
    """
    now = now_kst()
    agents = load_agents()
    prev = chronicle.meeting_state(topic)
    round_no = (prev["round"] if prev else 0) + 1
    if prev:
        topic = prev["title"]
        names = names or prev.get("participants")
        place = place or prev.get("place", "")
    people, asleep = _meeting_people(names, agents, now)
    if prev and len(people) < 3:  # 지난번 사람들이 많이 자고 있으면 깨어 있는 동네 사람으로 채운다
        more, _ = _meeting_people(None, agents, now)
        people += [p for p in more if p not in people][:MEETING_MAX_PEOPLE - len(people)]
    place = (place or "").strip() or MEETING_PLACE
    scope = (prev or {}).get("scope") or TOWN_SCOPE
    turns = max(4, min(30, int(turns or (prev or {}).get("turns") or MEETING_DEFAULT_TURNS)))
    result = {"participants": [p["name"] for p in people], "asleep": asleep, "lines": 0, "conclusion": "",
              "decided": False, "round": round_no, "vote": None, "next_at": ""}
    if len(people) < 2:
        return result
    final = round_no >= MEETING_MAX_ROUNDS
    history = ""
    if prev and prev.get("notes"):
        history = "[지난 모임 경과]\n" + "\n".join(f"- {n}" for n in prev["notes"][-6:])
    context = (f"[{round_no}차 모임] " + ("이번이 마지막 모임이다 - 이번에도 결론이 안 나면 끝에 투표로 정한다." if final
                                       else f"최대 {MEETING_MAX_ROUNDS}차까지 모이고, 그래도 안 되면 투표로 정한다.")
               + (f"\n{history}" if history else ""))
    _post_as("🏛️ 동네 모임", f"-# 🏛️ {place} · {now.strftime('%H:%M')}" + (f" · {round_no}차 모임" if round_no > 1 else "")
             + f"\n**{topic}**\n-# 참석: {', '.join(result['participants'])}")
    print(f"[에이전트] 모임 시작({round_no}차): {topic} ({', '.join(result['participants'])})")
    rnd = random.Random()
    transcript: list[dict] = []
    proposals: list[str] = []
    for i in range(turns):
        sp = _next_speaker(people, transcript, rnd)
        if transcript:
            _pause()
        out = _meeting_turn(sp, topic, place, people, transcript, last_turn=(i == turns - 1), context=context)
        if not out:
            continue
        line = str(out["say"]).strip()[:300]
        transcript.append({"speaker": sp["name"], "line": line, "stance": str(out.get("stance") or "")[:10]})
        if str(out.get("proposal") or "").strip():
            proposals.append(f"{sp['name']}: {str(out['proposal']).strip()[:80]}")
        _post(sp, line)
    result["lines"] = len(transcript)
    if not transcript:
        return result

    talk = "\n".join(f"{t['speaker']}({t['stance']}): {t['line']}" for t in transcript)
    out = llm_json(
        "너는 모임 기록 담당이다. 실제로 오간 말만 보고 결론을 정리한다. 합의되지 않았으면 결론이 났다고 하지 마라.",
        f"[주제] {topic}\n[장소] {place}\n" + (f"{history}\n" if history else "")
        + f"[이번 모임에서 나온 안] {' / '.join(proposals) or '없음'}\n[오간 말]\n{talk}\n\n"
        "반드시 JSON으로만 응답:\n"
        '{"decided": true/false, "conclusion": "결론 한 줄 (결정됐으면 무엇으로 정했는지, 아니면 어디까지 왔는지)", '
        '"detail": "누가 어떤 안을 냈고 찬반이 어땠는지 1~2문장", "next": "결론이 안 났으면 다음에 할 일, 났으면 빈 문자열", '
        '"candidates": ["결론이 안 났으면 지금까지 나온 유력한 후보안들 2~5개 (짧은 이름 그대로)"]}',
        temperature=0.3, timeout=DECIDE_TIMEOUT_SEC, tag="모임 결론")
    decided = bool(out and out.get("decided"))
    conclusion = str((out or {}).get("conclusion") or "결론 없이 끝남").strip()[:200]
    detail = str((out or {}).get("detail") or "").strip()[:300]
    nxt = str((out or {}).get("next") or "").strip()[:200]
    candidates = [str(c).strip()[:40] for c in ((out or {}).get("candidates") or []) if str(c).strip()]
    candidates = list(dict.fromkeys(candidates))[:5]
    if len(candidates) < 2:
        candidates = list(dict.fromkeys(p.split(": ", 1)[-1] for p in proposals))[:5]

    vote_line = ""
    if not decided and final and len(candidates) >= 2:
        _post_as("🏛️ 동네 모임", f"-# 🏛️ {round_no}차 모임에서도 결론이 안 나서 투표로 정합니다")
        winner, tally, note = _vote(people, topic, candidates, talk)
        vote_line = ", ".join(f"{c} {n}표" for c, n in sorted(tally.items(), key=lambda x: -x[1]))
        decided = True
        conclusion = f"투표로 '{winner}'(으)로 결정"
        detail = f"투표 결과: {vote_line}" + (f" / {note}" if note else "")
        nxt = ""
        result["vote"] = {"winner": winner, "tally": tally, "note": note}
    result.update({"conclusion": conclusion, "decided": decided})
    if decided:
        _post_as("🏛️ 동네 모임", f"-# 🏛️ {'투표 결과' if vote_line else '모임 결론'}\n**{conclusion}**" + (f"\n{detail}" if detail else ""))
    else:
        result["next_at"] = _next_meeting_at(now)
        when = datetime.fromisoformat(result["next_at"])
        _post_as("🏛️ 동네 모임", f"-# 🏛️ {round_no}차 모임 정리 (아직 결론 없음)\n**{conclusion}**"
                 + (f"\n{detail}" if detail else "") + (f"\n-# 다음: {nxt}" if nxt else "")
                 + f"\n-# 📅 {when.month}/{when.day} {when.hour}시에 {round_no + 1}차 모임"
                 + (" (마지막 - 결론이 안 나면 투표)" if round_no + 1 >= MEETING_MAX_ROUNDS else ""))

    # 영구 기록: 대화 원문 / 세계 사건 / 진행 중인 일
    summary = f"{place} {round_no}차 모임 '{topic}': {conclusion}"
    with open(DIALOGUES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"id": uuid.uuid4().hex[:12], "ts": now.isoformat(timespec="seconds"), "hour": now.hour,
                            "location": place, "participants": result["participants"], "arc_id": None,
                            "lines": [{"speaker": t["speaker"], "line": t["line"]} for t in transcript],
                            "narration": "", "summary": summary, "agents": True, "meeting": True,
                            "round": round_no, "vote": result["vote"]},
                           ensure_ascii=False) + "\n")
    chronicle.record_event("투표" if vote_line else "모임", f"{topic} ({round_no}차)", f"{conclusion} {detail}".strip(),
                           result["participants"], decided=decided, place=place, round=round_no, vote=result["vote"])
    names_in = result["participants"]
    if decided:
        chronicle.open_thread(topic)  # 진행 중인 일이 없었어도 해결 기록을 남긴다
        vnote = (result["vote"] or {}).get("note", "")
        resolution = conclusion + (f" ({vote_line}" + (f" / {vnote}" if vnote else "") + ")" if vote_line else "")
        chronicle.update_thread(topic, f"{round_no}차 모임: {conclusion}", resolved=True, resolution=resolution, permanent=True)
        # 정해진 일은 모두의 영구 기억 - 기억 정리(prune)로도 지워지지 않는다
        for a in agents:
            here = a["name"] in names_in
            text = (f"{place}에서 '{topic}' {round_no}차 모임에 참석했다 - {resolution}" if here
                    else f"동네에서 '{topic}'이(가) 정해졌다 - {resolution}")
            memory.remember(a["name"], text, 9, [n for n in names_in if n != a["name"]] if here else [], "모임", permanent=True)
            if not here:
                deliver(a["name"], f"'{topic}' 결정 소식: {resolution}", "소식", quiet=True)
        # 정해진 일 중 실제로 해 나갈 일이 있으면 마을 프로젝트로
        try:
            result["project"] = _start_project(topic, resolution, talk, people, scope, agents)
        except Exception as e:  # noqa: BLE001
            print(f"[경고] 마을 프로젝트 만들기 실패: {e}")
    else:
        chronicle.schedule_meeting(
            topic, round_no, place, names_in, turns, result["next_at"],
            f"{round_no}차 모임 결론 없음 - {conclusion}" + (f" / 후보: {', '.join(candidates)}" if candidates else "")
            + (f" / 다음: {nxt}" if nxt else ""), scope=scope)
        for p in people:
            memory.remember(p["name"], f"{place}에서 '{topic}' {round_no}차 모임에 참석했다 - {conclusion}", 6,
                            [n for n in names_in if n != p["name"]], "모임")
        for a in agents:
            if a["name"] not in names_in:
                deliver(a["name"], f"'{topic}' {round_no}차 모임 소식: {conclusion}", "소식", quiet=True)
    print(f"[에이전트] 모임 끝({round_no}차): {topic} -> {conclusion} ({'결정' if decided else '미결'})")
    return result


COMPANY_SCOPE = "회사"
TOWN_SCOPE = "동네"


def _meeting_scopes(agent: dict) -> list[str]:
    """이 사람이 모임을 제안할 수 있는 범위 - 동네 단톡방 멤버면 동네, 회사(에이전트가 여럿인 곳) 사람이면 회사."""
    out = []
    if TOWN_GROUP in (agent.get("groups") or []):
        out.append(TOWN_SCOPE)
    if agent.get("company") in (GROUP_COMPANY, "에린 로지스틱스 (Erinn Logistics)"):
        out.append(COMPANY_SCOPE)
    return out


def _scope_members(agent: dict, scope: str, agents: list[dict]) -> list[str]:
    if scope == TOWN_SCOPE:
        return [a["name"] for a in agents]  # 동네 일은 이 동네에서 살고 일하는 사람 전부의 일
    return [a["name"] for a in agents if a.get("company") == agent.get("company")]


def _announce(agent: dict, scope: str, text: str, data: dict, agents: list[dict]) -> None:
    """모임 제안/확정을 그 범위 사람들이 보는 곳에 올린다 (동네 단톡방 / 회사 단톡방 / 대화 채널)."""
    if scope == TOWN_SCOPE and TOWN_GROUP in _groups_of(agent):
        _post_group(agent, TOWN_GROUP, text, data, agents)
    elif scope == COMPANY_SCOPE and GROUP_NAME in _groups_of(agent):
        _post_group(agent, GROUP_NAME, text, data, agents)
    else:
        _post(agent, f"-# 📣 {agent.get('company', '').split(' ')[0]}\n{text}")


def _handle_proposals(decisions: dict[str, dict], fresh: list[str], by_name: dict, agents: list[dict],
                      data: dict, now) -> None:
    """
    에이전트가 직접 제안한 모임: 제안 → 범위(동네/회사) 사람들에게 알림(깨움) → 찬성이 SUPPORT_NEEDED명 모이면
    모임 확정(오늘 그 시각, 하루 상한) → 시각이 되면 run_due_meetings가 연다. 호응이 없으면 흐지부지.
    """
    for name in fresh:
        d = decisions.get(name) or {}
        sup = d.get("meeting_support")
        if sup:
            p = chronicle.support_proposal(sup, name)
            if p:
                print(f"[에이전트] 모임 찬성: {name} -> {p['title']} ({len(p['supporters'])}명)")
        m = d.get("meeting")
        if not m or d.get("meeting_done"):
            continue
        agent = by_name[name]
        members = _scope_members(agent, m["scope"], agents)
        place = MEETING_PLACE if m["scope"] == TOWN_SCOPE else f"{agent.get('company', '').split(' ')[0]} 회의실"
        # 찬성 10명(본인 포함)이면 잡힌다 - 범위 인원이 그보다 적으면(작은 회사) 그 인원 전원
        needed = min(chronicle.SUPPORT_NEEDED, len(members))
        why_not = chronicle.propose_meeting(m["topic"], name, m["scope"], members, m["hour"], place, m["why"], now, needed)
        d["meeting_done"] = True
        data.setdefault(name, {}).setdefault("decision", d)["meeting_done"] = True
        if why_not:
            print(f"[에이전트] 모임 제안 안 받음: {name} '{m['topic']}' ({why_not})")
            continue
        _announce(agent, m["scope"], f"[모임 제안] '{m['topic']}' - {m['why']} 오늘 {m['hour']}시 {place}에서 모여서 얘기해 볼까요?",
                  data, agents)
        for other in members:
            if other != name:
                # 깨우지는 않는다 (인원이 많아서) - 각자 다음 판단 때 보고 찬성할지 정한다
                _push_inbox(data, other, f"{j(name, '가')} '{m['topic']}' 모임을 제안했다 ({m['why']}) - 오늘 {m['hour']}시 {place}",
                            "모임제안", quiet=True)
        print(f"[에이전트] 모임 제안: {name} '{m['topic']}' ({m['scope']}, {m['hour']}시)")
    # 찬성이 모인 제안은 모임으로 확정
    for t in chronicle.pending_proposals():
        p = t["proposal"]
        if len(p["supporters"]) < p.get("needed", chronicle.SUPPORT_NEEDED):
            continue
        at = chronicle.confirm_proposal(t["title"], now)
        if not at:
            continue
        when = datetime.fromisoformat(at)
        by = by_name.get(p["by"])
        merged = next((x["resolution"] for x in chronicle.all_threads()
                       if x["title"] == t["title"] and x["status"] == "resolved"), "")
        if by:
            text = (f"📅 '{t['title']}' - {merged}, {when.hour}시로 당겨서 같이 모여요 (찬성: {', '.join(p['supporters'])})"
                    if merged else f"📅 '{t['title']}' 모임 확정 - 오늘 {when.hour}시 {p['place']} "
                                   f"(찬성: {', '.join(p['supporters'])})")
            _announce(by, p["scope"], text, data, agents)
        print(f"[에이전트] 모임 확정: {t['title']} {when.strftime('%H시')} ({', '.join(p['supporters'])})")
    for p in chronicle.expire_proposals(now):
        print(f"[에이전트] 모임 제안 흐지부지: {p['title']} (찬성 {len(p['supporters'])}명)")


# ===================================================================== 마을 프로젝트
def _project_scope_members(scope: str, people: list[dict], agents: list[dict]) -> list[str]:
    if scope == COMPANY_SCOPE and people:
        return [a["name"] for a in agents if a.get("company") == people[0].get("company")]
    return [a["name"] for a in agents]


def _start_project(topic: str, decision: str, talk: str, people: list[dict], scope: str, agents: list[dict]) -> str:
    """모임 결정에 실제로 해 나갈 일이 있으면 프로젝트로 만든다 (LLM 1회). 반환: 프로젝트 이름 또는 ""."""
    names = [p["name"] for p in people]
    out = llm_json(
        "너는 동네 모임 기록 담당이다. 모임에서 정해진 일을 실제로 해 나갈 계획으로 바꾼다. 지어내지 말고 정해진 내용 안에서.",
        f"[주제] {topic}\n[결정] {decision}\n[참석자] {', '.join(names)}\n[오간 말 일부]\n{talk[-2500:]}\n\n"
        "이 결정에 사람들이 실제로 손을 움직여 해 나갈 일(만들기, 꾸미기, 행사 열기, 바꾸기 등)이 있으면 프로젝트로 정리해라. "
        "이름 짓기처럼 정하는 것으로 끝나는 일이면 그것을 알리고 반영하는 일(현판, 안내문 등)이 있을 때만.\n"
        "반드시 JSON으로만 응답:\n"
        '{"is_project": true/false, "title": "프로젝트 이름(짧게)", "goal": "다 되면 동네가 어떻게 바뀌는지 한 줄", '
        '"steps": ["단계 3~5개, 각각 짧게"], "owner": "참석자 중 맡을 사람(성격/처지에 맞게)", "helpers": ["도울 참석자 1~4명"]}',
        temperature=0.4, timeout=DECIDE_TIMEOUT_SEC, tag="마을 프로젝트")
    if not out or not out.get("is_project") or not str(out.get("title") or "").strip():
        return ""
    owner = out.get("owner") if out.get("owner") in names else names[0]
    helpers = [h for h in (out.get("helpers") or []) if h in names and h != owner]
    p = projects.create(str(out["title"]), str(out.get("goal") or decision), [str(x) for x in (out.get("steps") or [])],
                        owner, helpers, scope, _project_scope_members(scope, people, agents), topic)
    if not p:
        return ""
    _post_as("🛠️ 마을 프로젝트", f"-# 🛠️ 새 마을 프로젝트\n**{p['title']}** - {p['goal']}\n"
                            f"-# 단계: {' → '.join(p['steps'])} · 담당 {p['owner']}"
                            + (f" · 도움 {', '.join(p['helpers'])}" if p["helpers"] else ""))
    chronicle.record_event("프로젝트", f"{p['title']} 시작", p["goal"], [p["owner"]] + p["helpers"])
    deliver(p["owner"], f"'{p['title']}' 프로젝트를 맡게 됐다 - 첫 단계: {p['steps'][0]}", "프로젝트")
    for h in p["helpers"]:
        deliver(h, f"'{p['title']}' 프로젝트를 돕기로 했다 (담당 {p['owner']}) - 첫 단계: {p['steps'][0]}", "프로젝트")
    return p["title"]


def _handle_projects(decisions: dict[str, dict], fresh: list[str], by_name: dict, agents: list[dict],
                     data: dict, now) -> None:
    """이번 시간 project_work를 쓴 사람의 일을 반영하고, 단계/완료 소식을 올리고, 멈춘 프로젝트는 담당자를 깨운다."""
    for name in fresh:
        w = (decisions.get(name) or {}).get("project_work")
        if not w:
            continue
        res = projects.contribute(w["project"], name, w["did"])
        if not res or not res["gain"]:
            continue
        p = res["project"]
        print(f"[에이전트] 프로젝트 참여: {name} -> {p['title']} +{res['gain']} ({p['progress']}%) - {w['did']}")
        if res["completed"]:
            crew = list(dict.fromkeys([p["owner"]] + p["helpers"]))
            _post_as("🛠️ 마을 프로젝트", f"-# 🎉 마을 프로젝트 완료\n**{p['title']}** - {p['goal']}\n"
                                    f"-# 함께한 사람: {', '.join(crew)}")
            chronicle.record_event("프로젝트", f"{p['title']} 완료", p["goal"], crew)
            for a in agents:
                if p.get("members") and a["name"] not in p["members"]:
                    continue
                here = a["name"] in crew
                memory.remember(a["name"], (f"'{p['title']}'을(를) 다 같이 해냈다 - {p['goal']}" if here
                                            else f"동네에 '{p['title']}'이(가) 생겼다 - {p['goal']}"),
                                9, [c for c in crew if c != a["name"]][:5] if here else [], "프로젝트", permanent=True)
                _push_inbox(data, a["name"], f"마을 프로젝트 '{p['title']}' 완료 - {p['goal']}", "소식", quiet=True)
        elif res["step_done"]:
            nxt = projects.current_step(p)
            _post_as("🛠️ 마을 프로젝트", f"-# 🛠️ {p['title']} · {p['step']}/{len(p['steps'])} 단계 '{res['step_done']}' 끝 "
                                    f"({p['progress']}%) - 다음: {nxt}\n{name}: {w['did']}")
            chronicle.record_event("프로젝트", f"{p['title']} - {res['step_done']} 끝", w["did"], [name])
            for who in [p["owner"]] + p["helpers"]:
                if who != name:
                    _push_inbox(data, who, f"'{p['title']}' {res['step_done']} 단계 끝 - 다음: {nxt}", "프로젝트", quiet=True)
    for p in projects.stalled(now):
        _push_inbox(data, p["owner"], f"내가 맡은 '{p['title']}'가 며칠째 멈춰 있다 - 지금 단계: {projects.current_step(p)}",
                    "프로젝트")
        print(f"[에이전트] 프로젝트 멈춤 알림: {p['title']} -> {p['owner']}")


def run_due_meetings() -> None:
    """[매시 회차] 다시 모일 시각이 된 모임을 연다 (한 회차에 하나). 모일 사람이 없으면 한 시간 미룬다."""
    now = now_kst()
    for t in chronicle.due_meetings(now)[:1]:
        res = run_meeting(t["title"])
        if len(res["participants"]) < 2:
            later = now + timedelta(hours=1)
            if later.hour >= 23 or later.hour < 9:
                later = (now + timedelta(days=1)).replace(hour=MEETING_AUTO_HOUR, minute=0)
            chronicle.postpone_meeting(t["title"], later.isoformat(timespec="minutes"))
            print(f"[에이전트] 모임 연기: {t['title']} -> {later.strftime('%m/%d %H시')} (모일 사람 부족)")


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

    # 0) 연대기 - 새벽이 지났으면 어제 하루를 요약 (하루 한 번)
    try:
        chronicle.tick()
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 연대기 요약 실패: {e}")
    # 0.5) 교체 - 하루 한 번 소속별로 에이전트와 배경 인물이 자리를 바꾼다
    try:
        _rotate(now)
        agents = load_agents()
        by_name = {a["name"]: a for a in agents}
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 에이전트 교체 실패: {e}")

    # 1) 세계 엔진 - 사건 계획/발생 (에이전트에게는 발생한 것만 전달)
    try:
        for name, text, kind in world.tick(agents, roster, signals.format_block(signals.collect(include_factory=False))):
            deliver(name, text, kind)
    except Exception as e:  # noqa: BLE001
        print(f"[에러] 세계 엔진 실패: {e}")

    data = _load_state()
    try:
        _duty_updates(agents, data, now)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 담당 업무 갱신 실패: {e}")
    decisions: dict[str, dict] = {}
    fresh: list[str] = []  # 이번 시간 새로 판단한 사람 (근황 요약용)
    pushed: set[str] = set()  # 관리자 사건/메시지로 다시 판단한 사람 - 이번 시간 대화를 이미 했어도 한 번 더 허용
    awake = [a for a in agents if not _asleep(a, now.hour)]
    for agent in awake:
        name = agent["name"]
        st = data.setdefault(name, {})
        urgent = any(m.get("urgent") for m in st.get("inbox", []))
        if (st.get("decision") or {}).get("hour") == hk and not urgent:
            decisions[name] = st["decision"]
            continue
        # 몇 시간마다 판단하는 에이전트: 차례가 아니고 새 소식도 없으면 이전 판단을 이어간다 (LLM 호출 절약)
        if not _due(agent, now.hour) and not any(not m.get("quiet") for m in st.get("inbox", [])):
            continue
        if urgent:
            pushed.add(name)
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
        st["log"] = st["log"][-MAX_LOG:]  # 최근 것만 지각용으로 - 전체는 연대기 보관함에 영구 보관
        chronicle.record_action(name, d)
        decisions[name] = d
        fresh.append(name)
        print(f"[에이전트] {name}: {d['location']}에서 {d['activity']} ({d['state']}) - {d['thought']}")
    _save_state(data)

    # 2) 연락 -> 대화 (시간당 1번, 하루 최대 MAX_CONVOS_PER_DAY번)
    data = _load_state()
    today = now.strftime("%Y-%m-%d")
    convo = data.setdefault("_convos", {})
    if convo.get("date") != today:
        convo.clear()
        convo.update({"date": today, "count": 0, "hours": []})
    pushed_contacts = [(n, decisions[n]) for n in pushed
                       if n in decisions and (decisions[n].get("contact") or {}).get("to")]
    if (hk not in convo["hours"] or pushed_contacts) and convo["count"] < MAX_CONVOS_PER_DAY:
        # 연락하려는 사람이 여럿이면 매번 같은 사람이 먼저 되지 않게 섞는다
        order = list(decisions.items())
        random.Random(hk).shuffle(order)
        if hk in convo["hours"]:
            order = pushed_contacts  # 이번 시간 대화는 이미 있었다 - 관리자 개입으로 깨어난 사람만
        for name, d in order:
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
                scene = _conversation(by_name[name], target, str(c["opening"]), c.get("how", "메신저"), place)
                for who, text in scene.get("gossip", []):
                    _push_inbox(data, who, text, "소문")
                    print(f"[에이전트] 소문: {who} <- {text}")
            c["done"] = True
            data.setdefault(name, {}).setdefault("decision", d)["contact"] = c
            convo["count"] += 1
            convo["hours"].append(hk)
            break
    # 3) 회사 단톡방 - 판단 때 group_post를 쓴 사람이 올린다 (시간당/하루 상한)
    gc = data.setdefault("_group", {})
    if gc.get("date") != today:
        gc.clear()
        gc.update({"date": today, "counts": {}})
    counts = gc.setdefault("counts", {})
    posted = 0
    for name, d in decisions.items():
        gp = d.get("group_post")
        if not isinstance(gp, dict) or d.get("group_done"):
            continue
        if posted >= GROUP_MAX_POSTS_PER_HOUR:
            break
        if counts.get(gp["group"], 0) >= GROUP_MAX_POSTS_PER_DAY:
            continue
        _post_group(by_name[name], gp["group"], gp["text"], data, agents)
        data.setdefault(name, {}).setdefault("decision", d)["group_done"] = True
        counts[gp["group"]] = counts.get(gp["group"], 0) + 1
        posted += 1
    # 3.5) 에이전트가 직접 제안한 모임 - 제안/찬성/확정/흐지부지
    try:
        _handle_proposals(decisions, fresh, by_name, agents, data, now)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 모임 제안 처리 실패: {e}")
    # 3.6) 마을 프로젝트 - 손을 보탠 일 반영, 단계/완료 소식, 멈춘 프로젝트 챙기기
    try:
        _handle_projects(decisions, fresh, by_name, agents, data, now)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 마을 프로젝트 처리 실패: {e}")
    _save_state(data)

    # 4) 다시 모이기로 한 모임 (결론 안 난 모임 자동 소집, 최대 MEETING_MAX_ROUNDS차)
    try:
        run_due_meetings()
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 자동 모임 실패: {e}")

    # 5) 근황 - 이번 시간 새로 판단한 사람들이 어디서 뭘 하는지 한 메시지로 (대화 채널)
    if STATUS_DIGEST and fresh:
        _post_digest(now, [(by_name[n], decisions[n]) for n in fresh if n in decisions])
    return decisions


def _post_digest(now, items: list[tuple[dict, dict]]) -> None:
    lines = [f"-# 🕒 {now.strftime('%H:%M')} 지금 다들 뭐 하나"]
    for agent, d in items:
        thought = d.get("thought", "")
        thought = (thought[:45] + "…") if len(thought) > 45 else thought
        lines.append(f"• **{agent['name']}** · {d['location']} — {d['activity']}" + (f" *({thought})*" if thought else ""))
    # 사람이 많은 시간엔 한 메시지(2000자)를 넘는다 - 줄 단위로 나눠 보낸다
    chunks, cur = [], ""
    for line in lines:
        line = line[:300]
        if cur and len(cur) + len(line) + 1 > 1900:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    if not runtime.has_agent_sender():
        print("[에이전트] (채널 미설정) 근황\n" + "\n".join(chunks))
        return
    for text in chunks:
        try:
            runtime.send_as("", "동네 소식", text, "")
        except Exception as e:  # noqa: BLE001
            print(f"[경고] 근황 전송 실패: {e}")
            return


# ===================================================================== 다른 곳에서 쓰는 요약
def diary_block(name: str) -> str:
    """주인공 일지용: 이번 시간 그 에이전트가 실제로 한 일/생각 (작가 회의 줄거리 대신)."""
    agent = next((a for a in load_agents() if a["name"] == name), {})
    d = latest(name, _every(agent)) if agent else current(name)
    if not d:
        return ""
    lines = [f"[이번 시간 {j(name, '가')} 실제로 한 일과 생각 - 이걸 바탕으로 써라]",
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
