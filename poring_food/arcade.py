"""
봇 오락실 - 포링푸드 인물들이 봇에 있는 미니게임(가위바위보/주사위/슬롯머신/용호/다이스포커/블랙잭/바카라)을 직접 한다.

- 매시 판단(agents._decide)에서 개인시간에 하고 싶으면 "game"을 고른다 (게임, 건 돈, 선택). LLM 호출은 늘지 않는다.
- 판정/배당/일일 횟수 제한은 사람과 똑같이 봇의 게임 엔진(core/game_engine.py)을 그대로 쓴다.
  방은 ROOM(가상 세계 전용, 사람들 머니와 섞이지 않는다), 유저는 인물 이름.
- 돈은 이야기 속 돈(life.py money): 하기 전에 게임 머니를 그 사람 돈으로 맞추고, 끝나면 바뀐 만큼 이야기 속 돈에 반영한다.
- 건 돈은 가진 돈의 MAX_BET_RATIO 이하, MAX_BET 이하로 자른다. 한 사람 하루 MAX_PLAYS_PER_DAY판.
- 결과: 인물이 친 명령과 오락실 결과를 에이전트 채널에 올리고(사람이 하듯), 기분이 바뀌고, 크게 따거나 잃으면 기억에 남는다.
- 주인: 오락기는 골목 LP바(PLACE)에 있고 주인은 MANAGER(기본 오윤 - 실적 자리 "오락기 매출", 교체되면 인수인계받은 사람).
  손님이 한 판 할 때마다 주인이 자기 말투로 한마디(LLM 1회/판, agents._arcade_comment), 너무 잃는 손님은 오늘 그만 받을 수 있고
  (cut_off), 크게 딴 손님이 나오면 동네 단톡방에 알린다. 손님들이 잃은 돈이 가게 매출 = 주인 실적(kpi.py "arcade").
저장: storage/poring_food/arcade.json {"days": {날짜: {"plays", "revenue", "by": {이름: 손익}}}, "cutoff": {날짜: [이름]}} - 14일
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

from . import life, memory
from .clock import now_kst
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

ENABLED = os.getenv("PORING_ARCADE", "1") not in ("0", "false", "False", "")
ROOM = os.getenv("PORING_ARCADE_ROOM", "poring_food_world")
MAX_BET = int(os.getenv("PORING_ARCADE_MAX_BET", "100000"))
MAX_BET_RATIO = 0.1
MIN_BET = 1000
MAX_PLAYS_PER_DAY = int(os.getenv("PORING_ARCADE_MAX_PER_DAY", "3"))
BIG = 200_000            # 이만큼 넘게 따거나 잃으면 오래 기억한다
MANAGER = os.getenv("PORING_ARCADE_MANAGER", "오윤")
PLACE = os.getenv("PORING_ARCADE_PLACE", "LP바 오락기")
ARCADE_PATH = os.path.join(STATE_DIR, "arcade.json")
KEEP_DAYS = 14


def _load() -> dict:
    try:
        with open(ARCADE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(ARCADE_PATH), exist_ok=True)
    tmp = ARCADE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ARCADE_PATH)


def today_stats(now: datetime | None = None) -> dict:
    """오늘 가게 장사: {"plays", "revenue"(손님들이 잃은 돈 - 딴 돈), "by": {이름: 그 손님 손익}}."""
    day = (now or now_kst()).strftime("%Y-%m-%d")
    return (_load().get("days") or {}).get(day) or {"plays": 0, "revenue": 0, "by": {}}


def _record(name: str, change: int, now: datetime) -> None:
    day = now.strftime("%Y-%m-%d")
    data = _load()
    days = data.setdefault("days", {})
    st = days.setdefault(day, {"plays": 0, "revenue": 0, "by": {}})
    st["plays"] += 1
    st["revenue"] -= change
    st["by"][name] = st["by"].get(name, 0) + change
    data["days"] = {d: v for d, v in days.items() if d >= sorted(days)[-KEEP_DAYS:][0]}
    _save(data)


def cut_off(name: str, now: datetime | None = None) -> None:
    """주인이 오늘은 그만 받기로 한 손님."""
    day = (now or now_kst()).strftime("%Y-%m-%d")
    data = _load()
    data["cutoff"] = {day: sorted(set((data.get("cutoff") or {}).get(day, [])) | {name})}
    _save(data)


def is_cut_off(name: str, now: datetime | None = None) -> bool:
    day = (now or now_kst()).strftime("%Y-%m-%d")
    return name in ((_load().get("cutoff") or {}).get(day) or [])

# 게임 이름 -> (게임 엔진 함수 이름, 선택지 - 없으면 None, 일일 제한 키)
GAMES = {
    "가위바위보": ("play_rps", ["가위", "바위", "보"], "game_rps"),
    "주사위": ("play_dice", None, "game_dice"),
    "슬롯머신": ("play_slot", None, "game_slots"),
    "용호": ("play_dragontiger", ["용", "호랑이", "무승부"], "game_dragontiger"),
    "다이스포커": ("play_dice_poker", None, "game_dice_poker"),
    "블랙잭": ("play_blackjack", None, "game_blackjack"),
    "바카라": ("play_baccarat", ["홀", "짝"], "game_baccarat"),
}


def prompt_rule(name: str) -> str:
    """판단 프롬프트에 넣는 규칙 (오늘 다 했으면 "")."""
    if not ENABLED or plays_today(name) >= MAX_PLAYS_PER_DAY or is_cut_off(name):
        return ""
    money = int(life.get(name).get("money", 0))
    games = ", ".join(f"{g}({'/'.join(c)})" if c else g for g, (_, c, _) in GAMES.items())
    where = f"골목 {PLACE}(주인 {MANAGER})" if name != MANAGER else f"내 가게의 {PLACE}"
    return (f"- 개인시간에 심심하거나 기분 전환/한탕이 필요하면 {where}로 봇 미니게임을 할 수 있다: {games}. "
            f"내 돈 {money:,}원, 한 판에 최대 {min(MAX_BET, int(money * MAX_BET_RATIO)):,}원. 성격과 돈 사정대로 - "
            "하고 싶을 때만 game에 쓰고 대부분은 null.\n")


SPEC = (', "game": null 또는 {"name": "가위바위보|주사위|슬롯머신|용호|다이스포커|블랙잭|바카라", '
        '"bet": 건 돈(원, 정수), "choice": "가위/바위/보, 용/호랑이/무승부, 홀/짝 중 하나(그 게임만)", '
        '"why": "왜 하는지 한 줄"}')


def parse(raw) -> dict | None:
    if not ENABLED or not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()
    if name not in GAMES:
        return None
    try:
        bet = int(str(raw.get("bet") or 0).replace(",", "").replace("원", ""))
    except ValueError:
        return None
    choices = GAMES[name][1]
    choice = str(raw.get("choice") or "").strip()
    if choices and choice not in choices:
        choice = choices[0]
    return {"name": name, "bet": bet, "choice": choice if choices else "", "why": str(raw.get("why") or "").strip()[:80]}


def _engine():
    from core import game_engine
    return game_engine


def plays_today(name: str) -> int:
    try:
        ms = _engine().money_system
        return sum(ms.get_daily_count(ROOM, name, key) for _, _, key in GAMES.values())
    except Exception:  # noqa: BLE001
        return 0


def _change(text: str) -> int | None:
    m = re.search(r"변동:\s*([+-]?[\d,]+)원", text)
    return int(m.group(1).replace(",", "")) if m else None


def play(name: str, game: dict, now: datetime | None = None) -> dict | None:
    """한 판 한다. 반환: {"game", "bet", "command", "result", "change", "money"} 또는 None(못 함)."""
    if (not ENABLED or not game or game["name"] not in GAMES or plays_today(name) >= MAX_PLAYS_PER_DAY
            or is_cut_off(name, now)):
        return None
    now = now or now_kst()
    money = int(life.get(name).get("money", 0))
    bet = max(0, min(int(game["bet"]), MAX_BET, int(money * MAX_BET_RATIO)))
    if bet < MIN_BET:
        return None
    eng = _engine()
    func, choices, _ = GAMES[game["name"]]
    eng.money_system.set_balance(ROOM, name, money, "포링푸드 이야기 속 돈과 맞춤")
    args = (ROOM, name, game["choice"], bet) if choices else (ROOM, name, bet)
    result = getattr(eng, func)(*args)
    change = _change(result)
    if change is None:  # 일일 제한/잔액 부족 등으로 못 함
        print(f"[오락실] {name} {game['name']} 못 함: {result.splitlines()[0]}")
        return None
    _record(name, change, now)
    money = life.give_money(name, change, f"봇 오락실 {game['name']} {'+' if change >= 0 else ''}{change:,}원")
    if change > 0:
        life.apply_changes(name, {"happiness": 0.08 if change < BIG else 0.15, "confidence": 0.04}, reason=f"{game['name']}에서 땀")
    elif change < 0:
        life.apply_changes(name, {"stress": 0.05 if -change < BIG else 0.12, "happiness": -0.04}, reason=f"{game['name']}에서 잃음")
    if abs(change) >= BIG:
        memory.remember(name, f"봇 오락실 {game['name']}에서 {abs(change):,}원을 {'땄다' if change > 0 else '잃었다'}", 7, [], "오락")
    command = f"/{game['choice'] if game['name'] == '가위바위보' else game['name']}" + \
              (f" {game['choice']}" if choices and game["name"] != "가위바위보" else "") + f" {bet}"
    print(f"[오락실] {name} {command} -> {'+' if change >= 0 else ''}{change:,}원 (잔액 {money:,}원)")
    return {"game": game["name"], "bet": bet, "command": command, "result": result, "change": change, "money": money}
