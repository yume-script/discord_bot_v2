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
- 카톡 메시지 로그는 `core/katalk_bridge.log_message()`가 방(room_id) 단위 JSONL로 저장한다.
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

## 아직 안 된 것 (TODO)
- [x] `core/money_system.py` 원본 소스로 교체 완료
- [ ] `core/discord_channel_log.py`: 순수 디스코드 채널 로그 저장 (자율 응답 맥락용, 카톡 로그와 분리 - `DISCORD_LOG_CHANNEL_IDS`)
- [ ] `ai/rag_engine.py`의 tool_calls 실행 루프 완성
- [ ] `config/mcp_servers.yaml`에 실제 MCP 서버 등록 (예: BookOasis mcp_server.py)
- [x] systemd 서비스 파일 (`deploy/discord-bot-v2.service`)
- [x] 새 GitHub 저장소 초기 커밋 스크립트 (`scripts/init_new_repo.sh`)
- [ ] 포링푸드 쪽 파서를 새 `katalk_log` JSONL 포맷에 맞춰 업데이트
- [ ] 디스코드→카톡(봇 답장을 실제 카톡방으로) 전송 엔드포인트 확인/구현 — 검토한 브릿지 코드는 카톡→디스코드 단방향만 구현되어 있음
- [ ] `KATALK_LINKED_CHANNEL_IDS`에 브릿지의 실제 스레드 ID 목록(방별 매핑 + 기본 스레드) 채워넣기
