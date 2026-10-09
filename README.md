# discord_bot_v2 (아메하나)

기존 `yume-script/discord_bot`을 대체하는 새 디스코드 봇의 뼈대입니다.
기존 봇 운영 중 겪었던 문제(구조·보안·동시성·중복 구현)를 반영해 초기 구조를 잡았고,
카톡 연동은 실제 원본 코드(`app.py`, `app_config.py`, `app_kakao_handler.py`)를 확인해서
그대로 이식했습니다.

## 결정된 방향
- **페르소나**: 이 봇(디스코드/카톡)은 **"아메하나"**. 포링푸드(porning_food)는 별개 봇 인격
  **"애순이"**를 그대로 유지 — 두 프로젝트는 완전히 독립적이라 서로 페르소나가 섞이지 않는다
  (자세한 관계는 아래 TODO의 "포링푸드" 항목 참고).
- 카톡 로그 / 벡터DB / 회원상태: **초기화** (이관하지 않음, `storage/`는 빈 상태로 시작)
- 카카오톡 연동: **유지** (디스코드 채널 릴레이 방식 - 아래 "카톡 연동" 참고)
- 포링푸드(porning_food): **봇으로 합침** (`poring_food/`, 아래 "포링푸드" 참고 - 별도 프로젝트/cron/`/mnt/poring_food` 폴더 없음)
- BookOasis 대화방 플러그인: **폐기** (Firebase 브릿지 관련 코드 없음)
- 이미지 생성 백엔드: AI Horde만 사용 (구글 코랩 연동은 불편해서 제외)

## 구조
```
config/     설정 단일 진입점 (.env, mcp_servers.yaml)
core/       UserRef, 자율 응답 로직, 카톡 닉네임 파싱, 카톡 답장 전송, 동시성 워커풀
ai/         LLM 클라이언트, MCP 매니저, RAG 엔진, 프롬프트, 이미지 생성 엔진
cogs/       디스코드 이벤트/명령어 (1기능 1파일)
storage/    런타임 데이터 (git에 커밋되지 않음 - .gitignore 확인)
scripts/    배포/저장소 초기화 스크립트
deploy/     systemd 유닛 파일
```

## 새 GitHub 저장소로 초기 커밋
1. GitHub에서 빈 저장소를 먼저 만든다 (README/LICENSE 자동생성 없이 완전히 빈 상태로 - 이미 이 프로젝트에 README가 있음).
2. `./scripts/init_new_repo.sh git@github.com:<계정>/<새 저장소>.git` 실행.
   - `.env`가 실수로 커밋되지 않도록 이중으로 체크한다 (`.gitignore`가 1차 방어, 스크립트가 2차 확인).
   - `git init` → `git add .` → 초기 커밋 → `origin` 등록 → `git push -u origin main` 순서로 진행.

## MCP 서버 설정 (`config/mcp_servers.yaml`)
- 이 파일은 git에 커밋되므로 **API 키/토큰을 직접 적지 않는다.** `PLEX_TOKEN: "${PLEX_TOKEN}"`처럼
  자리표시자로 쓰고 실제 값은 `.env`에 둔다 (`ai/mcp_manager.py`의 `prepare_server_config`가 치환,
  `.env`에 없으면 그 서버만 건너뛴다).
- `admin_only: true`를 붙인 서버(`filesystem`, `docker_local`, `docker_bookoasis`, `sqlite`)의 도구는
  이름과 상관없이 관리자만 실행할 수 있다 - `/mnt` 아래 `.env`, 컨테이너 환경변수, 전체 대화 로그처럼
  "조회"만으로도 민감한 정보에 닿기 때문이다.
- **위험한 작업은 관리자도 확인 후 실행**: 서버 상태를 바꾸는 도구(`core/tool_policy.py` 기준 -
  컨테이너 정지, 파일 쓰기, 데이터 삭제 등)는 관리자 대화에서도 바로 실행되지 않는다. 봇이 실행할
  도구와 인자를 보여주고, 같은 채널에서 그 관리자가 3분 안에 `확인`이라고 답해야 실행된다(`취소`로
  취소). 관리자 대화에서도 LLM이 유튜브 자막·메모리 같은 외부 텍스트를 읽기 때문에, 거기 섞인
  지시로 위험한 작업이 실행되는 걸 막기 위한 장치다 (`core/pending_actions.py`).

## 서버 배포 (systemd)
1. 서버에 저장소를 clone하고 `.env`를 채운다.
2. `deploy/discord_bot_v2.service`를 서버 환경(`User`, `WorkingDirectory`, `ExecStart` 경로)에 맞게 수정한 뒤:
   ```bash
   sudo mkdir -p /var/log/discord-bot-v2 && sudo chown discordbot:discordbot /var/log/discord-bot-v2
   sudo cp deploy/discord_bot_v2.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now discord_bot_v2.service
   ```
3. 이후 업데이트 배포는 `scripts/git_pull_deploy.sh`가 `git pull` → 의존성 설치 → `systemctl restart discord_bot_v2.service`까지 처리한다 (서비스명이 위 유닛 파일과 일치해야 함).

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
  실제 카톡방에도 내보낸다. **원본 `katalk_webhook.py` 소스를 확보해서 그대로 이식 완료** —
  엔드포인트(`{KATALK_BRIDGE_URL}/reply`, 원본 하드코딩 값 `http://192.168.0.50:3000/reply`와
  정확히 일치)와 payload 형식(`{"type": "text"|"image", "room": room_id, "data": ...}`)을
  전부 원본과 동일하게 맞췄고, payload 구조까지 테스트로 검증했다. 원본은 curl 서브프로세스로
  쐈는데 여기서는 프로젝트 전역에서 쓰는 httpx로 동일한 JSON POST를 보낸다 (기능은 동일).
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

## 보조 디스코드 계정 (애순이 / 소라)
- 봇 프로세스는 하나다. 메시지 수신/명령어/판단은 아메하나 계정이 전부 하고, 애순이 페르소나로 정해진 답만
  두 번째 봇 계정(`AESUN_BOT_TOKEN`, `core/side_accounts.py`)으로 올린다: 카톡 연동 채널의 애순이 답장(디스코드 쪽
  표시, 원래 메시지에 답장으로), 포링푸드 매시 일지와 연재 드라마 장면.
- 애순이 계정은 이벤트를 처리하지 않아서 중복 응답이 없고, 명령어 등록이나 특수 권한(privileged intent)이 필요 없다.
  아메하나는 애순이 계정이 올린 메시지를 자기 메시지처럼 무시한다.
- 토큰이 없거나 로그인/전송이 실패하면(채널 미초대 등 403) 예전처럼 아메하나 계정으로 보낸다.
- 애순이 봇 초대 권한: View Channel / Send Messages / Send Messages in Threads / Read Message History
  (카톡 연동 채널이 스레드면 그 스레드에도 접근할 수 있어야 한다).
- 비공개 채널 권한: `venv/bin/python scripts/grant_channel_access.py` - 봇마다 서버/채널 접근을 진단하고, 서버에 없는 봇은
  초대 링크를 출력(초대 승인은 서버 관리자가 직접), 서버엔 있는데 채널이 안 보이는 봇은 권한 관리가 있는 봇이 채널 권한에
  추가(보기/보내기/기록 보기). `--check`는 진단만.
- 소라(`SORA_BOT_TOKEN`)는 지금 맡은 일 없이 로그인만 해 둔다(온라인 표시, `SORA_BOT_ACTIVITY`로 상태 메시지).
  나중에 일을 맡길 땐 `side_accounts.sora.send(...)`를 쓰면 된다.

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
  넣어준다 — 아메하나가 직전 대화 내용을 참고해서 답하게 하려는 목적. 카톡/디스코드 모두 같은
  구조를 쓴다.
- 봇 자신의 답장(`direction='out'`)도 저장되기 때문에, 맥락에는 "무슨 말을 들었고 내가 뭐라고
  답했는지"가 둘 다 들어간다.

## 자율 응답 (시간 기반, 카톡/디스코드 공용)
- `core/autonomous_reply.py`가 기존 봇(`app.py`의 `_handle_auto_response`/`_should_skip`,
  호출어 감지)을 그대로 이식한 곳. 로직 값(호출어, 쿨다운, 확률)은 바꾸지 않았다.
- **호출어**: "하나야"가 있으면 무조건 응답. "아메하나야"/"아메하나"/"하나"도 넣어봤는데
  "하나"가 부분 문자열로 걸리다 보니 "아메리카노 하나 주세요", "오늘 하나만 살게요" 같은
  무관한 대화에도 반응하는 오탐이 심해서, 최종적으로 "하나야" 하나로만 좁혔다.
- **이어지는 대화**: 호출어로 부른 사람은 그 뒤 `CALL_FOLLOWUP_SEC`(기본 300초 = 5분) 동안
  호출어 없이 말해도 응답한다. 같은 채널의 같은 사람(카톡은 같은 방의 같은 회원)에게만 적용되고,
  말할 때마다 창이 다시 5분으로 늘어난다. `0`이면 끈다.
- **일반 메시지**: `AUTO_REPLY_TRIGGER_KEYWORDS`(기본 "아메하나,똑똑,안녕")가 있으면 바로,
  없으면 쿨다운(`AUTO_REPLY_COOLDOWN_SEC`, 기본 60초) + 확률(`AUTO_REPLY_PROBABILITY`, 기본 3%)을
  둘 다 통과해야 참견.
- **상태 공유**: 마지막 참견 시각과 활성 채널 집합이 모듈 전역이라, 카톡이든 디스코드든 같은
  `on_message`(`cogs/chat.py`)를 타기 때문에 자연히 공유된다 — 기존 봇과 동일한 동작.
- **직전 메시지가 봇이면 스킵**: `channel.history()`로 확인 (`cogs/chat.py`의 `_should_skip`).
  카톡 메시지도 결국 같은 디스코드 채널의 메시지라서 별도 처리가 필요 없다.

## 포링푸드 (애순이와 동료들의 회사 일상)
- 원래 별도 저장소(`yume-script/porning_food`)의 cron 스크립트였는데 봇으로 합쳤다. 코드는 `poring_food/`,
  기본 데이터(조직도/페르소나/경쟁사)는 `poring_food/data/`, 실행 중 쌓이는 상태(현재 상태/히스토리/
  인물 상태/관계/최근 이슈/서고 점검)는 `storage/poring_food/`.
- **실행**: `cogs/poring_food.py`가 매시 `PORING_FOOD_RUN_MINUTE`분(기본 3분, KST)에 한 회차를 돌린다.
  포링푸드 코드는 동기(requests) 코드라 스레드에서 돌려서 봇을 막지 않는다. 관리자는 `/포링푸드실행`으로
  지금 바로 한 회차를 돌려볼 수 있다. 봇이 꺼져 있던 시간의 회차는 건너뛴다.
- **봇 것을 그대로 쓰는 것**: LLM(`LITELLM_*`, 모델만 `PORING_LLM_MODEL`로 따로 지정 가능), 카톡 브릿지
  (`KATALK_BRIDGE_URL`, 방은 `PORING_KAKAO_ROOM_ID`), 디스코드(`PORING_DISCORD_CHANNEL_ID`면 봇이 직접
  올림, 없으면 `PORING_DISCORD_WEBHOOK_URL` 웹훅), 북오아시스 MCP 연결, 대화 로그 DB("주문·고객 문의"/경쟁사 지표).
- **대화 중 조회**: `poring_food/tools.py`의 도구(`get_current_status`, `get_recent_history`,
  `get_all_characters_status`, `get_character_story`, `get_bookoasis_report` 등)를 아메하나/애순이가 바로
  쓴다 - 예전처럼 MCP 하위 프로세스를 띄우지 않는다.
- **애순이 겸직 - 사내 자료실 "북오아시스"**: 애순이가 방송 주인공인 시간에 봇의 bookoasis MCP로 신간/장서를
  **읽기 전용** 조회(`search_books`, `get_library_stats`만)해서, 신간 입고나 서고 연결 끊김/복구가 있을 때만
  이야기에 1~2문장 넣는다. 이야기는 카톡으로도 나가므로 기본값으로 adult 서재는 제목·통계 모두 뺀다
  (`BOOKOASIS_STORY_DB_TYPES`).
- 확률/가중치 조정값(`AESUN_SPOTLIGHT_WEIGHT`, `INTERACTION_HOURS`, `EXTERNAL_TOPIC_PROBABILITY` 등)은
  예전 이름 그대로 봇 `.env`에 둔다.
- **연재 드라마** (`poring_food/story.py`, `signals.py`): 인물들이 실제로 대화하고 그 대화가 쌓여 줄거리가 된다.
  - **장면**: `PORING_SCENE_HOURS`(기본 9,11,13,15,17,20시)마다 2~3명이 대사 4~8줄짜리 장면을 만들어
    디스코드/카톡에 올린다. 출연진은 진행 중인 줄거리의 등장인물에서 고르고(80%), 가끔은 우연한 일상 장면.
    대사 화자가 출연진이 아니면 그 장면은 버린다.
  - **기억**: 대화 원문(`dialogues.jsonl`), 두 사람 사이의 최근 사건 10개+친밀도(`relationships.json`),
    줄거리 2~4개와 "지난 이야기" 압축 요약(`story_arcs.json`) - 모두 `storage/poring_food/`.
  - **작가 회의**: 하루 한 번(`PORING_WRITERS_ROOM_HOUR`, 기본 23시) 그날 장면과 **바깥 세상 변화**를 보고
    줄거리를 진행/종결/새로 띄운다. 바깥 변화 = 광주 날씨, 오늘의 화제(뉴스/스포츠/영화), 카톡 브릿지 상태
    (공장 라인), 서버 상태(공장 설비), 생산/판매/주문 지표, 북오아시스 신간/장애(자료실), Redroid 차단(사내 보안),
    요일·월말·계절. 매 장면에도 같은 신호가 들어가서 인물들이 그날 실제 변화에 반응한다.
  - **MCP 실측 신호** (`poring_food/mcp_signals.py`): 매시 회차 시작 때 봇이 연결해 둔 MCP 서버로 한 번 조회한다.
    `korea_weather`(기상청 초단기예보, 광주 좌표) → 날씨 - 받으면 일지의 LLM 날씨 검색을 건너뛴다.
    `server_status` → 공장 설비(CPU=가동률, 메모리=작업장 혼잡도, 디스크=창고 적재율, 온도=설비 온도,
    가동시간=연속 가동, 멈춘 서비스=멈춘 라인). 평소엔 수치 한 줄, 임계값(폭염/폭우/과부하/창고 80·90%/
    과열/재부팅 등)을 넘을 때만 "사건"으로 표시한다. 실패한 서버는 그냥 빠진다.
  - **생산/판매/주문 지표** (`poring_food/metrics.py`, 매시 회차 시작 때 집계 → `storage/poring_food/metrics.json`):
    생산량 = 오늘 Plex 신규 등록 + 북오아시스 신규 권 수(이야기용 서재의 전체 권 수 - 어제 마지막 값), 판매량 = 오늘 Plex 재생 수(Tautulli,
    지금 시청 중인 수는 "매장 손님"), 주문·고객 문의 = 오늘 사람이 봇에게 보낸 메시지 수. 목표는 고정값이
    아니라 각 지표의 최근 7일 하루 평균이고, 지금 시각까지로 비례 환산해서 바쁨/평소/한산을 정한다.
    이야기에는 개수만 들어간다(제목 없음). 조회 실패 시 같은 날의 직전 값을 쓴다.
  - **매시 일지**에도 진행 중인 줄거리와 그 인물이 오늘 겪은 장면을 넣는다.
  - 대화 중 "포링푸드 요즘 무슨 일 있어?"는 `get_poring_story` 도구로 답한다.
  - 끄려면 `PORING_STORY_ENABLED=0` (예전 "우연한 마주침" 요약 방식으로 돌아감).
- **인물 상태와 기억** (`poring_food/life.py`, `memory.py`): 페르소나만이 아니라 "상태"가 있어서 사건이 다음 행동으로 이어진다.
  - 상태(`life_state.json`): 감정 6축(행복/스트레스/외로움/분노/설렘/자신감), 체력, 직무 만족, 돈, 목표.
    매시 규칙으로 바뀐다(근무·휴식·수면, 기준값으로 서서히 회복, 25일 월급·1일 월세·식비·여가비, 잔고 부족 스트레스,
    월말 마감/금요일/비/설비 이상 같은 바깥 신호). 같은 시간에 두 번 돌아도 한 번만 반영.
  - 사건 반영: 일지/장면 LLM이 JSON으로 같이 돌려준 `state_change`/`state_changes`를 한 번에 ±0.2까지만 반영
    (추가 LLM 호출 없음). 돈은 LLM이 못 바꾼다(규칙으로만).
  - 행동 연결: 개인 시간 일정의 가중치를 상태로 조정한다 (스트레스↑ → 포장마차, 체력↓ → 집 휴식, 외로움↑ → 친구/카페,
    설렘↑ → 데이트, 돈 부족 → 쇼핑/맛집↓). 애순이 일정에도 같은 규칙.
  - 기억(`memories.jsonl`): 경험을 한 줄 기억(중요도 1~10)으로 압축해 인물별로 쌓고, 최근성+중요도+지금 상황 관련성으로
    골라 일지/장면 프롬프트에 넣는다. 파일이 커지면 인물별 최근 150개 + 중요한 기억(60일)만 남긴다.
  - 조회 도구(`get_current_status`)에도 "지금 컨디션"이 같이 나온다.
- **LLM 에이전트 (3단계)** (`poring_food/agents.py`, `world.py`, `data/agents.json`): 스스로 판단하며 사는 인물.
  1번 애순이, 2번 소라(포링푸드 1층 카페 '오후세시' 사장, 새 인물) - 각자 디스코드 봇 계정.
  + 봇 토큰 없는 에이전트 10명: 김동식/오상식/이지안/오동백/권민우(포링푸드), 성덕선/조이서/리정혁(에린 로지스틱스),
  박새로이(뒷골목 포장마차 '단밤' 사장)/길라임(동네 헬스장 트레이너, `data/gwangju_dongne_organization.json`).
  + 회사 밖 사람들 5명: 김선영(애순이 엄마)/한지평(라그M 길드장 '포링사랑')/강단이(출판사 편집자, 소라 동기)
  (`data/aesun_circle_organization.json`), 서달미(편의점 알바)/김정봉(이지안 옆집 수험생)(동네 주민).
  채널 웹훅 하나로 이름/아바타를 바꿔 말한다(웹훅은 `PORING_AGENT_WEBHOOK_URL` 또는 카제 봇 `KAJE_BOT_TOKEN`/아메하나가
  채널 "웹후크 관리" 권한으로 찾거나 만듦, 안 되면 아메하나가 "**이름**: …"으로 대신).
  토큰 없는 에이전트는 `decide_every`(기본 3)시간마다 판단하고, 사건/메시지를 받으면 바로 깨어난다(그 사이엔 최근 판단을 이어감).
  최대 `PORING_AGENT_MAX`명(기본 20).
  - 매시: 지각(시각/날씨/바깥 신호, 평소 루틴, 자기 상태와 기억, 받은편지함의 사건/소문/메시지, 애순이는 지난 회차 이후
    카톡 대화) → 판단(어디서 무엇을, 속마음, 계획, 마음 변화, 기억, 연락) → 필요하면 다른 에이전트와 대화.
  - 대화: 두 에이전트가 한 턴씩, 턴마다 말하는 사람의 LLM이 자기 상태/기억만 보고 말한다(상대 속마음은 모름).
    각자 자기 디스코드 계정(애순이/소라 봇)으로 `PORING_AGENT_CHANNEL_ID`(기본 포링푸드 방송 채널)에 올린다.
    시간당 1번, 하루 최대 `PORING_AGENT_MAX_CONVOS_PER_DAY`번. 상대가 자는 시간이면 메시지만 남기고, 깨어나면 읽는다.
  - 세계 엔진: 그날 처음(6시 이후) LLM이 사건 3~6개를 시각별로 계획해 두고 에이전트에게는 숨긴다. 시각이 되면 당사자에게만
    전달(회사 내부의 불확실한 일은 먼저 소문, 2시간 뒤 사실로). 회사/동네 사건은 그곳 배경 인물 상태에도 반영되고
    "오늘 회사/동네에서 생긴 일"로 장면/일지에 들어간다(에이전트 개인 사건은 당사자만 앎).
  - 작가 회의 줄거리와 작가가 쓰는 장면에는 에이전트를 넣지 않는다(에이전트는 미래를 모르고, 스스로 말한다).
    에이전트가 일지 주인공이면 줄거리 대신 "이번 시간 실제로 한 일과 생각"으로 쓴다.
  - 엿듣기/소문: 대화가 끝나면 같은 곳에 있던 사람(직접 만난 대화)이나 같은 회사 동료에게 확률적으로
    "누구랑 누가 이런 얘기를 하더라"가 받은편지함 소문으로 전해진다(최대 2명, 메신저 대화는 잘 안 새고 중요한 얘기일수록
    잘 퍼짐). 받은 사람은 그 소식으로 깨어나 판단하고, 또 다른 사람에게 옮길 수 있다. 추가 LLM 호출 없음.
  - 회사 단톡방(`PORING_GROUP_CHAT_CHANNEL_ID`, 기본 기존 포링푸드 이야기 채널): 포링푸드 소속 에이전트는 판단할 때
    단톡방 최근 글(`group_chat.jsonl`)을 보고, 공지/질문/잡담/답이 있으면 `group_post`로 올린다(시간당 2개, 하루
    `PORING_GROUP_MAX_POSTS_PER_DAY`개). 모두가 보는 곳이라 비밀/험담은 안 쓰게 하고, 이름이 불린 동료는 바로 깨어난다.
    토큰 없는 인물은 그 채널 웹훅으로(`PORING_GROUP_WEBHOOK_URL` 또는 자동 생성).
  - 동네 단톡방: `agents.json`의 `"groups": ["동네 단톡방"]` 인물(애순이/소라/박새로이/길라임/이지안/서달미/김정봉)이
    같은 방식으로 올린다. 채널은 `PORING_TOWN_CHAT_CHANNEL_ID`, 비우면 에이전트 대화 채널에 "🏘️ 동네 단톡방" 머리말을 붙여서.
    `group_post`는 `{"group": "...", "text": "..."}`로 어느 방에 쓸지 고른다(방마다 하루 상한 따로).
  - 단골 가게 소문: `"hangouts"`(카페 오후세시/포장마차 단밤/동네 헬스장/동네 편의점)가 겹치는 사람에게도 대화가
    "○○에서 들었는데…"로 전해진다(회사가 달라도 단골끼리 말이 돎).
  - 근황 요약(`PORING_AGENT_STATUS_DIGEST`, 기본 켬): 매시 그 시간에 새로 판단한 인물들이 어디서 뭘 하는지(+속마음 한 줄)를
    "동네 소식" 이름으로 에이전트 대화 채널에 한 메시지로 올린다 - 대화/단톡방에 안 나오는 외부 인물의 하루도 보인다.
  - 관리자 개입(세계 밖에서 조종, 에이전트는 관리자가 한 줄 모름):
    `/포링푸드사건 대상 내용 [제목] [바로반영]` - 세계 엔진에 지금 사건을 넣는다(대상: 에이전트/회사/동네, 쉼표로 여럿,
    자동완성). 회사/동네 사건은 소속 에이전트 전원과 배경 인물 장면/일지에도 들어간다.
    `/포링푸드메시지 대상 내용 [보낸사람] [바로반영]` - 에이전트 받은편지함에 "보낸사람에게서 온 메시지"로 넣는다(비우면 '누군가').
    바로반영(기본 켬)이면 일지/방송 없이 에이전트 회차만 바로 돌아, 받은 사람이 이번 시간에 이미 판단했어도 다시 판단하고
    필요하면 이번 시간 대화를 한 번 더 한다. 자는 에이전트는 깨어나서 읽는다.
  - 카톡 애순이 = 에이전트 애순이: 카톡 대화가 다음 회차 지각/기억으로 들어가고, 카톡 프롬프트에는 지금 실제 상황이 들어간다.
  - 판단에 실패하면(LLM 장애/형식 오류) 그 시간은 평소 루틴(규칙 일정)대로 - 방송이 끊기지 않는다.
- **인물 이름**: 애순이를 뺀 포링푸드/에린 로지스틱스 인물 이름을 한국 드라마 인물 이름으로 바꿨다(예: 오크 히어로 → 오상식,
  에드가 → 김동식). 몬스터 몸 묘사(촉수/가시/모래 등)는 사람에 맞게 고쳤다. 서버에 쌓인 기존 기록은 처음 한 번
  `poring_food/rename_migration.py`가 새 이름으로 바꾼다(`storage/poring_food/.names_v2_migrated`).

## Redroid 패키지 감시 알림
- **감시/자동 삭제는 cron**이 한다: `scripts/redroid_check_packages.sh`가 15분마다 Redroid(192.168.0.50)의
  서드파티 앱 목록을 확인하고 허용 목록(카카오톡, Uptodown)에 없는 앱을 지운다. 봇이 재시작되거나
  죽어도 감시는 멈추지 않고, 봇에는 redroid 접속 권한이 없다.
  ```
  */15 * * * * /bin/bash /mnt/discord_bot_v2/scripts/redroid_check_packages.sh
  ```
- 스크립트는 **변동이 있을 때만** 결과를 `/mnt/redroid_watch/events.jsonl`에 JSON 한 줄로 추가한다
  (같은 이유로 계속 실패하는 삭제는 처음 한 번만 기록).
- **봇이 알린다**: `cogs/redroid_watch.py`가 1분마다 새 줄을 읽어서 LLM이 쓴 아메하나 말투 메시지로
  `REDROID_NOTIFY_CHANNEL_ID` 채널에 올린다. 보안 알림이라 LLM 문장에 패키지 이름이 빠지면 정해진
  문장 틀로 대신 보내고, 메시지 끝에는 실제 결과를 작은 글씨로 항상 붙인다. 봇이 꺼져 있던 동안의
  결과는 켜진 뒤 순서대로 올린다.

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
  일일 제한 횟수를 원본 값 그대로 이식했다 (`core/game_engine.py`).
- **배당 재조정**: 원본 배율 그대로면 기대 회수율이 주사위 142%, 슬롯머신 370%, 바카라('짝' 고정)
  105%로 100%를 넘어서 걸기만 해도 머니가 무한히 불어났다. 주사위(더블 2.5배 / 8 이상 1.4배 /
  7은 절반 회수), 슬롯머신(2개 일치 1.2배, 3개 일치는 희귀도 비례 약 2~63배), 바카라(홀 2배 /
  짝 1.8배)만 조정해서 모든 게임의 회수율이 100% 이하(약 89~97%, 가위바위보만 100%)가 되게 했다.
- **딱 하나 다른 점**: 원본은 PIL로 카드/주사위/슬롯 이미지를 그려서 보여줬는데, 그 이미지
  에셋(`game_asset_manager.py`가 참조하는 카드·주사위 그림 파일들)을 이 프로젝트로 가져오지
  못해서 **결과를 텍스트로 단순화**했다. 슬롯머신은 원본이 라그나로크M 카드 RAG 데이터(외부
  jsonl)에 의존했는데 그것도 없어서 고정 이모지 심볼(🍒🍋🔔⭐7️⃣)로 대체했다 — 희귀
  심볼일수록 3연속 적중 시 배당이 커지는 규칙은 유지(배율 값은 위 재조정 참고).
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
  `ai/rag_engine.py`(아메하나에게 자연어로 직접 물어볼 때)가 전부 `ai/local_tools.py`의
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
- **MBTI** (`core/mbti.py`) — 원본 `mbti_system.py`를 확보해서 그 로직으로 교체했다 (이전에
  못 찾아서 "자기 신고형 등록/조회"로 추측 구현했던 버전은 폐기). `/mbti [대상]`(대상 생략 시
  본인)을 치면, **같은 방/채널 안에서 그 사람이 보낸 최근 대화(최대 100개, SQLite
  `conversation_store`에서 조회)를 모아 LLM이 MBTI를 분석**해준다. 메시지가 5개 미만이면
  분석 불가 안내가 나온다 (원본과 동일한 기준).
  원본과 다른 점 2가지: (1) 로그 소스가 katalk_log JSONL → SQLite로 바뀌었고, (2) LLM 호출이
  google-genai(Gemini 직접 호출) → 이 프로젝트가 이미 쓰는 LiteLLM 프록시 경유로 바뀌었다
  (별도 Gemini API 키 관리가 필요 없어짐). "애순이 현재 기분" 인트로는 이 프로젝트에 그
  페르소나 모듈이 없어서 뺐다.
- 둘 다 슬래시 명령어(`cogs/lookup.py`)와 텍스트 명령(`cogs/chat.py`, `/운세 양띠`, `/mbti`)
  양쪽 다 지원한다. MBTI는 대화 기록이 있는 채널(카톡 연동 채널 또는 `DISCORD_LOG_CHANNEL_IDS`)
  에서만 동작한다.

## 개미소리 / 위성사진
- **개미소리** (`core/ant_voice.py`) — 원본 `ant_voice_gen.py`를 그대로 이식. 원본 소스
  이미지(`ant_source.webp`)와 폰트(`NanumGothicCoding.ttf`)는 원본 저장소(Public)에서
  최초 1회 다운로드해 `storage/assets/`에 캐싱한다. **실제로 이미지 생성까지 테스트 완료.**
- **위성사진** (`core/satellite.py`) — **[주의] 원본 `satellite.py` 소스를 확보하지 못해서
  새로 작성했다** (저장소 파일 목록에서 링크를 못 찾음). 히마와리-9 실시간 위성사진
  (NICT 제공, `himawari8.nict.go.jp`, 무료/API 키 불필요)을 가져온다. **[검증 안 됨]** 이
  작업 환경의 네트워크 정책이 해당 도메인을 막고 있어서 실제 호출을 테스트하지 못했다 —
  서버에 배포한 뒤 `/위성사진` 직접 확인 필요.
- 카톡 쪽 이미지 전송은 `core/katalk_bridge.send_image()`가 담당한다 — 원본 `katalk_webhook.py`의
  `send_katalk_image_webhook`과 동일한 엔드포인트/payload(`{"type": "image", "room", "data": base64}`)로
  확인 완료.
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
- [x] **버그 수정**: Plex 검색 결과처럼 도구 결과가 길게 나올 때 LLM이 답변에 그대로 옮겨 담으면, 디스코드 메시지 길이 제한(2000자)을 넘겨서 `discord.errors.HTTPException: Invalid Form Body`로 전송 자체가 실패하고 "대답을 못 만들었어요" 폴백으로 빠지던 버그. `cogs/chat.py`에 `_send_chunked()` 추가해서 `_reply()`/`_send_game_result()`가 2000자 넘는 응답을 자동으로 여러 메시지로 나눠 보내도록 수정. 청크 분할 로직 테스트(짧은 텍스트/정확히 2000자/2001자/5000자 케이스) 완료
- [x] **버그 수정**: MCP 서버가 여러 개(BookOasis/포링푸드/Plex 등)일 때 도구 이름이 겹치면(예: BookOasis와 Plex 둘 다 `get_library_stats`) Gemini가 "Duplicate function declaration" 오류로 대화 자체를 거부하던 버그. `ai/mcp_manager.py`를 서버별로 따로 연결해서 모으는 방식으로 바꾸고, 이름이 겹치면 나중 서버 쪽을 `{서버이름}_{도구이름}`으로 자동 개명하도록 수정 - 서버 하나가 실패해도 그 서버만 건너뛰고 나머지는 정상 연결됨(기존엔 하나가 실패하면 전체가 빈 목록이 됐음). 시뮬레이션으로 충돌 해결 로직 검증 완료
- [x] **버그 수정**: 미니게임 텍스트 명령(`/가위`, `/주사위` 등)이 인자 없이 명령어만 쳤을 때 조건 매칭이 `"/가위 "`처럼 끝 공백을 요구해서, 공백 없는 bare 명령("/가위"만)은 아무 조건에도 안 걸려 조용히 무시되던 버그 수정 - 이제 `content.strip() in GAME_BARE_COMMANDS`로 잡아서 사용법 안내가 뜬다
- [x] **버그 수정**: 여러 텍스트 명령의 "사용법 안내"/"입력 오류"/"실패" 메시지가 디스코드에만 가고 카톡으로는 안 가던 버그 일괄 수정 (`_handle_game_rps`/`_handle_game_bet_only`/`_handle_game_choice`/`_handle_lookup`/`_handle_text_image_command`/`_safe_reply`/개미소리/위성사진/MBTI의 에러·안내 분기들 - 전부 `message.reply()` 단독 호출이라 카톡 전송이 빠져있었음). 정상 결과는 원래도 `_send_game_result`로 잘 가고 있었고, **입력이 잘못됐을 때 나오는 안내 메시지들만** 빠져 있었음.
- [x] **버그 수정**: `/날씨`, `/전국날씨`, `/환율`, `/주식` 텍스트 명령이 디스코드에만 답하고 카톡으로는 전송 안 하던 버그 (`_handle_lookup`에 카톡 전송 누락) 수정
- [x] **버그 수정**: `/그림`, `/그림스타일` 텍스트 명령이 실제 발신자(카톡 유저 포함)를 무시하고 항상 디스코드 유저로 취급하던 버그 (`_handle_text_image_command`가 `user`를 안 받고 내부에서 디스코드로 재생성) 수정 - 머니 계정이 엉뚱하게 잡히고 카톡 전송도 안 되고 있었음
- [x] `/잔고` 조회 + 관리자 `/머니설정` 잔액 강제 조정 기능 추가
- [x] 미니게임 7종(바카라/블랙잭/드래곤타이거/가위바위보/주사위/슬롯머신/다이스포커) 이식 — 결과 표시는 텍스트로 단순화 (이미지 에셋 없음)
- [x] 운세(`core/fortune.py`, 원본 그대로) / MBTI(`core/mbti.py`, 원본 확보해서 교체 - SQLite+LiteLLM으로 어댑팅) 이식
- [x] 로또는 폐기하기로 함 (스케줄 추첨 방식이라 성격이 다름)
- [ ] 라그나로크M RAG 연동 — 방향성만 정리됨, 데이터 소스 확보부터 필요 (위 섹션 참고)
- [x] 개미소리(`core/ant_voice.py`, 원본 그대로 - 테스트 완료) / 위성사진(`core/satellite.py`, 원본(기상청 KMA API) 확보해서 교체 - 이 환경 네트워크 정책상 미검증, 서버에서 확인 필요) 이식
- [x] `core/katalk_bridge.py` 원본 `katalk_webhook.py` 소스 확보해서 정확한 엔드포인트/payload로 교체 완료 (테스트로 검증)
- [x] 카톡통계(`/월간카톡`, `/카톡순위`)/오늘대화요약(`/오늘대화요약`) 이식 완료 — **[주의] 원본 소스를 못 구해서 새로 작성함** (`core/katalk_stats.py`). SQLite 대화 로그(`core/conversation_store.py`)를 유저별로 집계하는 방식으로 구현 - `/카톡순위`(오늘/이번달 TOP5), `/월간카톡`(이번 달 전체 통계), `/오늘대화요약`(LLM으로 오늘 대화 요약, 메시지 5개 미만이면 스킵). 순위/통계는 시뮬레이션 테스트로 검증 완료, 요약은 메시지 수집·프롬프트 구성까지 검증(실제 LLM 호출은 이 환경에 API 키가 없어 미검증 - mbti와 동일한 한계)
- [ ] 나머지 기타 명령어(쿠폰 등) 이식 여부 결정
- [x] `core/money_system.py` 원본 소스로 교체 완료
- [x] `core/discord_channel_log.py` 역할 → `core/conversation_store.py`(SQLite)로 흡수 완료
- [x] `ai/rag_engine.py`의 tool_calls 실행 루프 완성 (날씨/환율/주식 도구 연동과 함께)
- [x] `config/mcp_servers.yaml`에 BookOasis MCP 서버 접속정보 확정 (`root@192.168.0.31`, 컨테이너 `bookoasis`, `/app/tools/mcp_server.py`)
- [x] systemd 서비스 파일 (`deploy/discord_bot_v2.service`)
- [x] 새 GitHub 저장소 초기 커밋 스크립트 (`scripts/init_new_repo.sh`)
- [x] ~~포링푸드 쪽 파서를 새 저장 방식(SQLite)에 맞춰 업데이트~~ → **정정: 포링푸드는 애초에 카톡 로그를 안 읽는다** (전체 소스 확인 완료, `loader.py`가 자기 저장소의 조직도/이슈/페르소나 JSON만 읽음). 유일한 실제 연결은 카톡 브릿지 서버 공유(코드 공유 아님)와, 포링푸드가 `aesun_current_status.json`에 상태를 쓰는 것뿐.
- [x] **애순이 상태 조회를 파일 공유 방식에서 MCP 서버 방식으로 전환** — 포링푸드가 discord_bot_v2 폴더에 파일을 쓰는 방식(`aesun_current_status.json` 공유)은 "포링푸드가 독립된 개체"라는 원칙과 안 맞아서 폐기했다. 대신 BookOasis와 같은 패턴으로 포링푸드 쪽에 `mcp_server.py`(stdio MCP 서버, `get_current_status` 도구)를 추가하고, `config/mcp_servers.yaml`에 `poring_food` 항목으로 등록했다 — 같은 서버라 SSH 없이 바로 `python3`로 실행한다. `ai/local_tools.py`의 `get_poring_food_status()`와 `integrations/poring_food_bridge.py`(직접 파일 읽기 방식)는 제거했고, `PORING_FOOD_STATUS_JSON_PATH` 설정도 뺐다.
- [ ] **포링푸드 쪽 적용 필요** — `/mnt/user-data/outputs/poring_food_mcp_server.py`를 포링푸드 저장소에 `mcp_server.py`로 추가, `pip install mcp`, `config.py`의 `STATUS_OUT_PATH`를 포링푸드 자기 폴더 안(`os.path.join(BASE_DIR, "aesun_current_status.json")`)으로 변경 필요. 이제 discord_bot_v2 폴더에는 아무것도 안 씀.
- [x] **페르소나 분리**: 이 봇을 "아메하나"로, 포링푸드는 "애순이"로 유지하기로 결정 - 호출어(`core/autonomous_reply.py`의 `CALL_TRIGGER_WORDS`), 자율응답 기본 키워드(`AUTO_REPLY_TRIGGER_KEYWORDS`), 시스템 프롬프트(`ai/prompts.py`), MBTI 분석 프롬프트(`core/mbti.py`) 전부 "아메하나"로 교체 완료. 포링푸드 쪽 코드/설정은 건드리지 않음 (별도 프로젝트, 매시 3분 공장일지 발송은 그대로 유지)
- [x] 디스코드→카톡 전송 엔드포인트 확인/구현 완료 (`core/katalk_bridge.py`, 원본 `katalk_webhook.py` 그대로 이식)
- [ ] `KATALK_LINKED_CHANNEL_IDS`에 브릿지의 실제 스레드 ID 목록(방별 매핑 + 기본 스레드) 채워넣기
