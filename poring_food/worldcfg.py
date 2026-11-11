"""
세계 설정 - 출판사 「(주)ZEEPS」 (2026-11 현대 출판사 이야기로 새로 시작, 예전 마법 판타지 「오아시스 마을」을 대신함).

서울 변두리 '오아시스 거리'의 작은 출판사. 같은 거리에 BookOasis 3D 서재(서점 건물)가 있어, 출판사가 만든 책·웹툰·영상이
그 서점으로 배달된다(디스코드 봇 ↔ 3D 서재 연동: 대표 자리 전화, 신간 배달). 사옥 1층엔 카페 '오후세시'.
한 회사 안의 두 부서가 실적을 두고 경쟁하면서도 같은 서버실, 같은 물류창고, 같은 관리비를 나눠 쓴다.

| 영역           | 실제 서비스                    | 이야기 속 모습                                              |
|----------------|-------------------------------|-----------------------------------------------------------|
| BOOK           | 북오아시스 (BookOasis)          | ZEEPS 편집국 - 책·웹툰·오디오북 출간, 성인 서고(19금 라인)        |
| CINEMA         | Plex / Tautulli               | ZEEPS 영상콘텐츠팀 - 원작 영상화·시사회·스트리밍 + 1층 카페 '오후세시' |
| STREET (통합)  | 서버 상태 / LLM 사용량 / 뉴스  | 오아시스 거리 - 사옥 시설관리팀(서버실·건물), 거리 가게들          |

실제 수치는 이렇게 읽는다 (각 모듈 docstring에 자세히):
- 서버 CPU = 서버 부하, 디스크 = 지하 물류창고, 멈춘 서비스/가동일 = 서버실 안정, 컨테이너 = 모니터링 화면
- LLM 요청/토큰 = AI 어시스턴트 사용량, 북오아시스 = 출간·입고, Plex = 영상 상영, 날씨 = 출퇴근 날씨
- 바깥 뉴스 = 뉴스 속보·업계 소식

회사 이름은 여기 한 곳에서만 정한다 (다른 모듈은 이 상수를 쓴다).
"""
COMPANY = "(주)ZEEPS"
BOOK = "ZEEPS 편집국"
CINEMA = "ZEEPS 영상콘텐츠팀"
STREET = "오아시스 거리"           # 통합 세상 = 사옥과 거리
FRIENDS = "애순이·소라의 사람들"   # 가족/친구 (어느 부서에도 속하지 않음)

# 안의 일(실적/인사/수치/안 다툼)은 그 부서 사람만 안다 - chronicle 칸막이, 소문 범위
OFFICES = (BOOK, CINEMA)
# 짧은 이름 -> 회사 이름 (LLM 응답/글에서 짐작)
SHORT = {"편집국": BOOK, "편집부": BOOK, "출판": BOOK, "영상팀": CINEMA, "영상콘텐츠팀": CINEMA, "영상": CINEMA}

# 부서별 단톡방 (메신저 대화방 - 모두 같은 채널에 머리줄로 구분해 올린다 - agents._group_channel)
BOOK_GROUP = "편집국 단톡방"
CINEMA_GROUP = "영상팀 단톡방"
TOWN_GROUP = "사내 단톡방"
GROUP_OF = {BOOK: BOOK_GROUP, CINEMA: CINEMA_GROUP}
REMOTE = "메신저"                   # 멀리서 하는 대화 - agents의 how 값

# 장소
BOOK_PLACE = "ZEEPS 편집국"
CINEMA_PLACE = "ZEEPS 영상콘텐츠팀"
CAFE_PLACE = "카페 오후세시"        # 사옥 1층 카페 (소라)
OFFICE_PLACE = "관리실"             # 사옥 시설관리팀
WALL_PLACE = "서버실"
STORAGE_PLACE = "지하 물류창고"
GUARD = "시설관리팀"                # 거리 조직도의 부서 (예전 성벽 수비대)
BOOKSTORE = "BookOasis 3D 서재"     # 같은 거리의 서점 - 신간이 이리로 배달된다

LABEL = {BOOK: "📚 ZEEPS 편집국", CINEMA: "🎬 ZEEPS 영상콘텐츠팀", STREET: "🏙️ 오아시스 거리", FRIENDS: "👥 주변 사람들"}

# 모든 판단/장면 프롬프트에 붙는 세계 규칙 (판타지 요소가 끼어들지 않게)
WORLD_RULE = (
    "[세계] 여기는 지금의 한국, 서울 변두리 '오아시스 거리'에 있는 작은 출판사 '(주)ZEEPS'다. 마법·몬스터·용사 같은 판타지는 없다 - "
    "멀리 있는 사람과는 메신저로 말하고, 돈은 원으로 센다. 같은 거리에 서점 'BookOasis 3D 서재'가 있어 새로 나온 책·웹툰·영상이 그리로 배달된다.\n"
    "[회사의 사명] 1) 독자가 보는 영상과 책·웹툰 서비스가 절대 끊기거나 느려지지 않게 한다 - 무엇보다 먼저. "
    "2) 그 안에서 원고(웹툰·책 자료)를 최대한 많이 도서 장부(DB)에 올린다. "
    "서버 부하가 높아지면 영상 서비스·책 서비스·메신저는 절대 끄지 않고, 자동화 봇과 정리 작업부터 쉬게 했다가 부하가 내려가면 다시 돌린다."
)

# 처음 시작할 때(초기화 직후) 세계 엔진/연대기에 넣는 배경
PREMISE = (
    f"서울 변두리 '{STREET}'. 애순이가 3대째 이어 온 동네 책방 자리를 물려받아 작은 출판사 '{COMPANY}'를 차렸다. "
    f"회사엔 두 부서가 있다 - 책·웹툰·오디오북을 내는 '{BOOK}'(성인 서고 라인 포함)과, 원작을 영상으로 만들고 시사회를 여는 "
    f"'{CINEMA}'(사옥 1층엔 소라의 카페 오후세시). 두 부서는 실적과 예산을 두고 경쟁하지만 같은 서버실과 지하 물류창고, "
    f"관리비를 나눠 쓴다. 같은 거리의 서점 '{BOOKSTORE}'에는 사서 한서윤이 있어, 새로 나온 책이 나오면 회사 사람이 직접 배달 간다. "
    "건물주 알다프는 '임대료 현실화'를 핑계로 관리비를 올릴 궁리를 하고, 시설관리팀장 베니마루는 서버실과 건물을 묵묵히 지킨다."
)


def short(company: str) -> str:
    """표시용 짧은 이름 ('포링푸드 (Poring Food)' 같은 괄호 영문 표기를 뗀다)."""
    return (company or "").split(" (")[0]


# 1화(개업 첫 주) 사건 씨앗 - 정해진 대본이 아니라 세계 엔진이 그날 하루 계획에 참고하는 거리 (실제 수치에 따라 달라진다)
SEEDS = {
    1: "창립 첫날 - (주)ZEEPS가 문을 연다. 편집국과 영상콘텐츠팀이 개업 떡을 센시네 주점에 맞춰서 거리에 소문이 난다.",
    2: "지하 물류창고 칸을 어떻게 나눌지 - 시설관리팀(베니마루)이 사내 회의 안건으로 올린다 (첫 투표감).",
    3: "영상콘텐츠팀 첫 심야 시사회 - 동시 접속이 몰려 서버가 버거웠다는 불만이 시설관리팀(고브타)에 들어온다.",
    4: "편집국 첫 대량 입고 - 원고 상자를 쌓을 자리가 없다 (물류창고가 차오르는 정도는 실제 창고 적재율 수치대로).",
    5: "건물주 알다프가 '창립 축하' 명목으로 두 부서에 관리비 인상 얘기를 꺼낸다.",
    6: "첫 신간 배달 - 편집국이 낸 책을 같은 거리 서점 BookOasis 3D 서재(사서 한서윤)에 직접 들고 간다.",
    7: "첫 주 결산 - 편집국과 영상팀 중 어디가 더 바빴는지 (실제 입고/시청 수치대로), 그리고 첫 주 동안 서버가 버틴 밤들.",
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
