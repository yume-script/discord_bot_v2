import json

from .config import ORG_PATH, ISSUE_PATH, PERSONA_PATH


def load_resources():
    try:
        with open(ORG_PATH, "r", encoding="utf-8") as f:
            org = json.load(f)
        with open(ISSUE_PATH, "r", encoding="utf-8") as f:
            issues = json.load(f)
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            persona = json.load(f)

        # 경쟁사 정보가 org.json에 없을 경우 기본값 할당 (안전성 확보)
        if "market_info" not in org:
            org["market_info"] = {
                "rival": "체인 서점 '북스타'",
                "rival_product": "OTT 앱과 할인 전자책",
                "relation": "감찰 마법사(덴켄)를 보내 도서관을 마탑 지부로 삼키려는 왕도 마탑, 방문객 시간을 빼앗는 경쟁자"
            }

        return org, issues, persona
    except Exception as e:
        # [변경] cron 스크립트 시절엔 sys.exit(1)로 끝냈는데, 봇 안에서는 SystemExit가 봇까지
        # 끌어내릴 수 있어서 일반 예외로 올린다 (cogs/poring_food.py가 잡아서 이번 회차만 건너뜀).
        raise RuntimeError(f"포링푸드 리소스 로드 실패: {e}") from e


def fallback_perspective_converter(text):
    """
    3인칭으로 작성된 텍스트를 1인칭(애순이 시점)으로 변환합니다.
    """
    replacements = {
        "애순이 사장이": "내가", "애순이 사장의": "나의", "애순이 사장입니다": "나다",
        "애순이 사장은": "나는", "애순이 사장": "나",
        "애순이 대리가": "내가", "애순이 대리의": "나의", "애순이 대리입니다": "나다",
        "애순이 대리는": "나는", "애순이 대리": "나", "애순이는": "나는", "애순이의": "나의",
        "합니다.": "한다.", "봅니다.": "본다.", "느낍니다.": "느낀다.", "선보입니다.": "선보인다."
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text
