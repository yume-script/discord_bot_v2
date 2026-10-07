import random
import requests
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from .clock import now_kst
from . import life, metrics
from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import (
    API_URL, LITELLM_MASTER_KEY, LLM_MODEL, SEARCH_MODEL, DISCORD_BOT_V2_DB_PATH, ISSUE_LOG_PATH, RIVALS_PATH,
)

# [변경] 봇으로 합치면서 .env는 봇(config/settings.py)이 이미 읽어둔다 - 여기서 다시 load_dotenv 안 함.
# [변경] 실행 위치 기준 상대경로("last_issue.json")였던 걸 storage/poring_food/last_issue.json으로 고정.
ISSUE_LOG_FILE = ISSUE_LOG_PATH
KST = timezone(timedelta(hours=9))

# 이 길드(디스코드 서버) 전체로 들어오는 입력을 "고객의 요청사항"으로 간주.
# https://discord.com/channels/{길드ID}/{채널ID} 에서 길드ID 부분.
TARGET_GUILD_ID = os.getenv("TARGET_GUILD_ID", "591180628842774550")


def get_production_stats():
    """
    (오늘 생산량, 최근 7일 하루 평균 대비 %). [변경] 예전엔 대화 로그의 사람 메시지 수였는데,
    이제 생산량은 오늘 Plex/북오아시스 신규 입고 수다 (poring_food/metrics.py, 회차 시작 때 갱신).
    """
    return metrics.production()


def get_sales_stats():
    """오늘 영업 판매수량 = 오늘 Plex 재생 수 (Tautulli)."""
    return metrics.sales()


def get_order_stats():
    """오늘 주문·고객 문의 수 = 오늘 사람이 봇에게 보낸 메시지 수 (예전 "생산량")."""
    return metrics.orders()


# =============================================================================
# 경쟁사 비교 (업계 동향 - 회사일에 대한 경각심)
# =============================================================================


def _load_rivals() -> list:
    if os.path.exists(RIVALS_PATH):
        try:
            with open(RIVALS_PATH, "r", encoding="utf-8") as f:
                return json.load(f).get("rivals", [])
        except Exception as e:
            print(f"[경고] 경쟁사 데이터 로드 실패: {e}")
    return []


def _count_today_messages_in_room(room_id: str) -> int:
    """
    특정 방(room_id)의 오늘(KST) 메시지 수 - "특정 게시판의 갱신을 경쟁사 성과율로 비교"하는 용도.

    [변경] 원래 예전 봇(v1)의 카톡 로그(/mnt/discord_bot/katalk_log/log_{room_id}.jsonl)를 읽었는데,
    v1 봇은 꺼서 그 로그가 더 이상 갱신되지 않는다(실측값이 계속 0이 됨). 지금 봇의 대화 로그
    DB에서 같은 방(conversation_key = 카톡 방ID)의 오늘자 유저 메시지 수를 센다.
    """
    if not os.path.exists(DISCORD_BOT_V2_DB_PATH):
        return 0
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(f"file:{DISCORD_BOT_V2_DB_PATH}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_key = ? AND direction = 'in' AND ts LIKE ?",
                (str(room_id), f"{today_str}%"),
            ).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()
    except Exception as e:
        print(f"[경고] 경쟁사 벤치마크 집계 실패: {e}")
        return 0


def get_rival_performance_report(our_count: int) -> list:
    """
    경쟁사별 오늘의 성과 지표를 계산해서 우리 실적(our_count)과 비교한 리스트로 반환.
    - benchmark_room_id가 설정된 경쟁사: 그 채널(게시판)의 실제 오늘자 메시지 수를 그대로 사용 (실측)
    - 설정 안 된 경쟁사: 날짜+회사명 시드 기반이라 같은 날엔 항상 같은 값 (추정 시뮬레이션,
      하루 안에서 실행할 때마다 들쭉날쭉하지 않게 함)
    """
    rivals = _load_rivals()
    today_str = now_kst().strftime("%Y-%m-%d")
    report = []

    for rival in rivals:
        name = rival.get("name", "이름 없는 경쟁사")
        product = rival.get("product", "정체불명의 제품")
        flavor = rival.get("flavor", "")
        benchmark_room = rival.get("benchmark_room_id")

        if benchmark_room:
            performance = _count_today_messages_in_room(str(benchmark_room))
            source = "실측"
        else:
            rnd = random.Random(f"{name}-{today_str}")
            base = max(our_count, 10)
            performance = int(base * rnd.uniform(0.6, 1.4))
            source = "추정"

        report.append({
            "name": name,
            "product": product,
            "flavor": flavor,
            "performance": performance,
            "delta_vs_us": performance - our_count,
            "source": source,
        })

    return report


def _format_rival_block(rival_report: list) -> str:
    if not rival_report:
        return ""
    lines = []
    for r in rival_report:
        if r["delta_vs_us"] > 0:
            cmp_word = f"우리보다 {r['delta_vs_us']}건 앞서고"
        elif r["delta_vs_us"] < 0:
            cmp_word = f"우리보다 {-r['delta_vs_us']}건 뒤처지고"
        else:
            cmp_word = "우리와 정확히 동률이고"
        lines.append(f"- {r['name']}({r['product']}): 오늘 지표 {r['performance']}건, {cmp_word} 있음")

    joined = "\n".join(lines)
    return (
        f"\n[업계 동향 - 경쟁사 오늘의 성과]\n{joined}\n"
        "이 경쟁 구도를 이야기에 살짝 긴장감이나 경각심으로 녹여내도 좋다 "
        "(단, 애순이 특유의 낙천적인 태도는 잃지 않게 - 매번 심각하게 다룰 필요는 없다).\n"
    )


def fetch_gwangju_weather():
    """Gemini-search 모델을 이용해 현재 광주광역시의 실시간 날씨를 조회합니다."""
    if not API_URL or not LITELLM_MASTER_KEY:
        return "날씨 정보를 가져올 수 없음 (API 설정 미비)"

    prompt = "오늘 현재 대한민국 광주광역시의 날씨(기온, 하늘 상태, 특이사항)를 아주 짧게 한 문장으로 알려줘."
    try:
        headers = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": SEARCH_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5
        }
        res = requests.post(API_URL, json=payload, headers=headers, timeout=15)
        if res.status_code == 200:
            weather_text = res.json()["choices"][0]["message"]["content"]
            return weather_text.strip()
    except Exception as e:
        print(f"[경고] 날씨 조회 실패: {e}")
    return "날씨 정보 조회 실패 (평범한 흐린 날씨)"


def _weighted_choice(rnd, candidates):
    """
    candidates: [(location, activity, focus, state, weight), ...] 중 가중치 랜덤으로 하나 고른다.
    [변경] 애순이의 지금 상태(life.py - 스트레스/체력/외로움/돈 등)로 가중치를 조정한다 -
    스트레스가 쌓이면 술자리, 지치면 집에서 쉬기가 더 잘 나온다.
    """
    st = life.get("애순이")
    candidates = [(loc, act, focus, state, life.bias_weight(st, f"{loc} {act}", w))
                  for loc, act, focus, state, w in candidates]
    total = sum(c[4] for c in candidates)
    r = rnd.uniform(0, total)
    upto = 0
    for loc, act, focus, state, w in candidates:
        upto += w
        if upto >= r:
            return loc, act, focus, state
    loc, act, focus, state, _ = candidates[-1]
    return loc, act, focus, state


def get_aesun_detailed_schedule():
    """
    애순이의 시간별 상세 스케줄 및 상태를 반환합니다.
    [변경] 원래는 시간대마다 활동이 "일하는 중" 아니면 "게임 중" 둘 중 하나로 고정돼 있어서
    이야기가 회사/게임 얘기로만 편중됐다. 점심/저녁/주말처럼 개인 시간이 날 법한 시간대는
    영화·데이트·독서·쇼핑·운동·친구모임 같은 개인 일상 후보를 추가하고 가중치 랜덤으로
    고른다 - 날짜+시간을 시드로 써서 같은 시간대 안에서는 값이 고정되고(여러 번 돌아도 안
    바뀜), 시간이 지나면 자연스럽게 바뀐다. 출퇴근/취침/업무 시간대는 그대로 고정 - 실제로
    그 시간엔 다른 걸 하기 어려우니까.
    """
    now = now_kst()
    hour = now.hour
    weekday = now.weekday()
    is_weekend = (weekday == 6)
    is_sleeping = False
    rnd = random.Random(now.strftime("%Y-%m-%d-%H"))  # 시간대별로 고정, 시간이 바뀌면 갱신

    if 2 <= hour < 6:
        is_sleeping = True
        return "집(침대)", "깊은 취침 중", "자동 사냥 돌려놓고 꿈나라 여행 중", "자는 중", is_sleeping

    if is_weekend:
        if 6 <= hour < 11:
            is_sleeping = True
            return "집(침대)", "꿀같은 일요일 늦잠", "평일의 피로를 잠으로 보충 중", "자는 중", is_sleeping
        elif 11 <= hour < 19:
            loc, act, focus, state = _weighted_choice(rnd, [
                ("집(거실)", "배달 음식 먹으며 라그M 접속", "누가 숙제 버스 좀 태워줬으면 좋겠음", "게임 중", 3),
                ("영화관", "친구랑 신작 영화 관람", "팝콘 향에 취해 회사 생각은 완전히 잊음", "나들이 중", 2),
                ("동네 카페", "소개팅 겸 데이트", "상대방 눈치 보랴 대화 이어가랴 정신없음", "데이트 중", 1),
                ("서점", "다음 주 읽을 책 고르는 중", "에세이 코너에서 한참을 서성임", "나들이 중", 2),
                ("헬스장/공원", "밀린 운동 벼락치기", "숨은 차지만 그래도 상쾌함", "운동 중", 1),
                ("친구 집", "친구들과 브런치 모임", "오랜만에 회사 얘기 말고 딴 얘기만 함", "모임 중", 2),
                ("쇼핑몰", "계절 옷 쇼핑", "월급 스쳐 지나가는 소리가 들림", "나들이 중", 1),
            ])
            return loc, act, focus, state, False
        else:
            loc, act, focus, state = _weighted_choice(rnd, [
                ("집(침대 위)", "내일 출근 공포를 게임으로 잊기", "월요병 도지기 직전의 필사적인 레이드 시청", "게임 중", 3),
                ("집(소파)", "밀린 드라마/영화 정주행", "다음 화 눌러버리다 자정 넘김", "휴식 중", 2),
                ("집(책상)", "야식 먹으며 독서", "몇 장 못 읽고 또 딴생각", "휴식 중", 1),
            ])
            return loc, act, focus, state, False
    else:
        is_saturday = (weekday == 5)
        fatigue_label = " (토요일 특근으로 분노 상승)" if is_saturday else ""

        if 6 <= hour < 8:
            return "출근 버스 안", f"지옥의 출근길{fatigue_label}", "졸면서 단톡방 확인, 저녁 버스 파티 미리 구걸", "이동 중", False
        elif 8 <= hour < 12:
            return "회사(사무실/현장)", "오전 업무 수행 중", "상사 눈 피해 스마트폰 뒤집어놓고 몰래 자사 확인", "일하는 중", False
        elif 12 <= hour < 13:
            loc, act, focus, state = _weighted_choice(rnd, [
                ("회사 식당", "점심 빨리 먹고 구석에서 레이드", "밥 먹으면서도 채팅창에서 숙제 파티 탐색", "게임 중", 3),
                ("회사 앞 카페", "동료와 점심 커피 수다", "회사 뒷담화 반, 게임 얘기 반", "휴식 중", 2),
                ("회사 근처 서점", "점심시간 책방 산책", "딱 10분만 보고 오려다 늦을 뻔함", "나들이 중", 1),
                ("회사 옥상", "잠깐 혼자만의 낮잠", "5분만 자려다 15분 자버림", "휴식 중", 1),
            ])
            return loc, act, focus, state, False
        elif 13 <= hour < 19:
            return "회사(생산 현장)", f"오후 업무 진행 중{fatigue_label}", "체력 방전, 그냥 퇴근하고 싶음", "일하는 중", False
        elif 19 <= hour < 21:
            return "퇴근길 버스 안", "기력을 짜낸 길드 채팅", "집 도착 시각 계산하며 버스 예약", "이동 중", False
        else:
            loc, act, focus, state = _weighted_choice(rnd, [
                ("집(침대/컴퓨터 앞)", "본격적인 버스 탑승 및 채팅", "고수님들 뒤졸졸 따라다니며 숙제 완료", "게임 중", 4),
                ("집/영화관", "퇴근 후 영화 한 편", "오늘만큼은 아무 생각 없이 몰입", "휴식 중", 2),
                ("동네 카페/레스토랑", "저녁 데이트", "회사 얘기는 절대 안 하기로 다짐함", "데이트 중", 2),
                ("헬스장/요가원", "저녁 운동 클래스", "운동보다 씻고 나오는 뿌듯함이 더 큼", "운동 중", 1),
                ("집(책상)", "밤에 잠깐 독서", "두 페이지 읽고 스르륵 잠들 뻔함", "휴식 중", 1),
                ("친구와 전화", "밀린 수다 타임", "회사 스트레스를 친구한테 다 쏟아냄", "휴식 중", 1),
                ("동네 포장마차", "동기와 퇴근 후 소주 한잔", "오늘 쌓인 걸 안주 삼아 털어냄", "휴식 중", 1),
            ])
            return loc, act, focus, state, False


def get_daily_mood():
    moods = [
        "오늘따라 모닝 커피가 정말 맛있어서 기분이 좋다.",
        "유독 피곤해서 만사가 귀찮지만 퇴근 생각만 하며 버티는 중.",
        "동료들과 농담 따먹기를 해서 꽤 신나고 낄낄거리는 중이다.",
        "업무가 너무 많아 울고 싶지만 레이드 아이템 먹을 생각에 참는다.",
        "무미건조하지만 평화로운 하루, 이런 날도 있어야지 싶다."
    ]
    return random.choice(moods)


def get_last_issue():
    if os.path.exists(ISSUE_LOG_FILE):
        try:
            with open(ISSUE_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            last_time = datetime.fromisoformat(data.get("timestamp", now_kst().isoformat()))
            if now_kst() - last_time > timedelta(hours=24):
                return {"title": "평화로운 일상", "description": "지난 사건은 모두 해결되어 특별한 문제 없는 평온한 상태다."}
            return data
        except:
            return None
    return None


def save_current_issue(issue):
    issue["timestamp"] = now_kst().isoformat()
    with open(ISSUE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(issue, f, ensure_ascii=False)


# 외부 화제(사회/영화/스포츠/날씨)를 반영할 확률 (0~1). .env의 EXTERNAL_TOPIC_PROBABILITY로 조정 가능.
# 예전엔 "사회 이슈" 하나만 35% 확률로 살짝 곁들이는 정도였는데,
# 카테고리를 넓히고 확률도 올려서 이제 절반 이상은 회사 밖 이야기가 섞이도록 함.
EXTERNAL_TOPIC_PROBABILITY = float(os.getenv("EXTERNAL_TOPIC_PROBABILITY", "0.6"))

# [변경] 경쟁사 비교(rival_block)를 예전엔 매번 프롬프트에 무조건 끼워넣었다 - 결과적으로
# 거의 모든 보고서가 "경쟁사 지표" 얘기로 끌려가는 원인이 됨(프롬프트에 항상 존재하는
# 내용은 LLM이 언급할 확률이 높아진다). 이제 이 확률로만 등장시켜서 가끔 나오는
# 배경 정보 수준으로 낮춘다. .env의 RIVAL_MENTION_PROBABILITY로 조정 가능.
RIVAL_MENTION_PROBABILITY = float(os.getenv("RIVAL_MENTION_PROBABILITY", "0.3"))

# 카테고리별 검색 가이드. 무작위로 하나 골라서 그 분야의 오늘자 화제를 가져옴.
_TOPIC_CATEGORIES = {
    "사회": "가볍고 무난한 사회적 이슈나 화제 뉴스 (생활/문화/유행/훈훈한 미담 위주)",
    "영화": "최근 개봉했거나 화제가 되고 있는 영화 또는 OTT 시리즈 소식",
    "스포츠": "최근 있었던 국내외 스포츠 경기 결과나 화제 (야구, 축구, e스포츠 등)",
    "날씨": "오늘 대한민국의 날씨 특이사항이나 체감 이야기 (폭염, 장마, 미세먼지, 첫눈 등)",
}


def fetch_daily_topic():
    """
    검색 모델을 이용해 오늘 화제가 될 만한 소식을 무작위 카테고리(사회/영화/스포츠/날씨)에서
    하나 가져온다. 정치적으로 민감하거나 자극적인 주제는 프롬프트로 피하도록 유도한다.
    실패하거나 API 미설정 시 None을 반환 (호출부에서 안전하게 스킵됨).
    """
    if not API_URL or not LITELLM_MASTER_KEY:
        return None

    category = random.choice(list(_TOPIC_CATEGORIES.keys()))
    guide = _TOPIC_CATEGORIES[category]
    prompt = (
        f"오늘 한국에서 화제가 될 만한 '{category}' 분야의 가볍고 무난한 소식을 하나만 골라줘. "
        f"({guide})\n"
        "정치적으로 민감하거나 논쟁적인 주제, 자극적인 사건/사고, 혐오나 갈등을 조장할 수 있는 "
        "주제는 반드시 피해라.\n"
        "반드시 JSON 형식으로만 응답: "
        f'{{"category": "{category}", "topic_title": "...", "topic_summary": "한두 문장 요약"}}'
    )

    try:
        headers = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": SEARCH_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.5,
            "response_format": {"type": "json_object"}
        }
        res = requests.post(API_URL, json=payload, headers=headers, timeout=15)
        if res.status_code == 200:
            content = res.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            if data.get("topic_title"):
                return data
    except Exception as e:
        print(f"[경고] 오늘의 화제 조회 실패: {e}")
    return None


def generate_dynamic_issue(org_data, weather_info, factory_status, our_count=0, bookoasis_block="", story_block=""):
    location, activity, focus, state, _ = get_aesun_detailed_schedule()
    last_issue = get_last_issue()
    prev_context = f"이전 사건: '{last_issue['title']}' / 상황: {last_issue['description']}" if last_issue else "최근 특별한 사건 없음."

    main_product = org_data.get("main_product", "포링 젤리")
    all_members = []
    for dept in org_data["departments"]:
        for member in dept["members"]:
            all_members.append(f"{member.get('prefix', '')} {member['name']} {member['rank']}")
    selected = random.sample(all_members, 3)

    # 외부 화제(사회/영화/스포츠/날씨)를 이번 회차에 반영할지 결정
    daily_topic = None
    if random.random() < EXTERNAL_TOPIC_PROBABILITY:
        daily_topic = fetch_daily_topic()

    topic_block = ""
    if daily_topic:
        topic_block = (
            f"\n오늘의 화제 ({daily_topic.get('category', '일상')}): "
            f"'{daily_topic['topic_title']}' - {daily_topic.get('topic_summary', '')}\n"
            "이 화제를 애순이 특유의 시선으로 자연스럽게 이야기의 중심 소재로 삼거나, "
            "가볍게 곁들여라 (억지로 회사 업무와 엮으려 하지 않아도 되고, "
            "그냥 '아 이런 거 봤는데' 수준의 개인적인 감상/잡담으로 등장해도 좋다).\n"
        )

    # [변경] 경쟁사 동향 - 예전엔 매번 넣었는데, RIVAL_MENTION_PROBABILITY 확률로만 등장시켜서
    # 매 보고서가 "경쟁사 지표" 얘기로 끌려가지 않게 함.
    rival_block = ""
    if random.random() < RIVAL_MENTION_PROBABILITY:
        rival_report = get_rival_performance_report(our_count)
        rival_block = _format_rival_block(rival_report)

    prompt = (
        f"너는 '포링푸드'의 인간미 넘치는 애순이다.\n"
        f"우리 회사의 주력 생산 제품: {main_product}\n"
        f"현재 상황: {location}에서 {activity} 중.\n"
        f"오늘 날씨: {weather_info}\n"
        f"포링푸드 공장 가동 상태: {factory_status}\n"
        f"{prev_context}\n"
        f"등장 가능한 조연 후보(선택사항): {', '.join(selected)}\n"
        f"{topic_block}"
        f"{rival_block}"
        f"{bookoasis_block}"
        f"{story_block}\n"
        "작성 규칙:\n"
        "1. 이전 사건이 진행 중이라면 해결책을 제시하고, 이미 해결되었다면 그 후일담을 짧게 언급해라.\n"
        "2. 오늘 이야기의 중심 소재는 완전히 자유롭게 골라라 - 회사 업무/사내 정치일 필요는 전혀 없다. "
        "위에 '오늘의 화제'가 주어졌다면 그것을 중심 소재로 삼아도 좋고, 날씨 이야기, "
        "애순이의 개인적인 일상(출퇴근길 관찰, 점심 메뉴 고민, 어제 본 영화나 응원하는 팀 경기 결과, "
        "라그M 이야기)만으로 이야기를 이끌어가도 좋다. 회사 이야기를 할 때는 자연스럽게 "
        f"'{main_product}' 생산/품질/포장/맛 관련 소재를 곁들여도 좋다 (매번 그럴 필요는 없다). "
        "위에 나열한 조연 후보들은 등장이 필수가 아니다 - 오늘 이야기에 자연스럽게 어울리면 "
        "대화 상대나 배경으로 살짝 등장시키고, 안 어울리면 아예 등장시키지 않아도 된다.\n"
        "3. **중요: 화제나 회사 상황이 다소 무겁거나 아쉬운 내용이어도 비관적으로만 쓰지 마라. "
        "소소한 재미, 엉뚱한 발견, 혹은 긍정적인 반전을 반드시 포함시켜라.**\n"
        "4. 애순이는 '포링푸드 생산부 대리'이자 '라그나로크M 게이머'이기 이전에, 하루하루를 "
        "다채롭게 느끼며 사는 평범한 사람이다. 영화를 보고 여운에 잠기기도 하고, 좋아하는 "
        "스포츠팀 결과에 일희일비하기도 하고, 날씨 하나에 기분이 오락가락하기도 하는 등 "
        "회사/게임 밖의 감정도 자연스럽게 드러내라.\n"
        "5. [업계 동향]이 주어졌다면, 그 경쟁 구도를 가볍게 언급하며 '우리도 정신 차려야겠다'는 "
        "식의 자기 다짐이나 유머러스한 위기감으로 녹여내도 좋다 (매번 그럴 필요는 없고, "
        "오늘 이야기 소재와 안 어울리면 생략해도 된다).\n"
        "6. 반드시 JSON 형식으로만 응답: {\"title\": \"...\", \"description\": \"...\"}"
    )

    try:
        headers = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": LLM_MODEL,
            "messages": [{"role": "system", "content": "너는 사내 사건의 연속성을 기록하는 작가다."}, {"role": "user", "content": prompt}],
            "temperature": 0.8,
            "response_format": {"type": "json_object"}
        }
        res = requests.post(API_URL, json=payload, headers=headers, timeout=15)
        if res.status_code == 200:
            new_issue = json.loads(res.json()["choices"][0]["message"]["content"])
            save_current_issue(new_issue)
            return new_issue
    except Exception as e:
        print(f"[에러] 이슈 생성 실패: {e}")

    return {"title": "평범한 하루", "description": "특별한 일 없이 피곤한 하루가 지나가고 있다."}


def get_time_tag():
    now = now_kst()
    weekday = now.weekday()
    hour = now.hour
    if weekday == 6:
        return "[일요일/꿀잠/휴식]"
    elif weekday == 5:
        return "[토요일/지옥특근]"
    if 2 <= hour < 6:
        return "[심야/취침중]"
    elif 6 <= hour < 9:
        return "[오전/출근길]"
    elif 19 <= hour < 21:
        return "[저녁/퇴근길]"
    return "[평일/업무/게임]"
