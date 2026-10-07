"""
포링푸드(애순이와 동료들) 조회 도구 - 아메하나/애순이(ai/rag_engine.py)가 대화 중에 쓴다.

[변경] 원래는 별도 프로젝트였던 포링푸드가 이 파일을 MCP 서버(stdio)로 띄우고, 봇이
config/mcp_servers.yaml의 poring_food 항목으로 하위 프로세스를 실행해서 붙었다. 봇으로
합치면서 MCP 서버 부분은 없애고, 같은 함수들을 LangChain 도구(PORING_FOOD_TOOLS)로 바로
노출한다. 도구 이름/설명/인자는 그대로라 프롬프트와 LLM 동작은 바뀌지 않는다.

- get_current_status / get_recent_history / get_character_list / get_all_characters_status:
  storage/poring_food의 상태 파일만 읽는다.
- get_character_story: 방송 순서가 안 돌아온 인물의 이야기를 즉석 생성(LLM 호출) - 같은
  시간대 안에서는 캐시를 재사용한다.
- get_bookoasis_report: 애순이 겸직(사내 자료실 북오아시스)의 마지막 점검 결과.

함수들은 동기(파일 읽기, requests 기반 LLM 호출)라서 LangChain 도구로 감쌀 때 스레드에서
실행되게 한다(봇 이벤트 루프를 막지 않게).
"""
import asyncio
import glob
import inspect
import json
import os
from datetime import datetime, timedelta

from langchain_core.tools import StructuredTool

from .config import STATUS_OUT_PATH, HISTORY_LOG_PATH, CHARACTERS_STATE_PATH, ORGANIZATION_GLOB, BOOKOASIS_STATE_PATH
from .bookoasis import DB_TYPE_LABELS
from . import characters
from . import processor
from . import generator


# [신규] 온디맨드 이야기 캐시 - 같은 시간대(시 단위) 안에서 같은 인물을 여러 번 물어봐도
# LLM을 다시 호출하지 않는다. 봇 프로세스 안에 있으니 캐시가 봇 수명 동안 유지된다.
_story_cache: dict[tuple[str, str], str] = {}


def _load_characters_state() -> dict:
    if not os.path.exists(CHARACTERS_STATE_PATH):
        return {}
    try:
        with open(CHARACTERS_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _find_state_by_name(name: str) -> list[tuple[str, dict]]:
    """이름으로 상태를 찾는다. 이름이 겹치는 인물(예: 미믹)이 있으면 여러 개가 나올 수 있다."""
    states = _load_characters_state()
    return [(cid, info) for cid, info in states.items() if info.get("name") == name]


def get_current_status(character: str = "애순이") -> str:
    """
    한 인물의 현재 상태와 하고 있는 활동을 알려준다. character를 생략하면 애순이를 조회한다.
    애순이는 매시 LLM이 작성한 상세한 근황(기분/이야기 포함)까지 나오고, 다른 인물은
    위치/활동/소속 정도의 간단한 정보만 나온다. 누가 있는지 모르면 get_character_list를 먼저 써라.
    """
    if character == "애순이":
        if not os.path.exists(STATUS_OUT_PATH):
            return "아직 상태 정보가 없어요 (첫 실행 전이거나 파일이 없음)."
        try:
            with open(STATUS_OUT_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            return f"상태 파일을 읽는 중 오류가 발생했어요: {e}"

        parts = []
        if data.get("time_tag"):
            parts.append(f"[{data['time_tag']}]")
        if data.get("location") and data.get("activity"):
            parts.append(f"애순이는 지금 {data['location']}에서 {data['activity']} 중이에요.")
        elif data.get("activity"):
            parts.append(f"애순이는 지금 {data['activity']} 중이에요.")
        if data.get("state"):
            parts.append(f"(상태: {data['state']})")
        if data.get("mood"):
            parts.append(f"오늘 기분: {data['mood']}.")
        narrative = data.get("narrative") or data.get("full_report")
        if narrative:
            parts.append(str(narrative))
        return " ".join(parts) if parts else "상태 데이터는 있는데 읽을 수 있는 내용이 없어요."

    matches = _find_state_by_name(character)
    if not matches:
        return f"'{character}'라는 인물을 못 찾았어요. get_character_list로 누가 있는지 확인해보세요."

    if len(matches) > 1:
        lines = [f"'{character}'라는 이름이 여러 회사에 있어요:"]
        for cid, info in matches:
            lines.append(f"- {info.get('company')}: {info.get('location')}에서 {info.get('activity')} 중")
        return "\n".join(lines)

    _, info = matches[0]
    parts = [f"{character}({info.get('company')} {info.get('dept')})는 지금"]
    if info.get("location") and info.get("activity"):
        parts.append(f"{info['location']}에서 {info['activity']} 중이에요.")
    elif info.get("activity"):
        parts.append(f"{info['activity']} 중이에요.")
    if info.get("state"):
        parts.append(f"(상태: {info['state']})")
    narrative = info.get("narrative")
    if narrative:
        parts.append(str(narrative))
    return " ".join(parts)


def get_character_list() -> str:
    """포링푸드와 라이벌 회사에 등장하는 인물 전체 목록(이름/회사/부서/직급)을 알려준다."""
    entries = []
    for path in sorted(glob.glob(ORGANIZATION_GLOB)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        company = data.get("company_name", os.path.basename(path))
        for dept in data.get("departments", []):
            for m in dept.get("members", []):
                entries.append(f"- {m['name']} ({company} {dept.get('dept_name', '')} {m.get('rank', '')})")
    if not entries:
        return "조직도를 못 읽었어요."
    return "\n".join(entries)


def get_recent_history(character: str = "애순이", days: int = 1) -> str:
    """
    한 인물이 최근에 뭘 했는지 알려준다. character를 생략하면 애순이 기록을 본다.
    "어제 뭐 했어?"는 days=1, "요즘/최근 며칠 뭐 했어?"는 days=3~7 정도로 호출하면 된다.
    하루에 여러 번(시간대별) 기록이 쌓이기 때문에, 하루당 대표로 3개까지만 골라서
    너무 길어지지 않게 요약한다.
    """
    if not os.path.exists(HISTORY_LOG_PATH):
        return "아직 히스토리 기록이 없어요."

    try:
        entries = []
        with open(HISTORY_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # character 필드가 없는(옛날 형식) 줄은 애순이 기록으로 간주 - 하위 호환.
                if e.get("character", "애순이") == character:
                    entries.append(e)
    except OSError as e:
        return f"히스토리 파일을 읽는 중 오류가 발생했어요: {e}"

    if not entries:
        return f"'{character}'의 히스토리 기록이 없어요."

    cutoff = datetime.now() - timedelta(days=days)
    by_date: dict[str, list[dict]] = {}
    for e in entries:
        ts = e.get("timestamp")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if dt < cutoff:
            continue
        date_key = dt.strftime("%Y-%m-%d")
        by_date.setdefault(date_key, []).append(e)

    if not by_date:
        return f"최근 {days}일 동안의 기록이 없어요."

    lines = []
    for date_key in sorted(by_date.keys()):
        day_entries = by_date[date_key]
        # 하루에 너무 많이 쌓이니 대표로 최대 3개만 (앞/중간/끝)
        picks = day_entries if len(day_entries) <= 3 else [
            day_entries[0], day_entries[len(day_entries) // 2], day_entries[-1]
        ]
        day_lines = []
        for e in picks:
            tag = e.get("time_tag", "")
            act = e.get("activity", "")
            loc = e.get("location", "")
            piece = f"{tag} {loc}에서 {act}" if loc else f"{tag} {act}"
            if e.get("narrative"):
                piece += f" ({e['narrative']})"
            if act:
                day_lines.append(piece)
        if day_lines:
            lines.append(f"[{date_key}] " + " / ".join(day_lines))

    return "\n".join(lines) if lines else f"최근 {days}일 동안의 기록이 없어요."


def get_all_characters_status() -> str:
    """
    포링푸드와 라이벌 회사 전원(애순이 포함)이 지금 각자 어디서 뭘 하고 있는지 한 번에
    보여준다. "포링푸드 사람들 지금 뭐하고 있지?", "다들 뭐해?" 같은 전체 근황 질문에 쓴다.
    회사별로 묶어서 보여준다.
    """
    states = _load_characters_state()
    if not states:
        return "아직 상태 정보가 없어요 (첫 실행 전이거나 파일이 없음)."

    by_company: dict[str, list[str]] = {}
    for info in states.values():
        name = info.get("name", "?")
        location = info.get("location", "")
        activity = info.get("activity", "")
        company = info.get("company", "기타")
        if location and activity:
            line = f"{name}: {location}에서 {activity}"
        elif activity:
            line = f"{name}: {activity}"
        else:
            line = f"{name}: 상태 정보 없음"
        by_company.setdefault(company, []).append(line)

    blocks = []
    for company in sorted(by_company.keys()):
        lines = "\n".join(f"- {l}" for l in by_company[company])
        blocks.append(f"【{company}】\n{lines}")
    return "\n\n".join(blocks)


def get_character_story(character: str) -> str:
    """
    [신규] 스포트라이트(매시 1명 방송) 순서가 안 돌아온 인물이 지금 뭘 하고 있는지, 그 사람
    시점의 짧은 이야기를 그 자리에서 즉석으로 만들어 알려준다. "오크히어로 오늘 뭐해?"처럼
    누군가의 근황이 궁금할 때 쓴다. 애순이는 get_current_status("애순이")가 이미 상세하게
    답하니 이 도구는 애순이 외의 인물에 쓴다. 같은 시간대 안에서는 캐시된 결과를 재사용해서
    똑같은 사람을 여러 번 물어봐도 LLM을 다시 호출하지 않는다.
    """
    if character == "애순이":
        return get_current_status("애순이")

    cache_key = (character, datetime.now().strftime("%Y-%m-%d-%H"))
    if cache_key in _story_cache:
        return _story_cache[cache_key]

    matches = _find_state_by_name(character)
    if not matches:
        result = f"'{character}'라는 인물을 못 찾았어요. get_character_list로 누가 있는지 확인해보세요."
        _story_cache[cache_key] = result
        return result
    if len(matches) > 1:
        return f"'{character}'라는 이름이 여러 회사에 있어요 - 어느 회사인지 알려주시면 좁혀드릴게요."

    _, info = matches[0]
    if info.get("state") == "자는 중":
        result = f"{character}는 지금 자고 있어서 이야기를 만들 수가 없어요. 나중에 다시 물어봐주세요."
        _story_cache[cache_key] = result
        return result

    persona = None
    for c in characters.load_roster():
        if c["name"] == character and c["company"] == info.get("company"):
            persona = c
            break
    if not persona:
        result = f"'{character}'의 페르소나 정보를 조직도에서 못 찾았어요."
        _story_cache[cache_key] = result
        return result

    issue = processor.get_last_issue() or {"title": "평범한 하루", "description": "특별한 일 없는 하루"}
    mood = processor.get_daily_mood()

    try:
        report = generator.generate_generic_character_report(
            persona, issue, processor.get_time_tag(), "정보 없음", mood,
            info.get("location", ""), info.get("activity", ""), info.get("state", ""),
        )
        result = report.get("narrative", "") or "이야기를 만들었는데 내용이 비어있어요."
    except Exception as e:
        result = f"{character}의 이야기를 만드는 중 오류가 발생했어요: {e}"

    _story_cache[cache_key] = result
    return result


def get_bookoasis_report() -> str:
    """
    애순이가 겸직으로 관리하는 사내 자료실 '북오아시스'(도서/오디오북 서고)의 최근 점검 결과를
    알려준다 - 마지막 점검 시각, 서고 전산 연결 상태, 최근 새로 입고된 자료 목록. "애순아 서고
    요즘 어때?", "최근에 들어온 책 있어?"처럼 애순이 담당 업무로서의 서고 소식을 물을 때 쓴다.
    (애순이가 방송 주인공인 시간에 점검하므로, 점검 시각이 몇 시간 전일 수 있다)
    """
    if not os.path.exists(BOOKOASIS_STATE_PATH):
        return "아직 서고 점검 기록이 없어요 (애순이가 아직 점검하지 않았거나 기능이 꺼져 있음)."
    try:
        with open(BOOKOASIS_STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return f"서고 점검 기록을 읽는 중 오류가 발생했어요: {e}"

    lines = [f"마지막 점검: {data.get('last_checked', '알 수 없음')}"]
    if data.get("last_ok"):
        lines.append("서고 전산 연결: 정상")
    else:
        lines.append(f"서고 전산 연결: 실패 ({data.get('last_error') or '원인 불명'})")
    recent = data.get("recent_arrivals") or []
    if recent:
        lines.append("최근 입고된 자료:")
        for item in recent[:10]:
            label = DB_TYPE_LABELS.get(item.get("db_type"), item.get("db_type"))
            lines.append(f"- [{label}] {item.get('name')} ({item.get('added')})")
    else:
        lines.append("최근 새로 입고된 자료는 없어요.")
    if data.get("stats_summary"):
        lines.append(f"장서 통계: {data['stats_summary']}")
    return "\n".join(lines)


def _as_tool(fn) -> StructuredTool:
    """동기 함수를 LangChain 도구로. 실행은 스레드에서 해서 봇 이벤트 루프를 막지 않는다."""
    async def _arun(**kwargs):
        return await asyncio.to_thread(fn, **kwargs)

    return StructuredTool.from_function(
        func=fn, coroutine=_arun, name=fn.__name__, description=inspect.cleandoc(fn.__doc__ or ""),
    )


PORING_FOOD_TOOLS = [
    _as_tool(fn)
    for fn in (
        get_current_status,
        get_character_list,
        get_recent_history,
        get_all_characters_status,
        get_character_story,
        get_bookoasis_report,
    )
]
