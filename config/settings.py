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
AUTO_REPLY_TRIGGER_KEYWORDS = _csv_ids(os.environ.get("AUTO_REPLY_TRIGGER_KEYWORDS", "애순,똑똑,안녕"))
AUTO_REPLY_COOLDOWN_SEC = int(os.environ.get("AUTO_REPLY_COOLDOWN_SEC", "60"))
AUTO_REPLY_PROBABILITY = float(os.environ.get("AUTO_REPLY_PROBABILITY", "0.03"))

# --- LLM (LiteLLM proxy) ---
LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "")

# --- Image generation (AI Horde) ---
IMAGE_GEN_BACKEND = os.environ.get("IMAGE_GEN_BACKEND", "horde")
IMAGE_GEN_MAX_CONCURRENCY = int(os.environ.get("IMAGE_GEN_MAX_CONCURRENCY", "2"))

HORDE_API_KEY = os.environ.get("HORDE_API_KEY", "")
HORDE_DEFAULT_MODEL = os.environ.get("HORDE_DEFAULT_MODEL", "Nova Anime XL")

# --- Poring Food file contract ---
PORING_FOOD_STATUS_JSON_PATH = Path(
    os.environ.get("PORING_FOOD_STATUS_JSON_PATH", "./storage/aesun_current_status.json")
)

# --- Storage paths (초기화 결정: 빈 상태로 시작) ---
CONVERSATION_DB_PATH = BASE_DIR / "storage" / "conversations.db"  # 대화 로그 (SQLite - 최근 맥락 조회용)
CONVERSATION_CONTEXT_LIMIT = int(os.environ.get("CONVERSATION_CONTEXT_LIMIT", "12"))
VECTOR_DB_DIR = BASE_DIR / "storage" / "vector_db"
NICKNAME_DETECT_DIR = BASE_DIR / "storage" / "nickname_detect"  # 기존 check_and_update_nickname.py와 동일 용도
MONEY_DIR = BASE_DIR / "storage" / "money"
MCP_SERVERS_CONFIG_PATH = BASE_DIR / "config" / "mcp_servers.yaml"
