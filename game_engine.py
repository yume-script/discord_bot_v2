"""
7개 미니게임의 베팅/판정/정산 로직. 원본(game_rps.py, game_dice.py, game_slot_machine.py,
game_dragontiger.py, game_dice_poker.py, game_blackjack.py, game_baccarat_20260609.py)을
그대로 이식했다 - 베팅 검증, 일일 횟수 제한, 승패 배당 배율까지 전부 원본과 동일한 값이다.

딱 하나 다른 점: 원본은 PIL로 카드/주사위/슬롯 이미지를 그려서 결과를 보여줬는데, 그 이미지
에셋(game_asset_manager.py가 참조하는 파일들, 카드 이미지, 주사위 이미지)을 이 프로젝트로
가져오지 못해서 결과를 텍스트로 보여주는 것으로 단순화했다. 슬롯머신은 원본이 라그나로크M
카드 RAG 데이터(외부 jsonl)에 의존했는데, 그것도 없어서 고정 이모지 심볼로 대체했다.

베팅 검증 → 일일 횟수 제한 → 잔액 체크 → 판정 → money_system.transaction() 정산 순서는
7개 게임 모두 원본과 동일한 흐름이다.
"""
from __future__ import annotations

import random

from core.money_system import money_system
from core.user_ref import Channel, UserRef

# 게임별 일일 참여 횟수 제한 (원본 그대로)
DAILY_LIMITS = {
    "game_rps": 5,
    "game_dice": 10,
    "game_slots": 5,
    "game_dragontiger": 5,
    "game_dice_poker": 5,
    "game_blackjack": 5,
    "game_baccarat": 5,
}


def room_user(user: UserRef) -> tuple[str, str]:
    """
    UserRef -> (room_id, user_id).
    카톡은 방ID//회원번호를 그대로 분리해서 쓴다.
    순수 디스코드 유저는 원본의 폴백(parse_user_info)과 동일하게 room_id=user_id=author_id로
    맞췄다 (원본에 있던 방식을 그대로 유지 - 디스코드 유저는 사실상 자기 자신이 방인 셈).
    """
    if user.channel == Channel.KAKAO:
        room_id, member_no = user.raw_id.split("//", 1)
        return room_id, member_no
    return user.raw_id, user.raw_id


def check_can_play(room_id: str, user_id: str, game_type: str, bet_amount: int) -> str | None:
    """플레이 가능하면 None, 안 되면 사용자에게 보여줄 에러 메시지를 반환."""
    if bet_amount <= 0:
        return "❌ 금액은 0보다 커야 합니다."
    limit = DAILY_LIMITS[game_type]
    count = money_system.get_daily_count(room_id, user_id, game_type)
    if count >= limit:
        return f"🚫 오늘 참여 가능한 횟수({limit}회)를 모두 사용하셨습니다."
    balance = money_system.get_user_data(room_id, user_id)["balance"]
    if balance < bet_amount:
        return f"❌ 잔액 부족 (현재: {balance:,}원)"
    return None


def _settle(room_id: str, user_id: str, change: int, game_type: str, desc: str) -> str | None:
    """정산 실패 시 에러 메시지, 성공 시 None을 반환 (호출부에서 balance는 따로 조회)."""
    ok, result = money_system.transaction(room_id, user_id, change, game_type, desc)
    if not ok:
        return f"❌ 오류: {result}"
    return None


# ---------------------------------------------------------------- 가위바위보
RPS_CHOICES = ["가위", "바위", "보"]


def play_rps(room_id: str, user_id: str, choice: str, bet: int) -> str:
    err = check_can_play(room_id, user_id, "game_rps", bet)
    if err:
        return err
    if choice not in RPS_CHOICES:
        return "❌ /가위, /바위, /보 중 하나를 선택해주세요."

    user_idx = RPS_CHOICES.index(choice)
    bot_idx = random.randint(0, 2)
    result_val = (user_idx - bot_idx + 3) % 3
    bot_choice = RPS_CHOICES[bot_idx]

    if result_val == 1:
        change = bet
        outcome = f"🎉 승리! (상대: {bot_choice})"
    elif result_val == 2:
        change = -bet
        outcome = f"😢 패배... (상대: {bot_choice})"
    else:
        change = 0
        outcome = f"🤝 무승부 (상대: {bot_choice})"

    err = _settle(room_id, user_id, change, "game_rps", f"가위바위보 ({choice} vs {bot_choice})")
    if err:
        return err
    balance = money_system.get_user_data(room_id, user_id)["balance"]
    sign = "+" if change >= 0 else ""
    return f"{outcome}\n변동: {sign}{change:,}원 | 잔액: {balance:,}원"


# ---------------------------------------------------------------- 주사위
def play_dice(room_id: str, user_id: str, bet: int) -> str:
    err = check_can_play(room_id, user_id, "game_dice", bet)
    if err:
        return err

    d1, d2 = random.randint(1, 6), random.randint(1, 6)
    s = d1 + d2
    if d1 == d2:
        mult, txt = 3.0, f"✨ 더블! ({d1}-{d2})"
    elif s >= 8:
        mult, txt = 2.0, f"WIN! 합계 {s} (8 이상)"
    elif s == 7:
        mult, txt = 1.5, "SAFE! 합계 7 (보너스)"
    else:
        mult, txt = 0.0, f"LOSE... 합계 {s} (꽝)"

    change = int(bet * mult) - bet if mult > 0 else -bet
    err = _settle(room_id, user_id, change, "game_dice", f"주사위({s})")
    if err:
        return err
    balance = money_system.get_user_data(room_id, user_id)["balance"]
    sign = "+" if change >= 0 else ""
    return f"🎲 {d1} , {d2} → {txt}\n변동: {sign}{change:,}원 | 잔액: {balance:,}원"


# ---------------------------------------------------------------- 슬롯머신
# 원본은 라그나로크M 카드 RAG 데이터(외부 jsonl)에 의존했는데 여기선 없어서 고정 심볼로 대체.
# 희귀할수록(가중치가 낮을수록) 3연속 적중 시 배당이 커지는 규칙은 원본 공식을 그대로 썼다.
SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "⭐", "7️⃣"]
SLOT_WEIGHTS = [30, 25, 20, 15, 10]


def play_slot(room_id: str, user_id: str, bet: int) -> str:
    err = check_can_play(room_id, user_id, "game_slots", bet)
    if err:
        return err

    total_weight = sum(SLOT_WEIGHTS)
    spin = random.choices(SLOT_SYMBOLS, weights=SLOT_WEIGHTS, k=3)
    counts = {s: spin.count(s) for s in set(spin)}
    max_count = max(counts.values())
    win_symbol = max(counts, key=counts.get)

    if max_count == 3:
        idx = SLOT_SYMBOLS.index(win_symbol)
        p_triple = (SLOT_WEIGHTS[idx] / total_weight) ** 3
        mult = round(max(5.0, min(100.0, 0.85 / p_triple)), 1)
        outcome = f"🎰 JACKPOT! {win_symbol}{win_symbol}{win_symbol} ({mult}배)"
    elif max_count == 2:
        mult = 1.5
        outcome = "🎰 DOUBLE! (2개 일치, 1.5배)"
    else:
        mult = 0.0
        outcome = "🎰 꽝 (LOSE)"

    change = int(bet * mult) - bet if mult >= 1.0 else -bet
    err = _settle(room_id, user_id, change, "game_slots", outcome)
    if err:
        return err
    balance = money_system.get_user_data(room_id, user_id)["balance"]
    sign = "+" if change >= 0 else ""
    return f"{' '.join(spin)}\n{outcome}\n변동: {sign}{change:,}원 | 잔액: {balance:,}원"


# ---------------------------------------------------------------- 용호 (드래곤타이거)
def play_dragontiger(room_id: str, user_id: str, choice: str, bet: int) -> str:
    err = check_can_play(room_id, user_id, "game_dragontiger", bet)
    if err:
        return err
    if choice not in ("용", "호랑이", "무승부"):
        return "❌ [용/호랑이/무승부] 중에서 선택해주세요."

    d_val, t_val = random.randint(1, 13), random.randint(1, 13)
    winner = "용" if d_val > t_val else "호랑이" if d_val < t_val else "무승부"

    if choice == winner:
        mult = 9.0 if winner == "무승부" else 2.0
        change = int(bet * mult) - bet
        outcome = f"🎉 WIN! 결과: {winner} (용:{d_val} / 호랑이:{t_val}, {mult}배)"
    else:
        change = -bet
        outcome = f"😢 LOSE. 결과: {winner} (용:{d_val} / 호랑이:{t_val})"

    err = _settle(room_id, user_id, change, "game_dragontiger", f"용호 {'승리' if choice == winner else '패배'}")
    if err:
        return err
    balance = money_system.get_user_data(room_id, user_id)["balance"]
    sign = "+" if change >= 0 else ""
    return f"🐉 {outcome}\n변동: {sign}{change:,}원 | 잔액: {balance:,}원"


# ---------------------------------------------------------------- 다이스포커
def _evaluate_dice_poker(rolls: list[int]) -> tuple[str, float]:
    counts: dict[int, int] = {}
    for r in rolls:
        counts[r] = counts.get(r, 0) + 1
    sorted_counts = sorted(counts.values(), reverse=True)
    sorted_rolls = sorted(rolls)

    if 5 in sorted_counts:
        return "파이브 오브 카인드", 30.0
    if 4 in sorted_counts:
        return "포 오브 카인드", 8.0
    if sorted_counts == [3, 2]:
        return "풀하우스", 4.0
    if sorted_rolls in ([1, 2, 3, 4, 5], [2, 3, 4, 5, 6]):
        return "스트레이트", 3.0
    if 3 in sorted_counts:
        return "트리플", 1.5
    if sorted_counts == [2, 2, 1]:
        return "투페어 (본전)", 1.0
    return "노페어/원페어", 0.0


def play_dice_poker(room_id: str, user_id: str, bet: int) -> str:
    err = check_can_play(room_id, user_id, "game_dice_poker", bet)
    if err:
        return err

    rolls = [random.randint(1, 6) for _ in range(5)]
    hand_name, mult = _evaluate_dice_poker(rolls)
    change = int(bet * mult) - bet if mult >= 1.0 else -bet

    err = _settle(room_id, user_id, change, "game_dice_poker", f"다이스포커 {hand_name}")
    if err:
        return err
    balance = money_system.get_user_data(room_id, user_id)["balance"]
    sign = "+" if change >= 0 else ""
    dice_str = " ".join(f"[{r}]" for r in rolls)
    result_word = "WIN" if mult > 1.0 else "PUSH" if mult == 1.0 else "LOSE"
    return f"🎲 {dice_str} → {result_word} - {hand_name}\n변동: {sign}{change:,}원 | 잔액: {balance:,}원"


# ---------------------------------------------------------------- 블랙잭
_BJ_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_BJ_VALUES = {**{str(n): n for n in range(2, 11)}, "J": 10, "Q": 10, "K": 10, "A": 11}


def _bj_draw() -> str:
    return random.choice(_BJ_RANKS)


def _bj_value(hand: list[str]) -> int:
    total = sum(_BJ_VALUES[r] for r in hand)
    aces = hand.count("A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def _bj_is_natural(hand: list[str]) -> bool:
    return len(hand) == 2 and "A" in hand and any(r in ("10", "J", "Q", "K") for r in hand)


def play_blackjack(room_id: str, user_id: str, bet: int) -> str:
    err = check_can_play(room_id, user_id, "game_blackjack", bet)
    if err:
        return err

    player = [_bj_draw(), _bj_draw()]
    dealer = [_bj_draw(), _bj_draw()]
    while _bj_value(player) <= 11:
        player.append(_bj_draw())
    while _bj_value(dealer) <= 16:
        dealer.append(_bj_draw())

    p, d = _bj_value(player), _bj_value(dealer)
    natural = _bj_is_natural(player)

    if p > 21:
        mult, outcome = 0.0, "LOSE - 버스트(21 초과)"
    elif d > 21 or p > d:
        mult, outcome = (2.5, "BLACKJACK! 2.5배 승리") if natural else (2.0, "WIN - 승리!")
    elif p == d:
        mult, outcome = 1.0, "PUSH - 무승부"
    else:
        mult, outcome = 0.0, "LOSE - 패배"

    change = int(bet * mult) - bet if mult >= 1.0 else -bet
    err = _settle(room_id, user_id, change, "game_blackjack", f"블랙잭 {outcome}")
    if err:
        return err
    balance = money_system.get_user_data(room_id, user_id)["balance"]
    sign = "+" if change >= 0 else ""
    return (
        f"🃏 나: {' '.join(player)} (합계 {p}) | 딜러: {' '.join(dealer)} (합계 {d})\n"
        f"{outcome}\n변동: {sign}{change:,}원 | 잔액: {balance:,}원"
    )


# ---------------------------------------------------------------- 바카라
_BAC_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
_BAC_VALUES = {**{str(n): n for n in range(2, 10)}, "10": 0, "J": 0, "Q": 0, "K": 0, "A": 1}


def play_baccarat(room_id: str, user_id: str, choice: str, bet: int) -> str:
    err = check_can_play(room_id, user_id, "game_baccarat", bet)
    if err:
        return err
    if choice not in ("홀", "짝"):
        return "❌ [홀] 또는 [짝] 중에서 선택해주세요."

    c1, c2 = random.choice(_BAC_RANKS), random.choice(_BAC_RANKS)
    score = (_BAC_VALUES[c1] + _BAC_VALUES[c2]) % 10
    answer = "홀" if score % 2 else "짝"

    if choice == answer:
        change = bet
        outcome = f"🎉 WIN! {answer} 적중!"
    else:
        change = -bet
        outcome = f"😢 LOSE. 결과는 {answer}"

    err = _settle(room_id, user_id, change, "game_baccarat", f"바카라 {'승리' if choice == answer else '패배'}")
    if err:
        return err
    balance = money_system.get_user_data(room_id, user_id)["balance"]
    sign = "+" if change >= 0 else ""
    return f"🎴 {c1} + {c2} → 합계 {score}\n{outcome}\n변동: {sign}{change:,}원 | 잔액: {balance:,}원"
