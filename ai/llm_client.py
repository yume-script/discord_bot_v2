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


def message_text(msg) -> str:
    """
    LLM 응답 메시지의 content를 문자열로 정규화한다. 모델/프록시에 따라(예: LiteLLM 경유 Gemini)
    content가 문자열이 아니라 [{"type": "text", "text": ...}, ...] 블록 리스트로 오기도 해서,
    그대로 디스코드에 보내면 리스트 repr이 찍히거나 길이 계산이 어긋난다.
    """
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return str(content or "")
