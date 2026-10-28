#!/bin/bash
# zeeps_member 앞에 로컬 스테이징을 합친다 (mergerfs) - 새 파일은 로컬 디스크에 바로 쓰고, 드라이브 업로드는 나중에 몰아서.
#
#   호스트 /mnt/zeeps_merged  = mergerfs( 1순위 /mnt/staging/zeeps_member (로컬)  +  2순위 /mnt/zeeps_member (기존 rclone 마운트) )
#   CT105 mp1: /mnt/zeeps_merged -> CT 안 /mnt/zeeps_member   (CT 안의 경로, 북오아시스 DB 경로, 짐꾼 설정은 그대로)
#   village-mover.timer: 1시간마다 MIN_AGE분 넘은 로컬 파일을 올리고 지운다 (결계 경보 3단계 이상이면 미룸)
#   기존 rclone 마운트와 그 서비스, gds2는 건드리지 않는다.
#
#   bash staging.sh install     mergerfs 설치/마운트 + 업로드 타이머 (CT105는 아직 그대로 - 안전하게 먼저 확인)
#   bash staging.sh switch      CT105 mp1을 합친 폴더로 바꾸고 CT105 재시작 (짧게 끊김 - 시청 없을 때)
#   bash staging.sh status      상태 / 로컬에 쌓인 양 / 최근 업로드 기록
#   bash staging.sh uninstall   로컬 파일을 전부 올린 뒤 CT105 mp1을 원래대로, mergerfs/타이머 제거
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
CT=${CT:-105}
CONF=/etc/village/staging.conf
mkdir -p /etc/village
if [ ! -f "$CONF" ]; then
  cat > "$CONF" <<'C'
# staging.sh / village-mover.sh 설정 (고친 뒤: systemctl restart zeeps-merged)
STAGING=/mnt/staging/zeeps_member      # 로컬 스테이징 (호스트 디스크)
REMOTE_MOUNT=/mnt/zeeps_member         # 기존 rclone 마운트 (그대로)
MERGED=/mnt/zeeps_merged               # 합친 폴더 (CT105에 이걸 연결)
REMOTE=zeeps_member:                   # rclone 원격 이름
RCLONE_CONFIG=/mnt/ff/db/rclone.conf
RC_URL=http://127.0.0.1:5276           # 기존 zeeps_member 마운트의 rc (폴더 목록 새로 고침)
MIN_AGE=30                             # 분 - 이만큼 안 바뀐 파일만 올린다 (받는 중인 파일 제외)
MINFREE=20G                            # 로컬 여유가 이보다 적으면 새 파일은 원격으로 바로
BWLIMIT="08:00,5M 01:00,off"           # 업로드: 08~01시 5MB/s, 01~08시 제한 없음
C
fi
. "$CONF"

cmd=${1:-}
rclone_unit() { local p; p=$(pgrep -f "rclone mount ${REMOTE}" | head -1); [ -n "$p" ] && ps -o unit= -p "$p" | tr -d ' '; }

case "$cmd" in
install)
  command -v mergerfs >/dev/null || apt-get install -y mergerfs
  mountpoint -q "$REMOTE_MOUNT" || { echo "기존 rclone 마운트($REMOTE_MOUNT)가 안 보임 - 중단"; exit 1; }
  RU=$(rclone_unit || true)
  mkdir -p "$STAGING" "$MERGED"
  install -m 0755 "$HERE/village-mover.sh" /usr/local/sbin/village-mover.sh
  cat > /etc/systemd/system/zeeps-merged.service <<U
[Unit]
Description=zeeps_member + local staging (mergerfs, scripts/zeeps/staging.sh)
After=network-online.target ${RU}
Wants=${RU:-network-online.target}
[Service]
Type=simple
ExecStartPre=/bin/sh -c 'for i in \$(seq 120); do mountpoint -q $REMOTE_MOUNT && exit 0; sleep 1; done; exit 1'
ExecStart=/usr/bin/mergerfs -f -o allow_other,use_ino,category.create=ff,minfreespace=$MINFREE,moveonenospc=true,cache.files=partial,dropcacheonclose=true,fsname=zeeps_merged $STAGING=RW:$REMOTE_MOUNT=RW $MERGED
ExecStop=/bin/umount -l $MERGED
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
U
  cat > /etc/systemd/system/village-mover.service <<U
[Unit]
Description=Upload staged zeeps_member files to Drive (scripts/zeeps/village-mover.sh)
After=zeeps-merged.service
[Service]
Type=oneshot
ExecCondition=/usr/local/sbin/village-gate.sh 2
ExecStart=/usr/local/sbin/village-mover.sh
Nice=10
CPUWeight=20
U
  cat > /etc/systemd/system/village-mover.timer <<U
[Unit]
Description=Upload staged zeeps_member files every hour
[Timer]
OnBootSec=15min
OnUnitActiveSec=1h
RandomizedDelaySec=5min
Persistent=true
[Install]
WantedBy=timers.target
U
  systemctl daemon-reload
  systemctl enable --now zeeps-merged.service village-mover.timer
  sleep 2
  mountpoint -q "$MERGED" && echo "합친 폴더 OK: $MERGED (rclone 서비스: ${RU:-못 찾음})" || { echo "mergerfs 마운트 실패 - journalctl -u zeeps-merged"; exit 1; }
  a=$(ls "$REMOTE_MOUNT" | wc -l); b=$(ls "$MERGED" | wc -l)
  echo "맨 위 폴더 수: 원격 $a / 합친 폴더 $b (같아야 정상)"
  echo; echo "CT${CT}는 아직 그대로입니다. 확인 뒤 시청이 없을 때: bash $0 switch"
  ;;
switch)
  mountpoint -q "$MERGED" || { echo "합친 폴더가 안 보임 - 먼저 install"; exit 1; }
  cur=$(pct config "$CT" | awk -F': ' '/^mp1:/{print $2}')
  case "$cur" in "$MERGED,"*) echo "이미 바뀌어 있음: $cur"; exit 0 ;; esac
  case "$cur" in "$REMOTE_MOUNT,"*) ;; *) echo "mp1이 예상과 다름 ($cur) - 손으로 확인해 주세요"; exit 1 ;; esac
  echo "$cur" > /etc/village/mp1.orig
  new="$MERGED,${cur#*,}"
  pct set "$CT" -mp1 "$new"
  echo "mp1: $cur -> $new"; echo "CT${CT} 재시작..."
  pct reboot "$CT"
  sleep 20
  pct exec "$CT" -- sh -c "mount | grep ' /mnt/zeeps_member ' ; ls /mnt/zeeps_member | head -3"
  ;;
status)
  mountpoint -q "$MERGED" && echo "합친 폴더: 마운트됨" || echo "합친 폴더: 없음"
  echo "CT${CT} mp1: $(pct config "$CT" | awk -F': ' '/^mp1:/{print $2}')"
  echo "로컬 스테이징: $(find "$STAGING" -type f 2>/dev/null | wc -l)개, $(du -sh "$STAGING" 2>/dev/null | cut -f1) / 여유 $(df -h --output=avail "$STAGING" | tail -1)"
  systemctl list-timers village-mover.timer --no-pager | sed -n 2p
  journalctl -u village-mover --since "-1day" --no-pager | grep -E "MOVER_" | tail -5
  ;;
uninstall)
  systemctl disable --now village-mover.timer 2>/dev/null || true
  echo "로컬에 남은 파일을 전부 올리는 중..."; /usr/local/sbin/village-mover.sh --all || true
  left=$(find "$STAGING" -type f 2>/dev/null | wc -l)
  if [ "$left" -gt 0 ]; then echo "아직 $left개가 로컬에 남음 - 다시 실행하거나 확인해 주세요 (CT 연결은 그대로 둠)"; exit 1; fi
  if [ -f /etc/village/mp1.orig ]; then
    pct set "$CT" -mp1 "$(cat /etc/village/mp1.orig)" && echo "CT${CT} mp1 원래대로" && pct reboot "$CT"
    rm -f /etc/village/mp1.orig
  fi
  systemctl disable --now zeeps-merged.service 2>/dev/null || true
  rm -f /etc/systemd/system/zeeps-merged.service /etc/systemd/system/village-mover.service /etc/systemd/system/village-mover.timer /usr/local/sbin/village-mover.sh
  systemctl daemon-reload; echo "제거함 (설정 $CONF 와 빈 스테이징 폴더는 남겨 둠)"
  ;;
*) sed -n '2,13p' "$0"; exit 2 ;;
esac
