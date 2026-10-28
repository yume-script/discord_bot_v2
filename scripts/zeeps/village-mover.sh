#!/bin/bash
# /usr/local/sbin/village-mover.sh [--all] - 스테이징(로컬)에 쌓인 zeeps_member 새 파일을 드라이브로 올리고 로컬 사본을 지운다.
# staging.sh가 만든 village-mover.timer가 1시간마다 부른다 (결계 경보 3단계 이상이면 village-gate로 건너뜀).
# 순서: 받는 중일 수 있는 파일은 빼고(MIN_AGE분 이상 안 바뀐 것만) 목록을 정해 그 파일만 업로드
#   -> 원격 마운트(rclone rc)의 그 폴더 목록을 바로 새로 고침 -> 원격에서 같은 크기로 보이는 파일만 로컬에서 지운다
#   (그래야 합친 폴더(/mnt/zeeps_merged)에서 파일이 잠깐이라도 사라지지 않는다)
# --all: 나이 상관없이 전부 (staging.sh uninstall이 되돌리기 전에 부른다)
set -u
CONF=/etc/village/staging.conf
[ -r "$CONF" ] && . "$CONF"
STAGING=${STAGING:-/mnt/staging/zeeps_member}
REMOTE_MOUNT=${REMOTE_MOUNT:-/mnt/zeeps_member}
REMOTE=${REMOTE:-zeeps_member:}
RCLONE_CONFIG=${RCLONE_CONFIG:-/mnt/ff/db/rclone.conf}
RC_URL=${RC_URL:-http://127.0.0.1:5276}
MIN_AGE=${MIN_AGE:-30}
BWLIMIT=${BWLIMIT:-08:00,5M 01:00,off}
LIST=$(mktemp /tmp/village-mover.XXXXXX)
trap 'rm -f "$LIST"' EXIT
exec 9>/run/village-mover.lock
flock -n 9 || { echo "MOVER_SKIP reason=already_running"; exit 0; }
[ -d "$STAGING" ] || { echo "MOVER_SKIP reason=no_staging"; exit 0; }
mountpoint -q "$REMOTE_MOUNT" || { echo "MOVER_SKIP reason=remote_not_mounted"; exit 0; }

AGE=(-mmin "+$MIN_AGE"); [ "${1:-}" = "--all" ] && AGE=()
# 받는 중인 임시 파일은 빼고, 상대 경로 목록
( cd "$STAGING" && find . -type f "${AGE[@]}" ! -name '*.part' ! -name '*.tmp' ! -name '*.partial' ! -name '.~*' -printf '%P\n' ) > "$LIST"
N=$(wc -l < "$LIST")
[ "$N" -gt 0 ] || { echo "MOVER_OK files=0"; exit 0; }

rclone copy "$STAGING" "$REMOTE" --config "$RCLONE_CONFIG" --files-from-raw "$LIST" --no-traverse \
  --bwlimit "$BWLIMIT" --tpslimit 5 --transfers 3 --retries 3 --log-level ERROR
RC=$?
# 올린 파일이 든 폴더만 원격 마운트 목록을 새로 고친다 (전체 새로 고침은 수백만 개라 비싸다)
sed -n 's#/[^/]*$##p' "$LIST" | sort -u | while IFS= read -r d; do
  curl -s -m 30 -X POST "$RC_URL/vfs/refresh" -H 'Content-Type: application/json' \
    --data "$(python3 -c 'import json,sys; print(json.dumps({"dir": sys.argv[1]}))' "$d")" >/dev/null || true
done
curl -s -m 30 -X POST "$RC_URL/vfs/refresh" >/dev/null || true   # 맨 위 폴더 (최상위에 바로 놓인 파일)

MOVED=0; KEPT=0
while IFS= read -r f; do
  src="$STAGING/$f"; dst="$REMOTE_MOUNT/$f"
  [ -f "$src" ] || continue
  if [ -f "$dst" ] && [ "$(stat -c %s "$src")" = "$(stat -c %s "$dst")" ]; then
    rm -f "$src" && MOVED=$((MOVED+1))
  else
    KEPT=$((KEPT+1))      # 아직 원격에서 안 보이면 다음 회차에 다시
  fi
done < "$LIST"
find "$STAGING" -mindepth 1 -type d -empty -mmin "+$MIN_AGE" -delete 2>/dev/null
echo "MOVER_OK files=$N moved=$MOVED kept=$KEPT rclone_rc=$RC"
[ "$RC" -eq 0 ]
