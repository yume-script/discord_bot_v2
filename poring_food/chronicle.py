"""
세계 연대기 - 가상 세계에서 일어난 일은 지우지 않고 계속 쌓고, 그 위에 요약을 따로 만든다.

1) 원본 영구 보관 (archive/, 덧붙이기만 하고 지우지 않음)
   - events.jsonl   : 세계 사건(세계 엔진/관리자/담당 업무/모임 결론) 전부
   - actions.jsonl  : 에이전트 매시 판단(어디서 무엇을, 속마음)
   - memories.jsonl : 기억 정리(prune) 때 빠진 기억 - 지우지 않고 여기로 옮긴다
   대화 원문(dialogues.jsonl)과 단톡방(group_chat.jsonl)은 원래 지우지 않는 파일이라 그대로 쓴다.
2) 요약 (chronicle.jsonl, 영구 보관)
   - 매일 새벽(DAILY_FROM_HOUR 이후 첫 회차) 어제 하루를 LLM이 한 번 요약한다: 주요 사건/누가 무엇을/관계 변화
   - 월요일엔 지난주, 1일엔 지난달 요약을 하루 요약들로 만든다
3) 진행 중인 일 (threads.json)
   - "마을 이름 짓기 - 아직 결론 없음"처럼 끝나지 않은 일. 해결될 때까지 모든 에이전트 지각에 보인다.
   - 관리자 사건/모임이 열고, 하루 요약 LLM과 모임 결론이 갱신/해결한다. 해결된 일도 지우지 않는다.

에이전트 지각(prompt_block)에는 최근 며칠 요약 + 이번 주/달 요약 + 진행 중인 일이 들어간다.
이 모듈은 agents.py/world.py를 import하지 않는다 (순환 방지).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR
from .llm import llm_json

ARCHIVE_DIR = os.path.join(STATE_DIR, "archive")
EVENTS_PATH = os.path.join(ARCHIVE_DIR, "events.jsonl")
ACTIONS_PATH = os.path.join(ARCHIVE_DIR, "actions.jsonl")
MEMORY_ARCHIVE_PATH = os.path.join(ARCHIVE_DIR, "memories.jsonl")
CHRONICLE_PATH = os.path.join(STATE_DIR, "chronicle.jsonl")
THREADS_PATH = os.path.join(STATE_DIR, "threads.json")
DIALOGUES_PATH = os.path.join(STATE_DIR, "dialogues.jsonl")
GROUP_PATH = os.path.join(STATE_DIR, "group_chat.jsonl")

DAILY_FROM_HOUR = 4        # 이 시각 이후 첫 회차에 어제를 요약한다
RECENT_DAYS = 3            # 지각에 넣는 최근 하루 요약 수
RAW_BUDGET = 14000         # 하루 요약에 넣는 원본 글자 수 상한
SUMMARY_TIMEOUT_SEC = 120
MAX_OPEN_THREADS = 8       # 지각에 넣는 진행 중인 일 수


def _append(path: str, entry: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[경고] 연대기 기록 실패 ({os.path.basename(path)}): {e}")


def _read(path: str, date: str | None = None, key: str = "at") -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if date is None or str(m.get(key, "")).startswith(date):
                out.append(m)
    return out


# ===================================================================== 원본 기록
def record_event(kind: str, title: str, detail: str, targets: list[str] | None = None, **extra) -> None:
    """세계에서 일어난 일 하나 (종류: 세계/소문/관리자/업무/모임 ...)."""
    _append(EVENTS_PATH, {"at": now_kst().isoformat(timespec="minutes"), "kind": kind, "title": title,
                          "detail": detail, "targets": targets or [], **extra})


def record_action(name: str, d: dict) -> None:
    _append(ACTIONS_PATH, {"at": now_kst().isoformat(timespec="minutes"), "name": name,
                           **{k: d.get(k, "") for k in ("location", "activity", "state", "thought", "plan")}})


def archive_memories(mems: list[dict]) -> None:
    """기억 정리 때 빠지는 기억들 - 지우지 않고 보관한다."""
    if not mems:
        return
    try:
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        with open(MEMORY_ARCHIVE_PATH, "a", encoding="utf-8") as f:
            for m in mems:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[경고] 기억 보관 실패: {e}")


def archived_memories(name: str) -> list[dict]:
    return [m for m in _read(MEMORY_ARCHIVE_PATH) if m.get("name") == name]


# ===================================================================== 진행 중인 일
def _load_threads() -> list[dict]:
    try:
        with open(THREADS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_threads(threads: list[dict]) -> None:
    tmp = THREADS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(threads, f, ensure_ascii=False, indent=2)
    os.replace(tmp, THREADS_PATH)


def _same(a: str, b: str) -> bool:
    a, b = (a or "").replace(" ", ""), (b or "").replace(" ", "")
    return bool(a and b) and (a in b or b in a)


def open_thread(title: str, note: str = "") -> dict:
    """진행 중인 일을 연다 (같은 제목의 열린 일이 있으면 거기에 메모만 덧붙인다)."""
    threads = _load_threads()
    now = now_kst().isoformat(timespec="minutes")
    for t in threads:
        if t["status"] == "open" and _same(t["title"], title):
            if note:
                t.setdefault("updates", []).append({"at": now, "note": note[:300]})
            _save_threads(threads)
            return t
    t = {"id": uuid.uuid4().hex[:8], "title": title[:80], "status": "open", "since": now,
         "updates": [{"at": now, "note": note[:300]}] if note else [], "resolution": ""}
    threads.append(t)
    _save_threads(threads)
    print(f"[연대기] 진행 중인 일 시작: {t['title']}")
    return t


def update_thread(title: str, note: str = "", resolved: bool = False, resolution: str = "") -> bool:
    threads = _load_threads()
    now = now_kst().isoformat(timespec="minutes")
    for t in threads:
        if t["status"] == "open" and _same(t["title"], title):
            if note:
                t.setdefault("updates", []).append({"at": now, "note": note[:300]})
            if resolved:
                t["status"] = "resolved"
                t["resolution"] = (resolution or note)[:300]
                t["resolved_at"] = now
                print(f"[연대기] 진행 중인 일 해결: {t['title']} - {t['resolution']}")
            _save_threads(threads)
            return True
    return False


def open_threads() -> list[dict]:
    return [t for t in _load_threads() if t["status"] == "open"]


def all_threads() -> list[dict]:
    return _load_threads()


# ===================================================================== 요약
def _chronicle() -> list[dict]:
    return _read(CHRONICLE_PATH)


def entry(kind: str, key: str) -> dict | None:
    """요약 하나 (kind: day/week/month, key: 2026-10-09 / 2026-W41 / 2026-10)."""
    return next((c for c in reversed(_chronicle()) if c.get("kind") == kind and c.get("key") == key), None)


def _raw_day(date: str) -> str:
    """그날 원본을 요약 프롬프트용 글로 (글자 수 상한 안에서 중요한 것부터)."""
    parts = []
    events = _read(EVENTS_PATH, date)
    if events:
        parts.append("[사건]\n" + "\n".join(f"- {e['at'][11:16]} ({e.get('kind', '')}) {e['title']}: {e.get('detail', '')}"
                                           f" [{', '.join(e.get('targets') or [])}]" for e in events))
    scenes = [s for s in _read(DIALOGUES_PATH, date, key="ts")]
    if scenes:
        lines = []
        for s in scenes:
            talk = " / ".join(f"{t['speaker']}: {t['line'][:80]}" for t in (s.get("lines") or [])[:6])
            lines.append(f"- {s.get('ts', '')[11:16]} {', '.join(s.get('participants') or [])} @{s.get('location', '')}: {talk}")
        parts.append("[대화]\n" + "\n".join(lines))
    posts = _read(GROUP_PATH, date)
    if posts:
        parts.append("[단톡방]\n" + "\n".join(f"- {p['at'][11:16]} [{p.get('group', '')}] {p['name']}: {p['text'][:120]}"
                                             for p in posts))
    acts = _read(ACTIONS_PATH, date)
    if acts:
        by: dict[str, list[str]] = {}
        for a in acts:
            by.setdefault(a["name"], []).append(f"{a['at'][11:13]}시 {a.get('location', '')} {a.get('activity', '')}")
        parts.append("[각자 하루 행동]\n" + "\n".join(f"- {n}: " + " → ".join(v[-6:]) for n, v in by.items()))
    text = "\n\n".join(parts)
    return text[:RAW_BUDGET]


def _summarize_day(date: str) -> dict | None:
    raw = _raw_day(date)
    threads = open_threads()
    if not raw and not threads:
        return {"kind": "day", "key": date, "text": "조용히 지나간 하루.", "public": "조용히 지나간 하루.", "at": now_kst().isoformat(timespec="minutes")}
    system = ("너는 가상 도시의 연대기 기록자다. 그날 실제로 일어난 일만 사실대로 정리한다. "
              "지어내지 말고, 원본에 없는 결론을 만들지 마라.")
    user = (
        f"[날짜] {date}\n\n{raw or '(기록 없음)'}\n\n"
        "[진행 중인 일]\n" + ("\n".join(f"- {t['title']}" + (f" (최근: {t['updates'][-1]['note']})" if t.get('updates') else "")
                                    for t in threads) or "- 없음") + "\n\n"
        "이 하루를 연대기로 정리해라. 반드시 JSON으로만 응답:\n"
        '{"summary": "그날의 주요 사건과 흐름 4~8문장 (누가 무엇을 했고 어떻게 됐는지, 사람 이름 그대로)", '
        '"public": "동네 사람들이 다 알 만한 공개된 일만 2~5문장 - 속마음, 1:1 대화의 사적인 내용, 한 사람에게만 일어난 일, '
        '회사 내부 기밀은 빼라", '
        '"highlights": ["기억할 만한 일 한 줄", ...최대 5개], '
        '"threads": [{"title": "진행 중인 일 제목(기존 제목 그대로 또는 새로 생긴 공개된 일)", '
        '"status": "open 또는 resolved", "note": "오늘 어떻게 진행됐는지 한 줄 (해결됐으면 결론)"}]}\n'
        "threads는 동네/회사 사람들이 함께 아는 공개된 일만 (개인 비밀이나 속마음은 넣지 마라)."
    )
    out = llm_json(system, user, temperature=0.4, timeout=SUMMARY_TIMEOUT_SEC, tag="연대기")
    if not out or not str(out.get("summary") or "").strip():
        return None
    for t in out.get("threads") or []:
        if not isinstance(t, dict) or not str(t.get("title") or "").strip():
            continue
        title, note = str(t["title"]).strip(), str(t.get("note") or "").strip()
        resolved = t.get("status") == "resolved"
        if update_thread(title, note, resolved=resolved) or resolved:
            continue
        # 이미 해결된 일(모임 결론 등)을 요약 LLM이 다시 열지 않게 - 최근 해결된 같은 일이 있으면 새로 안 연다
        week_ago = (now_kst() - timedelta(days=7)).isoformat()
        if any(t["status"] == "resolved" and _same(t["title"], title) and t.get("resolved_at", "") >= week_ago
               for t in _load_threads()):
            continue
        open_thread(title, note)
    return {"kind": "day", "key": date, "text": str(out["summary"]).strip()[:1500],
            "public": str(out.get("public") or "").strip()[:800],
            "highlights": [str(h)[:150] for h in (out.get("highlights") or [])][:5],
            "at": now_kst().isoformat(timespec="minutes")}


def _rollup(kind: str, key: str, days: list[str]) -> dict | None:
    items = [c for c in _chronicle() if c.get("kind") == "day" and c.get("key") in days]
    if not items:
        return None
    label = "한 주" if kind == "week" else "한 달"
    body = "\n".join(f"- {c['key']}: {c['text']}" for c in items)[:RAW_BUDGET]
    out = llm_json("너는 가상 도시의 연대기 기록자다. 하루 요약들을 더 긴 기간의 흐름으로 묶는다. 지어내지 마라.",
                   f"[{key} {label}의 하루 요약들]\n{body}\n\n"
                   f"이 {label}를 정리해라. 반드시 JSON으로만 응답:\n"
                   '{"summary": "이 기간의 큰 흐름과 사건, 인물들의 변화 4~8문장", '
                   '"public": "동네 사람들이 다 알 만한 공개된 흐름만 2~4문장 (사적인 일/속마음/회사 기밀 제외)"}',
                   temperature=0.4, timeout=SUMMARY_TIMEOUT_SEC, tag="연대기")
    if not out or not str(out.get("summary") or "").strip():
        return None
    return {"kind": kind, "key": key, "text": str(out["summary"]).strip()[:2000],
            "public": str(out.get("public") or "").strip()[:800], "at": now_kst().isoformat(timespec="minutes")}


def tick() -> None:
    """[매시 회차] 새벽이 지났으면 어제 하루(필요하면 지난주/지난달)를 요약한다. 이미 했으면 아무것도 안 한다."""
    now = now_kst()
    if now.hour < DAILY_FROM_HOUR:
        return
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if not entry("day", yesterday):
        c = _summarize_day(yesterday)
        if c:
            _append(CHRONICLE_PATH, c)
            print(f"[연대기] {yesterday} 하루 요약 완료")
    if now.weekday() == 0:
        last_mon = now - timedelta(days=7)
        y, w, _ = last_mon.isocalendar()
        key = f"{y}-W{w:02d}"
        if not entry("week", key):
            days = [(last_mon + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
            c = _rollup("week", key, days)
            if c:
                _append(CHRONICLE_PATH, c)
                print(f"[연대기] {key} 주간 요약 완료")
    if now.day == 1:
        last = now - timedelta(days=1)
        key = last.strftime("%Y-%m")
        if not entry("month", key):
            days = [(last - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(last.day)]
            c = _rollup("month", key, days)
            if c:
                _append(CHRONICLE_PATH, c)
                print(f"[연대기] {key} 월간 요약 완료")


# ===================================================================== 읽기
def prompt_block() -> str:
    """에이전트 지각/세계 엔진용: 최근 며칠 공개 요약 + 지난주/지난달 + 진행 중인 일.
    전체 요약(text)은 사적인 대화/개인 사건까지 담겨 있어서 관리자 조회에만 쓰고, 여기엔 공개본(public)만."""
    chron = [c for c in _chronicle() if c.get("public")]
    days = [c for c in chron if c.get("kind") == "day"][-RECENT_DAYS:]
    week = next((c for c in reversed(chron) if c.get("kind") == "week"), None)
    month = next((c for c in reversed(chron) if c.get("kind") == "month"), None)
    lines = []
    if month:
        lines.append(f"- 지난달({month['key']}): {month['public'][:400]}")
    if week:
        lines.append(f"- 지난주({week['key']}): {week['public'][:400]}")
    for c in days:
        lines.append(f"- {c['key'][5:].replace('-', '/')}: {c['public'][:400]}")
    threads = open_threads()[-MAX_OPEN_THREADS:]
    since = (now_kst() - timedelta(days=RECENT_DAYS)).isoformat()
    done = [t for t in all_threads() if t["status"] == "resolved" and t.get("resolved_at", "") >= since]
    out = []
    if lines:
        out.append("[최근 이 도시에서 있었던 일 - 연대기]\n" + "\n".join(lines))
    if threads:
        out.append("[아직 끝나지 않은 일 - 모두가 알고 있다]\n" + "\n".join(
            f"- {t['title']} ({t['since'][5:10].replace('-', '/')}부터)"
            + (f": {t['updates'][-1]['note']}" if t.get("updates") else "") for t in threads))
    if done:
        out.append("[최근에 정해진 일]\n" + "\n".join(f"- {t['title']} → {t['resolution']}" for t in done[-5:]))
    return "\n\n".join(out)


def lookup(date: str) -> str:
    """관리자 조회용: 그날 요약(없으면 원본 사건 목록) + 진행 중인 일."""
    c = entry("day", date)
    parts = [f"📜 **{date} 연대기**"]
    if c:
        parts.append(c["text"])
        if c.get("highlights"):
            parts.append("\n".join(f"• {h}" for h in c["highlights"]))
    else:
        events = _read(EVENTS_PATH, date)
        scenes = _read(DIALOGUES_PATH, date, key="ts")
        posts = _read(GROUP_PATH, date)
        parts.append("(아직 요약 전 - 원본 기록)")
        parts += [f"• {e['at'][11:16]} {e['title']}: {e.get('detail', '')[:120]}" for e in events[-15:]]
        parts.append(f"대화 {len(scenes)}건 · 단톡방 글 {len(posts)}개")
    threads = open_threads()
    if threads:
        parts.append("**진행 중인 일**\n" + "\n".join(
            f"• {t['title']}" + (f" - {t['updates'][-1]['note']}" if t.get("updates") else "") for t in threads))
    done = [t for t in all_threads() if t["status"] == "resolved" and str(t.get("resolved_at", "")).startswith(date)]
    if done:
        parts.append("**이날 해결된 일**\n" + "\n".join(f"• {t['title']} → {t['resolution']}" for t in done))
    return "\n\n".join(parts)[:1900]


def parse_date(text: str) -> str | None:
    """'2026-10-09', '10/09', '어제', '오늘', 빈 값(=어제) -> YYYY-MM-DD."""
    now = now_kst()
    t = (text or "").strip()
    if t in ("", "어제"):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if t == "오늘":
        return now.strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d", "%m-%d"):
        try:
            d = datetime.strptime(t, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            d = d.replace(year=now.year)
        return d.strftime("%Y-%m-%d")
    return None
