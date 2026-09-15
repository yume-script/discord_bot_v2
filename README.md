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

- 브릿지가 카톡 메시지를 **"카톡이름//방ID//유저ID"** 형식의 닉네임으로 인코딩해서,
  `KATALK_LINKED_CHANNEL_IDS`(기존 `TARGET_THREAD_IDS`)에 등록된 디스코드 채널에
  일반 디스코드 메시지로 올려준다. 구분자는 `NICKNAME_DELIMITER`(기본 `//`).
- `core/kakao_relay.py`의 `parse_kakao_author()`가 `message.author.name`을 파싱해서
  카톡 메시지인지, 어느 방/유저인지 판별한다 (`cogs/chat.py`의 `on_message`에서 호출).
- 봇의 답장은 디스코드 채널에도 보내고(`message.reply`), 동시에 `core/katalk_bridge.send_message()`로
  브릿지 서버(`KATALK_BRIDGE_URL`, 기존 `192.168.0.50:3000`)에 POST해서 실제 카톡방에도 내보낸다
  (기존 `katalk_webhook.send_katalk_webhook` 역할).
- 카톡 메시지 로그는 `core/katalk_bridge.log_message()`가 방(room_id) 단위 JSONL로 저장한다.
- **아직 이식 안 한 것**: 카톡 입장/퇴장 피드 메시지(`{"feedType"...}`), 닉네임 변경 알림, 채팅 머니 지급
  (기존 `app_kakao_handler.handle_kakao_features`, `money_system.py`) — 이 봇에 포함시킬지부터 결정 필요.

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

## 아직 안 된 것 (TODO)
- [ ] 카톡 입장/퇴장 피드 메시지, 닉네임 변경 알림, 채팅 머니 지급 이식 여부 결정 및 구현
- [ ] `core/discord_channel_log.py`: 순수 디스코드 채널 로그 저장 (자율 응답 맥락용, 카톡 로그와 분리 - `DISCORD_LOG_CHANNEL_IDS`)
- [ ] `ai/rag_engine.py`의 tool_calls 실행 루프 완성
- [ ] `config/mcp_servers.yaml`에 실제 MCP 서버 등록 (예: BookOasis mcp_server.py)
- [x] systemd 서비스 파일 (`deploy/discord-bot-v2.service`)
- [x] 새 GitHub 저장소 초기 커밋 스크립트 (`scripts/init_new_repo.sh`)
- [ ] 포링푸드 쪽 파서를 새 `katalk_log` JSONL 포맷에 맞춰 업데이트
- [ ] 브릿지 서버 계정이 "카톡이름//방ID//유저ID" 닉네임으로 디스코드 메시지를 올리는 쪽 동작을 새 봇 환경에서도 그대로 쓸 수 있는지 확인
