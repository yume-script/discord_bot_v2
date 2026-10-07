"""
a_query()가 메인 진입점. conversation_key로 최근 대화 맥락(core/conversation_store.py)을
불러와 LLM에 먼저 넣어준 다음, 로컬 도구(ai/local_tools.py - 날씨/환율/주식)와 MCP 도구를
바인딩해서 필요하면 LLM이 알아서 tool_calls를 호출하게 한다 (최대 MAX_TOOL_TURNS번 왕복).

기존 봇의 aesun_rag_engine.py / aesun_rag_engine_models.py 구조를 정리해서 옮긴 것.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from ai.llm_client import build_chat_model, message_text
from ai.local_tools import LOCAL_TOOLS
from ai.mcp_manager import get_tools
from poring_food.tools import PORING_FOOD_TOOLS
from ai.prompts import build_system_prompt
from core import pending_actions
from core.conversation_store import get_recent_context
from core.tool_policy import is_server_affecting

MAX_TOOL_TURNS = 4

# [신규] 서버에 영향을 주는(쓰기/파괴적) 도구는 관리자만 실행할 수 있다 - 카톡은 관리자
# 판별이 안 되므로 항상 비관리자로 취급된다(cogs/chat.py의 _generate 참고). 도구 자체를
# 목록에서 빼지 않고 호출 시점에 막는 이유: LLM이 그 도구가 있다는 건 알아도 되고(다른
# 안전한 방법을 스스로 찾아볼 수 있으므로), 실제 실행만 막아야 하기 때문이다.
ADMIN_ONLY_TOOL_REPLY = "이건 관리자만 할 수 있는 작업이에요. 다른 걸 도와드릴까요?"

NO_ANSWER_REPLY = "죄송해요, 지금은 답을 만들지 못했어요."

# 관리자라도 서버에 영향을 주는 도구는 바로 실행하지 않고 core/pending_actions에 담아둔다 -
# 관리자가 "확인"이라고 답해야 cogs/chat.py가 실행한다. LLM에는 아직 실행 전이라고 알린다.
PENDING_CONFIRM_REPLY = (
    "확인 대기: 이 작업은 아직 실행되지 않았다. 관리자가 직접 확인해야 실행된다. 이 작업에 "
    "의존하는 다른 작업은 하지 말고, 무엇을 하려는지 한 줄로 설명한 뒤 확인을 기다린다고 답해."
)
NO_CONFIRM_SCOPE_REPLY = "확인 절차를 쓸 수 없는 경로라서 이 작업은 실행할 수 없어요."

# 도구 왕복 횟수를 다 쓰면 마지막 메시지는 ToolMessage(도구 원문)다 - 그걸 그대로 답장으로
# 내보내면 DB 조회 JSON 같은 게 채팅에 찍히므로, 이 지시를 붙여 한 번 더 정리된 답을 받는다.
FINALIZE_INSTRUCTION = (
    "도구는 더 이상 호출하지 말고, 지금까지 얻은 도구 결과만 바탕으로 사용자 질문에 "
    "자연스럽고 간결하게 최종 답변해."
)


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


async def a_query(
    conversation_key: str,
    message: str,
    is_kakao: bool = False,
    caller_is_admin: bool = False,
    confirm_scope: str | None = None,
) -> str:
    """
    is_kakao: 채널별 페르소나(디스코드=아메하나 / 카톡=애순이) 프롬프트를 고르는 데 쓴다.
    기본값 False라 기존 호출부(인자 안 넘기던 곳)는 그대로 아메하나로 동작한다.

    caller_is_admin: 서버에 영향을 주는 도구(core/tool_policy.is_server_affecting)를
    실제로 실행해도 되는지. 기본값 False라 인자를 안 넘기는 기존 호출부는 안전하게
    "관리자 아님"으로 동작한다(새 위험 기능이 실수로 열리는 방향이 아니라 닫히는 방향).

    confirm_scope: 관리자가 위험한 도구를 부르게 했을 때 확인 대기 목록에 담을 키
    (core/pending_actions.make_scope). 없으면 위험한 도구는 관리자라도 실행하지 않는다.
    """
    # 포링푸드 도구는 봇 안의 함수다(예전엔 poring_food MCP 서버였음). 혹시 MCP 쪽에 같은 이름의
    # 도구가 남아 있으면(예전 yaml) 이름이 겹쳐 LLM이 호출 자체를 거부하므로 로컬 쪽을 우선한다.
    local_tools = [*LOCAL_TOOLS, *PORING_FOOD_TOOLS]
    local_names = {t.name for t in local_tools}
    tools = [*local_tools, *(t for t in get_tools() if t.name not in local_names)]
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
            return message_text(ai_msg) or NO_ANSWER_REPLY

        for call in tool_calls:
            tool_fn = tools_by_name.get(call["name"])
            if tool_fn is None:
                result_text = f"알 수 없는 도구 호출: {call['name']}"
            elif is_server_affecting(call["name"]):
                if not caller_is_admin:
                    result_text = ADMIN_ONLY_TOOL_REPLY
                elif confirm_scope is None:
                    result_text = NO_CONFIRM_SCOPE_REPLY
                else:
                    pending_actions.add(confirm_scope, tool_fn, call["args"])
                    result_text = PENDING_CONFIRM_REPLY
            else:
                try:
                    result_text = await tool_fn.ainvoke(call["args"])
                except Exception as exc:  # noqa: BLE001 - 도구 실패는 결과로 돌려주고 계속 진행
                    result_text = f"도구 실행 실패: {exc}"
            messages.append(ToolMessage(content=str(result_text), tool_call_id=call["id"]))

    # MAX_TOOL_TURNS를 다 썼다 - 도구 결과 원문 대신 최종 요약을 한 번 더 요청한다.
    messages.append(HumanMessage(content=FINALIZE_INSTRUCTION))
    final_msg = await bound_model.ainvoke(messages)
    if getattr(final_msg, "tool_calls", None):
        # 지시를 무시하고 또 도구를 부르면 더 돌지 않고 끝낸다 (원문 노출보다 안전한 폴백).
        return NO_ANSWER_REPLY
    return message_text(final_msg) or NO_ANSWER_REPLY
