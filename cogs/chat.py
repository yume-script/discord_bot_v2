"""
디스코드 메시지 처리. 카톡 메시지도 브릿지가 "{발신자명}//{방ID}//{유저ID}" 닉네임으로
이 채널들(KATALK_LINKED_CHANNEL_IDS)에 올려주는 방식이라 같은 on_message로 함께 처리한다
(기존 app.py의 on_message 파이프라인을 그대로 이식).

카톡/디스코드가 core.autonomous_reply의 전역 상태(쿨다운/활성채널)를 공유하는 것도 기존과 동일.
"""
from __future__ import annotations

import io
import logging

import discord
from discord import Message
from discord.ext import commands

from ai.backends.base import ImageGenError
from ai.image_engine import generate_image
from ai.local_tools import get_exchange_rate, get_nationwide_weather, get_stock_price, get_weather
from ai.rag_engine import a_query
from config import settings
from core import ant_voice, autonomous_reply, fortune, game_engine, katalk_stats, mbti, satellite
from core.admin_auth import is_admin
from core.concurrency import image_gen_pool
from core.conversation_store import log_message
from core.kakao_feed import build_feed_reply, is_feed_message as _is_feed_message
from core.kakao_relay import parse_kakao_author
from core.katalk_bridge import send_image, send_message
from core.money_system import money_system
from core.nickname_watch import check_and_update_nickname
from core.user_ref import UserRef

log = logging.getLogger("chat")

GREETING_REPLY = "네! 저 여기 있어요. 궁금한 거 있으면 편하게 물어봐 주세요 :)"
FAILURE_REPLY = "어라, 지금 대답을 못 만들었어요. 잠시 후 다시 불러주세요."

# 디스코드 슬래시 명령어(app_commands)는 자동완성 목록을 거쳐야 파라미터가 채워지는 인터랙션
# 방식이라, "/그림 프롬프트"를 한 번에 빠르게 쳐서 보내면 인식이 안 될 수 있다. 예전 봇처럼
# 텍스트로 친 "/그림 ...", "/그림스타일 ... | 스타일" 도 그대로 인식해서 처리하는 경로를 따로 둔다
# (진짜 슬래시 명령어는 cogs/image_gen.py가 계속 별도로 처리 - 둘 다 지원).
TEXT_IMAGE_PREFIX = "/그림 "
TEXT_IMAGE_STYLE_PREFIX = "/그림스타일 "


class Chat(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: Message):
        # 기존 봇과 동일하게 "봇 자기 자신"만 거른다. 카톡 릴레이 메시지는 브릿지 계정(봇 또는
        # 웹훅)이 올리는 경우가 많아서, author.bot 전체를 걸러내면 카톡 메시지가 통째로 씹힌다.
        if message.author == self.bot.user:
            return

        content = message.content
        is_linked_channel = str(message.channel.id) in settings.KATALK_LINKED_CHANNEL_IDS

        kakao_user = parse_kakao_author(message.author.name) if is_linked_channel else None
        is_kakao = kakao_user is not None
        user = kakao_user or UserRef.from_discord(message.author.id, message.author.display_name)

        key = f"discord:{message.channel.id}"

        is_command = content.startswith("/")
        is_feed_message = _is_feed_message(content)

        # 대화 로그를 남길지, 남긴다면 어떤 conversation_key로 묶을지.
        # 카톡은 방(room_id) 단위로, 순수 디스코드는 채널 단위로 맥락을 분리한다.
        is_discord_log_channel = str(message.channel.id) in settings.DISCORD_LOG_CHANNEL_IDS
        should_log = (is_linked_channel and is_kakao) or is_discord_log_channel
        conversation_key = (user.raw_id.split("//", 1)[0] if is_kakao else key)

        # 1. 입장/퇴장 피드 메시지 - 원본과 동일하게 여기서 바로 응답하고 끝낸다
        # (호출어/자율응답 로직으로 안 내려가고, 로그에도 원문 JSON을 안 남긴다).
        if is_linked_channel and is_kakao and is_feed_message:
            feed_reply = build_feed_reply(content)
            if feed_reply is not None:
                await message.channel.send(feed_reply)
                await send_message(user.raw_id.split("//", 1)[0], feed_reply)
            return

        # 1-1. "/그림 프롬프트" / "/그림스타일 프롬프트 | 스타일" 텍스트 명령 - 슬래시 명령어
        # 자동완성 UI를 거치지 않고 빠르게 타이핑해서 보내도 바로 처리된다.
        if content.startswith(TEXT_IMAGE_STYLE_PREFIX):
            await self._handle_text_image_command(message, user, content[len(TEXT_IMAGE_STYLE_PREFIX):], with_style=True)
            return
        if content.startswith(TEXT_IMAGE_PREFIX):
            await self._handle_text_image_command(message, user, content[len(TEXT_IMAGE_PREFIX):], with_style=False)
            return
        if content.strip() in ("/그림", "/그림스타일"):
            await self._send_game_result(message, user, "사용법: `/그림 프롬프트` 또는 `/그림스타일 프롬프트 | 스타일명`")
            return

        # 1-2. "/날씨", "/전국날씨", "/환율", "/주식" 텍스트 명령 - 슬래시 명령어(cogs/lookup.py)와
        # 같은 ai/local_tools.py 함수를 호출한다.
        if content.startswith("/전국날씨"):
            await self._handle_lookup(message, user, get_nationwide_weather, {})
            return
        if content.startswith("/날씨"):
            location = content[len("/날씨"):].strip() or "서울"
            await self._handle_lookup(message, user, get_weather, {"location": location})
            return
        if content.startswith("/환율"):
            currency = content[len("/환율"):].strip() or "USD"
            await self._handle_lookup(message, user, get_exchange_rate, {"currency": currency})
            return
        if content.startswith("/주식"):
            ticker = content[len("/주식"):].strip()
            if not ticker:
                await self._send_game_result(message, user, "사용법: `/주식 005930.KS` (코스피), `/주식 AAPL` (미국주식)")
                return
            await self._handle_lookup(message, user, get_stock_price, {"ticker": ticker})
            return

        # 1-3. 미니게임 7종 텍스트 명령 - "/가위 100", "/용호 용 500"처럼 바로 타이핑.
        # 카톡 유저는 슬래시 인터랙션(cogs/games.py)을 못 쓰니까 이 경로가 유일한 진입점이다.
        # prefix가 "/가위 "처럼 공백 포함이라, 인자 없이 "/가위"만 친 경우(공백 없음)는 아래에서
        # 따로 잡아서 사용법을 보여준다 - 안 그러면 조건에 아예 안 걸려서 조용히 무시된다.
        GAME_BARE_COMMANDS = ("/가위", "/바위", "/보", "/주사위", "/슬롯머신", "/다이스포커", "/블랙잭", "/용호", "/바카라")

        if content.startswith("/가위 "):
            await self._handle_game_rps(message, user, "가위", content[len("/가위 "):])
            return
        if content.startswith("/바위 "):
            await self._handle_game_rps(message, user, "바위", content[len("/바위 "):])
            return
        if content.startswith("/보 "):
            await self._handle_game_rps(message, user, "보", content[len("/보 "):])
            return
        if content.startswith("/주사위 "):
            await self._handle_game_bet_only(message, user, game_engine.play_dice, content[len("/주사위 "):])
            return
        if content.startswith("/슬롯머신 "):
            await self._handle_game_bet_only(message, user, game_engine.play_slot, content[len("/슬롯머신 "):])
            return
        if content.startswith("/다이스포커 "):
            await self._handle_game_bet_only(message, user, game_engine.play_dice_poker, content[len("/다이스포커 "):])
            return
        if content.startswith("/블랙잭 "):
            await self._handle_game_bet_only(message, user, game_engine.play_blackjack, content[len("/블랙잭 "):])
            return
        if content.startswith("/용호 "):
            await self._handle_game_choice(message, user, game_engine.play_dragontiger, content[len("/용호 "):], "용/호랑이/무승부")
            return
        if content.startswith("/바카라 "):
            await self._handle_game_choice(message, user, game_engine.play_baccarat, content[len("/바카라 "):], "홀/짝")
            return
        if content.strip() in GAME_BARE_COMMANDS:
            await self._send_game_result(message, user, f"사용법: `{content.strip()} 금액` (용호/바카라는 `{content.strip()} 선택 금액`)")
            return

        # 1-4. "/운세", "/mbti" 텍스트 명령
        if content.startswith("/운세"):
            query = content[len("/운세"):].strip()
            result = await fortune.get_fortune(query)
            await message.reply(result)
            if user.channel.value == "kakao":
                await send_message(user.raw_id.split("//", 1)[0], fortune.to_kakao_text(result))
            return
        if content.startswith("/mbti"):
            if not should_log:
                await message.reply("❌ 이 채널은 대화 기록이 없어서 MBTI 분석을 할 수 없어요.")
                return
            target_name = content[len("/mbti"):].strip() or (user.display_name or "")
            if not target_name:
                await self._send_game_result(message, user, "❌ 분석할 대상 이름을 알 수 없어요.")
                return
            async with message.channel.typing():
                result = await mbti.analyze_mbti(conversation_key, target_name, is_kakao)
            await self._send_game_result(message, user, result)
            return

        # 1-4-1. "/월간카톡", "/카톡순위", "/오늘대화요약" - core/katalk_stats.py 새로 작성
        # (원본 소스를 못 구해서 SQLite 대화 로그로 새로 구현). 대화 기록이 있는 채널에서만 의미가 있다.
        if content.startswith("/월간카톡"):
            if not should_log:
                await self._send_game_result(message, user, "❌ 이 채널은 대화 기록이 없어서 통계를 낼 수 없어요.")
                return
            result = katalk_stats.get_monthly_stats(conversation_key)
            await self._send_game_result(message, user, result)
            return
        if content.startswith("/카톡순위"):
            if not should_log:
                await self._send_game_result(message, user, "❌ 이 채널은 대화 기록이 없어서 순위를 낼 수 없어요.")
                return
            arg = content[len("/카톡순위"):].strip()
            period = "month" if arg in ("이번달", "이번 달", "월") else "today"
            result = katalk_stats.get_ranking(conversation_key, period)
            await self._send_game_result(message, user, result)
            return
        if content.startswith("/오늘대화요약"):
            if not should_log:
                await self._send_game_result(message, user, "❌ 이 채널은 대화 기록이 없어서 요약할 수 없어요.")
                return
            async with message.channel.typing():
                result = await katalk_stats.summarize_today(conversation_key, is_kakao)
            await self._send_game_result(message, user, result)
            return

        # 1-5. "/잔고", "/머니" (조회, 누구나) - 원본 명령어 이름 그대로
        if content.startswith("/잔고") or content.startswith("/머니") and not content.startswith("/머니설정"):
            room_id, user_id = game_engine.room_user(user)
            data = money_system.get_user_data(room_id, user_id)
            result = f"💰 잔액: {data['balance']:,}원"
            if data["debt"]:
                result += f" | 빚: {data['debt']:,}원"
            await self._send_game_result(message, user, result)
            return

        # 1-6. "/머니설정", "/머니설정카톡" (관리자 전용, 카톡에서는 사용 불가 - 관리자 판별 불가)
        if content.startswith("/머니설정카톡"):
            if is_kakao:
                await message.reply("⚠️ 관리자 명령은 카톡에서는 사용할 수 없어요.")
                return
            if not is_admin(message.author.id):
                await message.reply("🚫 관리자만 사용할 수 있는 명령이에요.")
                return
            parts = content[len("/머니설정카톡"):].strip().split()
            if len(parts) != 3:
                await message.reply("사용법: `/머니설정카톡 방ID 유저ID 금액`")
                return
            target_room, target_user, amount_str = parts
            try:
                amount = int(amount_str)
            except ValueError:
                await message.reply("금액은 숫자로 입력해주세요.")
                return
            ok, result = money_system.set_balance(
                target_room, target_user, amount, description=f"관리자({message.author.display_name}) 설정"
            )
            await message.reply(f"✅ 잔액을 {amount:,}원으로 설정했어요." if ok else f"❌ 오류: {result}")
            return
        if content.startswith("/머니설정"):
            if is_kakao:
                await message.reply("⚠️ 관리자 명령은 카톡에서는 사용할 수 없어요.")
                return
            if not is_admin(message.author.id):
                await message.reply("🚫 관리자만 사용할 수 있는 명령이에요.")
                return
            rest = content[len("/머니설정"):].strip()
            target = message.mentions[0] if message.mentions else message.author
            amount_str = rest
            for m in message.mentions:
                amount_str = amount_str.replace(m.mention, "").strip()
            try:
                amount = int(amount_str)
            except ValueError:
                await message.reply("사용법: `/머니설정 @대상 금액` (대상 생략 시 본인)")
                return
            room_id = user_id = str(target.id)
            ok, result = money_system.set_balance(
                room_id, user_id, amount, description=f"관리자({message.author.display_name}) 설정"
            )
            await message.reply(
                f"✅ {target.display_name}님의 잔액을 {amount:,}원으로 설정했어요." if ok else f"❌ 오류: {result}"
            )
            return

        # 1-7. "/개미소리", "/위성사진" 텍스트 명령
        if content.startswith("/개미소리"):
            text = content[len("/개미소리"):].strip()
            if not text:
                await self._send_game_result(message, user, "🐜 내용을 입력해주세요! (예: `/개미소리 가즈아!!`)")
                return
            image_bytes = await ant_voice.generate_ant_voice(text)
            if image_bytes is None:
                await self._send_game_result(message, user, "❌ 이미지 생성에 실패했어요 (에셋을 못 가져왔어요).")
                return
            file = discord.File(io.BytesIO(image_bytes), filename="ant_voice.webp")
            await message.reply(content=f"🐜 **개미의 외침:** {text}", file=file)
            if is_kakao:
                try:
                    await send_image(user.raw_id.split("//", 1)[0], image_bytes, filename="ant_voice.webp")
                except Exception:
                    log.exception("카톡 이미지 전송 실패 (user=%s)", user.key)
            return

        if content.startswith("/위성사진"):
            result = await satellite.get_satellite_image()
            if result is None:
                await self._send_game_result(message, user, "❌ 위성사진을 가져오지 못했어요. 잠시 후 다시 시도해주세요.")
                return
            image_bytes, obs_time = result
            file = discord.File(io.BytesIO(image_bytes), filename="satellite_latest.png")
            await message.reply(content=f"📡 천리안 2A호 최신 위성 영상 (관측 시간: {obs_time})", file=file)
            if is_kakao:
                try:
                    await send_image(user.raw_id.split("//", 1)[0], image_bytes, filename="satellite_latest.png")
                except Exception:
                    log.exception("카톡 이미지 전송 실패 (user=%s)", user.key)
            return

        # 대화 로그는 스킵 판단과 무관하게 항상 먼저 남긴다 (기존 봇이 이 순서를 [1-1]로 옮긴 이유와 동일 -
        # 늦게 기록하면 "봇이 이미 응답 중일 때 온 메시지"가 조용히 로그에서 누락됨)
        if not is_command and not is_feed_message and should_log:
            log_message(conversation_key, user.display_name, content, direction="in")

        # 2. 닉네임 변경 알림 + 채팅 머니 지급 - 카톡 일반 메시지에서 매번(호출어/자율응답 여부와
        # 무관하게) 실행. 원본 app_kakao_handler.handle_kakao_features의 '2. 일반 메시지 처리' 그대로.
        if is_linked_channel and is_kakao and not is_command and not is_feed_message:
            room_id, member_no = user.raw_id.split("//", 1)
            notice = check_and_update_nickname(user.display_name or "", member_no, room_id)
            if notice:
                await message.reply(notice)
                await send_message(room_id, notice)
            money_system.transaction(room_id=room_id, user_id=member_no, amount=10, transaction_type="chat")

        # 호출어(디스코드="하나야", 카톡="애순아"/"애순이") - 감지되면 확률/쿨다운 없이 무조건 응답
        call_word = autonomous_reply.detect_call_word(content, is_kakao)
        if call_word:
            prompt = autonomous_reply.strip_call_word(content, is_kakao)
            autonomous_reply.mark_active(key)
            try:
                reply = GREETING_REPLY if not prompt else await self._generate(conversation_key, message, prompt, is_kakao)
                await self._reply(message, user, is_kakao, reply, conversation_key, should_log)
            except Exception:
                # LLM/LiteLLM 인증 실패, 타임아웃 등 - 조용히 실패하지 않고 최소한 사용자에게 알린다.
                log.exception("호출어 응답 생성 실패 (channel=%s)", message.channel.id)
                await self._safe_reply(message, user, FAILURE_REPLY)
            finally:
                autonomous_reply.clear_active(key)
            return

        # 명령어(/)가 아닌 일반 대화일 때만 스킵/자율응답 판단
        if not is_command:
            if await self._should_skip(message, key):
                return

        if is_command:
            return  # 새 봇은 discord.py의 진짜 슬래시 명령(app_commands)이 별도로 처리

        if not autonomous_reply.should_auto_reply(content):
            return

        autonomous_reply.mark_active(key)
        try:
            reply = await self._generate(conversation_key, message, content, is_kakao)
            await self._reply(message, user, is_kakao, reply, conversation_key, should_log)
        except Exception:
            # 자율 응답은 원래 확률적으로 참견하는 거라, 실패했다고 채널에 에러 메시지까지
            # 남기면 오히려 더 어색하다 - 로그만 남기고 조용히 넘어간다.
            log.exception("자율 응답 생성 실패 (channel=%s)", message.channel.id)
        finally:
            autonomous_reply.clear_active(key)

    async def _handle_game_rps(self, message: Message, user: UserRef, choice: str, rest: str) -> None:
        try:
            bet = int(rest.strip())
        except ValueError:
            await self._send_game_result(message, user, f"사용법: `/{choice} 금액`")
            return
        room_id, user_id = game_engine.room_user(user)
        result = game_engine.play_rps(room_id, user_id, choice, bet)
        await self._send_game_result(message, user, result)

    async def _handle_game_bet_only(self, message: Message, user: UserRef, play_fn, rest: str) -> None:
        try:
            bet = int(rest.strip())
        except ValueError:
            await self._send_game_result(message, user, "사용법: `/명령어 금액` (금액은 숫자로 입력해주세요)")
            return
        room_id, user_id = game_engine.room_user(user)
        result = play_fn(room_id, user_id, bet)
        await self._send_game_result(message, user, result)

    async def _handle_game_choice(
        self, message: Message, user: UserRef, play_fn, rest: str, choices_hint: str
    ) -> None:
        parts = rest.strip().split(maxsplit=1)
        if len(parts) < 2:
            await self._send_game_result(message, user, f"사용법: `/명령어 [{choices_hint}] 금액`")
            return
        choice, bet_str = parts[0], parts[1]
        try:
            bet = int(bet_str)
        except ValueError:
            await self._send_game_result(message, user, "금액은 숫자로 입력해주세요.")
            return
        room_id, user_id = game_engine.room_user(user)
        result = play_fn(room_id, user_id, choice, bet)
        await self._send_game_result(message, user, result)

    async def _send_game_result(self, message: Message, user: UserRef, result: str) -> None:
        await self._send_chunked(message, result)
        if user.channel.value == "kakao":
            await send_message(user.raw_id.split("//", 1)[0], result)

    async def _handle_lookup(self, message: Message, user: UserRef, tool_fn, args: dict) -> None:
        try:
            async with message.channel.typing():
                result = await tool_fn.ainvoke(args)
            await self._send_game_result(message, user, result)
        except Exception:
            log.exception("조회 명령 실패 (tool=%s, args=%s)", getattr(tool_fn, "name", tool_fn), args)
            await self._send_game_result(message, user, "조회 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.")

    async def _handle_text_image_command(self, message: Message, user: UserRef, rest: str, *, with_style: bool) -> None:
        prompt = rest.strip()
        style = None
        if with_style and "|" in prompt:
            prompt, _, style = prompt.rpartition("|")
            prompt = prompt.strip()
            style = style.strip() or None

        if not prompt:
            usage = "사용법: `/그림 프롬프트` 또는 `/그림스타일 프롬프트 | 스타일명`"
            await self._send_game_result(message, user, usage)
            return

        async def job():
            return await generate_image(prompt, style=style)

        try:
            async with message.channel.typing():
                result = await image_gen_pool.submit(user, job)
        except ImageGenError as exc:
            await self._send_game_result(message, user, f"이미지 생성 실패: {exc}")
            return
        except Exception:
            log.exception("텍스트 명령 이미지 생성 실패 (user=%s, prompt=%s)", user.key, prompt)
            await self._send_game_result(message, user, "이미지 생성 중 예상치 못한 오류가 발생했어요. 잠시 후 다시 시도해주세요.")
            return

        file = discord.File(io.BytesIO(result.image_bytes), filename="generated.png")
        caption = f"`{prompt}` ({result.backend}/{result.model})"
        await message.reply(content=caption, file=file)
        if user.channel.value == "kakao":
            try:
                await send_image(user.raw_id.split("//", 1)[0], result.image_bytes, filename="generated.png")
            except Exception:
                log.exception("카톡 이미지 전송 실패 (user=%s)", user.key)

    async def _safe_reply(self, message: Message, user: UserRef, text: str) -> None:
        try:
            await message.reply(text)
        except discord.HTTPException:
            log.exception("실패 메시지 전송조차 실패 (channel=%s)", message.channel.id)
        if user.channel.value == "kakao":
            try:
                await send_message(user.raw_id.split("//", 1)[0], text)
            except Exception:
                log.exception("카톡 실패 메시지 전송 실패 (user=%s)", user.key)

    async def _generate(self, conversation_key: str, message: Message, prompt: str, is_kakao: bool) -> str:
        async with message.channel.typing():
            return await a_query(conversation_key, prompt, is_kakao=is_kakao)

    async def _reply(
        self,
        message: Message,
        user: UserRef,
        is_kakao: bool,
        reply: str,
        conversation_key: str,
        should_log: bool,
    ) -> None:
        await self._send_chunked(message, reply)
        if should_log:
            log_message(conversation_key, "애순이" if is_kakao else "아메하나", reply, direction="out")
        if is_kakao:
            await send_message(user.raw_id.split("//", 1)[0], reply)

    async def _send_chunked(self, message: Message, text: str) -> None:
        """
        [신규] 디스코드는 메시지 하나에 2000자 제한이 있다(계정/서버에 따라 더 관대한
        경우도 있지만 최소 기준이 2000). Plex 검색 결과처럼 도구 결과가 길게 나올 때
        LLM이 그걸 그대로 답변에 옮겨 담으면 전송 자체가 discord.errors.HTTPException
        (Invalid Form Body: 4000자 초과 등)으로 실패해서 "대답을 못 만들었어요" 폴백으로
        빠지는 문제가 있었다 - 길면 2000자 단위로 잘라서 여러 메시지로 나눠 보낸다.
        """
        limit = 2000
        if len(text) <= limit:
            await message.reply(text)
            return

        chunks = [text[i:i + limit] for i in range(0, len(text), limit)]
        await message.reply(chunks[0])
        for chunk in chunks[1:]:
            await message.channel.send(chunk)

    async def _should_skip(self, message: Message, key: str) -> bool:
        """이미 응답 생성 중이거나 직전 메시지가 봇 메시지면 건너뜀 (기존 _should_skip 이식)."""
        if autonomous_reply.is_active(key):
            return True
        try:
            history = [m async for m in message.channel.history(limit=2)]
        except discord.HTTPException:
            return False
        if len(history) > 1:
            prev = history[1]
            if prev.author.bot and prev.webhook_id is None:
                return True
        return False


async def setup(bot: commands.Bot):
    await bot.add_cog(Chat(bot))
