"""
원본 봇의 check_and_update_nickname.py를 그대로 이식 (저장 경로만 storage/nickname_detect로 조정).
사용자의 닉네임 변경을 감지하고, 변경된 경우에만 변경 이력 전체를 담은 알림 문자열을 반환한다.
새 사용자/첫 기록이거나 변경이 없으면 빈 문자열을 반환한다. 파일 저장은 닉네임이 바뀌었거나
첫 기록일 때만 한다.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from config import settings

settings.NICKNAME_DETECT_DIR.mkdir(parents=True, exist_ok=True)


def check_and_update_nickname(sender_name: str, sender_id: str, room_id: str) -> str:
    filepath = settings.NICKNAME_DETECT_DIR / f"{room_id}_{sender_id}.jsonl"

    # 1. 기존 기록 읽기 및 변경 이력 추적
    history: list[dict[str, Any]] = []
    last_saved_name: str | None = None

    if filepath.exists():
        try:
            with filepath.open("r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        history.append(entry)
                        last_saved_name = entry.get("chat.sender.name")
                    except json.JSONDecodeError:
                        continue
        except Exception:
            last_saved_name = None
            history = []

    # 2. 닉네임 변경 감지
    is_first_record = not bool(history)
    is_name_changed = last_saved_name != sender_name

    # 3. 현재 데이터 기록 (닉네임 변경 또는 첫 기록일 때만)
    now_date = datetime.now().isoformat()
    if is_first_record or is_name_changed:
        new_entry = {
            "date": now_date,
            "chat.sender.name": sender_name,
            "chat.sender.id": sender_id,
            "room_id": room_id,
        }
        json_line = json.dumps(new_entry, ensure_ascii=False) + "\n"
        try:
            with filepath.open("a", encoding="utf-8") as f:
                f.write(json_line)
        except Exception as e:
            return f"❌ 오류: 파일 쓰기 실패 ({e})"

    # 4. 알림 메시지 생성 및 반환 (닉네임 변경이 발생했을 때만, 첫 기록은 제외)
    if is_name_changed and not is_first_record:
        unique_history: list[dict[str, str]] = []
        previous_name = None

        for entry in history:
            current_name = entry.get("chat.sender.name")
            if current_name is not None and current_name != previous_name:
                date_part = entry.get("date", "").split("T")[0]
                unique_history.append({"date": date_part, "name": current_name})
                previous_name = current_name

        date_part = now_date.split("T")[0]
        if sender_name != previous_name:
            unique_history.append({"date": date_part, "name": sender_name})

        notification_title = "[ 🚨 닉네임 변경 감지! ]"
        history_lines = [f"\t- {h['date']} : {h['name']}" for h in unique_history]
        history_text = "\n".join(history_lines)
        return f"{notification_title}\n\n[닉네임 변경 이력]\n{history_text}"

    # 5. 새 사용자/첫 기록이거나 닉네임 변경이 없는 경우
    return ""
