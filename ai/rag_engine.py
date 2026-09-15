"""
a_query()가 메인 진입점: 의도 분류 -> needs_mcp면 tool-call 루프(최대 4턴) -> 최종 응답.
기존 봇의 aesun_rag_engine.py / aesun_rag_engine_models.py 구조를 정리해서 옮긴 것.
"""
from __future__ import annotations

import json

from langchain_core.messages import HumanMessage, SystemMessage

from ai.llm_client import build_chat_model
from ai.mcp_manager import get_tools
from ai.prompts import CLASSIFIER_PROMPT, RESPONSE_SYSTEM_PROMPT

MAX_TOOL_TURNS = 4


async def _classify(message: str) -> dict:
    model = build_chat_model(temperature=0)
    resp = await model.ainvoke([HumanMessage(content=CLASSIFIER_PROMPT.format(message=message))])
    try:
        return json.loads(resp.content)
    except (json.JSONDecodeError, TypeError):
        return {"needs_mcp": False, "intent": "unknown"}


async def _handle_with_tools(message: str) -> str:
    tools = get_tools()
    model = build_chat_model().bind_tools(tools) if tools else build_chat_model()

    messages = [SystemMessage(content=RESPONSE_SYSTEM_PROMPT), HumanMessage(content=message)]
    for _ in range(MAX_TOOL_TURNS):
        ai_msg = await model.ainvoke(messages)
        messages.append(ai_msg)
        if not getattr(ai_msg, "tool_calls", None):
            return ai_msg.content
        # TODO: tool_calls 실행 -> ToolMessage로 messages에 append 후 계속 루프
        break
    return messages[-1].content


async def _handle_plain(message: str) -> str:
    model = build_chat_model()
    resp = await model.ainvoke(
        [SystemMessage(content=RESPONSE_SYSTEM_PROMPT), HumanMessage(content=message)]
    )
    return resp.content


async def a_query(message: str) -> str:
    classification = await _classify(message)
    if classification.get("needs_mcp"):
        return await _handle_with_tools(message)
    return await _handle_plain(message)
