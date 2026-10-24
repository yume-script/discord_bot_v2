"""
반복 방지 - 몇 주 돌아가도 같은 얘기를 처음 하는 것처럼 되풀이하지 않게.

예전 반복 방지는 짧은 창만 봤다 (세계 사건 최근 8개, 장면 최근 5개, 내 최근 행동 몇 시간). 그래서 2주 전에 한 얘기를
처음 하는 것처럼 다시 하고, 사건 종류가 비슷한 데서 맴돌았다. 이제는:

1) 화제 기록 (world.db topics): 대화/연재 장면/통신방 글/세계 사건을 한 줄씩 남긴다 (누가, 무엇을). 21일 지나면 지운다.
   의미 검색 모델(semantic.py, ko-sroberta)을 쓸 수 있으면 벡터도 같이 저장하고, 못 쓰면 글자 묶음(2글자) 겹침으로 비교한다.
2) 반복 감지:
   - hint(text, who): 대화 첫마디/장면이 최근 14일의 화제와 비슷하면 "이 얘기는 N번 했다 - 진전시키거나 다른 얘기" 안내.
   - worn_out(name): 그 사람이 최근 14일 동안 3번 넘게 한 비슷한 얘기 묶음 - 판단할 때 "요즘 지겹게 한 얘기"로 보여준다.
3) 사건 다양성 (세계 엔진): 사건마다 종류(EVENT_KINDS)를 붙여 두고, 최근 14일에 많이 나온 종류는 피하고 거의 안 나온 종류를
   넣으라고 알려 준다 (kind_balance). 실제 달력의 공휴일/기념일/계절 행사도 다가오면 알려 준다 (calendar_hint).
LLM 호출은 늘지 않는다 (프롬프트에 몇 줄 더 들어갈 뿐).
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta

from . import semantic, store
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)

KEEP_DAYS = 21
WINDOW_DAYS = 14
SEM_SIMILAR = 0.72      # 의미 벡터 코사인 유사도 - 이 이상이면 "비슷한 얘기"
TEXT_SIMILAR = 0.30     # 벡터가 없을 때: 2글자 묶음 겹침(자카드)
HINT_MIN = 2            # 비슷한 얘기가 이만큼 있으면 안내
WORN_MIN = 3            # 한 사람이 이만큼 한 묶음은 "지겹게 한 얘기"
MAX_SCAN = 600          # 한 번에 비교하는 최근 화제 수

EVENT_KINDS = ["연애/설렘", "돈/경제", "사고/고장", "건강", "가족", "외부 손님/낯선 사람", "축제/행사", "날씨/계절",
               "경쟁 기관/왕도", "취미/여가", "갈등/다툼", "행운/좋은 소식", "마을 변화(개업/폐업/공사)", "기관 인사/평가",
               "실수/해프닝", "추억/과거", "몬스터/숲의 기척", "마법/수상한 현상"]

_SCHEMA = """CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, who TEXT NOT NULL, text TEXT NOT NULL, vec BLOB);
CREATE INDEX IF NOT EXISTS idx_topics_at ON topics(at);"""


def _conn():
    conn = store.connect()
    conn.executescript(_SCHEMA)
    return conn


def _bigrams(text: str) -> set[str]:
    t = "".join(ch for ch in (text or "") if ch.isalnum())
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _text_sim(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _vec(text: str):
    if not semantic.ENABLED:
        return None
    try:
        v = semantic._encode([text[:300]])
        return v[0] if v else None
    except Exception:  # noqa: BLE001 - 벡터가 없으면 글자 비교로
        return None


def note(kind: str, text: str, who: list[str] | None = None, at: datetime | None = None) -> None:
    """화제 하나를 남긴다 (kind: 대화/장면/통신방/사건)."""
    text = " ".join(str(text or "").split())[:300]
    if len(text) < 4:
        return
    at = at or now_kst()
    v = _vec(text)
    try:
        conn = _conn()
        try:
            with conn:
                conn.execute("INSERT INTO topics (at, kind, who, text, vec) VALUES (?, ?, ?, ?, ?)",
                             (at.isoformat(timespec="minutes"), kind, json.dumps(who or [], ensure_ascii=False), text,
                              semantic._pack(v) if v is not None else None))
                conn.execute("DELETE FROM topics WHERE at < ?",
                             ((at - timedelta(days=KEEP_DAYS)).isoformat(timespec="minutes"),))
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        print(f"[경고] 화제 기록 실패: {e}")


def _recent(days: int = WINDOW_DAYS, who: list[str] | None = None, now: datetime | None = None,
            kinds: tuple[str, ...] | None = None) -> list[dict]:
    now = now or now_kst()
    since = (now - timedelta(days=days)).isoformat(timespec="minutes")
    try:
        conn = _conn()
        try:
            rows = conn.execute("SELECT at, kind, who, text, vec FROM topics WHERE at >= ? ORDER BY id DESC LIMIT ?",
                                (since, MAX_SCAN)).fetchall()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        names = json.loads(r["who"] or "[]")
        if who and not set(who) & set(names):
            continue
        if kinds and r["kind"] not in kinds:
            continue
        out.append({"at": r["at"], "kind": r["kind"], "who": names, "text": r["text"],
                    "vec": semantic._unpack(r["vec"]) if r["vec"] else None, "bg": _bigrams(r["text"])})
    return out


def _similar(x: dict, y: dict) -> bool:
    if x.get("vec") is not None and y.get("vec") is not None:
        return semantic._dot(x["vec"], y["vec"]) >= SEM_SIMILAR
    return _text_sim(x["bg"], y["bg"]) >= TEXT_SIMILAR


def repeats(text: str, who: list[str] | None = None, now: datetime | None = None) -> list[dict]:
    """최근 14일 동안 이 얘기와 비슷했던 화제 (who가 있으면 그 사람이 낀 것만)."""
    text = " ".join(str(text or "").split())
    if len(text) < 4:
        return []
    q = {"vec": _vec(text), "bg": _bigrams(text)}
    return [r for r in _recent(who=who, now=now) if _similar(q, r)]


def hint(text: str, who: list[str] | None = None, now: datetime | None = None) -> str:
    """대화/장면 프롬프트에 넣는 안내 (반복이 아니면 "")."""
    same = repeats(text, who, now)
    if len(same) < HINT_MIN:
        return ""
    ex = " / ".join(f"{r['at'][5:10].replace('-', '/')} {r['text'][:50]}" for r in same[:3])
    return (f"[이 얘기는 처음이 아니다] 최근 {WINDOW_DAYS}일 동안 비슷한 얘기를 {len(same)}번 했다 ({ex}). "
            "같은 말을 되풀이하지 말고, 그 뒤로 달라진 점·새 진전·결론을 이야기하거나 자연스럽게 다른 얘기로 넘어가라.")


def _clusters(items: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    for it in items:
        for g in groups:
            if _similar(it, g[0]):
                g.append(it)
                break
        else:
            groups.append([it])
    return groups


_worn_cache: dict[tuple[str, str], str] = {}


def worn_out(name: str, now: datetime | None = None, limit: int = 4) -> str:
    """판단 프롬프트용: 이 사람이 요즘 지겹게 한 얘기 (없으면 ""). 한 시간에 한 번만 계산한다."""
    now = now or now_kst()
    key = (name, now.strftime("%Y-%m-%d %H"))
    if key not in _worn_cache:
        if len(_worn_cache) > 500:
            _worn_cache.clear()
        _worn_cache[key] = _worn_out(name, now, limit)
    return _worn_cache[key]


def _worn_out(name: str, now: datetime, limit: int) -> str:
    mine = _recent(who=[name], now=now, kinds=("대화", "장면", "통신방"))
    groups = sorted((g for g in _clusters(mine) if len(g) >= WORN_MIN), key=len, reverse=True)[:limit]
    if not groups:
        return ""
    return ("[요즘 지겹게 한 얘기 - 또 꺼내지 마라. 꺼내려면 새 진전이 있을 때만]\n"
            + "\n".join(f"- {g[-1]['text'][:60]} (최근 {WINDOW_DAYS}일 {len(g)}번)" for g in groups))


# ===================================================================== 사건 다양성 (세계 엔진)
def kind_balance(log: list[dict], now: datetime | None = None) -> str:
    """세계 엔진 프롬프트용: 최근 14일 사건 종류 분포 - 많이 나온 건 피하고, 안 나온 건 넣게."""
    now = now or now_kst()
    since = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    counts = Counter(e.get("kind") for e in log if e.get("date", "") >= since and e.get("kind") in EVENT_KINDS)
    lines = [f"[사건 종류 - 반드시 이 중에서 kind를 골라라] {', '.join(EVENT_KINDS)}"]
    if counts:
        lines.append("[최근 2주 많이 나온 종류 - 오늘은 되도록 피해라] "
                     + ", ".join(f"{k}({n})" for k, n in counts.most_common(4)))
    rare = [k for k in EVENT_KINDS if counts.get(k, 0) == 0]
    if rare:
        lines.append("[최근 2주 한 번도 안 나온 종류 - 오늘 이 중 2개 이상 넣어라] " + ", ".join(rare[:6]))
    return "\n".join(lines)


# 날짜가 해마다 바뀌는 명절 (양력)
_LUNAR = {"설날": ["2026-02-17", "2027-02-07", "2028-01-27", "2029-02-13", "2030-02-03"],
          "추석": ["2026-09-25", "2027-09-15", "2028-10-03", "2029-09-22", "2030-09-12"]}
_FIXED = {(1, 1): "신정", (2, 14): "밸런타인데이", (3, 1): "삼일절", (3, 14): "화이트데이", (4, 5): "식목일",
          (5, 1): "근로자의 날", (5, 5): "어린이날", (5, 8): "어버이날", (5, 15): "스승의 날", (6, 6): "현충일",
          (8, 15): "광복절", (10, 3): "개천절", (10, 9): "한글날", (10, 31): "핼러윈", (11, 11): "빼빼로데이",
          (12, 24): "크리스마스이브", (12, 25): "크리스마스", (12, 31): "연말"}
_SEASON = {1: "새해 다짐·연말정산", 2: "졸업 시즌", 3: "입학·새 학기, 봄맞이", 4: "벚꽃 구경", 5: "가정의 달, 나들이",
           6: "초여름, 상반기 마감", 7: "장마", 8: "휴가철·무더위", 9: "환절기", 10: "단풍·가을 축제",
           11: "수능·김장철", 12: "송년회·연말 분위기"}


def calendar_hint(now: datetime | None = None, ahead: int = 3) -> str:
    """오늘~며칠 뒤의 공휴일/기념일/명절 + 이달의 계절 행사 (세계 엔진이 실제 달력에 맞는 사건을 넣게)."""
    now = now or now_kst()
    days = []
    for d in range(ahead + 1):
        day = now + timedelta(days=d)
        names = [_FIXED[(day.month, day.day)]] if (day.month, day.day) in _FIXED else []
        names += [k for k, v in _LUNAR.items() if day.strftime("%Y-%m-%d") in v]
        for n in names:
            days.append(f"{n}({'오늘' if d == 0 else f'{d}일 뒤'})")
    out = f"[달력] 이달 분위기: {_SEASON[now.month]}"
    if days:
        out += " / 다가오는 날: " + ", ".join(days) + " - 어울리는 사건을 하나쯤 넣어라"
    return out
