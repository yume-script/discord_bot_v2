"""
전투 레벨 - 밤마다 몬스터 침입(raids.py)에서 싸우거나 돕거나 다친 사람을 돌본 인물이 경험치를 얻고 레벨이 오른다.

- 경험치: 침입 한 번에 결과별 기본(XP_BY_OUTCOME) + 몬스터 등급 보너스, 그날 가장 힘을 많이 낸 사람(MVP)은 +MVP_XP.
  싸움꾼이 아닌 사람(화살 나르기/횃불 들기/부상자 옮기기)은 절반. 치유사(raids.HEALER)는 다친 사람 1명당 HEAL_XP.
  방어 시설을 세우거나 고친 사람(decor)도 DECOR_XP.
- 레벨: 다음 레벨까지 필요한 경험치 = XP_STEP x 지금 레벨 (Lv.1->2 80, 2->3 160 ...), 최대 MAX_LEVEL.
- 효과: 침입 때 낸 힘이 레벨당 POWER_PER_LEVEL씩 커진다 (raids.defenders). 몬스터도 마을 평균 레벨만큼 조금 세진다.
- 칭호: TITLES (Lv.5 숙련, Lv.10 정예, Lv.20 영웅, Lv.30 전설) - 판단/명단에 보인다, 오르면 기억/연대기에 남는다.
저장: storage/poring_food/levels.json {이름: {"level", "xp", "battles", "mvp", "best"}}
"""
from __future__ import annotations

import json
import os

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

LEVELS_PATH = os.path.join(STATE_DIR, "levels.json")
XP_STEP = 80
MAX_LEVEL = 50
POWER_PER_LEVEL = 0.08          # Lv.10이면 1.72배
XP_BY_OUTCOME = {"대승": 30, "격퇴": 25, "고전": 20, "성벽 돌파": 12}
MVP_XP = 15
SUPPORT_RATIO = 0.5             # 싸움꾼이 아닌 사람 (돕기)
HEAL_XP = 10
DECOR_XP = 8
TITLES = [(30, "전설"), (20, "영웅"), (10, "정예"), (5, "숙련")]


def _load() -> dict:
    try:
        with open(LEVELS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(LEVELS_PATH), exist_ok=True)
    tmp = LEVELS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LEVELS_PATH)


def need(level: int) -> int:
    """level에서 다음 레벨까지 필요한 경험치."""
    return XP_STEP * level


def title(level: int) -> str:
    return next((t for lv, t in TITLES if level >= lv), "")


def get(name: str) -> dict:
    st = _load().get(name) or {}
    return {"level": int(st.get("level", 1)), "xp": int(st.get("xp", 0)), "battles": int(st.get("battles", 0)),
            "mvp": int(st.get("mvp", 0)), "best": st.get("best", "")}


def level(name: str) -> int:
    return get(name)["level"]


def multiplier(name: str) -> float:
    return 1 + POWER_PER_LEVEL * (level(name) - 1)


def average(names: list[str]) -> float:
    return sum(level(n) for n in names) / len(names) if names else 1.0


def gain(gains: dict[str, int], battle: bool = False, mvp: str = "", best: str = "") -> list[dict]:
    """경험치를 더하고 레벨을 올린다. gains = {이름: 경험치}. battle=True면 싸움 횟수도 센다.
    반환: 레벨이 오른 사람 [{"name", "from", "to", "title"(새로 받은 칭호, 없으면 "")}]."""
    data = _load()
    ups = []
    for name, xp in gains.items():
        if xp <= 0:
            continue
        st = data.setdefault(name, {"level": 1, "xp": 0, "battles": 0, "mvp": 0})
        st["xp"] = int(st.get("xp", 0)) + int(xp)
        if battle:
            st["battles"] = int(st.get("battles", 0)) + 1
            if best:
                st["best"] = best
        if name == mvp:
            st["mvp"] = int(st.get("mvp", 0)) + 1
        before = lv = int(st.get("level", 1))
        while lv < MAX_LEVEL and st["xp"] >= need(lv):
            st["xp"] -= need(lv)
            lv += 1
        st["level"] = lv
        if lv > before:
            new_title = title(lv) if title(lv) != title(before) else ""
            ups.append({"name": name, "from": before, "to": lv, "title": new_title})
            print(f"[레벨] {name} Lv.{before} -> Lv.{lv}" + (f" ({new_title})" if new_title else ""))
    _save(data)
    return ups


def tag(name: str) -> str:
    """'Lv.7 숙련' 같은 짧은 표시 (기록이 없으면 'Lv.1')."""
    st = get(name)
    t = title(st["level"])
    return f"Lv.{st['level']}" + (f" {t}" if t else "")


def prompt_block(name: str) -> str:
    """판단용: 내 전투 레벨."""
    st = get(name)
    if not st["battles"] and st["level"] == 1 and not st["xp"]:
        return ""
    return (f"[내 전투 레벨] {tag(name)} (경험치 {st['xp']}/{need(st['level'])}, 침입 {st['battles']}번 싸움"
            + (f", MVP {st['mvp']}번" if st["mvp"] else "") + ") - 밤마다 성벽에서 싸우면 오른다")


def ranking(limit: int = 10) -> list[tuple[str, dict]]:
    data = _load()
    rows = [(n, get(n)) for n in data]
    rows.sort(key=lambda r: (-r[1]["level"], -r[1]["xp"], r[0]))
    return rows[:limit]
