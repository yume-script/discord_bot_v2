"""
[2단계] 인물별 장기 기억 - 대화 기록을 그대로 쌓는 게 아니라 "경험"을 한 줄 기억으로 압축해 둔다.

예) "10/03 동네 카페에서 오동백 과장이 내가 인터스텔라 좋아하는 걸 기억하고 있었다 (중요도 7)"

- 저장: storage/poring_food/memories.jsonl  {"name", "at", "text", "importance"(1~10), "with"[이름], "source"}
- 만드는 곳: 일지/장면 LLM이 JSON으로 같이 돌려준 memory(추가 LLM 호출 없음), 월급날 같은 규칙 사건
- 떠올리기(recall): 최근일수록 + 중요할수록 + 지금 상황(같이 있는 사람, 화제 키워드)과 관련 있을수록
  점수가 높다. 프롬프트에는 상위 몇 개만 넣는다 (임베딩 없이 키워드로 충분한 규모).
- 정리(prune): 파일이 커지면 인물별 최근 MAX_PER_PERSON개 + 중요도 8 이상(60일 이내) + 영구 기억(permanent,
  투표/모임 결론)만 남긴다.
  빠진 기억은 지우지 않고 연대기 보관함(archive/memories.jsonl)으로 옮기고, 지금 상황과 관련 있으면
  (화제 키워드가 겹치면) 떠올리기에서 다시 꺼내 쓴다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from . import chronicle
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

MEMORY_PATH = os.path.join(STATE_DIR, "memories.jsonl")
MAX_PER_PERSON = 150
PRUNE_AT_LINES = 4000
KEEP_IMPORTANT_DAYS = 60
RECENCY_HALF_LIFE_H = 72


def remember(name: str, text: str, importance: int = 5, with_: list[str] | None = None, source: str = "",
             permanent: bool = False) -> None:
    """permanent=True(투표/모임 결론 같은 공동체 결정)는 정리(prune)해도 절대 빠지지 않는다."""
    text = str(text or "").strip()
    if not name or not text:
        return
    try:
        importance = max(1, min(10, int(importance)))
    except (TypeError, ValueError):
        importance = 5
    entry = {
        "name": name, "at": now_kst().isoformat(timespec="minutes"), "text": text[:200],
        "importance": importance, "with": [w for w in (with_ or []) if w and w != name][:5], "source": source,
    }
    if permanent:
        entry["permanent"] = True
    try:
        with open(MEMORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[경고] 기억 저장 실패: {e}")


def _load(name: str | None = None) -> list[dict]:
    if not os.path.exists(MEMORY_PATH):
        return []
    out = []
    with open(MEMORY_PATH, "r", encoding="utf-8") as f:
        for line in f:
            try:
                m = json.loads(line)
            except json.JSONDecodeError:
                continue
            if name is None or m.get("name") == name:
                out.append(m)
    return out


def _score(m: dict, now: datetime, terms: list[str]) -> float:
    try:
        age_h = max(0.0, (now - datetime.fromisoformat(m["at"])).total_seconds() / 3600)
    except (KeyError, ValueError):
        age_h = 24 * 30
    recency = 0.5 ** (age_h / RECENCY_HALF_LIFE_H)
    importance = m.get("importance", 5) / 10
    hay = m.get("text", "") + " " + " ".join(m.get("with", []))
    relevance = (sum(1 for t in terms if t and t in hay) / len(terms)) if terms else 0
    return 0.45 * recency + 0.35 * importance + 0.2 * relevance + (0.3 if m.get("permanent") else 0)


def recall(name: str, terms: list[str] | None = None, k: int = 4) -> list[dict]:
    """지금 상황(terms: 같이 있는 사람 이름, 화제 키워드)에 맞는 기억 상위 k개를 시간순으로."""
    now = now_kst()
    terms = [t for t in (terms or []) if t and t != name]
    mems = _load(name)
    if terms:
        # 정리돼서 보관함으로 간 오래된 기억도 지금 화제와 겹치면 떠올린다
        mems += [m for m in chronicle.archived_memories(name)
                 if any(t in m.get("text", "") + " " + " ".join(m.get("with", [])) for t in terms)]
    top = sorted(mems, key=lambda m: _score(m, now, terms), reverse=True)[:k]
    return sorted(top, key=lambda m: m.get("at", ""))


def prompt_block(name: str, terms: list[str] | None = None, k: int = 4) -> str:
    mems = recall(name, terms, k)
    if not mems:
        return ""
    lines = [f"[{name}의 기억 - 본인이 직접 겪은 일이다. 지금 상황과 관련 있으면 자연스럽게 떠올려라]"]
    for m in mems:
        lines.append(f"- {m.get('at', '')[5:10].replace('-', '/')} {m.get('text', '')}")
    return "\n".join(lines)


def apply_feedback(name: str, out: dict, source: str, with_: list[str] | None = None) -> None:
    """일지 LLM 출력의 memory({"text", "importance"})를 저장한다."""
    mem = out.get("memory") if isinstance(out, dict) else None
    if isinstance(mem, dict) and mem.get("text"):
        remember(name, mem["text"], mem.get("importance", 5), with_, source)


def prune() -> None:
    """파일이 커지면 인물별 최근 기억 + 오래 남길 중요한 기억만 남긴다."""
    mems = _load()
    if len(mems) < PRUNE_AT_LINES:
        return
    cutoff = (now_kst() - timedelta(days=KEEP_IMPORTANT_DAYS)).isoformat()
    by_name: dict[str, list[dict]] = {}
    for m in mems:
        by_name.setdefault(m.get("name", ""), []).append(m)
    keep = []
    for items in by_name.values():
        recent = items[-MAX_PER_PERSON:]
        older = items[:-MAX_PER_PERSON]
        important = [m for m in older if m.get("permanent")
                     or (m.get("importance", 0) >= 8 and m.get("at", "") >= cutoff)]
        keep.extend(important + recent)
    keep.sort(key=lambda m: m.get("at", ""))
    kept = {id(m) for m in keep}
    chronicle.archive_memories([m for m in mems if id(m) not in kept])
    tmp = MEMORY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for m in keep:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    os.replace(tmp, MEMORY_PATH)
    print(f"[기억] 정리: {len(mems)} -> {len(keep)}개")


FEEDBACK_SPEC = (
    '"memory": {"text": "이 일을 겪은 본인이 오래 기억할 만한 경험 한 줄(누구와 무엇을, 어떤 감정이었는지)", '
    '"importance": 1~10 정수(평범한 일상 2~4, 마음에 남는 일 5~7, 인생 사건 8~10)}'
)
