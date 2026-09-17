"""
기존 봇에서 실제 LLM 호출은 gemini_service.py(죽은 코드)가 아니라
LangChain ChatOpenAI로 LiteLLM 프록시를 경유하는 경로였다.
새 봇은 이 최종 형태를 처음부터 표준으로 채택한다.
"""
from langchain_openai import ChatOpenAI

from config import settings


def build_chat_model(*, temperature: float = 0.7) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.LITELLM_BASE_URL,
        api_key=settings.LITELLM_API_KEY,
        model=settings.LITELLM_MODEL,
        temperature=temperature,
    )
