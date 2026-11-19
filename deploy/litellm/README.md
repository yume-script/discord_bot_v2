# 디스코드 봇 MCP 일부를 LiteLLM MCP 게이트웨이로

봇이 직접 띄우던 MCP 서버 6개(youtube, tautulli, plex, korea_weather, bookoasis, docker_bookoasis)를 같은 서버의
LiteLLM(도커)이 띄우고, 봇은 LiteLLM의 `/mcp/` 하나로 붙는다. 봇 서버의 파일·DB를 보는 서버(filesystem, sqlite,
memory, docker_local, server_status, llm_usage)는 밖으로 내보내지 않게 봇에 그대로 둔다.

```
봇 ──HTTP──> LiteLLM /mcp/ ──stdio──> youtube / tautulli / plex / korea_weather / bookoasis / docker_bookoasis
봇 ──stdio──> filesystem / sqlite / memory / docker_local / server_status / llm_usage   (그대로)
```

LiteLLM 컨테이너 안에서 MCP 서버를 띄우므로 포트를 새로 열지 않는다. 대신 LiteLLM 이미지에 node·uv·ssh를 넣는다.

## 1. LiteLLM 이미지에 도구 넣기
- `Dockerfile`: 쓰던 LiteLLM 이미지에 없는 것만 더함 - openssh-client, uv(uvx - 공식 uv 이미지에서 복사, 이미지에 pip가 없음) (main-latest는 Chainguard/Wolfi라 node·npx가 이미 있음, 다시 깔면 openssl 패키지가 부딪힘)
- 시크릿(TAUTULLI_API_KEY 등)은 `mcp.env` 대신 LiteLLM compose의 `environment:`에 직접 넣어도 된다 (그때는 override의 `env_file` 두 줄을 지운다)
- `docker-compose.override.example.yml`을 LiteLLM compose 옆에 `docker-compose.override.yml`로 두고 이미지 태그·서비스 이름을 맞춘다
  - plex-mcp-server 폴더, bookoasis용 ssh 키·known_hosts를 읽기 전용으로 붙인다
- `mcp.env.example` -> `mcp.env`로 복사해 값을 채운다 (봇 .env의 TAUTULLI_API_KEY, PLEX_TOKEN, PUBLIC_DATA_API_SECRET)

- ssh로 붙는 두 서버 때문에 두 가지가 더 필요하다 (override 예시에 들어 있음)
  - `LITELLM_MCP_STDIO_EXTRA_COMMANDS=ssh`: LiteLLM은 deno·docker·node·npx·python·uvx만 MCP stdio로 띄운다 - bookoasis(`ssh …`)가 `'ssh' is not in the allowlist`로 막힘
  - `ssh_config` -> `/root/.ssh/config`: docker_bookoasis는 paramiko로 붙어 기본 이름이 아닌 키(`id_ed25519_bookoasis`)를 못 찾음 (`No authentication methods available`)
  - 192.168.0.31이 서버의 `/root/.ssh/known_hosts`에 있어야 한다 (`ssh-keyscan 192.168.0.31 >> /root/.ssh/known_hosts`)
  - 환경변수·볼륨을 바꾼 뒤에는 `docker restart`가 아니라 `docker compose up -d`로 컨테이너를 새로 만든다

## 2. LiteLLM 설정
- `mcp_servers.litellm.yaml`의 `mcp_servers:` 부분을 LiteLLM `config.yaml`에 붙여 넣는다
- `docker compose build litellm && docker compose up -d litellm`
- 봇이 이미 쓰는 LiteLLM 키(봇 .env의 `LITELLM_API_KEY`)가 이 MCP 서버들을 쓸 수 있게 한다 (마스터 키면 그대로, 가상 키면 MCP 권한 추가)
- 확인: LiteLLM 관리 화면의 MCP 메뉴, 또는 MCP Inspector로 `http://127.0.0.1:4000/mcp/` (헤더 `x-litellm-api-key: Bearer <키>`)
  - 도구 이름이 `plex-get_library_stats`처럼 서버 이름 앞머리로 나오면 정상 (봇이 앞머리를 뗀다)
  - 처음 띄울 때 uvx·npx가 패키지를 받느라 잠깐 걸린다

## 3. 봇 전환
1. 봇 `.env`의 `LITELLM_BASE_URL`·`LITELLM_API_KEY`를 그대로 쓴다 (따로 넣을 것 없음)
2. `config/mcp_servers.yaml` 맨 아래 `litellm:` 주석을 풀고, 위의 같은 이름 서버 6개를 지운다
3. 봇 `.env`에 `MCP_CONNECT_TIMEOUT_SEC=120` (LiteLLM이 도구 목록을 줄 때 6개 서버를 다 띄워 봐서 처음엔 30초를 넘기기 쉽다)
4. `systemctl restart discord_bot_v2` - 로그에 `LiteLLM MCP 게이트웨이로 붙은 서버: [...]`

되돌리기: 2번을 원래대로 (yaml만 바꾸고 재시작).

## 봇이 하는 일 (ai/mcp_manager.py)
- `url`·`headers`의 `${VAR}`도 .env 값으로 (키를 yaml=git에 적지 않게)
- 게이트웨이 도구 이름 `plex-get_library_stats` -> 서버 `plex`의 `get_library_stats` (구분자 `-`·`__`·`_`·`.`·`/`, 긴 서버 이름부터)
- `admin_only_servers`의 도구는 관리자 전용 (지금의 `admin_only: true`와 같게), 어느 서버 것인지 모르는 도구도 관리자 전용 서버가 있으면 관리자 전용
- `server_session("plex")`(포링푸드 지표 등)과 `scripts/new_content_notifier.py`도 게이트웨이를 거쳐 원래 이름으로 호출
