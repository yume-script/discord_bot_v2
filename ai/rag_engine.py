"""
a_query()가 메인 진입점. 매 호출마다 로컬 도구(ai/local_tools.py - 날씨/환율/주식)와
MCP 도구(연결돼 있다면)를 전부 LLM에 바인딩해두고, LLM이 필요하다고 판단하면 알아서
tool_calls를 발생시켜 호출한다 (최대 MAX_TOOL_TURNS번 왕복). 도구가 필요 없는 일반
대화는 그냥 한 턴만에 끝난다.

기존 봇의 aesun_rag_engine.py / aesun_rag_engine_models.py 구조를 정리해서 옮긴 것.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from ai.llm_client import build_chat_model
from ai.local_tools import LOCAL_TOOLS
from ai.mcp_manager import get_tools
from ai.prompts import RESPONSE_SYSTEM_PROMPT

MAX_TOOL_TURNS = 4


async def a_query(message: str) -> str:
    tools = [*LOCAL_TOOLS, *get_tools()]
    tools_by_name = {t.name: t for t in tools}

    model = build_chat_model()
    bound_model = model.bind_tools(tools) if tools else model

    messages = [SystemMessage(content=RESPONSE_SYSTEM_PROMPT), HumanMessage(content=message)]

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
