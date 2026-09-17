# discord_bot_v2 (애순이 v2)

기존 `yume-script/discord_bot`을 대체하는 새 디스코드 봇의 뼈대입니다.
기존 봇 운영 중 겪었던 문제(구조·보안·동시성·중복 구현)를 반영해 초기 구조를 잡았고,
카톡 연동은 실제 원본 코드(`app.py`, `app_config.py`, `app_kakao_handler.py`)를 확인해서
그대로 이식했습니다.

## 결정된 방향
- 카톡 로그 / 벡터DB / 회원상태: **초기화** (이관하지 않음, `storage/`는 빈 상태로 시작)
- 카카오톡 연동: **유지** (디스코드 채널 릴레이 방식 - 아래 "카톡 연동" 참고)
- 포링푸드(porning_food) 연동: **유지** (`integrations/poring_food_bridge.py` 참고)
- BookOasis 대화방 플러그인: **폐기** (Firebase 브릿지 관련 코드 없음)
- 이미지 생성 백엔드: AI Horde만 사용 (구글 코랩 연동은 불편해서 제외)

## 구조
```
config/     설정 단일 진입점 (.env, mcp_servers.yaml)
core/       UserRef, 자율 응답 로직, 카톡 닉네임 파싱, 카톡 답장 전송, 동시성 워커풀
ai/         LLM 클라이언트, MCP 매니저, RAG 엔진, 프롬프트, 이미지 생성 엔진
cogs/       디스코드 이벤트/명령어 (1기능 1파일)
integrations/  포링푸드 등 외부 프로젝트와의 파일 계약
storage/    런타임 데이터 (git에 커밋되지 않음 - .gitignore 확인)
scripts/    배포/저장소 초기화 스크립트
deploy/     systemd 유닛 파일
```

## 새 GitHub 저장소로 초기 커밋
1. GitHub에서 빈 저장소를 먼저 만든다 (README/LICENSE 자동생성 없이 완전히 빈 상태로 - 이미 이 프로젝트에 README가 있음).
2. `./scripts/init_new_repo.sh git@github.com:<계정>/<새 저장소>.git` 실행.
   - `.env`가 실수로 커밋되지 않도록 이중으로 체크한다 (`.gitignore`가 1차 방어, 스크립트가 2차 확인).
   - `git init` → `git add .` → 초기 커밋 → `origin` 등록 → `git push -u origin main` 순서로 진행.

## 서버 배포 (systemd)
1. 서버에 저장소를 clone하고 `.env`를 채운다.
2. `deploy/discord-bot-v2.service`를 서버 환경(`User`, `WorkingDirectory`, `ExecStart` 경로)에 맞게 수정한 뒤:
   ```bash
   sudo mkdir -p /var/log/discord-bot-v2 && sudo chown discordbot:discordbot /var/log/discord-bot-v2
   sudo cp deploy/discord-bot-v2.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now discord-bot-v2.service
   ```
3. 이후 업데이트 배포는 `scripts/git_pull_deploy.sh`가 `git pull` → 의존성 설치 → `systemctl restart discord-bot-v2.service`까지 처리한다 (서비스명이 위 유닛 파일과 일치해야 함).

## 시작하기
```bash
cp .env.example .env   # 값 채우기
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```

## 카톡 연동 (디스코드 채널 릴레이 방식)
기존 봇과 동일한 방식을 그대로 씁니다 - **별도 수신 서버가 없습니다.**
실제 브릿지 코드(Flask 서버, 카톡→디스코드 웹훅 릴레이)를 검토해서 정확한 형식에 맞췄습니다.

- 브릿지가 카톡 메시지를 **`"{발신자명}//{방ID}//{유저ID}"`** 형식의 닉네임으로 인코딩해서
  디스코드 웹훅(스레드 지정 가능)으로 올려준다. 구분자는 `NICKNAME_DELIMITER`(기본 `"//"`).
- 브릿지는 카톡방(방ID)별로 다른 디스코드 스레드에 매핑해서 보낼 수 있고, 매핑 안 된 방은
  기본 스레드로 몰린다 — **한 스레드에 여러 방이 섞일 수 있음**. `KATALK_LINKED_CHANNEL_IDS`에는
  브릿지가 쓰는 모든 스레드 ID(매핑된 것 + 기본 스레드)를 넣어야 한다.
- `core/kakao_relay.py`의 `parse_kakao_author()`가 `message.author.name`을 뒤에서부터
  `rsplit`으로 파싱해서 카톡 메시지인지, 어느 방/유저인지 판별한다
  (`cogs/chat.py`의 `on_message`에서 호출).
- 봇의 답장은 디스코드 채널에도 보내고(`message.reply`), 동시에 `core/katalk_bridge.send_message()`로
  `KATALK_BRIDGE_URL`에 POST해서 실제 카톡방에도 내보낸다.
  **확인 필요**: 지금까지 본 브릿지 코드는 카톡→디스코드 단방향(수신)만 구현되어 있다.
  디스코드→카톡(봇 답장을 실제 카톡방에 전송) 쪽 엔드포인트가 별도로 있는지, 아니면 새로 만들어야
  하는지 확인이 필요하다.
- 카톡 메시지 로그는 `core/conversation_store.py`가 SQLite(`storage/conversations.db`)에
  저장한다 (아래 "대화 맥락" 항목 참고).
- **입장/퇴장 피드, 닉네임 변경 알림, 채팅 머니 지급**: 원본 `app_kakao_handler.py`를 그대로 이식했다.
  - `core/kakao_feed.py` — `{"feedType"...}` 메시지를 파싱해서 입장(4)/퇴장(2) 환영·작별 인사를 만든다
    (`build_feed_reply`). 원본과 동일하게 피드 메시지는 여기서 처리가 끝나고 호출어/자율응답으로
    안 내려간다.
  - `core/nickname_watch.py` — 원본 `check_and_update_nickname.py`를 그대로 옮김. 카톡 발신자의
    닉네임 변경을 감지해서, 바뀐 경우에만 변경 이력을 담은 알림을 만든다 (`storage/nickname_detect/`에
    방ID_유저ID별 JSONL로 이력 저장).
  - `core/money_system.py` — 원본 `money_system.py`를 그대로 이식 (저장 폴더만 `storage/money/`로
    조정). 방ID+유저ID별 JSONL에 거래 기록을 append하고 마지막 줄을 최신 잔액/빚으로 취급한다.
    수익 발생 시 빚이 있으면 10% 자동 상환, 같은 날 채팅 보상은 한 줄로 합쳐서 파일 비대화를
    막고, 하루 한 번 100원 대출(`borrow_money`, 빚으로 잡힘) 기능도 포함되어 있다. `transaction()`은
    `(성공여부, 새 잔액 또는 메시지)` 튜플을 반환하지만 채팅 보상 호출부(`cogs/chat.py`)는
    원본처럼 반환값을 쓰지 않는다.
  - 카톡 일반 메시지(피드/명령어 제외)마다 닉네임 변경 체크 + 채팅 머니 10원 지급이 자동으로
    실행된다 (`cogs/chat.py`, 호출어/자율응답 여부와 무관하게 항상 실행 — 원본과 동일).

## 대화 맥락 (SQLite)
- `core/conversation_store.py` — `storage/conversations.db` 하나에 모든 대화를 저장한다.
  카톡은 방(room_id), 순수 디스코드는 채널(`discord:{channel_id}`) 단위로 `conversation_key`가
  나뉜다.
- **저장 대상**: 카톡 연동 채널(`KATALK_LINKED_CHANNEL_IDS`)의 카톡 메시지 + `DISCORD_LOG_CHANNEL_IDS`에
  등록된 순수 디스코드 채널의 메시지. 둘 다 아닌 채널은 저장되지 않는다.
- 원래는 방별 JSONL 파일에 무한정 append하는 방식이었는데, "최근 N개 메시지"를 가져오려면
  매번 파일 전체를 읽어야 하는 게 문제였다. SQLite로 바꿔서 인덱스 조회 한 번으로 해결했고,
  나중에 오래된 데이터를 정리하고 싶어지면 `prune_older_than(days)` 하나로 가능하다 (지금은
  자동 호출 안 됨 - 필요해지면 스케줄러에 연결).
- `ai/rag_engine.py`의 `a_query(conversation_key, message)`가 응답을 만들 때마다
  `CONVERSATION_CONTEXT_LIMIT`(기본 12개)만큼 최근 메시지를 불러와 LLM에 대화 맥락으로 먼저
  넣어준다 — 애순이가 직전 대화 내용을 참고해서 답하게 하려는 목적. 카톡/디스코드 모두 같은
  구조를 쓴다.
- 봇 자신의 답장(`direction='out'`)도 저장되기 때문에, 맥락에는 "무슨 말을 들었고 내가 뭐라고
  답했는지"가 둘 다 들어간다.

## 자율 응답 (시간 기반, 카톡/디스코드 공용)
- `core/autonomous_reply.py`가 기존 봇(`app.py`의 `_handle_auto_response`/`_should_skip`,
  호출어 감지)을 그대로 이식한 곳. 로직 값(호출어, 쿨다운, 확률)은 바꾸지 않았다.
- **호출어**: "애순아" / "애순이" / "애순" 중 하나라도 있으면 무조건 응답.
- **일반 메시지**: `AUTO_REPLY_TRIGGER_KEYWORDS`(기본 "애순,똑똑,안녕")가 있으면 바로,
  없으면 쿨다운(`AUTO_REPLY_COOLDOWN_SEC`, 기본 60초) + 확률(`AUTO_REPLY_PROBABILITY`, 기본 3%)을
  둘 다 통과해야 참견.
- **상태 공유**: 마지막 참견 시각과 활성 채널 집합이 모듈 전역이라, 카톡이든 디스코드든 같은
  `on_message`(`cogs/chat.py`)를 타기 때문에 자연히 공유된다 — 기존 봇과 동일한 동작.
- **직전 메시지가 봇이면 스킵**: `channel.history()`로 확인 (`cogs/chat.py`의 `_should_skip`).
  카톡 메시지도 결국 같은 디스코드 채널의 메시지라서 별도 처리가 필요 없다.

## 이미지 생성
- `ai/image_engine.py`가 단일 진입점 — 명령어(`cogs/image_gen.py`)와 자율대화가 이 함수 하나만 호출한다.
- 백엔드는 AI Horde 하나만 사용.
- 기본 모델은 `HORDE_DEFAULT_MODEL=Nova Anime XL` (기존 봇의 자율대화 기본 모델 승격 결정을 반영).
- **두 가지 방식 모두 지원**:
  - 진짜 디스코드 슬래시 명령어 (`/그림`, `/그림스타일`) — `cogs/image_gen.py`. 자동완성 목록을
    거쳐 파라미터를 채우는 인터랙션 방식.
  - 텍스트로 빠르게 치는 `/그림 프롬프트`, `/그림스타일 프롬프트 | 스타일명` — `cogs/chat.py`의
    `on_message`에서 직접 감지해서 처리 (슬래시 명령어 UI를 거치지 않고 한 번에 타이핑해서
    보내도 바로 인식됨, 예전 봇과 동일한 방식). 둘 다 같은 `ai/image_engine.generate_image()`와
    `core/concurrency.image_gen_pool`을 공유한다.

## 잔액 조회 / 관리자 머니 조정
- **`/잔고`, `/머니`** (조회, 누구나) — 원본 명령어 이름 그대로. 자기 잔액/빚을 보여준다
  (`core/game_engine.room_user()`로 카톡/디스코드 구분 없이 동일하게 동작).
- **`/머니설정 [@대상] 금액`** (관리자 전용, 디스코드에서만) — `core/money_system.set_balance()`로
  잔액을 원하는 값으로 **절대 설정**한다. `transaction()`과 달리 빚 자동상환이나 잔액 부족
  체크를 타지 않는다 (관리자가 의도적으로 정확한 숫자를 넣는 용도라서). 대상 생략 시 본인 계정.
  슬래시 명령어(`cogs/admin.py`)와 텍스트 명령(`cogs/chat.py`) 둘 다 지원.
- **`/머니설정카톡 방ID 유저ID 금액`** — 카톡 유저를 대상으로 같은 걸 한다 (멘션이 안 되니 방ID/유저ID를
  직접 입력).
- **관리자 판별**: `.env`의 `ADMIN_DISCORD_IDS`(디스코드 유저ID, comma-separated)에 등록된
  사람만 사용 가능. **카톡 릴레이 메시지로는 관리자 명령을 쓸 수 없다** — 카톡 메시지의
  `message.author`는 브릿지 계정이라 실제 관리자인지 구분이 안 되기 때문.
- **참고**: 채팅으로 자동 지급되는 10원은 지금 카톡 메시지에만 붙고(`app_kakao_handler` 원본
  동작 그대로), 순수 디스코드 채팅에는 안 붙는다 — 원한다면 확장 가능, 별도 결정 필요.

## 미니게임 7종
- 원본 게임 파일(`game_rps.py`, `game_dice.py`, `game_slot_machine.py`, `game_dragontiger.py`,
  `game_dice_poker.py`, `game_blackjack.py`, `game_baccarat_20260609.py`)을 확인해서
  **베팅 검증 → 일일 횟수 제한 → 잔액 체크 → 판정 → `money_system.transaction()` 정산** 흐름과
  배당 배율, 일일 제한 횟수를 전부 원본 값 그대로 이식했다 (`core/game_engine.py`).
- **딱 하나 다른 점**: 원본은 PIL로 카드/주사위/슬롯 이미지를 그려서 보여줬는데, 그 이미지
  에셋(`game_asset_manager.py`가 참조하는 카드·주사위 그림 파일들)을 이 프로젝트로 가져오지
  못해서 **결과를 텍스트로 단순화**했다. 슬롯머신은 원본이 라그나로크M 카드 RAG 데이터(외부
  jsonl)에 의존했는데 그것도 없어서 고정 이모지 심볼(🍒🍋🔔⭐7️⃣)로 대체했다 — 배당 공식(희귀
  심볼일수록 3연속 적중 시 배당 ↑)은 원본 그대로.
- 명령어: `/가위`, `/바위`, `/보`(각 일일 5회), `/주사위`(일일 10회), `/용호`(용/호랑이/무승부,
  일일 5회), `/다이스포커`(일일 5회), `/블랙잭`(일일 5회), `/바카라`(홀/짝, 일일 5회),
  `/슬롯머신`(일일 5회).
- **슬래시 명령어**(`cogs/games.py`, 디스코드 네이티브 유저 전용)와 **텍스트 명령**
  (`cogs/chat.py`, 카톡+디스코드 둘 다 - 카톡은 슬래시 인터랙션을 못 쓰니 텍스트가 유일한
  경로)가 `core/game_engine.py`의 같은 함수를 공유한다.
- room_id/user_id 규칙은 원본(`parse_user_info`)과 동일: 카톡은 방ID/회원번호로 분리, 순수
  디스코드 유저는 원본 폴백 그대로 room_id=user_id=author_id.
- **머니 시스템 동작 검증**: 채팅 보상 병합, 잔액 부족 차단, 게임별 일일 횟수 제한, 빚 10%
  자동 상환, 하루 대출 중복 방지, 7개 게임 전부(잘못된 입력 방어 포함)를 시뮬레이션 테스트로
  확인했다 — 전부 정상 동작.

## 날씨 / 환율 / 주식
- 원본 저장소를 다시 확인해보니 `app_command_handler.py`에 30개 이상의 명령어가 라우팅되어
  있었다 (미니게임 7종, 날씨/환율/주식, 라그나로크M RAG 연동, mbti, 운세 등). 미니게임 7종과
  날씨/환율/주식은 이식 완료했다.
- `ai/local_tools.py` — 날씨(Open-Meteo), 환율(Frankfurter/ECB 기준), 주식(Yahoo Finance
  비공식 차트 API) 조회 함수를 LangChain `@tool`로 감쌌다. 전부 API 키가 필요 없는 공개
  API라, 원본 `weather_district.py`/`weather_nation.py`/`exchange.py`/`stock.py` 소스를
  확보하지 못해서 새로 작성했다 (원본을 구하면 교체 가능).
- **명령어와 자연어 대화가 항상 같은 데이터를 쓴다** — `cogs/lookup.py`(슬래시 명령어),
  `cogs/chat.py`의 텍스트 명령(`/날씨`, `/전국날씨`, `/환율`, `/주식`), 그리고
  `ai/rag_engine.py`(애순이에게 자연어로 직접 물어볼 때)가 전부 `ai/local_tools.py`의
  같은 함수를 호출한다.
- `ai/rag_engine.py`는 이제 매 호출마다 로컬 도구(+MCP 도구)를 전부 LLM에 바인딩해두고,
  필요하다고 판단하면 LLM이 알아서 tool_calls를 발생시켜 호출한다 (최대 4턴 왕복). 예전엔
  의도 분류(needs_mcp) 후 필요할 때만 도구를 붙이는 방식이었는데, 분류가 틀려서 도구가
  안 붙는 경우를 없애려고 항상 바인딩하는 쪽으로 단순화했다.

## 운세 / MBTI
- **운세** (`core/fortune.py`) — 원본 `fortune.py`를 그대로 이식. 네이버 검색 결과를
  `requests`+`BeautifulSoup`(lxml)로 스크레이핑한다 (동기 함수라 원본처럼 스레드풀에서 실행).
  디스코드엔 마크다운 포함 텍스트를, 카톡엔 마크다운 걷어낸 텍스트를 보내는 것도 원본과 동일.
  **주의**: 네이버가 요청을 403으로 막는 경우가 있다 (스크레이핑이라 원래도 불안정한 방식) —
  서버 환경에서 직접 테스트 필요.
- **MBTI** (`core/mbti.py`) — **[주의] 원본 `mbti_system.py` 소스를 확보하지 못해서
  (저장소 파일 목록이 "image_gen.py"까지만 보여서 "m"으로 시작하는 파일 링크를 못 찾음)
  새로 작성한 최소 구현이다.** `/mbti INTJ`처럼 유형을 등록하고, `/mbti`만 치면 등록된 유형과
  짧은 설명을 보여준다 (`storage/mbti.json`에 저장). 원본을 구하면 교체할 것.
- 둘 다 슬래시 명령어(`cogs/lookup.py`)와 텍스트 명령(`cogs/chat.py`, `/운세 양띠`, `/mbti INTJ`)
  양쪽 다 지원한다.

## 개미소리 / 위성사진
- **개미소리** (`core/ant_voice.py`) — 원본 `ant_voice_gen.py`를 그대로 이식. 원본 소스
  이미지(`ant_source.webp`)와 폰트(`NanumGothicCoding.ttf`)는 원본 저장소(Public)에서
  최초 1회 다운로드해 `storage/assets/`에 캐싱한다. **실제로 이미지 생성까지 테스트 완료.**
- **위성사진** (`core/satellite.py`) — **[주의] 원본 `satellite.py` 소스를 확보하지 못해서
  새로 작성했다** (저장소 파일 목록에서 링크를 못 찾음). 히마와리-9 실시간 위성사진
  (NICT 제공, `himawari8.nict.go.jp`, 무료/API 키 불필요)을 가져온다. **[검증 안 됨]** 이
  작업 환경의 네트워크 정책이 해당 도메인을 막고 있어서 실제 호출을 테스트하지 못했다 —
  서버에 배포한 뒤 `/위성사진` 직접 확인 필요.
- 카톡 쪽 이미지 전송은 `core/katalk_bridge.send_image()`를 새로 추가했다. **[주의]** 원본은
  `send_katalk_image_webhook`이라는 별도 웹훅을 썼는데 정확한 API 계약(엔드포인트/payload
  형식)을 확보하지 못해서, 텍스트 전송과 비슷한 형태로 추정해 구현했다 — 실제 브릿지 서버
  구현에 맞춰 조정이 필요할 수 있다.
- 슬래시 명령어(`cogs/fun.py`)와 텍스트 명령(`cogs/chat.py`, `/개미소리 내용`, `/위성사진`)
  둘 다 지원.

## MCP 서버 연동 (BookOasis)
- `config/mcp_servers.yaml`에 BookOasis의 `tools/mcp_server.py`를 SSH+`docker exec`로 접속하는
  stdio MCP 서버로 등록했다. SSH 키 기반 무인증 접속이 전제 (MCP는 대화형 비밀번호 입력이
  안 되므로 필수).
- **연결 정보 확정 완료** — `root@192.168.0.31`, 컨테이너 `bookoasis`, 스크립트 경로
  `/app/tools/mcp_server.py`. SSH 키(`/root/.ssh/id_ed25519_bookoasis`) 인증도 확인됨.
- `ai/prompts.py`의 `RESPONSE_SYSTEM_PROMPT`에 "bookoasis"와 "북오아시스"가 같은 서비스를
  가리킨다는 걸 명시해서, 자연어 대화에서 어느 이름으로 불러도 관련 도구를 쓰도록 했다.
- **안정성 보완**: `ai/mcp_manager.py`의 `init_mcp()`가 예외를 흡수하도록 고쳤다 — MCP 연결
  실패(SSH 오류, 컨테이너 없음, 경로 오류 등)가 `setup_hook()` 맨 앞에서 무방비로 터지면
  봇 전체가 크래시 루프에 빠지는 문제가 있었음. 이제 MCP가 실패해도 나머지 기능은 정상 기동.

## 라그나로크M RAG 연동 — 방향성만 정리 (아직 구현 안 함)
원본은 `/가이드`, `/검색`, `/카뽑`(카드확률), `/안전제련`, `/어구`(어비스홀 타이머) 5개
명령어가 라그나로크M 게임 데이터(RAG 벡터DB + `card_probabilities.jsonl`)에 물려있었다.
전체를 이식하려면 아래 순서가 필요하다:

1. **데이터 소스 확보** — 원본의 `db/`(chroma 벡터DB), `card_probabilities.jsonl`,
   `guide_data.txt`를 가져오거나, [[discord-bot-romel-scraper]] 스크래퍼로 처음부터
   다시 수집해야 한다. 데이터 초기화 결정(카톡 로그/벡터DB 미이관)과 같은 맥락 — 이 RAG
   데이터도 새로 쌓을지, 예전 걸 가져올지부터 정해야 한다.
2. **벡터스토어 선택** — 기존 Chroma를 그대로 쓸지, LangChain 생태계에 맞춰 다른 걸(FAISS 등)
   쓸지. `ai/rag_engine.py`가 이미 LangChain 기반이라 `langchain_community.vectorstores`로
   붙이는 게 제일 자연스럽다.
3. **도구로 노출** — `ai/local_tools.py`에 있는 날씨/환율/주식과 같은 패턴으로
   `@tool async def search_ragm_guide(query: str)`, `get_card_probability(card_name: str)`
   같은 함수를 만들어서 `LOCAL_TOOLS`에 추가하면, `/가이드`·`/검색`·`/카뽑` 슬래시 명령어와
   "라그나로크 카드확률 알려줘" 같은 자연어 질문이 **동시에** 해결된다 (날씨/환율처럼 명령어와
   대화가 같은 함수를 공유하는 패턴을 그대로 재사용).
4. **어비스홀 타이머**(`/어구`)는 RAG 검색이 아니라 시간 계산 로직이라 별도 함수로 분리하는 게
   맞다 - 원본 `abyss_manager.py` 확인 필요.
5. **안전제련**(`/안전제련`)은 확률 계산기 성격이라 `card_probabilities_module.py`처럼
   순수 계산 로직일 가능성이 높다 - 이것도 원본 확인 후 이식하면 됨.

**제안**: 1번(데이터 소스)부터 정하고 시작하는 게 순서상 맞다. 원본 벡터DB/jsonl을 서버에서
가져올 수 있으면 그걸 쓰고, 없으면 스크래퍼를 다시 돌려야 해서 시간이 걸린다.

## 아직 안 된 것 (TODO)
- [x] `/잔고` 조회 + 관리자 `/머니설정` 잔액 강제 조정 기능 추가
- [x] 미니게임 7종(바카라/블랙잭/드래곤타이거/가위바위보/주사위/슬롯머신/다이스포커) 이식 — 결과 표시는 텍스트로 단순화 (이미지 에셋 없음)
- [x] 운세(`core/fortune.py`, 원본 그대로) / MBTI(`core/mbti.py`, 새로 작성 - 원본 미확보) 이식
- [x] 로또는 폐기하기로 함 (스케줄 추첨 방식이라 성격이 다름)
- [ ] 라그나로크M RAG 연동 — 방향성만 정리됨, 데이터 소스 확보부터 필요 (위 섹션 참고)
- [x] 개미소리(`core/ant_voice.py`, 원본 그대로 - 테스트 완료) / 위성사진(`core/satellite.py`, 새로 작성 - 미검증) 이식
- [ ] `core/katalk_bridge.send_image()`의 브릿지 API 계약(엔드포인트/payload) 실제 확인 - 추정으로 구현함
- [ ] 기타 명령어(카톡통계, 쿠폰, 오늘대화요약 등) 이식 여부 결정
- [x] `core/money_system.py` 원본 소스로 교체 완료
- [x] `core/discord_channel_log.py` 역할 → `core/conversation_store.py`(SQLite)로 흡수 완료
- [x] `ai/rag_engine.py`의 tool_calls 실행 루프 완성 (날씨/환율/주식 도구 연동과 함께)
- [x] `config/mcp_servers.yaml`에 BookOasis MCP 서버 접속정보 확정 (`root@192.168.0.31`, 컨테이너 `bookoasis`, `/app/tools/mcp_server.py`)
- [x] systemd 서비스 파일 (`deploy/discord-bot-v2.service`)
- [x] 새 GitHub 저장소 초기 커밋 스크립트 (`scripts/init_new_repo.sh`)
- [ ] 포링푸드 쪽 파서를 새 저장 방식(SQLite, `storage/conversations.db`)에 맞춰 업데이트 — 기존 JSONL을 읽던 방식은 더 이상 안 맞음
- [ ] 디스코드→카톡(봇 답장을 실제 카톡방으로) 전송 엔드포인트 확인/구현 — 검토한 브릿지 코드는 카톡→디스코드 단방향만 구현되어 있음
- [ ] `KATALK_LINKED_CHANNEL_IDS`에 브릿지의 실제 스레드 ID 목록(방별 매핑 + 기본 스레드) 채워넣기
