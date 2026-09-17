"""
[주의] 원본 mbti_system.py 소스를 확보하지 못했다 - 저장소 파일 목록 페이지가 알파벳순으로
잘려서(image_gen.py까지만 보임) "m"으로 시작하는 파일들의 링크를 못 찾았다. 그래서 이 파일은
원본 이식이 아니라 인터페이스만 상상해서 새로 작성한 최소 구현이다.

"/mbti [유형]"으로 자신의 MBTI를 등록하고, 그 후 "/mbti"만 치면 등록된 유형과 간단한 설명을
보여준다. 원본 파일을 구하면 이 구현을 원본 로직으로 교체할 것.
"""
from __future__ import annotations

import json

from config import settings

MBTI_TYPES = {
    "INTJ": "전략적이고 독립적인 설계자",
    "INTP": "논리적 호기심을 좇는 사색가",
    "ENTJ": "결단력 있는 지휘관",
    "ENTP": "아이디어 넘치는 변론가",
    "INFJ": "신념이 깊은 옹호자",
    "INFP": "이상을 좇는 중재자",
    "ENFJ": "사람을 이끄는 선도자",
    "ENFP": "열정적인 활동가",
    "ISTJ": "책임감 있는 관리자",
    "ISFJ": "헌신적인 수호자",
    "ESTJ": "체계적인 경영자",
    "ESFJ": "다정한 집정관",
    "ISTP": "손재주 좋은 장인",
    "ISFP": "자유로운 모험가",
    "ESTP": "행동파 사업가",
    "ESFP": "즉흥적인 연예인",
}

MBTI_STORE_PATH = settings.BASE_DIR / "storage" / "mbti.json"


def _load() -> dict[str, str]:
    if not MBTI_STORE_PATH.exists():
        return {}
    try:
        with MBTI_STORE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, str]) -> None:
    MBTI_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MBTI_STORE_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_mbti(user_key: str, mbti_type: str) -> str:
    mbti_type = mbti_type.strip().upper()
    if mbti_type not in MBTI_TYPES:
        return f"❌ '{mbti_type}'는 올바른 MBTI 유형이 아니에요. (예: INTJ, ENFP)"
    data = _load()
    data[user_key] = mbti_type
    _save(data)
    return f"✅ MBTI가 **{mbti_type}**로 등록됐어요! ({MBTI_TYPES[mbti_type]})"


def get_mbti(user_key: str) -> str:
    data = _load()
    mbti_type = data.get(user_key)
    if not mbti_type:
        return "아직 등록된 MBTI가 없어요. `/mbti INTJ`처럼 유형을 알려주시면 등록해드릴게요."
    return f"**{mbti_type}** - {MBTI_TYPES[mbti_type]}"
