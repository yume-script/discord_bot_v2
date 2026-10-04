"""
Redroid(안드로이드 컨테이너) 패키지 감시 결과를 아메하나가 대화체로 알려준다.

역할 분담:
- 감시/자동 삭제: cron으로 15분마다 도는 scripts/redroid_check_packages.sh가 한다.
  봇이 재시작되거나 죽어도 감시는 멈추지 않고, 봇은 redroid에 접속할 권한이 없다.
- 알림: 스크립트가 변동이 있을 때 REDROID_EVENTS_PATH(jsonl)에 결과를 한 줄씩 추가하면,
  이 cog가 1분마다 새 줄을 읽어서 LLM이 쓴 아메하나 말투 메시지로 채널에 올린다.

보안 알림이라 LLM이 사실을 바꿔 말하지 않게 두 가지 안전장치를 둔다:
1) LLM 문장에 패키지 이름이 하나라도 빠지면 정해진 문장 틀(_fallback_text)로 대신 보낸다.
2) 메시지 끝에 실제 결과를 작은 글씨(-#) 한 줄로 항상 붙인다.

어디까지 알렸는지는 REDROID_WATCH_STATE_PATH에 바이트 오프셋으로 저장한다 - 봇이 꺼져 있던
동안 쌓인 결과는 다시 켜졌을 때 순서대로 올린다. 상태 파일이 없을 때(처음 켤 때)는 기존
내용을 건너뛰고 그 뒤에 추가되는 것부터 알린다(과거 기록이 한꺼번에 쏟아지지 않게).
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from discord.ext import commands, tasks
from langchain_core.messages import HumanMessage, SystemMessage

from ai.llm_client import build_chat_model, message_text
from config import settings

log = logging.getLogger("redroid_watch")

POLL_INTERVAL_SEC = 60
LLM_TIMEOUT_SEC = 60
MAX_EVENTS_PER_POLL = 10  # 오래 꺼져 있다 켜졌을 때 한 번에 너무 많이 올리지 않게

_SYSTEM_PROMPT = (
    "너는 디스코드 봇 아메하나야. 서버 관리 도우미로서, 방금 Redroid(안드로이드 컨테이너)를 "
    "점검한 결과를 채널 사람들에게 직접 처리한 것처럼 자연스럽게 알려줘."
)

_USER_PROMPT = """\
아래는 방금 Redroid 패키지 점검 결과야 (JSON). 이걸 바탕으로 디스코드 채널에 올릴 알림을 써줘.

규칙:
- 2~4문장, 아메하나다운 친근한 말투. 인사말이나 서론 없이 바로 본론.
- 결과에 나온 패키지 이름은 하나도 빠짐없이 전부 `백틱`으로 감싸서 그대로 적어.
- auto_removed의 result가 "Success"가 아니면 삭제에 실패한 거야. 실패는 실패라고 솔직하게 말하고,
  성공한 척하지 마.
- added는 새로 설치된 앱, removed는 사라진 앱, auto_removed는 허용 목록(카카오톡, Uptodown)에
  없어서 자동으로 지운 앱이야. 결과에 없는 내용은 지어내지 마.

[점검 결과]
{event_json}
"""


def _load_offset() -> int | None:
    path = settings.REDROID_WATCH_STATE_PATH
    if not path.exists():
        return None
    try:
        return int(json.loads(path.read_text(encoding="utf-8")).get("offset", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _save_offset(offset: int) -> None:
    path = settings.REDROID_WATCH_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def _read_new_events(events_path: Path, offset: int) -> list[tuple[dict, int]]:
    """
    offset 이후의 "완성된" 줄만 읽어서 [(이벤트, 그 줄 다음 오프셋), ...]으로 돌려준다.
    스크립트가 아직 쓰는 중인 마지막 줄(줄바꿈 없음)은 다음 번에 읽는다.
    """
    with events_path.open("rb") as f:
        f.seek(offset)
        data = f.read()
    events: list[tuple[dict, int]] = []
    pos = offset
    for raw in data.splitlines(keepends=True):
        if not raw.endswith(b"\n"):
            break  # 쓰는 중인 줄
        pos += len(raw)
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("redroid 이벤트 줄 파싱 실패 - 건너뜀: %r", line[:200])
            continue
        if isinstance(event, dict):
            events.append((event, pos))
    return events


def _package_names(event: dict) -> list[str]:
    names = list(event.get("added") or []) + list(event.get("removed") or [])
    names += [item.get("pkg", "") for item in event.get("auto_removed") or [] if isinstance(item, dict)]
    return [n for n in dict.fromkeys(names) if n]


def _facts_line(event: dict) -> str:
    """메시지 끝에 항상 붙이는 실제 결과 한 줄 (디스코드 작은 글씨 -#)."""
    parts = []
    if event.get("added"):
        parts.append("신규 설치: " + ", ".join(event["added"]))
    if event.get("removed"):
        parts.append("삭제 감지: " + ", ".join(event["removed"]))
    removed_auto = [
        f"{item.get('pkg')}({item.get('result') or '결과 없음'})"
        for item in event.get("auto_removed") or []
        if isinstance(item, dict)
    ]
    if removed_auto:
        parts.append("자동 삭제: " + ", ".join(removed_auto))
    when = event.get("time", "")
    return f"-# 🔎 {when} 점검 결과 · " + " · ".join(parts) if parts else ""


def _fallback_text(event: dict) -> str:
    """LLM이 실패하거나 패키지 이름을 빠뜨렸을 때 쓰는 정해진 문장 틀."""
    lines = ["🧹 Redroid 점검 결과를 알려드려요."]
    if event.get("added"):
        lines.append("새로 설치된 앱: " + ", ".join(f"`{p}`" for p in event["added"]))
    if event.get("removed"):
        lines.append("사라진 앱: " + ", ".join(f"`{p}`" for p in event["removed"]))
    for item in event.get("auto_removed") or []:
        if not isinstance(item, dict):
            continue
        result = item.get("result") or "결과 없음"
        status = "지웠어요" if "Success" in result else f"지우려 했는데 실패했어요 ({result})"
        lines.append(f"허용 목록에 없는 `{item.get('pkg')}` - {status}")
    return "\n".join(lines)


async def _compose_message(event: dict) -> str:
    names = _package_names(event)
    text = ""
    try:
        model = build_chat_model()
        resp = await asyncio.wait_for(
            model.ainvoke([
                SystemMessage(content=_SYSTEM_PROMPT),
                HumanMessage(content=_USER_PROMPT.format(event_json=json.dumps(event, ensure_ascii=False))),
            ]),
            timeout=LLM_TIMEOUT_SEC,
        )
        text = message_text(resp).strip()
    except Exception:
        log.exception("redroid 알림 문장 생성 실패 - 정해진 문장 틀로 대신 보낸다")

    missing = [n for n in names if n not in text]
    if not text or missing:
        if text:
            log.warning("LLM 알림에 패키지 이름이 빠짐(%s) - 정해진 문장 틀로 대신 보낸다", missing)
        text = _fallback_text(event)

    facts = _facts_line(event)
    message = f"{text}\n{facts}" if facts else text
    return message[:1990]


class RedroidWatch(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._offset: int | None = None
        if settings.REDROID_NOTIFY_CHANNEL_ID:
            self.poll.start()
        else:
            log.info("REDROID_NOTIFY_CHANNEL_ID가 비어 있어서 redroid 알림을 끈다.")

    def cog_unload(self) -> None:
        self.poll.cancel()

    async def _get_channel(self):
        channel_id = settings.REDROID_NOTIFY_CHANNEL_ID
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        return channel

    @tasks.loop(seconds=POLL_INTERVAL_SEC)
    async def poll(self) -> None:
        # 예외가 루프 밖으로 나가면 discord.ext.tasks 루프가 멈춰버린다 - 여기서 잡고 로그만
        # 남긴 뒤 다음 회차(1분 뒤)에 같은 지점부터 다시 시도한다.
        try:
            await self._poll_once()
        except Exception:
            log.exception("redroid 알림 처리 중 오류 - 다음 회차에 다시 시도")

    async def _poll_once(self) -> None:
        events_path = settings.REDROID_EVENTS_PATH
        if not events_path.exists():
            return

        size = events_path.stat().st_size
        if self._offset is None:
            self._offset = _load_offset()
            if self._offset is None:
                # 처음 켤 때는 기존 기록을 건너뛴다 (과거 알림이 한꺼번에 올라가지 않게)
                self._offset = size
                _save_offset(self._offset)
                log.info("redroid 알림 시작 - 기존 기록 %d바이트는 건너뜀", size)
                return
        if size < self._offset:
            log.info("redroid 이벤트 파일이 줄어듦(교체/정리됨) - 처음부터 다시 읽는다")
            self._offset = 0

        events = await asyncio.to_thread(_read_new_events, events_path, self._offset)
        if not events:
            return

        channel = await self._get_channel()
        for event, next_offset in events[:MAX_EVENTS_PER_POLL]:
            text = await _compose_message(event)
            # 전송에 실패하면 예외로 이번 회차가 끝나고, 오프셋을 저장하지 않았으니
            # 다음 회차에 같은 이벤트를 다시 보낸다.
            await channel.send(text)
            self._offset = next_offset
            _save_offset(self._offset)

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(RedroidWatch(bot))
