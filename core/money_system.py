"""
[주의] 원본 봇의 money_system.py 소스를 확보하지 못해서, 이 파일은 원본을 이식한 게 아니라
app_kakao_handler.py의 호출부(bot.money_system.transaction(room_id=, user_id=, amount=10,
transaction_type='chat'))만 보고 같은 인터페이스로 새로 작성한 최소 구현이다.
원본 파일을 구할 수 있으면 이 파일을 원본 로직으로 교체할 것 (특히 잔액 초기값, 동시성 처리,
거래 내역 스키마가 원본과 다를 수 있음).

지금 구현: 방(room_id)별 JSON 파일에 {user_id: balance}를 저장하고, 거래 내역은 별도
JSONL로 append-only 기록한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from config import settings

settings.MONEY_DIR.mkdir(parents=True, exist_ok=True)


class MoneySystem:
    def _balance_path(self, room_id: str):
        return settings.MONEY_DIR / f"{room_id}_balances.json"

    def _ledger_path(self, room_id: str):
        return settings.MONEY_DIR / f"{room_id}_ledger.jsonl"

    def _load_balances(self, room_id: str) -> dict[str, int]:
        path = self._balance_path(room_id)
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_balances(self, room_id: str, balances: dict[str, int]) -> None:
        with self._balance_path(room_id).open("w", encoding="utf-8") as f:
            json.dump(balances, f, ensure_ascii=False, indent=2)

    def transaction(self, *, room_id: str, user_id: str, amount: int, transaction_type: str) -> int:
        """잔액에 amount를 더하고(차감은 음수로) 새 잔액을 반환. 거래 내역도 함께 남긴다."""
        balances = self._load_balances(room_id)
        new_balance = balances.get(user_id, 0) + amount
        balances[user_id] = new_balance
        self._save_balances(room_id, balances)

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "amount": amount,
            "type": transaction_type,
            "balance_after": new_balance,
        }
        with self._ledger_path(room_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return new_balance

    def get_balance(self, room_id: str, user_id: str) -> int:
        return self._load_balances(room_id).get(user_id, 0)


money_system = MoneySystem()
