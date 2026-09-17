"""
원본 mbti_system.py(discord_bot)를 확보해서 그 로직으로 교체했다 - 카톡/디스코드 대화 로그를
기반으로 LLM이 MBTI를 분석해주는 게 원래 기능이었다. 이전에 원본을 못 찾아서 "자기 신고형
등록/조회"로 추측 구현했던 버전은 폐기한다.

원본과 다른 점:
- 로그 소스: 원본은 katalk_log_utils의 JSONL 파일에서 읽었는데, 이 프로젝트는 대화 로그를
  SQLite(core/conversation_store.py)로 저장하는 방식이라 거기서 같은 유저의 메시지를 모은다.
- LLM 호출: 원본은 google-genai SDK로 Gemini를 직접 불렀는데, 이 프로젝트는 LiteLLM 프록시를
  거치는 ai/llm_client.py를 재사용한다 (별도 Gemini API 키 관리가 필요 없어짐).
- "애순이 현재 기분" 인트로(aesun_persona.get_current_mood())는 이 프로젝트에 그 모듈이
  없어서 뺐다.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from ai.llm_client import build_chat_model
from core.conversation_store import get_messages_by_display_name

MIN_MESSAGES = 5

ANALYSIS_PROMPT_TEMPLATE = """\
당신은 제공된 대화 로그를 기반으로 이 사용자의 성향을 분석하여 MBTI를 추론해야 합니다.

[분석할 대화 로그]
{chat_log}

[답변 가이드라인]
1. 해요체와 음슴체를 섞은 애순이 말투를 유지하세요.
2. 불필요한 인사말이나 서론은 생략하고 바로 본론으로 들어가세요.
3. 다음 형식을 갖춰서 답변하세요:
   - **추정 MBTI**: (예: ISTP)
   - **지표별 근거 (E/I, S/N, T/F, J/P)**: (채팅 패턴을 근거로 설명)
   - **애순이의 한줄평**: (사용자에 대한 뼈 때리는 조언이나 무심한 격려)
"""


async def analyze_mbti(conversation_key: str, target_display_name: str) -> str:
    """conversation_key(방/채널) 안에서 target_display_name이 보낸 최근 메시지를 모아 MBTI를 분석한다."""
    messages = get_messages_by_display_name(conversation_key, target_display_name, limit=100)
    if len(messages) < MIN_MESSAGES:
        return f"❌ '{target_display_name}'님의 데이터가 너무 적어 분석이 불가능합니다. (현재 {len(messages)}개)"

    chat_log = "\n".join(f"- {m}" for m in messages)
    prompt = ANALYSIS_PROMPT_TEMPLATE.format(chat_log=chat_log)

    model = build_chat_model()
    resp = await model.ainvoke(
        [
            SystemMessage(content="너는 디스코드 봇 애순이야."),
            HumanMessage(content=prompt),
        ]
    )
    return (
        f"🌸 **애순이의 데이터 기반 MBTI 연산**\n"
        f"분석 대상: {target_display_name}님 (최근 {len(messages)}개 메시지 기반)\n\n"
        f"{resp.content}"
    )
