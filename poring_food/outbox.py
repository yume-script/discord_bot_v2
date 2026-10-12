"""
보낼 글 예약함 - 대화/단톡방/장면을 한꺼번에 쏟아내지 않고, 이야기 속 시각에 맞춰 사람이 하듯 차례대로 올린다.

매시 회차는 몇 분 안에 그 시간의 대화를 전부 만들어 낸다(LLM). 예전엔 만드는 족족 바로 올려서 한 시간치 대화가
몇 분 사이에 몰려 올라왔고, 연재 장면(카톡/디스코드)은 대사 전체가 한 메시지로 갔다. 이제는:

- 만드는 쪽(agents.py, main.py)은 글마다 "올릴 시각(due)"을 정해 여기에 넣는다.
  예) 처음 대화는 HH:05~17 사이에 시작, 파급 반응 대화는 HH:20/35/50 무렵, 한 마디 다음 마디는 글 길이만큼 20~90초 뒤.
  서로 다른 곳의 대화는 각자 시간표대로라서 중간에 자연스럽게 끼어든다.
- 봇(cogs/poring_food.py)이 몇 초마다 due가 지난 글을 시각 순서대로 꺼내 보낸다 (디스코드 에이전트 채널/단톡방/
  포링푸드 채널, 카톡 방).
- 봇이 꺼져 있다 켜지면 STALE_MIN분 넘게 지난 글은 보내지 않고 버린다 (한꺼번에 쏟아지지 않게).
- 봇에 붙어 있지 않을 때(테스트/단독 실행)나 PORING_PACED=0이면 예전처럼 바로 보낸다.

저장: world.db outbox 테이블 (보낸 글은 sent=1, 버린 글은 sent=2로 남는다 - 7일 지나면 지운다).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from . import runtime, store
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .clock import now_kst

PACED = os.getenv("PORING_PACED", "1") not in ("0", "false", "False", "")
STALE_MIN = 90       # 이만큼 넘게 늦은 글은 버린다 (봇이 오래 꺼졌다 켜진 경우)
KEEP_DAYS = 7
_SCHEMA = """CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY, due TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL, sent INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(sent, due);"""


def paced() -> bool:
    """예약해서 보낼지 (봇에 붙어 있고 PORING_PACED가 켜져 있을 때만)."""
    return PACED and runtime.is_bound()


def _conn():
    conn = store.connect()
    conn.executescript(_SCHEMA)
    return conn


def _deliver_now(kind: str, p: dict) -> None:
    """예약 없이 바로 (봇에 안 붙어 있으면 로그로만)."""
    if kind == "agent":
        if not runtime.has_agent_sender():
            print(f"[에이전트] (채널 미설정) {p['name']}: {p['text']}")
            return
        runtime.send_as(p.get("account", ""), p["name"], p["text"], p.get("avatar", ""), channel_id=p.get("channel_id", 0))
    elif kind == "discord":
        from . import notifier
        notifier.send_to_discord(p["text"])
    elif kind == "kakao":
        from . import notifier
        notifier.send_to_local_bot(p["text"])


def schedule(due: datetime | None, kind: str, **payload) -> None:
    """글 하나를 due 시각에 보내도록 예약 (kind: agent/discord/kakao). 예약을 안 쓰는 환경이면 바로 보낸다."""
    if not paced():
        try:
            _deliver_now(kind, payload)
        except Exception as e:  # noqa: BLE001
            print(f"[경고] 전송 실패 ({kind}): {e}")
        return
    due = due or now_kst()
    conn = _conn()
    try:
        with conn:
            conn.execute("INSERT INTO outbox (due, kind, payload) VALUES (?, ?, ?)",
                         (due.isoformat(timespec="seconds"), kind, json.dumps(payload, ensure_ascii=False)))
    finally:
        conn.close()


def take_due(now: datetime | None = None, limit: int = 20) -> list[dict]:
    """[봇이 몇 초마다] 보낼 때가 된 글 (시각 순서). 꺼낸 글은 보낸 것으로 표시한다. 너무 늦은 글은 버린다."""
    now = now or now_kst()
    stale = (now - timedelta(minutes=STALE_MIN)).isoformat(timespec="seconds")
    conn = _conn()
    try:
        with conn:
            dropped = conn.execute("UPDATE outbox SET sent=2 WHERE sent=0 AND due < ?", (stale,)).rowcount
            rows = conn.execute("SELECT id, due, kind, payload FROM outbox WHERE sent=0 AND due <= ? ORDER BY due, id LIMIT ?",
                                (now.isoformat(timespec="seconds"), limit)).fetchall()
            if rows:
                conn.executemany("UPDATE outbox SET sent=1 WHERE id=?", [(r[0],) for r in rows])
            conn.execute("DELETE FROM outbox WHERE sent>0 AND due < ?",
                         ((now - timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds"),))
    finally:
        conn.close()
    if dropped:
        print(f"[예약함] {STALE_MIN}분 넘게 늦은 글 {dropped}개는 버림")
    return [{"id": r[0], "due": r[1], "kind": r[2], **json.loads(r[3])} for r in rows]


def pending() -> int:
    conn = _conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM outbox WHERE sent=0").fetchone()[0]
    finally:
        conn.close()


# ===================================================================== 시각 계산
def slot(now: datetime, lo: int, hi: int, rnd) -> datetime:
    """이번 시간 HH:lo ~ HH:hi 사이 무작위 시각 (이미 지났으면 지금부터 조금 뒤)."""
    t = now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=rnd.uniform(lo, hi))
    return t if t > now else now + timedelta(seconds=rnd.uniform(5, 40))


def gap(text: str, rnd, messenger: bool = False) -> timedelta:
    """다음 마디까지 걸리는 시간 - 말이 길수록 오래, 메신저는 답장이 더 늦다 (20~90초 / 메신저 40~180초)."""
    base = 12 + len(text) * 0.5 + rnd.uniform(0, 20)
    if messenger:
        base = base * 1.8 + rnd.uniform(10, 40)
    return timedelta(seconds=min(180 if messenger else 90, max(20 if not messenger else 40, base)))
