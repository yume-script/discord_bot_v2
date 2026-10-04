"""파일 저장 경로 관련 공용 유틸 - core/money_system.py, core/nickname_watch.py가 공유한다."""
from __future__ import annotations

import re

_UNSAFE_CHARS = re.compile(r"[^0-9A-Za-z_\-]")


def safe_path_component(value: object) -> str:
    """
    방ID/유저ID처럼 외부(카톡 브릿지, 관리자 명령 인자)에서 들어온 값을 파일명 조각으로 쓸 수
    있게 정제한다. 숫자/영문/_/- 외의 문자는 전부 "_"로 바꿔서 "../" 같은 경로 이탈이나
    구분자 문자가 파일 경로에 섞이지 않게 한다 (실제 ID는 숫자라 결과가 바뀌지 않는다).
    """
    return _UNSAFE_CHARS.sub("_", str(value).strip()) or "_"
