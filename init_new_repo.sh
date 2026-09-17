#!/usr/bin/env bash
# 사용법: ./scripts/init_new_repo.sh git@github.com:yume-script/discord_bot_v2.git
#   (HTTPS로 쓰려면: ./scripts/init_new_repo.sh https://github.com/yume-script/discord_bot_v2.git)
#
# GitHub에서 먼저 빈 저장소를 만들어둔 뒤 이 스크립트를 실행할 것 (README/LICENSE 자동생성 없이 완전히 빈 상태로).
set -euo pipefail

REMOTE_URL="${1:?새 GitHub 저장소 URL을 인자로 넘겨주세요. 예: ./scripts/init_new_repo.sh git@github.com:yume-script/discord_bot_v2.git}"

cd "$(dirname "$0")/.."

if [ -d .git ]; then
  echo "이미 git 저장소입니다 (.git 존재) - init은 건너뜁니다."
else
  git init
  git branch -M main
fi

# .env가 실수로 스테이징되면 시크릿이 그대로 커밋될 수 있으므로 커밋 전에 한 번 더 확인.
# (.gitignore에 .env가 이미 포함돼 있어야 하며, 여기서는 이중 안전장치로 재확인만 한다)
if git status --porcelain --ignored | grep -qE "^\?\? \.env$"; then
  echo "⚠️  .env가 추적되지 않은 상태로 감지됐습니다 (.gitignore가 정상 작동 중) - 계속 진행합니다."
fi
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "❌ .env가 이미 git에 추적되고 있습니다. 커밋을 중단합니다. 'git rm --cached .env'로 먼저 제거하세요."
  exit 1
fi

git add .
git commit -m "chore: initial scaffold for discord_bot_v2" || echo "커밋할 변경사항이 없습니다 (이미 커밋됨) - 계속 진행합니다."

git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE_URL"
git push -u origin main

echo "✅ 완료: $REMOTE_URL 에 초기 커밋을 푸시했습니다."
