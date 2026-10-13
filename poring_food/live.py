"""
준실시간 대화 - 대화를 매시 회차에 미리 다 만들어 두지 않고, 그 대화가 시작되는 실제 시각에 만든다.

예전: 03분 회차가 한 시간치 대화를 전부 만들고(그때 LLM에게 "지금은 15:03"), 예약함이 15:05~15:55에 나눠 올렸다.
  → 15:35에 올라온 대화가 "15시에 회의하자"고 하고, 같은 시간에 벌어진 다른 대화/소식을 서로 모른 채 문맥이 어긋났다.
이제: 03분 회차는 판단(누가 누구에게 연락할지)까지만 하고, 대화는 여기 작업으로 넣는다(시작 시각 = 판단이 정한 시각).
  봇(cogs/poring_food.py)이 20초마다 시각이 된 작업을 하나씩 꺼내 그 순간의 시각/상태/받은편지함으로 대화를 만들고
  바로 올린다. 대화가 끝나면 그 자리에서 파급 반응을 고민하고, 반응 대화는 몇 분 뒤 작업으로 이어 붙인다.

- 회차/모임/실시간 대화는 봇의 같은 잠금으로 하나씩만 돈다 (상태 파일을 동시에 고치지 않게).
- 봇이 꺼졌다 켜지면 STALE_MIN분 넘게 늦은 작업은 버린다 (다음 시간에 지난 대화가 뒤늦게 나오지 않게).
- 봇에 안 붙어 있거나(테스트/단독 실행) PORING_LIVE=0이면 예전처럼 회차 안에서 바로 만든다.
저장: world.db live_jobs (끝난 작업은 done=1, 버린 건 done=2 - 3일 지나면 지운다)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from . import outbox, store
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)

ENABLED = os.getenv("PORING_LIVE", "1") not in ("0", "false", "False", "")
STALE_MIN = 40
KEEP_DAYS = 3
_SCHEMA = """CREATE TABLE IF NOT EXISTS live_jobs (
    id INTEGER PRIMARY KEY, due TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, done INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_live_due ON live_jobs(done, due);"""


def active() -> bool:
    """준실시간으로 돌리는지 (봇에 붙어서 예약함을 쓸 때만)."""
    return ENABLED and outbox.paced()


def _conn():
    conn = store.connect()
    conn.executescript(_SCHEMA)
    return conn


def enqueue(due: datetime, kind: str, **payload) -> None:
    outbox.SCHEDULED["live"] = outbox.SCHEDULED.get("live", 0) + 1  # 이상 감지: 이번 회차에 만든 일로 센다
    conn = _conn()
    try:
        with conn:
            conn.execute("INSERT INTO live_jobs (due, kind, payload) VALUES (?, ?, ?)",
                         (due.isoformat(timespec="seconds"), kind, json.dumps(payload, ensure_ascii=False)))
    finally:
        conn.close()


def take_one(now: datetime | None = None) -> dict | None:
    """시각이 된 작업 하나 (가장 이른 것). 너무 늦은 건 버린다. 꺼낸 작업은 끝난 것으로 표시한다."""
    now = now or now_kst()
    stale = (now - timedelta(minutes=STALE_MIN)).isoformat(timespec="seconds")
    conn = _conn()
    try:
        with conn:
            dropped = conn.execute("UPDATE live_jobs SET done=2 WHERE done=0 AND due < ?", (stale,)).rowcount
            row = conn.execute("SELECT id, due, kind, payload FROM live_jobs WHERE done=0 AND due <= ? ORDER BY due, id LIMIT 1",
                               (now.isoformat(timespec="seconds"),)).fetchone()
            if row:
                conn.execute("UPDATE live_jobs SET done=1 WHERE id=?", (row[0],))
            conn.execute("DELETE FROM live_jobs WHERE done>0 AND due < ?",
                         ((now - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds"),))
    finally:
        conn.close()
    if dropped:
        print(f"[실시간] {STALE_MIN}분 넘게 늦은 대화 {dropped}개는 버림")
    if not row:
        return None
    return {"id": row[0], "due": row[1], "kind": row[2], **json.loads(row[3])}


def pending(hour_key: str | None = None) -> int:
    conn = _conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM live_jobs WHERE done=0").fetchone()[0]
    finally:
        conn.close()


def run_one(now: datetime | None = None) -> bool:
    """[봇이 20초마다, 잠금 안에서] 시각이 된 대화 하나를 지금 만든다. 반환: 하나라도 했는지."""
    job = take_one(now)
    if not job:
        return False
    from . import agents  # agents가 live를 쓴다 - 순환 import 피하려고 여기서
    try:
        if job["kind"] == "convo":
            agents.run_live_conversation(job)
    except Exception as e:  # noqa: BLE001 - 대화 하나가 실패해도 다음 작업은 돈다
        print(f"[경고] 실시간 대화 실패: {e}")
    return True
