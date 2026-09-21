"""
a_query()가 메인 진입점. conversation_key로 최근 대화 맥락(core/conversation_store.py)을
불러와 LLM에 먼저 넣어준 다음, 로컬 도구(ai/local_tools.py - 날씨/환율/주식)와 MCP 도구를
바인딩해서 필요하면 LLM이 알아서 tool_calls를 호출하게 한다 (최대 MAX_TOOL_TURNS번 왕복).

기존 봇의 aesun_rag_engine.py / aesun_rag_engine_models.py 구조를 정리해서 옮긴 것.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ai.llm_client import build_chat_model
from ai.local_tools import LOCAL_TOOLS
from ai.mcp_manager import get_tools
from ai.prompts import build_system_prompt
from core.conversation_store import get_recent_context

MAX_TOOL_TURNS = 4


def _build_history_messages(conversation_key: str) -> list:
    history = get_recent_context(conversation_key)
    messages = []
    for display_name, text, direction in history:
        if direction == "out":
            messages.append(AIMessage(content=text))
        else:
            content = f"{display_name}: {text}" if display_name else text
            messages.append(HumanMessage(content=content))
    return messages


async def a_query(conversation_key: str, message: str, is_kakao: bool = False) -> str:
    """
    is_kakao: 채널별 페르소나(디스코드=아메하나 / 카톡=애순이) 프롬프트를 고르는 데 쓴다.
    기본값 False라 기존 호출부(인자 안 넘기던 곳)는 그대로 아메하나로 동작한다.
    """
    tools = [*LOCAL_TOOLS, *get_tools()]
    tools_by_name = {t.name: t for t in tools}

    model = build_chat_model()
    bound_model = model.bind_tools(tools) if tools else model

    messages = [SystemMessage(content=build_system_prompt(is_kakao))]
    messages.extend(_build_history_messages(conversation_key))
    messages.append(HumanMessage(content=message))

    for _ in range(MAX_TOOL_TURNS):
        ai_msg = await bound_model.ainvoke(messages)
        messages.append(ai_msg)

        tool_calls = getattr(ai_msg, "tool_calls", None)
        if not tool_calls:
            return ai_msg.content

        for call in tool_calls:
            tool_fn = tools_by_name.get(call["name"])
            if tool_fn is None:
                result_text = f"알 수 없는 도구 호출: {call['name']}"
            else:
                try:
                    result_text = await tool_fn.ainvoke(call["args"])
                except Exception as exc:  # noqa: BLE001 - 도구 실패는 결과로 돌려주고 계속 진행
                    result_text = f"도구 실행 실패: {exc}"
            messages.append(ToolMessage(content=str(result_text), tool_call_id=call["id"]))

    # MAX_TOOL_TURNS를 넘어가면 마지막 메시지라도 반환 (도구 결과만 있고 최종 요약이 없을 수 있음)
    return messages[-1].content or "죄송해요, 지금은 답을 만들지 못했어요."
