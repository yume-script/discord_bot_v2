"""
원본 봇의 money_system.py를 그대로 이식 (저장 폴더만 storage/money로 조정).
방ID+유저ID별 JSONL 파일에 거래 기록을 append하고, 마지막 줄을 최신 잔액/빚 상태로 취급한다.

특징(원본 그대로):
- 수익 발생 시 빚이 있으면 자동으로 10% 상환
- 'chat' 타입 거래는 같은 날짜 마지막 기록이 채팅 보상이면 하나로 합쳐서 파일 비대화 방지
- 하루 한 번 100원 대출(borrow_money) - 빚으로 잡힘
- transaction()은 (성공여부: bool, 새 잔액 또는 에러/거절 메시지: int|str) 튜플을 반환
"""
import json
import os
from datetime import datetime

from config import settings


class MoneySystem:
    def __init__(self, base_dir=None):
        self.base_dir = str(base_dir or settings.MONEY_DIR)
        settings.MONEY_DIR.mkdir(parents=True, exist_ok=True)

    def _get_file_path(self, room_id, user_id):
        """방 ID와 유저 ID를 기반으로 파일 경로 생성 (특수문자 치환)"""
        safe_room_id = str(room_id).strip().replace("/", "_").replace("\\", "_")
        safe_user_id = str(user_id).strip()
        return os.path.join(self.base_dir, f"{safe_room_id}_{safe_user_id}.jsonl")

    def get_user_data(self, room_id, user_id):
        """유저의 최신 상태(잔액, 빚)를 가져옴"""
        file_path = self._get_file_path(room_id, user_id)
        if not os.path.exists(file_path):
            return {"balance": 0, "debt": 0}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                if not lines:
                    return {"balance": 0, "debt": 0}
                last_line = json.loads(lines[-1])
                return {
                    "balance": last_line.get('balance', 0),
                    "debt": last_line.get('debt', 0)
                }
        except Exception:
            return {"balance": 0, "debt": 0}

    def transaction(self, room_id, user_id, amount, transaction_type, description="", debt_change=0):
        """
        통합 거래 처리 함수
        - amount: 잔액 변화량
        - transaction_type: 거래 종류 (chat, game_win, loan_take 등)
        - debt_change: 빚 변화량 (대출 시 사용)
        """
        file_path = self._get_file_path(room_id, user_id)
        user_data = self.get_user_data(room_id, user_id)

        current_balance = user_data['balance']
        current_debt = user_data['debt']

        # --- [로직] 수익 발생 시 10% 자동 상환 ---
        if amount > 0 and current_debt > 0 and transaction_type != 'loan_take':
            repay_target = int(amount * 0.1)
            actual_repay = min(repay_target, current_debt)

            if actual_repay > 0:
                current_debt -= actual_repay
                amount -= actual_repay
                description += f" (빚 {actual_repay}원 자동 상환)"

        new_balance = current_balance + amount
        new_debt = max(0, current_debt + debt_change)

        if new_balance < 0:
            return False, "잔액이 부족합니다."

        now = datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        today_str = now.strftime("%Y-%m-%d")

        record = {
            "timestamp": timestamp_str,
            "type": transaction_type,
            "change": amount,
            "balance": new_balance,
            "debt": new_debt,
            "desc": description
        }

        # --- [로직] 채팅 보상 합치기 (파일 최적화) ---
        if transaction_type == 'chat':
            try:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    if lines:
                        last_record = json.loads(lines[-1])
                        last_date = last_record.get('timestamp', '').split(' ')[0]
                        if last_record.get('type') == 'chat' and last_date == today_str:
                            last_record['change'] += amount
                            last_record['balance'] = new_balance
                            last_record['debt'] = new_debt
                            last_record['timestamp'] = timestamp_str
                            if "자동 상환" in description:
                                last_record['desc'] = "오늘의 채팅 보상 (자동상환 중)"
                            else:
                                last_record['desc'] = "오늘의 채팅 보상 합계"

                            lines[-1] = json.dumps(last_record, ensure_ascii=False) + "\n"
                            with open(file_path, 'w', encoding='utf-8') as f:
                                f.writelines(lines)
                            return True, new_balance
            except Exception:
                pass

        # --- [저장] 일반 거래 기록 추가 ---
        try:
            with open(file_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True, new_balance
        except Exception as e:
            return False, f"저장 오류: {e}"

    def borrow_money(self, room_id, user_id):
        """[대출 기능] 하루 한 번 100원 지급"""
        if self.get_daily_count(room_id, user_id, "loan_take") >= 1:
            return False, "대출은 하루에 한 번만 가능합니다."

        return self.transaction(
            room_id, user_id,
            amount=100,
            transaction_type='loan_take',
            description="일일 긴급 대출",
            debt_change=100
        )

    def get_daily_count(self, room_id, user_id, transaction_type):
        """오늘 특정 타입의 거래가 몇 번 있었는지 확인"""
        file_path = self._get_file_path(room_id, user_id)
        if not os.path.exists(file_path):
            return 0

        today = datetime.now().strftime("%Y-%m-%d")
        count = 0
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    data = json.loads(line)
                    if data.get('timestamp', '').startswith(today) and \
                       data.get('type') == transaction_type:
                        count += 1
            return count
        except Exception:
            return 0

    def update_balance(self, room_id, user_id, amount, description="관리자 수정"):
        """관리자용 수동 잔액 조정"""
        return self.transaction(room_id, user_id, amount, 'manual', description)


money_system = MoneySystem()
