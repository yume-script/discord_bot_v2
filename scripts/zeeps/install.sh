#!/bin/bash
# 호스트 zeeps에서 root로 한 번 실행: 게이트/제어 스크립트 설치 + 방역관(ClamAV)·정비 타이머 드롭인 + 봇 키 안내.
#   bash install.sh              설치
#   bash install.sh --uninstall  드롭인/스크립트 제거 (원본 유닛은 처음부터 안 건드림)
# 가이드 7.1/7.3: 원본 유닛은 그대로 두고 /etc/systemd/system/<unit>.d/village.conf 만 만든다.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
# 유닛 : 허용 최대 단계 : 추가 설정
UNITS=(
  "clamav-risk-scan.service:1:CPUWeight=10|CPUQuota=60%|MemoryHigh=1500M|MemoryMax=2200M|RuntimeMaxSec=1800"
  "clamav-deep-scan.service:2:CPUWeight=10|CPUQuota=60%|MemoryHigh=1500M|MemoryMax=2200M|RuntimeMaxSec=7200"
  "clamav-freshclam-local.service:2:CPUWeight=10"
  "malware-feed-update.service:2:CPUWeight=10"
  "malware-watch.service:2:CPUWeight=20"
  "apt-daily.service:1:"
  "apt-daily-upgrade.service:1:"
  "pve-daily-update.service:2:"
  "dpkg-db-backup.service:2:"
)
if [ "${1:-}" = "--uninstall" ]; then
  for e in "${UNITS[@]}"; do rm -f "/etc/systemd/system/${e%%:*}.d/village.conf"; done
  rm -f /usr/local/sbin/village-gate.sh /usr/local/sbin/village-ctl.sh
  systemctl daemon-reload; echo "제거함 (봇 키는 /root/.ssh/authorized_keys에서 직접 지우세요)"; exit 0
fi
install -m 0755 "$HERE/village-gate.sh" /usr/local/sbin/village-gate.sh
install -m 0755 "$HERE/village-ctl.sh" /usr/local/sbin/village-ctl.sh
for e in "${UNITS[@]}"; do
  unit=${e%%:*}; rest=${e#*:}; max=${rest%%:*}; extra=${rest#*:}
  if ! systemctl cat "$unit" >/dev/null 2>&1; then echo "건너뜀(없는 유닛): $unit"; continue; fi
  mkdir -p "/etc/systemd/system/$unit.d"
  { echo "# 집스마을 결계 게이트 (scripts/zeeps/install.sh) - 원본 유닛은 그대로"; echo "[Service]"
    echo "ExecCondition=/usr/local/sbin/village-gate.sh $max"
    [ -n "$extra" ] && tr '|' '\n' <<< "$extra"; } > "/etc/systemd/system/$unit.d/village.conf"
  echo "드롭인: $unit (최대 ${max}단계${extra:+, ${extra//|/ }})"
done
systemctl daemon-reload
mkdir -p /run/village
cat <<MSG

설치 끝. 시험:
  echo 3 > /run/village/stage; /usr/local/sbin/village-gate.sh 1; echo \$?   # 1 = 건너뜀
  echo 0 > /run/village/stage; /usr/local/sbin/village-gate.sh 1; echo \$?   # 0 = 실행
  systemctl show clamav-risk-scan.service -p CPUQuotaPerSecUSec -p ExecCondition

봇 연결 (etc에서 키를 만들고, 그 공개키를 여기 /root/.ssh/authorized_keys에 아래 형식 한 줄로):
  command="/usr/local/sbin/village-ctl.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA... discord_bot_v2
그다음 etc .env: PORING_LOADSHED_ZEEPS_SSH=root@<zeeps 주소>  (그 키로 probe/stage/status 말고는 아무것도 못 한다)
MSG
