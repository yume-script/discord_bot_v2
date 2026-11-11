"""
가상 세계 영구 기록 - SQLite (storage/poring_food/world.db).

계속 쌓이기만 하는 기록을 jsonl 파일 대신 여기에 둔다. jsonl은 읽을 때마다 파일 전체를 훑어서, 몇 달 쌓이면
매시 회차가 점점 느려진다. SQLite는 이름/날짜 색인으로 필요한 줄만 꺼낸다.

- memories : 인물 기억 (archived=1이면 정리돼서 평소엔 안 떠올리는 기억 - 지우지 않는다, permanent=1은 절대 정리 안 됨)
- events   : 세계에서 일어난 일 (세계 엔진/소문/업무/모임/투표/프로젝트/교체/이사...)
- actions  : 에이전트 매시 판단 (어디서 무엇을, 속마음)
- memory_vecs : 기억 의미 검색용 벡터 (semantic.py, 기억 id별)

처음 열 때 예전 jsonl(memories.jsonl, archive/memories.jsonl, archive/events.jsonl, archive/actions.jsonl)을
한 번 옮겨 담고, 원래 파일은 *.imported로 이름만 바꿔 백업으로 남긴다.
대화 원문(dialogues.jsonl)/단톡방(group_chat.jsonl)/연대기 요약(chronicle.jsonl)은 다른 모듈이 그대로 쓴다.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

DB_PATH = os.path.join(STATE_DIR, "world.db")
_LEGACY = {
    "memories": os.path.join(STATE_DIR, "memories.jsonl"),
    "memories_archived": os.path.join(STATE_DIR, "archive", "memories.jsonl"),
    "events": os.path.join(STATE_DIR, "archive", "events.jsonl"),
    "actions": os.path.join(STATE_DIR, "archive", "actions.jsonl"),
}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, at TEXT NOT NULL, text TEXT NOT NULL,
    importance INTEGER DEFAULT 5, with_json TEXT DEFAULT '[]', source TEXT DEFAULT '',
    permanent INTEGER DEFAULT 0, archived INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_mem_name ON memories(name, archived, at);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT, title TEXT, detail TEXT,
    targets_json TEXT DEFAULT '[]', extra_json TEXT DEFAULT '{}');
CREATE INDEX IF NOT EXISTS idx_events_at ON events(at);
CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, name TEXT, location TEXT, activity TEXT, state TEXT,
    thought TEXT, plan TEXT);
CREATE INDEX IF NOT EXISTS idx_actions_at ON actions(at);
CREATE INDEX IF NOT EXISTS idx_actions_name ON actions(name, at);
CREATE TABLE IF NOT EXISTS memory_vecs (id INTEGER PRIMARY KEY, vec BLOB NOT NULL);
"""
_init_lock = threading.Lock()
_ready: set[str] = set()


def connect() -> sqlite3.Connection:
    """호출마다 새 연결 (봇 스레드/회차 스레드가 같이 써도 안전하게). 처음이면 테이블 만들고 예전 jsonl을 옮긴다."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    if DB_PATH not in _ready:
        with _init_lock:
            if DB_PATH not in _ready:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(_SCHEMA)
                _import_legacy(conn)
                _ready.add(DB_PATH)
    return conn


@contextmanager
def _db():
    """연결을 열고, 끝나면 커밋(예외면 롤백)하고 닫는다."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _import_legacy(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM meta WHERE key='imported_v1'").fetchone():
        return
    counts = {}
    for key, path in _LEGACY.items():
        n = 0
        for r in _jsonl(path):
            if key.startswith("memories"):
                if not r.get("name") or not r.get("text"):
                    continue
                conn.execute(
                    "INSERT INTO memories (name, at, text, importance, with_json, source, permanent, archived) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (r["name"], r.get("at", ""), r["text"], int(r.get("importance", 5) or 5),
                     json.dumps(r.get("with") or [], ensure_ascii=False), r.get("source", ""),
                     1 if r.get("permanent") else 0, 1 if key == "memories_archived" else 0))
            elif key == "events":
                extra = {k: v for k, v in r.items() if k not in ("at", "kind", "title", "detail", "targets")}
                conn.execute("INSERT INTO events (at, kind, title, detail, targets_json, extra_json) VALUES (?,?,?,?,?,?)",
                             (r.get("at", ""), r.get("kind", ""), r.get("title", ""), r.get("detail", ""),
                              json.dumps(r.get("targets") or [], ensure_ascii=False),
                              json.dumps(extra, ensure_ascii=False)))
            else:
                conn.execute("INSERT INTO actions (at, name, location, activity, state, thought, plan) VALUES (?,?,?,?,?,?,?)",
                             (r.get("at", ""), r.get("name", ""), r.get("location", ""), r.get("activity", ""),
                              r.get("state", ""), r.get("thought", ""), r.get("plan", "")))
            n += 1
        counts[key] = n
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('imported_v1', ?)", (json.dumps(counts),))
    conn.commit()
    for key, path in _LEGACY.items():  # 옮긴 파일은 지우지 않고 백업으로
        if os.path.exists(path):
            os.replace(path, path + ".imported")
    if any(counts.values()):
        print(f"[기록] 예전 jsonl을 SQLite(world.db)로 옮김: {counts}")


# ===================================================================== 기억
def _mem(row: sqlite3.Row) -> dict:
    m = {"name": row["name"], "at": row["at"], "text": row["text"], "importance": row["importance"],
         "with": json.loads(row["with_json"] or "[]"), "source": row["source"], "id": row["id"]}
    if row["permanent"]:
        m["permanent"] = True
    return m


def add_memory(name: str, at: str, text: str, importance: int, with_: list[str], source: str, permanent: bool) -> None:
    with _db() as conn:
        conn.execute("INSERT INTO memories (name, at, text, importance, with_json, source, permanent) VALUES (?,?,?,?,?,?,?)",
                     (name, at, text, importance, json.dumps(with_, ensure_ascii=False), source, 1 if permanent else 0))


def memories(name: str | None = None, archived: bool = False, like: list[str] | None = None) -> list[dict]:
    """시간순 기억. like가 있으면 그 말 중 하나라도 들어 있는 것만 (text/함께한 사람)."""
    sql = "SELECT * FROM memories WHERE archived=?"
    args: list = [1 if archived else 0]
    if name is not None:
        sql += " AND name=?"
        args.append(name)
    if like:
        sql += " AND (" + " OR ".join(["text LIKE ? OR with_json LIKE ?"] * len(like)) + ")"
        for t in like:
            args += [f"%{t}%", f"%{t}%"]
    sql += " ORDER BY at, id"
    with _db() as conn:
        return [_mem(r) for r in conn.execute(sql, args)]


def count_memories() -> int:
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM memories WHERE archived=0").fetchone()[0]


def archive_memory_ids(ids: list[int]) -> None:
    if not ids:
        return
    with _db() as conn:
        conn.executemany("UPDATE memories SET archived=1 WHERE id=?", [(i,) for i in ids])


def memory_vecs(ids: list[int]) -> dict[int, bytes]:
    if not ids:
        return {}
    out: dict[int, bytes] = {}
    with _db() as conn:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = "SELECT id, vec FROM memory_vecs WHERE id IN (" + ",".join("?" * len(chunk)) + ")"
            out.update({r[0]: r[1] for r in conn.execute(q, chunk)})
    return out


def set_memory_vecs(rows: dict[int, bytes]) -> None:
    if not rows:
        return
    with _db() as conn:
        conn.executemany("INSERT OR REPLACE INTO memory_vecs (id, vec) VALUES (?, ?)", list(rows.items()))


def memory_names() -> set[str]:
    with _db() as conn:
        return {r[0] for r in conn.execute("SELECT DISTINCT name FROM memories")}


def replace_name(old: str, new: str) -> None:
    """이름 변경 마이그레이션용 (기억/사건/행동 기록 속 이름)."""
    with _db() as conn:
        conn.execute("UPDATE memories SET name=? WHERE name=?", (new, old))
        conn.execute("UPDATE memories SET text=replace(text, ?, ?), with_json=replace(with_json, ?, ?)", (old, new, old, new))
        conn.execute("UPDATE actions SET name=? WHERE name=?", (new, old))
        conn.execute("UPDATE events SET title=replace(title, ?, ?), detail=replace(detail, ?, ?), "
                     "targets_json=replace(targets_json, ?, ?)", (old, new, old, new, old, new))


# ===================================================================== 사건 / 행동
def add_event(at: str, kind: str, title: str, detail: str, targets: list[str], extra: dict) -> None:
    with _db() as conn:
        conn.execute("INSERT INTO events (at, kind, title, detail, targets_json, extra_json) VALUES (?,?,?,?,?,?)",
                     (at, kind, title, detail, json.dumps(targets, ensure_ascii=False), json.dumps(extra, ensure_ascii=False)))


def events_on(date: str) -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE at LIKE ? ORDER BY at, id", (f"{date}%",)).fetchall()
    return [{"at": r["at"], "kind": r["kind"], "title": r["title"], "detail": r["detail"],
             "targets": json.loads(r["targets_json"] or "[]"), **json.loads(r["extra_json"] or "{}")} for r in rows]


def add_action(at: str, name: str, d: dict) -> None:
    with _db() as conn:
        conn.execute("INSERT INTO actions (at, name, location, activity, state, thought, plan) VALUES (?,?,?,?,?,?,?)",
                     (at, name, d.get("location", ""), d.get("activity", ""), d.get("state", ""),
                      d.get("thought", ""), d.get("plan", "")))


def actions_on(date: str) -> list[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM actions WHERE at LIKE ? ORDER BY at, id", (f"{date}%",)).fetchall()
    return [dict(r) for r in rows]
