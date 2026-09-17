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

sudo systemctl restart discord-bot-v2.service
echo "deployed."
