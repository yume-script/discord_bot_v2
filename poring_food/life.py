"""
[1단계] 인물마다 수치 상태(감정/체력/돈/직무 만족/목표)를 두고, 사건이 다음 행동으로 이어지게 한다.

"페르소나"만 있으면 같은 질문에 늘 같은 반응이 나온다. 상태가 있어야 "아침 회의에서 깨짐 →
스트레스↑ → 퇴근 후 술자리 확률↑ → 친구와 수다 → 기분 회복"처럼 사건이 다음 행동에 영향을 준다.

- 상태 파일: storage/poring_food/life_state.json  {이름: {...}}
- 감정 6축(0~1): happiness(행복) stress(스트레스) loneliness(외로움) anger(분노) romance(설렘)
  confidence(자신감) + energy(체력), job_satisfaction(직무 만족), money(원), goals(목표)
- 변화는 두 갈래:
  1) 규칙(tick, LLM 없음): 매시 근무/휴식/수면에 따른 체력·스트레스 변화, 기준값으로 서서히 회복,
     월급날(25일)·월세(1일)·식비·여가비, 잔고 부족 스트레스, 바깥 세상 신호(월말 마감, 금요일, 비, 설비 이상)
  2) 사건(apply_changes): 일지/장면 LLM이 JSON으로 같이 돌려준 변화 제안. 한 번에 ±0.2까지만
     반영하고 돈은 LLM이 못 바꾼다(규칙으로만 움직임 - 세계의 규칙을 LLM이 우회하지 못하게).
- 행동 연결(bias_weight): 일정 후보의 가중치를 상태로 조정한다 (스트레스↑ → 술자리, 체력↓ → 집 휴식 등).
- 프롬프트(prompt_block): 수치를 말로 바꿔 일지/장면에 넣는다. "수치를 직접 말하지 말고 묻어나게".
"""
from __future__ import annotations

import json
import os
import random
from datetime import datetime

from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

LIFE_PATH = os.path.join(STATE_DIR, "life_state.json")

EMOTIONS = ("happiness", "stress", "loneliness", "anger", "romance", "confidence")
LEVELS = EMOTIONS + ("energy", "job_satisfaction")
LABELS = {
    "happiness": "행복", "stress": "스트레스", "loneliness": "외로움", "anger": "분노",
    "romance": "설렘", "confidence": "자신감", "energy": "체력", "job_satisfaction": "직무 만족",
}
MAX_DELTA = 0.2          # 사건 하나로 바뀔 수 있는 최대 폭
MAX_RECENT = 6           # 최근 변화 이유 보관 개수
LOW_MONEY = 300_000      # 이 밑이면 돈 걱정
PAYDAY = 25
RENT_DAY = 1

# 직급별 월급/월세 (원) - 대충 그럴듯한 값. 세계의 경제 규칙.
SALARY = {"사원": 2_800_000, "주임": 3_100_000, "대리": 3_500_000, "과장": 4_200_000,
          "차장": 4_800_000, "부장": 5_600_000}
RENT = {"사원": 550_000, "주임": 600_000, "대리": 650_000, "과장": 750_000, "차장": 850_000, "부장": 950_000}

# 개인 활동 비용 (활동/장소 텍스트에 키워드가 있으면 그 시간에 한 번 지출)
ACTIVITY_COST = [
    ("쇼핑", 70_000), ("영화", 15_000), ("맛집", 30_000), ("술", 40_000), ("포장마차", 35_000),
    ("소주", 35_000), ("데이트", 50_000), ("소개팅", 50_000), ("카페", 6_000), ("커피", 5_000),
    ("배달", 25_000), ("브런치", 20_000),
]

GOAL_POOL = [
    "올해 안에 승진하기", "돈 모아서 해외여행 가기", "건강 챙기기(운동 꾸준히)", "좋은 사람 만나기",
    "취미 하나 제대로 해보기", "회사에서 인정받기", "몰래 이직 준비하기", "가족과 시간 더 보내기",
    "빚 없이 적금 만기 채우기", "사내에서 믿을 만한 동료 만들기",
]
SPECIAL_GOALS = {
    "애순이": ["라그M 길드 공성전 우승", "연말 보너스까지 버티기 (이직은 일단 보류)", "안구건조증 낫게 하기"],
}


# ===================================================================== 저장
def _load() -> dict:
    try:
        with open(LIFE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    try:
        tmp = LIFE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LIFE_PATH)
    except OSError as e:
        print(f"[경고] 인물 상태 저장 실패: {e}")


def _clamp(v: float) -> float:
    return round(max(0.0, min(1.0, v)), 3)


def _new_state(name: str, rank: str) -> dict:
    """처음 보는 인물의 상태. 이름으로 시드를 고정해서 사람마다 다른 기질(기준값)을 갖게 한다."""
    rnd = random.Random(f"life:{name}")
    base = {
        "happiness": rnd.uniform(0.4, 0.65), "stress": rnd.uniform(0.25, 0.5),
        "loneliness": rnd.uniform(0.2, 0.5), "anger": rnd.uniform(0.05, 0.2),
        "romance": rnd.uniform(0.1, 0.4), "confidence": rnd.uniform(0.35, 0.7),
        "energy": rnd.uniform(0.6, 0.85), "job_satisfaction": rnd.uniform(0.35, 0.65),
    }
    if name == "애순이":  # 만성 피로의 고인물 대리
        base.update(stress=0.55, energy=0.45, job_satisfaction=0.4)
    base = {k: round(v, 3) for k, v in base.items()}
    salary = SALARY.get(rank, 3_200_000)
    return {
        **base,
        "baseline": dict(base),
        "rank": rank,
        "money": int(salary * rnd.uniform(0.6, 2.5)),
        "goals": SPECIAL_GOALS.get(name) or rnd.sample(GOAL_POOL, 2),
        "recent": [],
        # 처음 생긴 달은 월세/월급을 이미 처리한 걸로 본다 (생기자마자 돈이 빠지거나 들어오지 않게)
        "last_paid": now_kst().strftime("%Y-%m"), "last_rent": now_kst().strftime("%Y-%m"), "last_tick": "",
    }


def _ensure(data: dict, name: str, rank: str = "") -> dict:
    st = data.get(name)
    if not isinstance(st, dict):
        st = data[name] = _new_state(name, rank)
    elif rank and not st.get("rank"):
        st["rank"] = rank
    return st


def load_all() -> dict:
    return _load()


def get(name: str) -> dict:
    data = _load()
    return data.get(name) or _new_state(name, "")


# ===================================================================== 사건 -> 상태 변화
def _note(st: dict, reason: str, deltas: dict) -> None:
    if not reason:
        return
    st.setdefault("recent", []).append({
        "at": now_kst().isoformat(timespec="minutes"), "reason": reason[:80],
        "deltas": {k: round(v, 2) for k, v in deltas.items()},
    })
    st["recent"] = st["recent"][-MAX_RECENT:]


def apply_changes(name: str, deltas: dict, reason: str = "", rank: str = "") -> dict:
    """
    사건으로 인한 변화(LLM 제안 등)를 반영한다. 허용된 축만, 한 번에 ±MAX_DELTA까지.
    돈은 여기서 못 바꾼다(규칙으로만). 실제로 반영된 변화를 돌려준다.
    """
    applied = {}
    for key, raw in (deltas or {}).items():
        if key not in LEVELS:
            continue
        try:
            d = float(raw)
        except (TypeError, ValueError):
            continue
        d = max(-MAX_DELTA, min(MAX_DELTA, d))
        if abs(d) >= 0.005:
            applied[key] = d
    if not applied:
        return {}
    data = _load()
    st = _ensure(data, name, rank)
    for key, d in applied.items():
        st[key] = _clamp(st.get(key, 0.5) + d)
    _note(st, reason, applied)
    _save(data)
    return applied


def apply_feedback(name: str, out: dict, source: str, rank: str = "") -> None:
    """일지/장면 LLM 출력의 state_change를 반영한다 (형식이 틀리면 조용히 무시)."""
    change = out.get("state_change") if isinstance(out, dict) else None
    if isinstance(change, dict):
        reason = str(change.pop("reason", "") or source)
        applied = apply_changes(name, change, reason=reason, rank=rank)
        if applied:
            print(f"[상태] {name}: {reason} -> {applied}")


# ===================================================================== 매시 규칙 (LLM 없음)
def _cost_of(text: str) -> int:
    return max((cost for kw, cost in ACTIVITY_COST if kw in text), default=0)


def _signal_effects(st: dict, info: dict, signals: dict, now: datetime) -> dict:
    d: dict[str, float] = {}
    working = info.get("state") == "일하는 중"
    cal = signals.get("calendar", "")
    if working and "월말 마감" in cal:
        d["stress"] = d.get("stress", 0) + 0.02
    if "금요일" in cal and now.hour >= 17:
        d["happiness"] = d.get("happiness", 0) + 0.02
    weather = signals.get("weather", "")
    if any(w in weather for w in ("폭우", "폭염", "한파")):
        d["happiness"] = d.get("happiness", 0) - 0.02
        d["energy"] = d.get("energy", 0) - 0.02
    elif "비" in weather or "눈" in weather:
        d["happiness"] = d.get("happiness", 0) - 0.01
    if info.get("company", "포링푸드 (Poring Food)") != "포링푸드 (Poring Food)":
        return d  # 아래는 포링푸드 내부 사정(공장 설비/생산) - 다른 회사 사람에겐 상관없다
    facility = signals.get("facility", "") + signals.get("factory", "")
    if working and any(w in facility for w in ("과부하", "과열", "포화", "멈춘", "이상")):
        d["stress"] = d.get("stress", 0) + 0.02
    if working and "훨씬 바쁨" in signals.get("production", ""):
        d["stress"] = d.get("stress", 0) + 0.01
        d["job_satisfaction"] = d.get("job_satisfaction", 0) + 0.005
    return d


def tick(roster: list[dict], states: dict, signals: dict | None = None) -> None:
    """
    매시 회차에서 한 번. 근무/휴식/수면, 기준값 회복, 돈(월급/월세/지출), 바깥 신호를 규칙으로 반영한다.
    같은 시간에 두 번 돌아도(관리자 수동 실행) 한 번만 반영되게 last_tick으로 막는다.
    """
    now = now_kst()
    hour_key = now.strftime("%Y-%m-%d %H")
    month_key = now.strftime("%Y-%m")
    signals = signals or {}
    data = _load()
    rank_by_id = {c["id"]: c.get("rank", "") for c in roster}

    for cid, info in states.items():
        name = info.get("name") or cid
        st = _ensure(data, name, rank_by_id.get(cid, ""))
        if st.get("last_tick") == hour_key:
            continue
        st["last_tick"] = hour_key
        base = st.get("baseline") or {}
        state = info.get("state", "")
        activity = f"{info.get('location', '')} {info.get('activity', '')}"
        d: dict[str, float] = {}

        # 기준값으로 서서히 돌아간다 (분노는 빨리 식는다)
        for k in EMOTIONS + ("job_satisfaction",):
            rate = 0.15 if k == "anger" else 0.05
            d[k] = (base.get(k, st.get(k, 0.5)) - st.get(k, 0.5)) * rate

        if state == "자는 중":
            d["energy"] = d.get("energy", 0) + 0.12
            d["stress"] -= 0.02
        elif state == "일하는 중":
            d["energy"] = d.get("energy", 0) - 0.05
            d["stress"] += 0.01
        else:  # 개인 시간
            d["energy"] = d.get("energy", 0) - 0.02
            d["stress"] -= 0.03
            alone = any(w in activity for w in ("휴식", "혼자", "집"))
            d["loneliness"] += 0.02 if alone else -0.04
            if any(w in activity for w in ("데이트", "소개팅")):
                d["romance"] += 0.05
            if any(w in activity for w in ("술", "포장마차", "소주")):
                d["stress"] -= 0.04
                d["energy"] -= 0.02

        for k, v in _signal_effects(st, info, signals, now).items():
            d[k] = d.get(k, 0) + v

        # 돈 - 세계의 경제 규칙 (LLM은 못 건드린다)
        rank = st.get("rank", "")
        if now.day >= PAYDAY and st.get("last_paid") != month_key:
            st["money"] = st.get("money", 0) + SALARY.get(rank, 3_200_000)
            st["last_paid"] = month_key
            d["happiness"] += 0.08
            d["stress"] -= 0.05
            _note(st, "월급날", {"happiness": 0.08})
        if now.day >= RENT_DAY and st.get("last_rent") != month_key:
            st["money"] = st.get("money", 0) - RENT.get(rank, 650_000)
            st["last_rent"] = month_key
        if now.hour in (12, 19) and state != "자는 중":
            st["money"] = st.get("money", 0) - (9_000 if now.hour == 12 else 13_000)
        if state not in ("자는 중", "일하는 중"):
            st["money"] = st.get("money", 0) - _cost_of(activity)
        if st.get("money", 0) < LOW_MONEY:
            d["stress"] += 0.02
            d["happiness"] -= 0.01

        for k, v in d.items():
            st[k] = _clamp(st.get(k, 0.5) + v)

    _save(data)


# ===================================================================== 상태 -> 행동
_BIAS_RULES = [
    # (조건, 키워드들, 배율)
    (lambda s: s.get("stress", 0) >= 0.65, ("술", "포장마차", "소주", "맥주"), 2.5),
    (lambda s: s.get("stress", 0) >= 0.65, ("운동", "헬스", "산책"), 1.3),
    (lambda s: s.get("energy", 1) <= 0.3, ("휴식", "집", "침대", "낮잠", "잠"), 2.5),
    (lambda s: s.get("energy", 1) <= 0.3, ("운동", "쇼핑", "모임", "나들이"), 0.5),
    (lambda s: s.get("loneliness", 0) >= 0.6, ("친구", "모임", "카페", "동료", "수다"), 2.0),
    (lambda s: s.get("romance", 0) >= 0.6, ("소개팅", "데이트"), 2.5),
    (lambda s: s.get("money", 10**9) < LOW_MONEY, ("쇼핑", "맛집", "영화", "데이트"), 0.3),
    (lambda s: s.get("happiness", 0) >= 0.7, ("나들이", "영화", "맛집", "쇼핑"), 1.4),
    (lambda s: s.get("anger", 0) >= 0.5, ("운동", "헬스", "게임"), 1.8),
]


def bias_weight(state: dict, text: str, weight: float) -> float:
    """일정 후보 하나(장소+활동 텍스트)의 가중치를 상태에 맞게 조정한다."""
    for cond, keywords, factor in _BIAS_RULES:
        if cond(state) and any(k in text for k in keywords):
            weight *= factor
    return weight


# ===================================================================== 상태 -> 프롬프트
def _level_words(st: dict) -> list[str]:
    words = []
    def lv(v: float, hi: str, lo: str | None = None, th_hi: float = 0.65, th_lo: float = 0.3):
        if v >= th_hi:
            words.append(hi)
        elif lo and v <= th_lo:
            words.append(lo)
    lv(st.get("happiness", 0.5), "기분 좋음", "우울함")
    lv(st.get("stress", 0.4), "스트레스가 많이 쌓임", None)
    lv(st.get("loneliness", 0.3), "외로움을 타는 중", None)
    lv(st.get("anger", 0.1), "화가 나 있음", None, th_hi=0.45)
    lv(st.get("romance", 0.2), "누군가에게 설레는 중", None, th_hi=0.6)
    lv(st.get("confidence", 0.5), "자신감 넘침", "자신감이 떨어짐")
    lv(st.get("energy", 0.6), "쌩쌩함", "몹시 피곤함", th_hi=0.8)
    lv(st.get("job_satisfaction", 0.5), "요즘 일이 꽤 할 만함", "회사에 정이 떨어지는 중")
    return words or ["평소와 비슷한 컨디션"]


def describe(name: str) -> str:
    """짧은 한 줄 (도구 응답 등)."""
    st = get(name)
    money = st.get("money", 0)
    return f"{', '.join(_level_words(st))} / 통장 {money // 10_000:,}만원"


def prompt_block(name: str) -> str:
    st = get(name)
    now = now_kst()
    days_to_pay = (PAYDAY - now.day) if now.day < PAYDAY else None
    money = st.get("money", 0)
    money_line = f"통장 잔고 약 {money // 10_000:,}만원"
    if money < LOW_MONEY:
        money_line += " (돈 걱정 중)"
    if days_to_pay is not None:
        money_line += f", 월급날까지 {days_to_pay}일"
    recent = [r["reason"] for r in st.get("recent", [])[-3:] if r.get("reason")]
    lines = [
        f"[{name}의 지금 상태 - 말투와 행동에 자연스럽게 묻어나게 하라. 수치나 '스트레스 지수' 같은 말은 직접 하지 마라]",
        f"- 컨디션: {', '.join(_level_words(st))}",
        f"- {money_line}",
        f"- 요즘 목표: {', '.join(st.get('goals') or []) or '딱히 없음'}",
    ]
    if recent:
        lines.append(f"- 최근 마음에 남은 일: {' / '.join(recent)}")
    return "\n".join(lines)


FEEDBACK_SPEC = (
    '"state_change": {"reason": "무엇 때문에 마음이 바뀌었는지 한 줄", '
    '"happiness|stress|loneliness|anger|romance|confidence|energy|job_satisfaction": -0.2~0.2 사이 숫자(바뀐 것만)}'
)
