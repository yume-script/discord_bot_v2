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
    _migrate_v2()
    _migrate_v3()


# ===================================================================== v3: 동네 사람들 -> 드라마 인물
# 동네(광주 동네 상가) 인물을 「폭싹 속았수다」/「갯마을 차차차」 인물로 새로 꾸렸다. 성향이 완전히 달라져서 옛 동네
# 사람들의 기록을 새 사람에게 옮기지는 않는다 (옛 이름 기록은 그대로 남고, 더 이상 등장하지 않는다).
# (응답하라 쪽 배경 인물로 돌아온 성보라/김정환/김정봉/성나정/김재준은 이름만 같은 새 인물 - 예전 교체 프로필은 버린다)
# 단 에린 로지스틱스의 "홍두식"은 동네 홍반장과 이름이 겹쳐서 "고동만"으로 바꾸고 기록도 옮긴다.
MARKER_V3 = os.path.join(STATE_DIR, ".names_v3_migrated")
OLD_TOWN = {"박새로이", "길라임", "서달미", "김정봉", "강정희", "고애신", "최무성", "안정원", "김정환", "성보라",
            "박동훈", "송삼동", "진상필", "하명희", "강동희", "곽덕순", "장만옥", "홍자영", "박상훈", "윤명주",
            "백승수", "박기훈", "최향미", "고혜미", "성나정", "김재준", "장만복"}
REDRAWN = {"오춘재", "오윤", "장영국"}  # 이름은 그대로지만 성향이 드라마를 따라 바뀐 사람 (예전에 써 둔 교체 프로필은 버린다)


def _migrate_v3() -> None:
    import json
    if os.path.exists(MARKER_V3) or not os.path.isdir(STATE_DIR):
        return
    files = (glob.glob(os.path.join(STATE_DIR, "*.json")) + glob.glob(os.path.join(STATE_DIR, "*.jsonl"))
             + glob.glob(os.path.join(STATE_DIR, "archive", "*.jsonl")))
    changed = 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        new = text.replace(f"{ERINN_COMPANY}|홍두식", f"{ERINN_COMPANY}|고동만").replace("홍두식", "고동만")
        if new != text:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(new)
            os.replace(tmp, path)
            changed += 1
    # 교체 상태: 사라진 동네 사람/성향이 바뀐 사람의 프로필과 등장 기록을 정리
    rot = os.path.join(STATE_DIR, "rotation.json")
    try:
        with open(rot, "r", encoding="utf-8") as f:
            data = json.load(f)
        gone = OLD_TOWN | REDRAWN
        data["benched"] = [n for n in data.get("benched", []) if n not in gone]
        data["promoted"] = {n: e for n, e in (data.get("promoted") or {}).items() if n not in gone}
        data["profiles"] = {n: e for n, e in (data.get("profiles") or {}).items() if n not in gone}
        with open(rot + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(rot + ".tmp", rot)
    except (OSError, ValueError):
        pass
    # 진행 중인 마을 프로젝트: 담당자가 사라진 사람이면 통장에게
    proj = os.path.join(STATE_DIR, "projects.json")
    try:
        with open(proj, "r", encoding="utf-8") as f:
            items = json.load(f)
        for p in items:
            if p.get("stage") != "완료" and p.get("owner") in OLD_TOWN:
                p["owner"] = "여화정"
            p["helpers"] = [h for h in p.get("helpers", []) if h not in OLD_TOWN]
        with open(proj + ".tmp", "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(proj + ".tmp", proj)
    except (OSError, ValueError):
        pass
    with open(MARKER_V3, "w", encoding="utf-8") as f:
        f.write("ok\n")
    print(f"[이름 변경] 동네 사람들을 드라마 인물로 - 에린 홍두식 -> 고동만 ({changed}개 파일), 교체/프로젝트 정리")


def _migrate_v2() -> None:
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
