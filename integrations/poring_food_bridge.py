"""
포링푸드(porning_food, 별도 저장소/프로세스, crontab 매시 3분 실행)는
이 봇과 파일 기반으로 연동된다:
  - 읽음: storage/katalk_log/*.jsonl  (core/katalk_bridge.py가 씀)
  - 씀:   PORING_FOOD_STATUS_JSON_PATH (aesun_current_status.json)

데이터 초기화 결정으로 katalk_log 포맷을 새로 정했으므로 (core/katalk_bridge.py의
JSONL 구조: ts/user/direction/text), 포링푸드 쪽 파서도 이 포맷에 맞춰 갱신이 필요하다.
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
