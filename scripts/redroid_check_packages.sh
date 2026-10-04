#!/bin/bash
# Redroid(192.168.0.50) 안드로이드 패키지 감시 - 허용 목록에 없는 앱을 자동 삭제한다.
# 실행 위치: 디스코드 봇 VM (192.168.0.26), cron으로 15분마다:
#   */15 * * * * /bin/bash /mnt/discord_bot_v2/scripts/redroid_check_packages.sh
#
# 알림은 직접 보내지 않는다. 변동(신규 설치/삭제 감지/자동 삭제)이 있을 때만 결과를
# EVENTS 파일에 JSON 한 줄로 추가하면, 봇(cogs/redroid_watch.py)이 읽어서 아메하나 말투로
# 디스코드에 알린다. 봇이 꺼져 있어도 감시/삭제는 계속되고, 결과는 파일에 쌓였다가
# 봇이 켜지면 순서대로 알림이 간다.
set -uo pipefail

SSH_KEY="$HOME/.ssh/redroid_watch"
LXC_HOST="spamuse@192.168.0.50"
WATCH_DIR="/mnt/redroid_watch"
PREV="$WATCH_DIR/prev_packages.txt"
LOG="$WATCH_DIR/alert.log"
EVENTS="$WATCH_DIR/events.jsonl"     # 봇의 REDROID_EVENTS_PATH와 같아야 함
FAILED_SEEN="$WATCH_DIR/failed_seen.txt"   # 이미 알린 삭제 실패 ("패키지<TAB>결과")
LOCK="/tmp/redroid_watch.lock"

# 허용 앱 (이 둘만 남겨둠)
WHITELIST=("com.kakao.talk" "com.uptodown")

SSH_OPTS=(-i "$SSH_KEY" -o BatchMode=yes -o ConnectTimeout=10)
CURRENT="$(mktemp)"
AFTER="$(mktemp)"
trap 'rm -f "$CURRENT" "$AFTER"' EXIT

# 이전 실행이 아직 끝나지 않았으면 이번 실행은 건너뛴다 (겹침 방지)
exec 9>"$LOCK"
flock -n 9 || exit 0

log()  { echo "$(date '+%F %T') $*" >> "$LOG"; }
rssh() { timeout 60 ssh "${SSH_OPTS[@]}" "$LXC_HOST" "$@"; }

list_packages() {
    rssh "docker exec redroid pm list packages -3" | sed 's/^package://' | tr -d '\r' | sort
}

is_whitelisted() {
    local pkg="$1" w
    for w in "${WHITELIST[@]}"; do
        [ "$pkg" == "$w" ] && return 0
    done
    return 1
}

list_packages > "$CURRENT"
if [ ! -s "$CURRENT" ]; then
    log "SSH 접속 실패 또는 결과 없음"
    exit 1
fi

# 이전 목록과 비교해서 추가/삭제 감지
ADDED=""
REMOVED=""
if [ -f "$PREV" ]; then
    ADDED=$(comm -13 "$PREV" "$CURRENT")
    REMOVED=$(comm -23 "$PREV" "$CURRENT")
fi

# 화이트리스트에 없는 앱 자동 삭제 - 결과는 "패키지<TAB>결과" 줄로 모은다.
# 같은 앱이 같은 이유로 계속 삭제에 실패하면 15분마다 같은 알림이 가므로, 이미 알린
# 실패(FAILED_SEEN)는 다시 기록하지 않는다(로그에는 매번 남김). 결과가 바뀌거나 성공하면 다시 알린다.
AUTO_REMOVED=""
FAILED_NOW=""
touch "$FAILED_SEEN"
while IFS= read -r pkg; do
    [ -z "$pkg" ] && continue
    is_whitelisted "$pkg" && continue
    if [[ ! "$pkg" =~ ^[A-Za-z0-9_.]+$ ]]; then
        log "패키지 이름 형식이 이상해서 건너뜀: $pkg"
        RESULT="이름 형식 이상 - 삭제 안 함"
        FAILED_NOW+="$pkg"$'\t'"$RESULT"$'\n'
        grep -qxF "$pkg"$'\t'"$RESULT" "$FAILED_SEEN" || AUTO_REMOVED+="$pkg"$'\t'"$RESULT"$'\n'
        continue
    fi
    log "비인가 앱 감지 - $pkg → 자동 삭제 시도"
    RESULT=$(rssh "docker exec redroid pm uninstall --user 0 $pkg" 2>&1 | tr -d '\r' | tail -n 1)
    log "삭제 결과 - $pkg: $RESULT"
    if [[ "$RESULT" != *Success* ]]; then
        FAILED_NOW+="$pkg"$'\t'"$RESULT"$'\n'
        grep -qxF "$pkg"$'\t'"$RESULT" "$FAILED_SEEN" && continue   # 이미 알린 실패
    fi
    AUTO_REMOVED+="$pkg"$'\t'"$RESULT"$'\n'
done < "$CURRENT"
printf '%s' "$FAILED_NOW" > "$FAILED_SEEN"

# 자동 삭제를 했으면 목록을 다시 받아서 저장한다 - 안 그러면 다음 실행 때
# 방금 지운 앱이 "삭제 감지"로 또 알림이 간다
if [ -n "$AUTO_REMOVED" ] && list_packages > "$AFTER" && [ -s "$AFTER" ]; then
    cp "$AFTER" "$CURRENT"
fi

# 변동이 있을 때만 결과를 JSON 한 줄로 기록 (값은 인자로 넘겨서 따옴표 등이 섞여도 안전)
if [ -n "$ADDED$REMOVED$AUTO_REMOVED" ]; then
    python3 - "$ADDED" "$REMOVED" "$AUTO_REMOVED" >> "$EVENTS" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone

def lines(s):
    return [x for x in s.splitlines() if x.strip()]

auto = []
for line in lines(sys.argv[3]):
    pkg, _, result = line.partition("\t")
    auto.append({"pkg": pkg, "result": result})

print(json.dumps({
    "time": datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"),
    "host": "192.168.0.50",
    "added": lines(sys.argv[1]),
    "removed": lines(sys.argv[2]),
    "auto_removed": auto,
}, ensure_ascii=False))
PY
    log "변동 기록 → $EVENTS"
fi

cp "$CURRENT" "$PREV"
