"""
세계 설정 - 「오아시스 거리」 (2026-10 새로 시작).

광주의 오래된 골목에 같은 날 두 가게가 문을 연다. 두 가게는 손님(사람들의 시간과 관심)을 두고 경쟁하면서도
같은 건물의 지하 창고/전기/관리사무소를 나눠 쓴다.

| 영역           | 실제 서비스                    | 이야기 속 모습                                   |
|----------------|-------------------------------|------------------------------------------------|
| BOOK           | 북오아시스 (BookOasis)          | 책방 오아시스 - 서가, 웹툰관, 심야 서고(성인 서재) |
| CINEMA         | Plex / Tautulli               | 별빛극장 - 동네 단관 극장 + 로비 카페 '오후세시'    |
| STREET (통합)  | 서버 상태 / LLM 사용량 / 뉴스  | 오아시스 거리 - 골목 가게들, 건물 관리사무소       |

회사 이름은 여기 한 곳에서만 정한다 (다른 모듈은 이 상수를 쓴다).
"""
BOOK = "책방 오아시스"
CINEMA = "별빛극장"
STREET = "오아시스 거리"          # 통합 세상 = 동네 (예전 "광주 동네 상가")
FRIENDS = "애순이·소라의 사람들"   # 가족/친구 (어느 가게에도 속하지 않음)

# 안의 일(실적/인사/수치/사내 다툼)은 그 가게 사람만 안다 - chronicle 칸막이, 소문 범위
OFFICES = (BOOK, CINEMA)
# 짧은 이름 -> 회사 이름 (LLM 응답/글에서 짐작)
SHORT = {"책방": BOOK, "오아시스 책방": BOOK, "별빛": CINEMA, "극장": CINEMA}

# 가게별 단톡방 (모두 같은 채널에 머리줄로 구분해 올린다 - agents._group_channel)
BOOK_GROUP = "책방 단톡방"
CINEMA_GROUP = "극장 단톡방"
GROUP_OF = {BOOK: BOOK_GROUP, CINEMA: CINEMA_GROUP}

# 장소
BOOK_PLACE = "책방 오아시스"
CINEMA_PLACE = "별빛극장"
CAFE_PLACE = "카페 오후세시"        # 별빛극장 1층 로비 카페 (소라)
OFFICE_PLACE = "관리사무소"         # 건물 관리사무소 (오아시스 거리)
STORAGE_PLACE = "지하 공용 창고"

LABEL = {BOOK: "📚 책방 오아시스", CINEMA: "🎬 별빛극장", STREET: "🏘️ 오아시스 거리", FRIENDS: "👥 주변 사람들"}

# 처음 시작할 때(초기화 직후) 세계 엔진/연대기에 넣는 배경
PREMISE = (
    f"광주의 오래된 골목 '{STREET}'에 같은 날 두 가게가 문을 열었다. 3대째 헌책방을 손녀 애순이가 물려받아 새로 연 "
    f"'{BOOK}'(서가/웹툰관/심야 서고)과, 문 닫았던 동네 단관 극장을 되살린 '{CINEMA}'(1층 로비에 소라의 카페 오후세시). "
    "두 가게는 손님의 시간과 관심을 두고 경쟁하지만, 같은 건물의 지하 공용 창고와 전기, 관리사무소를 나눠 쓴다. "
    "건물주 부상길은 개업 축하를 핑계로 월세를 올릴 궁리를 하고, 관리사무소장 리정혁은 창고와 전기를 묵묵히 지킨다."
)


def short(company: str) -> str:
    """표시용 짧은 이름 ('포링푸드 (Poring Food)' 같은 괄호 영문 표기를 뗀다)."""
    return (company or "").split(" (")[0]


# 1화(개업 첫 주) 사건 씨앗 - 정해진 대본이 아니라 세계 엔진이 그날 하루 계획에 참고하는 거리 (실제 수치에 따라 달라진다)
SEEDS = {
    1: "개업 첫날 - 책방 오아시스와 별빛극장이 같은 날 문을 연다. 두 가게 다 개업 떡을 남숙반점(조남숙)에 맞춰서 동네에 소문이 난다.",
    2: "지하 공용 창고 칸을 어떻게 나눌지 - 관리사무소(리정혁)가 반상회 안건으로 올린다 (첫 투표감).",
    3: "별빛극장 첫 심야 상영 - 다음 날 아침 골목에 소음 민원이 들어온다 (관리사무소 최택).",
    4: "책방 오아시스 첫 대량 입고 - 상자를 쌓을 자리가 없다 (지하 창고가 차오르는 정도는 실제 창고 적재율 수치대로).",
    5: "건물주 부상길이 '개업 축하' 명목으로 두 가게에 월세 인상 얘기를 꺼낸다.",
    7: "첫 주 결산 - 책방과 극장 중 어디가 더 바빴는지 (실제 입고/관객 수치대로).",
}
START_PATH_NAME = "world_start.json"   # STATE_DIR 안 - 초기화(reset_world)할 때 오늘 날짜로 쓴다


def _start_path() -> str:
    import os
    from .config import STATE_DIR
    return os.path.join(STATE_DIR, START_PATH_NAME)


def start_date() -> str:
    """세계가 새로 시작한 날 (YYYY-MM-DD, 기록이 없으면 "")."""
    import json
    try:
        with open(_start_path(), "r", encoding="utf-8") as f:
            return str(json.load(f).get("date") or "")
    except (OSError, ValueError, AttributeError):
        return ""


def day_number(today: str) -> int:
    """오늘이 세계 시작 며칠째인지 (1일차부터, 기록이 없으면 0)."""
    from datetime import date
    start = start_date()
    if not start:
        return 0
    try:
        return (date.fromisoformat(today) - date.fromisoformat(start)).days + 1
    except ValueError:
        return 0


def seed_block(today: str) -> str:
    """세계 엔진 하루 계획용: 세상의 시작 배경 + (첫 주라면) 오늘의 사건 씨앗."""
    n = day_number(today)
    if not n or n > max(SEEDS):
        return ""
    seed = SEEDS.get(n, "")
    return (f"[이 세계의 시작 - 오늘은 {n}일차]\n{PREMISE}\n"
            + (f"- 오늘의 씨앗(꼭 그대로일 필요는 없다, 어울리게): {seed}\n" if seed else ""))
