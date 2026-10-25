"""
[2단계] 인물별 장기 기억 - 대화 기록을 그대로 쌓는 게 아니라 "경험"을 한 줄 기억으로 압축해 둔다.

예) "10/03 마을 찻집에서 일레이나 과장이 내가 인터스텔라 좋아하는 걸 기억하고 있었다 (중요도 7)"

- 저장: storage/poring_food/world.db memories 테이블 (store.py) {"name", "at", "text", "importance"(1~10), "with"[이름], "source"}
- 만드는 곳: 일지/장면 LLM이 JSON으로 같이 돌려준 memory(추가 LLM 호출 없음), 월급날 같은 규칙 사건
- 떠올리기(recall): 최근일수록 + 중요할수록 + 지금 상황과 관련 있을수록 점수가 높다. 프롬프트에는 상위 몇 개만.
  관련도는 키워드(같이 있는 사람, 화제 단어)와 의미 검색(semantic.py - 로컬 임베딩, 단어가 달라도 뜻이 비슷하면) 중
  높은 쪽. 의미 검색을 못 쓰는 환경이면 키워드만.
- 정리(prune): 파일이 커지면 인물별 최근 MAX_PER_PERSON개 + 중요도 8 이상(60일 이내) + 영구 기억(permanent,
  투표/모임 결론)만 남긴다.
  빠진 기억은 지우지 않고 보관 표시(archived)만 하고, 지금 상황과 관련 있으면
  (화제 키워드가 겹치면) 떠올리기에서 다시 꺼내 쓴다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from . import semantic, store
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
    try:
        store.add_memory(name, now_kst().isoformat(timespec="minutes"), text[:200], importance,
                         [w for w in (with_ or []) if w and w != name][:5], source, permanent)
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 기억 저장 실패: {e}")


def _load(name: str | None = None) -> list[dict]:
    """평소 떠올리는(정리 안 된) 기억, 시간순."""
    return store.memories(name)


def all_names() -> set[str]:
    """기억이 한 줄이라도 있는 이름 전부 (예전에 있었던 사람 포함)."""
    return store.memory_names()


ARCHIVED_SEMANTIC_MAX = 300  # 의미 검색으로 같이 훑는 정리된(오래된) 기억 수 - 최근 것부터


def _semantic_rel(cos: float) -> float:
    """임베딩 유사도(대략 0.2~0.8) -> 관련도 0~1."""
    return max(0.0, min(1.0, (cos - 0.3) / 0.4))


def _score(m: dict, now: datetime, terms: list[str], sem: dict[int, float] | None = None) -> float:
    try:
        age_h = max(0.0, (now - datetime.fromisoformat(m["at"])).total_seconds() / 3600)
    except (KeyError, ValueError):
        age_h = 24 * 30
    recency = 0.5 ** (age_h / RECENCY_HALF_LIFE_H)
    importance = m.get("importance", 5) / 10
    hay = m.get("text", "") + " " + " ".join(m.get("with", []))
    relevance = (sum(1 for t in terms if t and t in hay) / len(terms)) if terms else 0
    if sem:
        relevance = max(relevance, _semantic_rel(sem.get(m.get("id"), 0.0)))
        return 0.35 * recency + 0.3 * importance + 0.35 * relevance + (0.3 if m.get("permanent") else 0)
    return 0.45 * recency + 0.35 * importance + 0.2 * relevance + (0.3 if m.get("permanent") else 0)


def recall(name: str, terms: list[str] | None = None, k: int = 4, query: str = "") -> list[dict]:
    """지금 상황(terms: 같이 있는 사람 이름, 화제 키워드 / query: 지금 상황을 풀어 쓴 글)에 맞는 기억 상위 k개를 시간순으로."""
    now = now_kst()
    terms = [t for t in (terms or []) if t and t != name]
    mems = _load(name)
    seen = {m.get("id") for m in mems}
    if terms:
        # 정리된 오래된 기억도 지금 화제와 겹치면 떠올린다 (색인으로 그 사람 것만 꺼낸다)
        mems += [m for m in store.memories(name, archived=True, like=terms) if m.get("id") not in seen]
    sem = {}
    if query and semantic.ENABLED:
        if semantic.available():
            seen = {m.get("id") for m in mems}
            old = store.memories(name, archived=True)[-ARCHIVED_SEMANTIC_MAX:]
            mems += [m for m in old if m.get("id") not in seen]
        sem = semantic.similarities(query, mems)
    top = sorted(mems, key=lambda m: _score(m, now, terms, sem), reverse=True)[:k]
    return sorted(top, key=lambda m: m.get("at", ""))


def prompt_block(name: str, terms: list[str] | None = None, k: int = 4, query: str = "") -> str:
    mems = recall(name, terms, k, query)
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
    """기억이 많아지면 인물별 최근 기억 + 오래 남길 중요한 기억 + 영구 기억만 평소에 떠올리고, 나머지는 보관 표시(지우지 않음)."""
    if store.count_memories() < PRUNE_AT_LINES:
        return
    mems = _load()
    cutoff = (now_kst() - timedelta(days=KEEP_IMPORTANT_DAYS)).isoformat()
    by_name: dict[str, list[dict]] = {}
    for m in mems:
        by_name.setdefault(m.get("name", ""), []).append(m)
    archive = []
    for items in by_name.values():
        for m in items[:-MAX_PER_PERSON]:
            if not (m.get("permanent") or (m.get("importance", 0) >= 8 and m.get("at", "") >= cutoff)):
                archive.append(m["id"])
    store.archive_memory_ids(archive)
    print(f"[기억] 정리: {len(mems)} -> {len(mems) - len(archive)}개 (보관 {len(archive)}개)")


FEEDBACK_SPEC = (
    '"memory": {"text": "이 일을 겪은 본인이 오래 기억할 만한 경험 한 줄(누구와 무엇을, 어떤 감정이었는지)", '
    '"importance": 1~10 정수(평범한 일상 2~4, 마음에 남는 일 5~7, 인생 사건 8~10)}'
)
