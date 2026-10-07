#!/bin/bash
# 예전 포링푸드(/mnt/poring_food) → discord_bot_v2(poring_food/)로 옮기는 1회용 도우미.
# 아무것도 지우지 않는다: 상태 파일 복사, 데이터 파일 차이 확인, .env 키 매핑 안내(값은 출력 안 함)만 한다.
# 사용법: sudo bash /mnt/discord_bot_v2/scripts/migrate_poring_food.sh
set -uo pipefail

OLD="${OLD_PORING_DIR:-/mnt/poring_food}"
BOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$BOT/storage/poring_food"
DATA="$BOT/poring_food/data"

[ -d "$OLD" ] || { echo "❌ $OLD 폴더가 없습니다."; exit 1; }
mkdir -p "$STATE"

echo "== 1) 실행 상태 파일 복사 ($OLD → $STATE)"
for f in aesun_current_status.json aesun_history.jsonl characters_state.json relationships.json last_issue.json bookoasis_state.json; do
    if [ -f "$OLD/$f" ]; then
        if [ -f "$STATE/$f" ]; then
            echo "   - $f: 이미 있음 → 건너뜀 (덮어쓰려면 직접 cp)"
        else
            cp -p "$OLD/$f" "$STATE/$f" && echo "   ✅ $f"
        fi
    fi
done

echo
echo "== 2) 기본 데이터 파일 비교 ($OLD ↔ $DATA)"
echo "   (aesun_persona.json은 이번에 \"side_duty\"(북오아시스 겸직) 한 줄이 추가돼서 그 차이는 정상)"
for f in "$DATA"/*.json; do
    name="$(basename "$f")"
    if [ ! -f "$OLD/$name" ]; then
        echo "   - $name: 예전 폴더에 없음"
    elif cmp -s "$OLD/$name" "$f"; then
        echo "   ✅ $name: 같음"
    else
        echo "   ⚠️  $name: 다름 → diff \"$OLD/$name\" \"$f\" 로 확인 (서버에서 고친 내용이면 알려주세요)"
    fi
done
for f in "$OLD"/*_organization.json; do
    [ -f "$f" ] && [ ! -f "$DATA/$(basename "$f")" ] && echo "   ⚠️  $(basename "$f"): 예전 폴더에만 있음 (추가된 회사?)"
done

echo
echo "== 3) 예전 .env 키 → 봇 .env로 옮길 이름 (값은 출력하지 않음)"
if [ -f "$OLD/.env" ]; then
    grep -oE '^[A-Za-z_][A-Za-z0-9_]*' "$OLD/.env" | sort -u | while read -r key; do
        case "$key" in
            DISCORD_WEBHOOK_URL) echo "   $key → PORING_DISCORD_WEBHOOK_URL (또는 PORING_DISCORD_CHANNEL_ID를 설정해 봇이 직접 올리게)";;
            ROOM_ID)             echo "   $key → PORING_KAKAO_ROOM_ID";;
            LLM_MODEL)           echo "   $key → PORING_LLM_MODEL (봇의 LITELLM_MODEL과 같으면 생략)";;
            SEARCH_MODEL)        echo "   $key → PORING_SEARCH_MODEL";;
            LOCAL_BOT_URL)       echo "   $key → 필요 없음 (봇의 KATALK_BRIDGE_URL + /reply 사용 - 같은 주소인지만 확인)";;
            ONE_API_URL|LITELLM_MASTER_KEY) echo "   $key → 필요 없음 (봇의 LITELLM_BASE_URL/LITELLM_API_KEY 사용 - 같은 LiteLLM인지만 확인)";;
            DISCORD_BOT_V2_DB_PATH) echo "   $key → 필요 없음 (봇의 대화 로그 DB를 바로 씀)";;
            *)                   echo "   $key → 같은 이름 그대로 봇 .env에 복사";;
        esac
    done
else
    echo "   $OLD/.env 없음"
fi

echo
echo "== 4) 예전 cron 확인 (직접 지워주세요: crontab -e)"
crontab -l 2>/dev/null | grep -n "poring_food" || echo "   (root crontab에 poring_food 줄 없음)"

echo
echo "다음: 봇 .env 수정 → systemctl restart discord_bot_v2 → 디스코드에서 /포링푸드실행 으로 확인"
echo "      며칠 문제없으면 tar로 백업 후 $OLD 폴더 삭제"
