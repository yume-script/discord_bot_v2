"""
[1회성] 인물 이름 변경(몬스터 이름 -> 한국 드라마 인물 이름) 이전에 쌓인 실행 기록을 새 이름으로 바꾼다.

storage/poring_food/ 아래 JSON/JSONL(관계, 대화 장면, 줄거리, 인물 상태/히스토리 등)에 옛 이름이
그대로 남아 있으면 같은 사람이 두 이름으로 갈라진다. 처음 한 번만 텍스트 치환으로 바꾸고 표시
파일(.names_v2_migrated)을 남긴다.

"미믹"은 예전에 포링푸드/에린 로지스틱스 양쪽에 있었다 - id("회사|이름")와 표시 키("미믹(포링푸드)")는
회사별로 정확히 바꾸고, 회사 표시 없이 남은 "미믹"은 포링푸드 쪽(권민우)으로 본다.
"""
from __future__ import annotations

import glob
import os

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import STATE_DIR

MARKER = os.path.join(STATE_DIR, ".names_v2_migrated")

PORING_COMPANY = "포링푸드 (Poring Food)"
ERINN_COMPANY = "에린 로지스틱스 (Erinn Logistics)"

PORING = {
    "오크 히어로": "오상식", "에드가": "김동식", "다크 로드": "장대희", "미믹": "권민우",
    "황금 도둑벌레": "노규태", "카프라": "최수연", "크라켄": "구동매", "고스트링": "장백기",
    "마야": "한서진", "소희": "오동백", "샌드맨": "류동룡", "무카": "이지안",
    "바포메트": "정명석", "도플갱어": "한석율",
}
ERINN = {
    "거대거미": "문동은", "새끼거미": "최택", "켈베로스": "홍두식", "랫맨": "성덕선",
    "골렘": "리정혁", "미믹": "황용식", "레드 드래곤": "김신", "헬하운드": "이준호",
    "고스트": "지은탁", "위습": "동그라미", "서큐버스": "윤세리", "임프": "조이서",
}


def _replacements() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    # 1) 내부 id "회사|이름" - 회사별로 정확히
    for company, mapping in ((PORING_COMPANY, PORING), (ERINN_COMPANY, ERINN)):
        pairs += [(f"{company}|{old}", f"{company}|{new}") for old, new in mapping.items()]
    # 2) 동명이인 표시 키 "미믹(포링푸드)" / "미믹(에린)"
    pairs += [("미믹(포링푸드)", PORING["미믹"]), ("미믹(에린)", ERINN["미믹"])]
    # 3) 나머지 이름 - 긴 것부터 (고스트링을 고스트보다 먼저)
    plain = {**ERINN, **PORING}  # 회사 표시 없는 "미믹"은 포링푸드 쪽
    pairs += sorted(plain.items(), key=lambda kv: len(kv[0]), reverse=True)
    pairs += [("오크부장", "오상식부장")]
    return pairs


def migrate() -> None:
    if os.path.exists(MARKER) or not os.path.isdir(STATE_DIR):
        return
    files = glob.glob(os.path.join(STATE_DIR, "*.json")) + glob.glob(os.path.join(STATE_DIR, "*.jsonl"))
    pairs = _replacements()
    changed = 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        new = text
        for old, rep in pairs:
            new = new.replace(old, rep)
        if new != text:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(new)
            os.replace(tmp, path)
            changed += 1
    with open(MARKER, "w", encoding="utf-8") as f:
        f.write("ok\n")
    print(f"[이름 변경] 실행 기록 {changed}개 파일의 옛 인물 이름을 새 이름으로 바꿈")
