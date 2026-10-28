#!/bin/bash
# 호스트 zeeps에서 root로 한 번 실행: 게이트/제어 스크립트 설치 + 방역관(ClamAV)·정비 타이머 드롭인 + 봇 키 안내.
#   bash install.sh              설치
#   bash install.sh --uninstall  드롭인/스크립트 제거 (원본 유닛은 처음부터 안 건드림)
# 가이드 7.1~7.3: 원본 유닛은 그대로 두고 /etc/systemd/system/<unit>.d/10-village.conf 만 만든다.
# (이름이 10-으로 시작해야 봇이 단계에 따라 거는 런타임 제한(systemctl set-property, 50-*.conf)이 이 파일보다 나중에 적용된다)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
# 유닛 : 허용 최대 단계 : 추가 설정
UNITS=(
  "clamav-risk-scan.service:1:ExecCondition=/usr/local/sbin/village-quick-gate.sh check|ExecStartPre=/usr/local/sbin/village-quick-gate.sh start|ExecStartPost=/usr/local/sbin/village-quick-gate.sh done|CPUWeight=10|CPUQuota=60%|MemoryHigh=1500M|MemoryMax=2200M|RuntimeMaxSec=1800"
  "clamav-deep-scan.service:2:CPUWeight=10|CPUQuota=60%|MemoryHigh=1500M|MemoryMax=2200M|RuntimeMaxSec=7200"
  "clamav-freshclam-local.service:2:CPUWeight=10"
  "malware-feed-update.service:2:CPUWeight=10"
  "malware-watch.service:2:CPUWeight=20"
  "apt-daily.service:1:"
  "apt-daily-upgrade.service:1:"
  "pve-daily-update.service:2:"
  "dpkg-db-backup.service:2:"
)
DROPIN=10-village.conf
# 서명 업데이트: 2시간 -> 6시간마다 (업데이트 뒤 빠른 검사도 같이 돌기 때문에 횟수가 곧 비용)
FRESHCLAM_EVERY=6h
if [ "${1:-}" = "--uninstall" ]; then
  for e in "${UNITS[@]}"; do rm -f "/etc/systemd/system/${e%%:*}.d/village.conf" "/etc/systemd/system/${e%%:*}.d/$DROPIN"; done
  rm -f /etc/systemd/system/clamav-freshclam-local.timer.d/$DROPIN
  rm -f /usr/local/sbin/village-gate.sh /usr/local/sbin/village-ctl.sh /usr/local/sbin/village-quick-gate.sh
  systemctl daemon-reload; echo "제거함 (봇 키는 /root/.ssh/authorized_keys에서 직접 지우세요)"; exit 0
fi
install -m 0755 "$HERE/village-gate.sh" /usr/local/sbin/village-gate.sh
install -m 0755 "$HERE/village-ctl.sh" /usr/local/sbin/village-ctl.sh
install -m 0755 "$HERE/village-quick-gate.sh" /usr/local/sbin/village-quick-gate.sh
for e in "${UNITS[@]}"; do
  unit=${e%%:*}; rest=${e#*:}; max=${rest%%:*}; extra=${rest#*:}
  if ! systemctl cat "$unit" >/dev/null 2>&1; then echo "건너뜀(없는 유닛): $unit"; continue; fi
  mkdir -p "/etc/systemd/system/$unit.d"
  rm -f "/etc/systemd/system/$unit.d/village.conf"     # 예전 이름 (봇의 런타임 제한을 덮어쓰던 것)
  { echo "# 집스마을 결계 게이트 (scripts/zeeps/install.sh) - 원본 유닛은 그대로"; echo "[Service]"
    echo "ExecCondition=/usr/local/sbin/village-gate.sh $max"
    [ -n "$extra" ] && tr '|' '\n' <<< "$extra"; } > "/etc/systemd/system/$unit.d/$DROPIN"
  echo "드롭인: $unit (최대 ${max}단계${extra:+, ${extra//|/ }})"
done
if systemctl cat clamav-freshclam-local.timer >/dev/null 2>&1; then
  mkdir -p /etc/systemd/system/clamav-freshclam-local.timer.d
  printf '# 집스마을: 서명 업데이트 주기\n[Timer]\nOnUnitActiveSec=\nOnUnitActiveSec=%s\n' "$FRESHCLAM_EVERY" \
    > /etc/systemd/system/clamav-freshclam-local.timer.d/$DROPIN
  echo "타이머: clamav-freshclam-local ${FRESHCLAM_EVERY}마다"
fi
systemctl daemon-reload
systemctl restart clamav-freshclam-local.timer 2>/dev/null || true
mkdir -p /run/village
cat <<MSG

설치 끝. 시험:
  echo 3 > /run/village/stage; /usr/local/sbin/village-gate.sh 1; echo \$?   # 1 = 건너뜀
  echo 0 > /run/village/stage; /usr/local/sbin/village-gate.sh 1; echo \$?   # 0 = 실행
  systemctl show clamav-risk-scan.service -p CPUQuotaPerSecUSec -p ExecCondition
  journalctl -u clamav-risk-scan --since today | grep -c CLAMAV_SKIP     # 새 파일이 없어 건너뛴 횟수

봇 연결 (etc에서 키를 만들고, 그 공개키를 여기 /root/.ssh/authorized_keys에 아래 형식 한 줄로):
  command="/usr/local/sbin/village-ctl.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA... discord_bot_v2
그다음 etc .env: PORING_LOADSHED_ZEEPS_SSH=root@<zeeps 주소>  (그 키로 probe/stage/status 말고는 아무것도 못 한다)
MSG
