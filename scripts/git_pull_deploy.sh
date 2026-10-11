#!/usr/bin/env bash
# 서버에서 실행: git clone/pull 기반 배포 (기존 봇에서 curl 개별 다운로드 방식의 후속 채택 방식)
set -euo pipefail

cd "$(dirname "$0")/.."

git pull origin main

if [ -d venv ]; then
  source venv/bin/activate
else
  python3 -m venv venv
  source venv/bin/activate
fi

pip install -r requirements.txt

# env.example에 새 변수가 생겼으면 .env에 기본값으로 채운다 (기존 값은 그대로, 원래 .env는 .env.bak.*로 백업).
# 끄려면: SYNC_ENV=0 ./scripts/git_pull_deploy.sh
if [ "${SYNC_ENV:-1}" != "0" ] && [ -f env.example ]; then
  python scripts/sync_env.py --apply || echo "[경고] .env 동기화 실패 - 수동 확인: python scripts/sync_env.py"
fi

# 실제 서버에 등록된 유닛 이름(discord_bot_v2.service)과 맞춘다. 다르면 SERVICE_NAME으로 덮어쓴다.
SERVICE_NAME="${SERVICE_NAME:-discord_bot_v2.service}"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true
echo "deployed."
