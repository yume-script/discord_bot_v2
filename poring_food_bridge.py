"""
포링푸드(porning_food, 별도 저장소/프로세스, crontab 매시 3분 실행)는
이 봇과 파일 기반으로 연동된다:
  - 읽음: storage/conversations.db (SQLite, core/conversation_store.py가 씀 - 예전엔
          storage/katalk_log/*.jsonl 였는데 SQLite로 전환됨. 포링푸드 쪽 파서도 SQLite
          조회로 갱신 필요 - messages 테이블, conversation_key/display_name/direction/text/ts 컬럼)
  - 씀:   PORING_FOOD_STATUS_JSON_PATH (aesun_current_status.json)

이 파일은 "이 봇이 쓰는 쪽" 계약만 담당한다 - 포맷을 바꿀 땐 여기 한 곳만 보면 된다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from config import settings


def write_current_status(status: dict[str, Any]) -> None:
    """포링푸드가 읽어가는 상태 파일. 스키마를 바꿀 땐 포링푸드 쪽도 같이 갱신해야 한다."""
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **status,
    }
    settings.PORING_FOOD_STATUS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings.PORING_FOOD_STATUS_JSON_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
