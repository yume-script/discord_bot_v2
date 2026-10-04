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

# 실제 서버에 등록된 유닛 이름(discord_bot_v2.service)과 맞춘다. 다르면 SERVICE_NAME으로 덮어쓴다.
SERVICE_NAME="${SERVICE_NAME:-discord_bot_v2.service}"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl --no-pager --lines=0 status "$SERVICE_NAME" || true
echo "deployed."
