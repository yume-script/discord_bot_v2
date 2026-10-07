"""
[신규] 애순이 겸직 - 사내 자료실 "북오아시스" 관리 담당.

checker.py가 카톡 브릿지 서버 상태를 "공장 가동 상태"로, processor.py가 discord_bot_v2 대화
로그를 "생산량"으로 바꿔 쓰는 것처럼, 실제 BookOasis 서버의 신간 입고/장서 현황을 애순이가
관리하는 "서고" 이야기 소재로 쓴다.

[연결] 봇(ai/mcp_manager.py)에 설정된 bookoasis MCP 서버에 세션 "하나"를 열어서 조회를 연달아
한다(server_session). 봇의 LangChain 도구는 호출마다 SSH+docker exec를 새로 띄워서, 조회 3번이면
연결도 3번이라 느렸다(실서버에서 45초 타임아웃). 이 모듈은 봇 이벤트 루프 밖(스레드)에서 돌기
때문에 세션 작업은 runtime.run_on_bot_loop()로 봇 루프에 넘겨서 실행한다.

[안전] BookOasis MCP 서버에는 데이터를 바꾸는 도구도 있지만, 여기서는 조회 도구
(search_books, get_library_stats)만 이름을 지정해서 호출한다. LLM이 도구를 고르는 구조가
아니므로 매시간 사람 없이 돌아도 실제 데이터가 바뀌는 일은 없다.

[동작]
- 매 실행(애순이가 방송 주인공일 때만) 서재 종류별 최신순 목록을 받아서, 지난번에 본 것보다
  새로 들어온 책만 "신간 입고"로 뽑는다. 처음 실행할 때는 기준점만 잡고 아무것도 알리지 않는다
  (기존 장서가 한꺼번에 "신간"으로 쏟아지지 않게).
- 서고 연결이 끊기거나 다시 붙은 "순간"에만 이야기에 넣는다 - 장애가 길어져도 매시간
  같은 얘기만 반복되지 않게.
- 결과는 BOOKOASIS_STATE_PATH에 저장해서, mcp_server.py의 get_bookoasis_report()가
  "애순아 서고 요즘 어때?" 같은 질문에 답할 수 있게 한다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from ._log import pf_print as print  # print()를 봇 로그로 (systemd에서 stdout 버퍼링 방지)
from . import runtime
from .config import (
    BOOKOASIS_ENABLED,
    BOOKOASIS_MCP_SERVER,
    BOOKOASIS_STATE_PATH,
    BOOKOASIS_STORY_DB_TYPES,
    BOOKOASIS_TIMEOUT_SEC,
)

DB_TYPE_LABELS = {"general": "일반 도서", "adult": "성인 서재", "audiobook": "오디오북"}
SEARCH_LIMIT = 10
MAX_RECENT_KEEP = 20  # 상태 파일에 남겨둘 최근 입고 기록 수
MAX_NEW_IN_PROMPT = 5  # 이야기 프롬프트에 넣을 신간 최대 개수


def _load_state() -> dict:
    if not os.path.exists(BOOKOASIS_STATE_PATH):
        return {}
    try:
        with open(BOOKOASIS_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    try:
        os.makedirs(os.path.dirname(BOOKOASIS_STATE_PATH), exist_ok=True)
        tmp = BOOKOASIS_STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BOOKOASIS_STATE_PATH)
    except OSError as e:
        print(f"[경고] 북오아시스 상태 저장 실패: {e}")


def _parse_tool_result(result) -> dict:
    """MCP 도구 결과(CallToolResult)를 dict로. 구조화 결과가 있으면 그걸, 없으면 텍스트를 JSON으로."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        # FastMCP는 반환값을 {"result": ...}로 감싸기도 한다 (문자열 JSON이면 한 번 더 파싱)
        inner = structured.get("result", structured)
        if isinstance(inner, dict):
            return inner
        if isinstance(inner, str):
            try:
                data = json.loads(inner)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return {}


async def _fetch_async() -> dict:
    """bookoasis MCP 서버에 세션 하나를 열고 조회 도구만 호출한다. [봇 이벤트 루프에서 실행]"""
    from ai.mcp_manager import server_session

    async with server_session(BOOKOASIS_MCP_SERVER) as session:
        tool_names = {t.name for t in (await session.list_tools()).tools}

        series_by_type: dict[str, list] = {}
        if "search_books" in tool_names:
            for db_type in BOOKOASIS_STORY_DB_TYPES:
                res = await session.call_tool(
                    "search_books", {"db_type": db_type, "sort": "date_desc", "limit": SEARCH_LIMIT}
                )
                if getattr(res, "isError", False):
                    continue
                series_by_type[db_type] = _parse_tool_result(res).get("series", []) or []

        stats: dict = {}
        if "get_library_stats" in tool_names:
            res = await session.call_tool("get_library_stats", {})
            if not getattr(res, "isError", False):
                stats = _parse_tool_result(res)

    return {"series_by_type": series_by_type, "stats": stats}


def _fetch() -> dict:
    """[스레드에서 호출] 봇 이벤트 루프에서 _fetch_async를 돌리고 결과를 기다린다."""
    return runtime.run_on_bot_loop(_fetch_async(), timeout=BOOKOASIS_TIMEOUT_SEC)


def _root_cause(exc: BaseException) -> BaseException:
    """mcp/anyio가 감싸서 올리는 ExceptionGroup에서 실제 원인 예외를 꺼낸다 (로그를 읽을 수 있게)."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _stats_summary(stats: dict) -> str:
    """
    장서 통계 응답 형식을 정확히 모르니, 숫자 값만 골라 짧게 요약한다 (프롬프트 참고용).
    이야기에 쓰지 않기로 한 서재(기본: adult)가 이름에 들어간 항목은 통계에서도 뺀다.
    """
    if not isinstance(stats, dict):
        return ""
    excluded = [t for t in DB_TYPE_LABELS if t not in BOOKOASIS_STORY_DB_TYPES]
    items = []

    def walk(prefix: str, value) -> None:
        if len(items) >= 8:
            return
        if any(t in prefix.lower() for t in excluded):
            return
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            items.append(f"{prefix}={value}")
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else str(k), v)

    walk("", stats)
    return ", ".join(items)


def check_bookoasis() -> dict:
    """
    서고를 점검하고 결과를 돌려준다. 실패해도 예외를 올리지 않는다(이야기 생성은 계속돼야 함).
    반환: {"enabled", "ok", "error", "new_items": [{"db_type", "name", "added"}],
           "stats_summary", "status_change": None|"down"|"recovered"}
    """
    report = {"enabled": BOOKOASIS_ENABLED, "ok": False, "error": None, "new_items": [],
              "stats_summary": "", "status_change": None}
    if not BOOKOASIS_ENABLED:
        return report

    state = _load_state()
    now = datetime.now().isoformat(timespec="seconds")
    was_ok = state.get("last_ok")  # None이면 첫 실행

    try:
        data = _fetch()
    except Exception as e:  # noqa: BLE001 - 연결 실패/타임아웃/형식 오류 전부 "서고 전산 먹통"으로 처리
        cause = _root_cause(e)
        report["error"] = (f"{type(cause).__name__}: {cause}" if str(cause) else type(cause).__name__)[:200]
        if was_ok is True:
            report["status_change"] = "down"
        state.update({"last_ok": False, "last_checked": now, "last_error": report["error"]})
        _save_state(state)
        print(f"[북오아시스] 서고 점검 실패: {report['error']}")
        return report

    report["ok"] = True
    if was_ok is False:
        report["status_change"] = "recovered"

    seen = state.get("last_seen_added", {})
    first_run = not seen
    new_items = []
    for db_type, series in data["series_by_type"].items():
        last_seen = str(seen.get(db_type, ""))
        newest = last_seen
        for item in series:
            added = str(item.get("latest_added", ""))
            if not added:
                continue
            if added > newest:
                newest = added
            if first_run or added <= last_seen:
                continue
            name = item.get("display_name") or item.get("series_name") or "제목 없음"
            new_items.append({"db_type": db_type, "name": name, "added": added})
        seen[db_type] = newest

    report["new_items"] = new_items
    report["stats_summary"] = _stats_summary(data["stats"])

    recent = (new_items + state.get("recent_arrivals", []))[:MAX_RECENT_KEEP]
    state.update({
        "last_ok": True,
        "last_checked": now,
        "last_error": None,
        "last_seen_added": seen,
        "recent_arrivals": recent,
        "stats_summary": report["stats_summary"],
    })
    _save_state(state)
    if first_run:
        print("[북오아시스] 첫 점검 - 신간 기준점만 저장했습니다.")
    else:
        print(f"[북오아시스] 서고 점검 완료 - 신간 {len(new_items)}건")
    return report


def build_prompt_block(report: dict) -> str:
    """
    이야기 프롬프트에 넣을 "서고 현황" 블록. 이야기할 거리(신간 입고, 장애 발생/복구)가
    있을 때만 내용을 만들고, 없으면 빈 문자열 - 매시간 서고 얘기가 반복되지 않게.
    """
    if not report.get("enabled"):
        return ""

    if report.get("status_change") == "down":
        return (
            "\n[애순이 담당 업무 - 사내 자료실 '북오아시스']\n"
            "- 방금 서고 전산(북오아시스 서버)에 접속이 안 된다. 애순이가 담당자로서 이 소식을 "
            "투덜대거나 걱정하며 짧게 언급해라. 원인은 모르니 지어내지 마라.\n"
        )

    lines = []
    if report.get("status_change") == "recovered":
        lines.append("- 먹통이던 서고 전산이 다시 접속된다. 담당자로서 안도하는 한마디를 곁들여라.")

    new_items = report.get("new_items") or []
    if new_items:
        by_type: dict[str, list[str]] = {}
        for item in new_items:
            by_type.setdefault(item["db_type"], []).append(item["name"])
        summary = ", ".join(f"{DB_TYPE_LABELS.get(t, t)} {len(names)}건" for t, names in by_type.items())
        titles = ", ".join(f"'{item['name']}'" for item in new_items[:MAX_NEW_IN_PROMPT])
        more = f" 외 {len(new_items) - MAX_NEW_IN_PROMPT}건" if len(new_items) > MAX_NEW_IN_PROMPT else ""
        lines.append(f"- 지난 점검 이후 새로 입고된 자료: {summary} (예: {titles}{more})")
        if report.get("stats_summary"):
            lines.append(f"- 참고용 장서 통계(원본 수치): {report['stats_summary']}")

    if not lines:
        return ""

    return (
        "\n[애순이 담당 업무 - 사내 자료실 '북오아시스']\n"
        + "\n".join(lines)
        + "\n- 애순이는 생산부 대리이면서 사내 자료실 관리도 겸직한다. 위 서고 소식을 담당자로서 "
        "1~2문장 정도 자연스럽게 섞어라(귀찮아하거나, 읽어보고 싶어하거나). 제목과 숫자는 주어진 "
        "그대로만 쓰고 없는 책 제목이나 수치를 지어내지 마라.\n"
    )
