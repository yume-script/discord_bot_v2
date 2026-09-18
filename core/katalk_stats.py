"""
[주의] 원본 소스(카톡통계/오늘대화요약 관련 파일)를 확보하지 못했다 - 저장소 파일 목록이
알파벳순으로 잘려서 안 보이는 문제가 이번에도 반복됐다. 그래서 이 파일은 새로 작성한
구현이다 - 이미 갖고 있는 SQLite 대화 로그(core/conversation_store.py)를 그대로 활용한다.

/월간카톡, /카톡순위는 집계만 하면 되니 LLM 호출 없이 가볍게 처리하고,
/오늘대화요약만 LLM(ai/llm_client)을 한 번 호출한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langchain_core.messages import HumanMessage, SystemMessage

from ai.llm_client import build_chat_model
from core.conversation_store import get_message_counts_by_user, get_messages_since

MIN_SUMMARY_MESSAGES = 5


def _month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()


def _today_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def get_monthly_stats(conversation_key: str) -> str:
    """이번 달(1일 0시부터 지금까지) 방 전체 대화량 + 유저별 건수."""
    since = _month_start_iso()
    counts = get_message_counts_by_user(conversation_key, since_iso=since)
    if not counts:
        return "이번 달엔 아직 기록된 대화가 없어요."

    total = sum(c for _, c in counts)
    month_label = datetime.now(timezone.utc).strftime("%Y년 %m월")
    lines = [f"📅 {month_label} 카톡 통계 - 총 {total:,}건", ""]
    for i, (name, count) in enumerate(counts[:15], start=1):
        lines.append(f"{i}. {name or '(이름 없음)'} - {count:,}건")
    if len(counts) > 15:
        lines.append(f"... 외 {len(counts) - 15}명")
    return "\n".join(lines)


def get_ranking(conversation_key: str, period: str = "today") -> str:
    """
    오늘(period='today') 또는 이번 달(period='month') 채팅 순위 TOP 5.
    """
    if period == "month":
        since = _month_start_iso()
        label = "이번 달"
    else:
        since = _today_start_iso()
        label = "오늘"

    counts = get_message_counts_by_user(conversation_key, since_iso=since)
    if not counts:
        return f"{label} 대화 기록이 없어요."

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 {label} 카톡 순위"]
    for i, (name, count) in enumerate(counts[:5]):
        rank_mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{rank_mark} {name or '(이름 없음)'} - {count:,}건")
    return "\n".join(lines)


async def summarize_today(conversation_key: str) -> str:
    """오늘 이 방에서 오간 대화를 LLM으로 짧게 요약한다."""
    since = _today_start_iso()
    messages = get_messages_since(conversation_key, since, direction="in")
    if len(messages) < MIN_SUMMARY_MESSAGES:
        return f"오늘은 아직 대화가 너무 적어서 요약할 게 없어요. (현재 {len(messages)}개)"

    # 너무 길면 토큰 낭비니 최근 300개까지만 사용
    messages = messages[-300:]
    chat_log = "\n".join(f"- {name or '누군가'}: {text}" for name, text in messages)

    prompt = (
        "다음은 오늘 하루 한 단체 카톡방에서 오간 대화 로그다. 이 대화를 5~8줄 정도로 "
        "요약해줘. 주요 화제, 재미있었던 순간, 누가 대화를 많이 이끌었는지 정도를 "
        "자연스럽게 담아줘.\n\n"
        f"[오늘의 대화 로그]\n{chat_log}"
    )

    model = build_chat_model()
    resp = await model.ainvoke(
        [
            SystemMessage(content="너는 디스코드 봇 아메하나야. 카톡방 대화를 요약해주는 중이야."),
            HumanMessage(content=prompt),
        ]
    )
    return f"📝 **오늘의 대화 요약** (총 {len(messages)}개 메시지 기준)\n\n{resp.content}"
