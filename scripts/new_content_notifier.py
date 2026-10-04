"""
Plex/BookOasis에 새 콘텐츠가 등록되면 디스코드로 알림을 보내는 크론 스크립트.

discord_bot_v2 본체(app.py)와는 완전히 별개의 프로세스로, 1시간마다 크론으로 실행된다.
Plex의 get_recently_added, BookOasis의 search_books(sort="date_desc")를
discord_bot_v2가 쓰는 것과 동일한 mcp_servers.yaml로 MCP 서버에 직접 연결해서 조회하고,
마지막으로 알림을 보낸 시점 이후의 "새 항목"만 골라 디스코드 채널로 전송한다.
상태(마지막으로 알림 보낸 시점)는 JSON 파일 하나에 저장해서 실행 간 유지한다.

디스코드 전송은 봇 토큰으로 REST API를 직접 호출한다(discord.py 클라이언트를 새로
띄우지 않음 - 이미 떠있는 본체 봇과 별개로 가볍게 메시지 하나만 보내면 되는 용도라
전체 게이트웨이 연결은 불필요).
"""
import asyncio
import json
import os
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

BASE_DIR = "/mnt/discord_bot_v2"
load_dotenv(os.path.join(BASE_DIR, ".env"))

MCP_CONFIG_PATH = os.path.join(BASE_DIR, "config", "mcp_servers.yaml")
STATE_FILE = Path(BASE_DIR) / "storage" / "new_content_notifier_state.json"
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
NOTIFY_CHANNEL_ID = os.environ.get("NEW_CONTENT_NOTIFY_CHANNEL_ID", "591180628842774554")

MAX_NOTIFY_LINES = 20  # 디스코드 2000자 제한 고려해서 한 번에 보여줄 항목 상한


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_tool_result(raw) -> dict:
    """MCP tool 결과는 보통 JSON 문자열로 온다 - dict가 아니면 파싱, 실패하면 빈 dict."""
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return {}


async def _get_server_tools(server_name: str) -> dict:
    with open(MCP_CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if server_name not in cfg:
        return {}
    client = MultiServerMCPClient({server_name: cfg[server_name]})
    tools = await client.get_tools()
    return {t.name: t for t in tools}


async def check_plex(state: dict) -> list[str]:
    tools = await _get_server_tools("plex")
    tool = tools.get("get_recently_added")
    if tool is None:
        print("[경고] plex의 get_recently_added 도구를 못 찾음")
        return []

    raw = await tool.ainvoke({"limit": 20})
    data = _parse_tool_result(raw)
    items = data.get("recentlyAdded", []) if isinstance(data, dict) else []

    last_seen = int(state.get("plex_last_added_at", 0) or 0)
    newest = last_seen
    new_lines = []
    for item in items:
        try:
            added_at = int(item.get("addedAt", 0) or 0)
        except (TypeError, ValueError):
            continue
        if added_at <= last_seen:
            continue
        title = item.get("grandparentTitle") or item.get("title") or "제목 없음"
        parent = item.get("parentTitle")
        label = f"{title} - {parent}" if parent and parent != title else title
        new_lines.append(f"🎬 [Plex] {label}")
        newest = max(newest, added_at)

    state["plex_last_added_at"] = newest
    return new_lines


async def check_bookoasis(state: dict) -> list[str]:
    tools = await _get_server_tools("bookoasis")
    tool = tools.get("search_books")
    if tool is None:
        print("[경고] bookoasis의 search_books 도구를 못 찾음")
        return []

    new_lines = []
    # video는 search_books가 다루지 않으니 book 계열 3종만 확인한다.
    for db_type in ("general", "adult", "audiobook"):
        raw = await tool.ainvoke({"db_type": db_type, "sort": "date_desc", "limit": 10})
        data = _parse_tool_result(raw)
        series = data.get("series", []) if isinstance(data, dict) else []

        state_key = f"bookoasis_last_added_{db_type}"
        last_seen = str(state.get(state_key, ""))
        newest = last_seen
        for item in series:
            added_at = str(item.get("latest_added", ""))
            if not added_at or added_at <= last_seen:
                continue
            name = item.get("display_name") or item.get("series_name") or "제목 없음"
            new_lines.append(f"📚 [BookOasis:{db_type}] {name}")
            if added_at > newest:
                newest = added_at
        state[state_key] = newest

    return new_lines


async def send_discord_message(text: str) -> None:
    url = f"https://discord.com/api/v10/channels/{NOTIFY_CHANNEL_ID}/messages"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, headers=headers, json={"content": text})
        resp.raise_for_status()


async def main():
    state = load_state()
    new_lines: list[str] = []

    try:
        new_lines.extend(await check_plex(state))
    except Exception as e:
        print(f"[경고] Plex 확인 실패: {e}")

    try:
        new_lines.extend(await check_bookoasis(state))
    except Exception as e:
        print(f"[경고] BookOasis 확인 실패: {e}")

    save_state(state)  # 일부만 성공했어도 그만큼은 반영해서 다음 실행 때 중복 알림 방지

    if not new_lines:
        print("새 콘텐츠 없음")
        return

    shown = new_lines[:MAX_NOTIFY_LINES]
    text = "📢 **새로 등록된 콘텐츠**\n" + "\n".join(shown)
    if len(new_lines) > MAX_NOTIFY_LINES:
        text += f"\n... 외 {len(new_lines) - MAX_NOTIFY_LINES}건 더"

    await send_discord_message(text)
    print(f"알림 전송 완료: {len(new_lines)}건")


if __name__ == "__main__":
    asyncio.run(main())
