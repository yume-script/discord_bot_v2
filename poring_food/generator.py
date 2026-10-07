import json
import os
import random
import requests
from datetime import datetime

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from .config import API_URL, LITELLM_MASTER_KEY, LLM_MODEL
from . import processor

# [변경] 마무리 섹션("🎮 나의 라그M 상태")이 활동과 상관없이 항상 게임 얘기로 고정되어
# 있었다 - 영화관에 있어도 "라그나로크M 접속은 못 했지만..." 식으로 억지로 게임을 끌어오게
# 됨. 오늘 실제 활동(state)에 맞는 헤더/화제로 바꿔서, 게임 중일 때만 게임 얘기가 나오고
# 나머지는 그날 활동에 맞는 마무리가 나오게 한다.
_STATUS_HEADERS = {
    "게임 중": ("🎮 나의 라그M 상태", "라그나로크M 게임 상황(버스/파티/숙제 등)"),
    "데이트 중": ("💕 오늘의 데이트 후기", "방금 있었던 데이트 소감"),
    "나들이 중": ("🚶 오늘의 나들이 후기", "영화/서점/쇼핑 등 오늘 다녀온 곳에 대한 소감"),
    "운동 중": ("💪 오늘의 운동 기록", "오늘 한 운동에 대한 소감"),
    "모임 중": ("👯 오늘의 모임 후기", "친구들과의 모임 소감"),
    "휴식 중": ("💭 오늘의 소회", "오늘 하루를 보내며 든 잡생각"),
}
_DEFAULT_STATUS_HEADER = ("💭 오늘의 소회", "오늘 하루를 보내며 든 잡생각")

# [변경] 아래 두 개가 매번 "반드시 포함"이라 거의 모든 보고서가 "지난번 ~은 해결됐지만..."
# 으로 시작하고, 활동과 무관하게 "포링 젤리"/생산량 얘기로 수렴하는 원인이었다.
# 이제 확률로만 등장시켜서, 나올 때도 있고 안 나올 때도 있게 한다. .env로 조정 가능.
PREV_ISSUE_CALLBACK_PROBABILITY = float(os.getenv("PREV_ISSUE_CALLBACK_PROBABILITY", "0.4"))
PRODUCTION_STATS_MENTION_PROBABILITY = float(os.getenv("PRODUCTION_STATS_MENTION_PROBABILITY", "0.35"))


# 함수 시그니처에 mood 인자를 추가했습니다.
def generate_aesun_report(issue, time_tag, org_data, persona_data, weather_info, stats, mood, sales_count=0,
                          bookoasis_block="", story_block=""):
    """
    processor에서 생성된 '이전 사건 후일담', '오늘의 기분', '생산 통계'를 바탕으로
    애순이의 인간적인 희노애락이 담긴 1인칭 보고서를 생성합니다.
    sales_count: 오늘 "영업 판매수량"(Plex 재생 수, poring_food/metrics.py) - 생산량과
    같은 확률(PRODUCTION_STATS_MENTION_PROBABILITY)로 같이 언급되거나 같이 빠진다.
    bookoasis_block: [신규] 애순이 겸직(사내 자료실 북오아시스) 소식 - 신간 입고나 서고 전산
    장애/복구가 있을 때만 내용이 있고(bookoasis.build_prompt_block), 없으면 빈 문자열이다.
    """
    count, progress = stats  # 생산량 통계 언패킹
    main_product = org_data.get("main_product", "포링 젤리")
    now = datetime.now()
    current_time_str = f"{now.strftime('%Y-%m-%d')} {now.hour:02d}:00 {time_tag}"

    # 상세 상태 가져오기
    location, activity, focus, state, _ = processor.get_aesun_detailed_schedule()
    status_header, status_topic_hint = _STATUS_HEADERS.get(state, _DEFAULT_STATUS_HEADER)

    include_prev_callback = random.random() < PREV_ISSUE_CALLBACK_PROBABILITY
    include_stats = random.random() < PRODUCTION_STATS_MENTION_PROBABILITY

    stats_line = (
        f"- 오늘 생산 현황(신규 입고): {main_product} {count}건 (평소 하루 생산량 대비 {progress}%)\n"
        f"- 오늘 영업 판매수량(출하): {sales_count}건\n\n"
    ) if include_stats else "\n"
    prev_callback_rule = (
        "- 이전 사건에 대한 후일담을 1~2문장 정도 자연스럽게 섞어라, 그때 느꼈던 감정도 살짝 곁들여라.\n"
        if include_prev_callback else
        "- 이전 사건은 이번 보고서에서 굳이 언급하지 않아도 된다 - 오늘 활동에 집중해서 써라.\n"
    )
    stats_rule = (
        "- '오늘의 기분'과 '생산 현황'/'영업 판매수량'을 자연스럽게 녹여라.\n"
        if include_stats else
        "- 오늘은 생산 현황/판매수량/젤리 얘기를 억지로 끌어오지 말고, 순수하게 오늘 활동과 기분 위주로 써라.\n"
    )

    # LLM 시스템 프롬프트: mood 인자를 직접 사용하여 프롬프트 주입
    system_prompt = (
        "너는 가상의 회사 '포링푸드' 생산부 대리이자, 일상 속에서 희노애락을 느끼는 인간적인 '애순이'다.\n"
        "너는 이전 사건이 어떻게 해결되었는지 알고 있으며, 이를 일기처럼 서술한다.\n\n"
        f"--- [현재 상태 및 기분] ---\n"
        f"- 오늘의 기분: {mood}\n"
        f"- 위치: {location}\n"
        f"- 현재 활동: {activity}\n"
        f"- 현재 심리: {focus}\n"
        f"- 현재 날씨: {weather_info}\n"
        f"- 우리 회사 주력 제품: {main_product}\n"
        f"{stats_line}"
        f"{bookoasis_block}"
        f"{story_block}"
        "--- [애순이의 캐릭터 특징] ---\n"
        "1. 주 6일 근무하는 생산부 대리. 업무 스트레스와 일상의 소소한 행복(커피, 농담 등)을 동시에 느낀다. "
        "사내 자료실 '북오아시스'(사내 도서·오디오북 서고) 관리 담당도 겸직하고 있다 - 서고 소식이 "
        "주어진 날에만 언급하고, 없는 날엔 굳이 꺼내지 마라.\n"
        "2. 라그나로크M도 하고 영화/데이트/독서/운동/친구모임 등 다양한 개인 생활도 즐기는 평범한 사람이다.\n"
        "3. 업무/일상 상황을 시니컬하지만 유머러스하게 표현한다.\n"
        "4. **매우 중요**: 오늘 기분이 안 좋더라도 무조건 부정적으로만 쓰지 마라. 기분이 {mood}이므로, 이를 애순이 특유의 방식으로 유머러스하게 승화하거나, '그래도 퇴근 후엔 보상받을 거야'라는 긍정적인 반전을 반드시 포함해라.\n\n"
        "작성 지침:\n"
        "- 제공된 [오늘의 사건]을 바탕으로 애순이의 독백을 작성해라.\n"
        f"{prev_callback_rule}"
        f"{stats_rule}"
        "- 매번 똑같은 표현/문장 구조를 반복하지 말고, 오늘 활동에 맞는 새로운 소재와 어휘로 써라.\n"
        f"- **중요**: 마지막 'game_status' 필드는 반드시 \"{status_topic_hint}\"에 대한 내용으로 채워라. "
        f"오늘 애순이는 '{state}'({activity}) 중이라 게임과 무관한 날일 수 있다 - 게임 중이 아닐 때는 "
        "게임 얘기를 억지로 끌어오지 말고, 실제 오늘 활동에 대한 짧은 소감으로 채워라.\n"
        "- 반드시 아래 키를 가진 JSON 객체로 응답: {\"narrative\": \"...\", \"game_status\": \"...\", \"cynical_thought\": \"...\"}"
    )

    user_prompt = f"[오늘의 사건]\n제목: {issue['title']}\n내용: {issue['description']}"

    if API_URL and LITELLM_MASTER_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.8,
                "response_format": {"type": "json_object"}
            }
            res = requests.post(API_URL, json=payload, headers=headers, timeout=15)
            if res.status_code == 200:
                result = res.json()
                content_str = result["choices"][0]["message"]["content"]
                parsed_data = json.loads(content_str)

                # 페르소나 템플릿에 데이터 주입
                formatted_report = persona_data["speech_style"]["formatting_template"].format(
                    current_time=current_time_str,
                    title=issue['title'],
                    narrative=parsed_data.get("narrative", ""),
                    status_header=status_header,
                    ragnarok_status=parsed_data.get("game_status", ""),
                    cynical_thought=parsed_data.get("cynical_thought", "")
                )
                parsed_data["full_report"] = formatted_report
                return parsed_data
        except Exception as e:
            print(f"[경고] LLM 보고서 생성 중 오류 발생: {e}")

    # Fallback 로직도 동일하게 mood를 반영
    fallback_narrative = (
        f"어제 {issue.get('title', '일')}은 해결된 것 같은데, 오늘도 {location}에서 {activity}라니... "
        f"기분은 '{mood}'지만, {main_product} 생산률 {progress}%인 상태로 오늘도 힘내서 채팅창에 '버스 부탁드려요!'라고 올리고 버텨봅니다."
    )
    if state == "게임 중":
        fallback_status = "버스 탑승 대기 중, 채팅창 밑밥 깔기 시전"
    else:
        fallback_status = f"{activity} 하며 잠깐 딴생각, 그래도 나쁘지 않은 하루"
    fallback_cynical = "희노애락 다 겪어도 결국 생산량 채우고 하루는 흘러간다."

    formatted_report = persona_data["speech_style"]["formatting_template"].format(
        current_time=current_time_str,
        title=issue.get('title', '오늘의 사건'),
        narrative=fallback_narrative,
        status_header=status_header,
        ragnarok_status=fallback_status,
        cynical_thought=fallback_cynical
    )

    return {
        "narrative": fallback_narrative,
        "game_status": fallback_status,
        "cynical_thought": fallback_cynical,
        "full_report": formatted_report
    }


# ============================================================= 스포트라이트 로테이션 + 온디맨드용
# [신규] 애순이 말고 다른 인물(포링푸드/에린 로지스틱스 27명 전원)에게도 쓸 수 있는 범용
# 리포트 생성기. aesun_persona.json 같은 전용 페르소나 파일이 없는 사람들이라, 조직도의
# outer_persona/inner_truth를 그대로 프롬프트에 넣어 "겉모습과 속마음의 괴리"를 살린다.
_GENERIC_STATUS_HEADERS = {
    "일하는 중": ("🏢 오늘의 업무 소감", "오늘 업무/직장에서 있었던 일에 대한 소감"),
    "개인시간": ("💭 오늘의 소회", "오늘 개인 시간을 보내며 든 생각"),
}
_GENERIC_DEFAULT_HEADER = ("💭 오늘의 소회", "오늘 하루를 보내며 든 생각")

GENERIC_TEMPLATE = (
    "[📢 {name}의 실시간 현장 보고]\n\n"
    "🕒 시간: {current_time}\n"
    "📌 현장 상황: {title}\n\n"
    "💬 {name}의 한마디:\n\"{narrative}\"\n\n"
    "{status_header}:\n- {closing}\n- (한줄 요약: {cynical_thought})"
)


def generate_generic_character_report(character, issue, time_tag, weather_info, mood, location, activity, state,
                                      story_block=""):
    """
    character: characters.load_roster()의 항목 하나 (name/company/dept/rank/outer_persona/
    inner_truth 포함). issue: 그날의 회사 공통 이슈(processor.get_last_issue() 등에서 가져온
    것 - 애순이 파이프라인과 같은 이슈를 공유해서 세계관 연속성을 유지한다).
    """
    name = character["name"]
    now = datetime.now()
    current_time_str = f"{now.strftime('%Y-%m-%d')} {now.hour:02d}:00 {time_tag}"
    status_header, topic_hint = _GENERIC_STATUS_HEADERS.get(state, _GENERIC_DEFAULT_HEADER)
    title = issue.get("title", "오늘의 사건")

    system_prompt = (
        f"너는 '{character.get('company', '')}' {character.get('dept', '')} {character.get('rank', '')} "
        f"'{name}'이다.\n"
        f"겉으로 보이는 모습: {character.get('outer_persona', '')}\n"
        f"속마음(진짜 성격): {character.get('inner_truth', '')}\n"
        "이 겉모습과 속마음의 괴리를 살려서, 시니컬하지만 유머러스한 1인칭 일기를 써라.\n\n"
        f"--- [현재 상태] ---\n"
        f"- 오늘의 기분: {mood}\n"
        f"- 위치: {location}\n"
        f"- 현재 활동: {activity}\n"
        f"- 현재 날씨: {weather_info}\n"
        f"{story_block}\n"
        "작성 지침:\n"
        f"- 회사에 떠도는 [오늘의 사건]을 알고 있다는 티를 살짝만 내되, '{name}'만의 시점과 속마음으로 "
        "재해석해서 써라 - 다른 사람(애순이 등)과 똑같은 반응/말투를 절대 흉내내지 마라.\n"
        f"- 마지막 'closing' 필드는 \"{topic_hint}\"에 대한 내용으로 채워라.\n"
        "- 매번 똑같은 표현/문장 구조를 반복하지 말고 새로운 소재와 어휘로 써라.\n"
        "- 반드시 아래 키를 가진 JSON 객체로 응답: {\"narrative\": \"...\", \"closing\": \"...\", \"cynical_thought\": \"...\"}"
    )
    user_prompt = f"[오늘의 사건]\n제목: {title}\n내용: {issue.get('description', '')}"

    if API_URL and LITELLM_MASTER_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.85,
                "response_format": {"type": "json_object"}
            }
            res = requests.post(API_URL, json=payload, headers=headers, timeout=15)
            if res.status_code == 200:
                parsed = json.loads(res.json()["choices"][0]["message"]["content"])
                formatted = GENERIC_TEMPLATE.format(
                    name=name, current_time=current_time_str, title=title,
                    narrative=parsed.get("narrative", ""), status_header=status_header,
                    closing=parsed.get("closing", ""), cynical_thought=parsed.get("cynical_thought", "")
                )
                parsed["full_report"] = formatted
                parsed["name"] = name
                return parsed
        except Exception as e:
            print(f"[경고] {name} 보고서 생성 중 오류 발생: {e}")

    # fallback
    fallback_narrative = f"{name}는 오늘 {location}에서 {activity} 중이다. 기분은 '{mood}'."
    fallback_closing = f"{activity}에 대한 짧은 소감, 그럭저럭 나쁘지 않다."
    fallback_cynical = "오늘도 그럭저럭 하루가 흘러간다."
    formatted = GENERIC_TEMPLATE.format(
        name=name, current_time=current_time_str, title=title,
        narrative=fallback_narrative, status_header=status_header,
        closing=fallback_closing, cynical_thought=fallback_cynical
    )
    return {
        "narrative": fallback_narrative,
        "closing": fallback_closing,
        "cynical_thought": fallback_cynical,
        "full_report": formatted,
        "name": name,
    }
