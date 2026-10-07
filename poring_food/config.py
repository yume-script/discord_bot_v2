"""
포링푸드 설정 - 봇의 config/settings.py에서 값을 받아 포링푸드 모듈들이 쓰던 이름 그대로 제공한다.

예전엔 /mnt/poring_food/.env를 직접 읽었는데, 봇으로 합치면서 .env는 봇의 것 하나만 쓴다
(config/settings.py가 python-dotenv로 이미 os.environ에 올려둔다). 그래서 확률/가중치 같은
세부 조정값(AESUN_SPOTLIGHT_WEIGHT, INTERACTION_HOURS 등)은 이름 그대로 봇 .env에 두면 된다.

- 기본 데이터(git 관리): PORING_FOOD_DATA_DIR (기본 poring_food/data)
- 실행 상태(런타임 기록): PORING_FOOD_STATE_DIR (기본 storage/poring_food)
"""
import os

from config import settings

DATA_DIR = str(settings.PORING_FOOD_DATA_DIR)
STATE_DIR = str(settings.PORING_FOOD_STATE_DIR)
os.makedirs(STATE_DIR, exist_ok=True)

# --- 기본 데이터 (조직도/이슈 템플릿/페르소나/경쟁사) ---
ORG_PATH = os.path.join(DATA_DIR, "poring_food_organization.json")
ISSUE_PATH = os.path.join(DATA_DIR, "porting_food_issue.json")
PERSONA_PATH = os.path.join(DATA_DIR, "aesun_persona.json")
RIVALS_PATH = os.path.join(DATA_DIR, "rival_companies.json")
# 이 패턴에 걸리는 파일은 전부 자동으로 "회사"로 인식된다 - 라이벌 회사가 늘어나도
# "무슨무슨_organization.json" 파일 하나만 data 폴더에 추가하면 코드 수정 없이 인식된다
# (파일의 최상위 "company_name" 값을 회사명으로 쓴다).
ORGANIZATION_GLOB = os.path.join(DATA_DIR, "*_organization.json")

# --- 실행 상태 ---
# 애순이 현재 상태 스냅샷 (poring_food/tools.py의 get_current_status가 읽는다)
STATUS_OUT_PATH = os.path.join(STATE_DIR, "aesun_current_status.json")
# 매 실행 누적 기록 - "어제 뭐 했어?"용. 다른 인물 기록도 character 필드로 구분해 같은 파일에 쌓는다.
HISTORY_LOG_PATH = os.path.join(STATE_DIR, "aesun_history.jsonl")
HISTORY_RETENTION_DAYS = int(os.getenv("HISTORY_RETENTION_DAYS", "30"))
CHARACTERS_STATE_PATH = os.path.join(STATE_DIR, "characters_state.json")
RELATIONSHIPS_PATH = os.path.join(STATE_DIR, "relationships.json")
# 직전 회차 이슈 (예전엔 실행 위치 기준 상대경로 "last_issue.json"이었다)
ISSUE_LOG_PATH = os.path.join(STATE_DIR, "last_issue.json")

# 상호작용 이벤트를 만들 시각(하루 3번). .env의 INTERACTION_HOURS="10,15,20" 형식으로 조정 가능.
INTERACTION_HOURS = {
    int(h.strip()) for h in os.getenv("INTERACTION_HOURS", "10,15,20").split(",") if h.strip()
}

# "생산량"/"영업 판매수량"과 경쟁사 실측 지표는 같은 봇의 대화 로그(SQLite)에서 센다.
DISCORD_BOT_V2_DB_PATH = str(settings.CONVERSATION_DB_PATH)

# --- 방송(매시 일지) 전송 ---
DISCORD_CHANNEL_ID = settings.PORING_DISCORD_CHANNEL_ID
DISCORD_WEBHOOK_URL = settings.PORING_DISCORD_WEBHOOK_URL
ROOM_ID = settings.PORING_KAKAO_ROOM_ID

# --- LLM (봇과 같은 LiteLLM) ---
LITELLM_MASTER_KEY = settings.LITELLM_API_KEY
LLM_MODEL = settings.PORING_LLM_MODEL
SEARCH_MODEL = settings.PORING_SEARCH_MODEL


def _chat_completions_url(base: str) -> str:
    """LITELLM_BASE_URL("http://host:4000" 또는 ".../v1")을 chat/completions 엔드포인트로 맞춘다."""
    base = (base or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


API_URL = _chat_completions_url(settings.LITELLM_BASE_URL)

# --- [겸직] 사내 자료실 "북오아시스" (poring_food/bookoasis.py) ---
# 봇이 이미 연결해 둔 bookoasis MCP 서버의 조회 도구만 쓴다 (SSH를 따로 열지 않음).
BOOKOASIS_ENABLED = os.getenv("BOOKOASIS_ENABLED", "1") not in ("0", "false", "False", "")
BOOKOASIS_MCP_SERVER = os.getenv("BOOKOASIS_MCP_SERVER", "bookoasis")  # config/mcp_servers.yaml의 서버 이름
BOOKOASIS_TIMEOUT_SEC = int(os.getenv("BOOKOASIS_TIMEOUT_SEC", "45"))
# 이야기에 쓸 서재 종류. 이야기는 카톡으로도 나가므로 기본값에서 adult는 뺐다.
BOOKOASIS_STORY_DB_TYPES = [
    t.strip() for t in os.getenv("BOOKOASIS_STORY_DB_TYPES", "general,audiobook").split(",") if t.strip()
]
BOOKOASIS_STATE_PATH = os.path.join(STATE_DIR, "bookoasis_state.json")
