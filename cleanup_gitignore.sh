#!/bin/bash
set -e
cd /mnt/discord_bot_v2

cat > .gitignore << 'GITIGNORE'
# secrets
.env
*.key
*.pem

# runtime data - never commit real user data
storage/vector_db/*
!storage/vector_db/.gitkeep
storage/nickname_detect/*
!storage/nickname_detect/.gitkeep
storage/money/*
!storage/money/.gitkeep
storage/assets/
storage/aesun_current_status.json
*.sqlite3
*.db

# python
__pycache__/
*.pyc
.venv/
venv/

# os / editor
.DS_Store
.vscode/
GITIGNORE

# 이미 추적 중인 파일들 전부 추적 해제 (로컬 파일은 그대로 남음)
git rm -r --cached venv/ 2>/dev/null || true
find . -name "__pycache__" -not -path "./venv/*" -exec git rm -r --cached {} + 2>/dev/null || true
git rm --cached storage/money/*.jsonl 2>/dev/null || true
git rm --cached storage/conversations.db 2>/dev/null || true

git add .gitignore
git add -A
git commit -m "chore: .gitignore 복원 + venv/캐시/데이터 파일 추적 해제"
git push

echo "=== 완료. 최종 상태 ==="
git status
