"""
포링푸드(porning_food, GitHub yume-script/porning_food) - 애순이의 일상 이야기를 생성해
디스코드/카톡으로 보내는 별도 크론 스크립트(매시 3분 실행). 전체 소스를 확인한 결과, 이
프로젝트(discord_bot_v2)와는 거의 완전히 독립적으로 동작한다:
  - 카톡 로그를 읽지 않는다 (자기 저장소의 조직도/이슈/페르소나 JSON만 읽음)
  - 날씨/LLM 호출도 전부 자체 설정으로 따로 한다
  - Discord 전송도 이 봇을 거치지 않고 포링푸드 자신의 Discord 웹훅으로 직접 보낸다
  - 카톡 전송만 같은 브릿지 서버(KATALK_BRIDGE_URL)를 이 봇과 공유한다 (코드 공유 아님,
    같은 외부 서버에 각자 요청을 보내는 것뿐)

[전에 있던 오해 정정] 예전엔 "이 봇이 aesun_current_status.json을 써서 포링푸드가 읽는다"로
잘못 알고 write_current_status()를 만들어뒀었는데, 실제로는 반대다 - 포링푸드가 이 파일을
쓴다(porning_food/notifier.py의 save_to_file, config.py의 STATUS_OUT_PATH). 다만 그 경로가
"/mnt/discord_bot"(옛날 봇 폴더)으로 고정되어 있고, 이 프로젝트를 포함해 그 파일을 실제로
읽는 코드는 어디서도 확인되지 않았다 - 즉 지금은 아무도 소비하지 않는 일방적 기록이다.

아래 read_current_status()는 "나중에 애순이가 자기 현재 상태/기분을 대화에 참고하게 만들고
싶어지면" 쓸 수 있는 읽기 함수다. 지금은 어디서도 호출되지 않는다 - 실제로 쓰려면
(1) 포링푸드 쪽 STATUS_OUT_PATH를 이 봇의 경로로 옮기고
(2) 이 함수를 ai/rag_engine.py 등에서 호출해 프롬프트에 넣어주는 배선이 필요하다.
"""
from __future__ import annotations

import json
from typing import Any

from config import settings


def read_current_status() -> dict[str, Any] | None:
    """포링푸드가 써주는 상태 파일을 읽는다. 파일이 없거나 깨졌으면 None."""
    path = settings.PORING_FOOD_STATUS_JSON_PATH
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
