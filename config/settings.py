"""
모든 설정값의 단일 진입점.
다른 모듈은 os.environ을 직접 읽지 말고 여기서 import해서 쓴다.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _csv_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


# --- Discord ---
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", "0") or 0)
DISCORD_LOG_CHANNEL_IDS = _csv_ids(os.environ.get("DISCORD_LOG_CHANNEL_IDS"))

# 머니 시스템을 마음대로 조정할 수 있는 디스코드 유저ID 목록 (comma-separated).
# 카톡 릴레이 메시지는 브릿지 계정이 author라서 진짜 관리자인지 판별 불가 - 관리자 명령은
# 디스코드에서 직접 실행할 때만 허용된다.
ADMIN_DISCORD_IDS = _csv_ids(os.environ.get("ADMIN_DISCORD_IDS"))

# --- Kakao (디스코드 채널 릴레이 방식 - 원본 봇과 동일, 실제 브릿지 코드로 형식 확인함) ---
# 브릿지가 카톡 메시지를 "{발신자명}//{방ID}//{유저ID}" 닉네임으로 인코딩해서 아래 채널들에
# 일반 디스코드 메시지로 올려준다. 이 봇은 별도 수신 서버 없이 on_message에서 파싱만 한다.
KATALK_BRIDGE_URL = os.environ.get("KATALK_BRIDGE_URL", "")  # 답장을 내보낼 때만 사용
KATALK_LINKED_CHANNEL_IDS = _csv_ids(os.environ.get("KATALK_LINKED_CHANNEL_IDS"))  # 기존 TARGET_THREAD_IDS
NICKNAME_DELIMITER = os.environ.get("NICKNAME_DELIMITER", "//")

# --- 자율 응답(호출어 없이 확률적으로 참견) 설정 - core/autonomous_reply.py ---
# 기존 봇(app.py의 _handle_auto_response)과 동일한 기본값: 60초 쿨다운, 3% 확률.
# 카톡/디스코드 구분 없이 전역으로 공유된다 (기존 봇도 last_aesun_active_time이 전역 단일값이었음).
AUTO_REPLY_TRIGGER_KEYWORDS = _csv_ids(os.environ.get("AUTO_REPLY_TRIGGER_KEYWORDS", "아메하나,애순이,똑똑,안녕"))

# 자동화 알림으로 보고 봇이 아무 반응도 하지 않을 발신자 이름 (쉼표로 여러 개).
# 이름이 ".GAS"로 끝나는 발신자와 카톡 릴레이가 아닌 웹훅은 여기 없어도 자동으로 무시된다
# (cogs/chat.py의 _is_automation_message) - 봇 계정처럼 웹훅이 아닌 자동화를 추가할 때 쓴다.
GAS_WEBHOOK_NAMES = set(
    n.strip() for n in os.environ.get("GAS_WEBHOOK_NAMES", "4KHD_SNDER.GAS").split(",") if n.strip()
)
AUTO_REPLY_COOLDOWN_SEC = int(os.environ.get("AUTO_REPLY_COOLDOWN_SEC", "60"))
AUTO_REPLY_PROBABILITY = float(os.environ.get("AUTO_REPLY_PROBABILITY", "0.03"))
# 호출어("하나야"/"애순아")로 부른 뒤 이 시간(초) 동안은 같은 사람이 호출어 없이 말해도 응답한다.
# 0이면 끈다(매번 호출어 필요).
CALL_FOLLOWUP_SEC = int(os.environ.get("CALL_FOLLOWUP_SEC", "300"))

# --- LLM (LiteLLM proxy) ---
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "")

# --- Image generation (AI Horde) ---
IMAGE_GEN_BACKEND = os.environ.get("IMAGE_GEN_BACKEND", "horde")
IMAGE_GEN_MAX_CONCURRENCY = int(os.environ.get("IMAGE_GEN_MAX_CONCURRENCY", "2"))

HORDE_API_KEY = os.environ.get("HORDE_API_KEY", "")
HORDE_DEFAULT_MODEL = os.environ.get("HORDE_DEFAULT_MODEL", "Nova Anime XL")

# --- Storage paths (초기화 결정: 빈 상태로 시작) ---
CONVERSATION_DB_PATH = BASE_DIR / "storage" / "conversations.db"  # 대화 로그 (SQLite - 최근 맥락 조회용)
CONVERSATION_CONTEXT_LIMIT = int(os.environ.get("CONVERSATION_CONTEXT_LIMIT", "12"))
VECTOR_DB_DIR = BASE_DIR / "storage" / "vector_db"
NICKNAME_DETECT_DIR = BASE_DIR / "storage" / "nickname_detect"  # 기존 check_and_update_nickname.py와 동일 용도
MONEY_DIR = BASE_DIR / "storage" / "money"
MCP_SERVERS_CONFIG_PATH = BASE_DIR / "config" / "mcp_servers.yaml"

# --- Redroid 패키지 감시 결과 알림 (cogs/redroid_watch.py) ---
# 실제 감시/삭제는 cron의 scripts/redroid_check_packages.sh가 하고, 변동이 있을 때 이 파일에
# 결과를 한 줄(JSON)씩 추가한다. 봇은 새 줄을 읽어서 아메하나 말투로 아래 채널에 알린다.
# REDROID_NOTIFY_CHANNEL_ID를 빈 값으로 두면 이 기능을 끈다.
REDROID_EVENTS_PATH = Path(os.environ.get("REDROID_EVENTS_PATH", "/mnt/redroid_watch/events.jsonl"))
REDROID_NOTIFY_CHANNEL_ID = int(os.environ.get("REDROID_NOTIFY_CHANNEL_ID", "591180628842774554") or 0)
REDROID_WATCH_STATE_PATH = BASE_DIR / "storage" / "redroid_watch_state.json"

# --- 포링푸드 (poring_food/ - 애순이와 동료들의 회사 일상, cogs/poring_food.py가 매시 실행) ---
# 원래 별도 cron 프로젝트(/mnt/poring_food)였는데 봇으로 합쳤다. LLM/카톡 브릿지는 봇 설정을 쓴다.
PORING_FOOD_ENABLED = os.environ.get("PORING_FOOD_ENABLED", "1") not in ("0", "false", "False", "")
PORING_FOOD_RUN_MINUTE = int(os.environ.get("PORING_FOOD_RUN_MINUTE", "3"))  # 매시 몇 분에 실행할지 (예전 cron: 매시 3분)
PORING_FOOD_DATA_DIR = Path(os.environ.get("PORING_FOOD_DATA_DIR", str(BASE_DIR / "poring_food" / "data")))
PORING_FOOD_STATE_DIR = Path(os.environ.get("PORING_FOOD_STATE_DIR", str(BASE_DIR / "storage" / "poring_food")))
# 방송(매시 일지) 전송 대상. 디스코드는 채널 ID(봇이 직접 올림)가 우선이고, 없으면 예전 웹훅 URL로 보낸다.
PORING_DISCORD_CHANNEL_ID = int(os.environ.get("PORING_DISCORD_CHANNEL_ID", "0") or 0)
PORING_DISCORD_WEBHOOK_URL = os.environ.get("PORING_DISCORD_WEBHOOK_URL", "")
PORING_KAKAO_ROOM_ID = os.environ.get("PORING_KAKAO_ROOM_ID", "")  # 예전 포링푸드 .env의 ROOM_ID
# LLM - 기본은 봇과 같은 LiteLLM. 포링푸드만 다른 모델을 쓰려면 지정 (예전 .env의 LLM_MODEL/SEARCH_MODEL)
PORING_LLM_MODEL = os.environ.get("PORING_LLM_MODEL", "") or LITELLM_MODEL
PORING_SEARCH_MODEL = os.environ.get("PORING_SEARCH_MODEL", "gemini-search")
