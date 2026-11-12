#!/bin/bash
# 집스마을 호스트(zeeps) 제어 - 봇(CT102 etc)이 SSH로 부르는 유일한 입구.
# authorized_keys에 command="/usr/local/sbin/village-ctl.sh" 로 고정해서, 봇 키로는 아래 네 가지 말고 아무것도 못 한다.
#   probe      : 부하 측정값 출력 (/proc/stat 두 번, meminfo, PSI) - 읽기만
#   top        : 호스트 전체에서 CPU를 쓰는 곳 - 게스트(CT/VM)별 합계와 상위 프로세스 (1초 측정, 읽기만)
#                프로세스는 이름(comm)만 낸다 - 명령줄에는 토큰·비밀번호가 들어 있을 수 있어서.
#                출력: "cores N" / "guest <종류>:<번호>:<이름> <CPU%>" / "proc <종류>:<번호>:<이름> <프로세스 이름> <CPU%>"
#                (CPU%는 코어 1개를 꽉 채우면 100, 종류는 ct·vm·host)
#   stage N    : 결계 경보 단계(0~5) 기록 + 방역관(ClamAV) 조절, 바뀐 것만 한 줄씩 출력
#   status     : 지금 단계와 방역관 상태
# 방역관 조절 (가이드 5.1/7.3):
#   0단계      : CPUQuota 60% (드롭인 기본값)
#   1단계 이상 : 방역관 CPUQuota 25%, CPUWeight 5 (런타임 - 재부팅/0단계면 원래대로)
#   2단계 이상 : 돌고 있는 정기 스캔(risk/deep) 중단 - 다음 타이머 때 게이트(village-gate.sh)가 단계를 보고 다시 판단
#   malware-watch(실시간 탐지)는 멈추지 않는다 (게이트가 3단계 이상에서만 건너뜀).
set -u
CMD=${SSH_ORIGINAL_COMMAND:-${*:-}}
DIR=/run/village
SCANS="clamav-risk-scan.service clamav-deep-scan.service"
SLOW="clamav-risk-scan.service clamav-deep-scan.service clamav-freshclam-local.service malware-feed-update.service"
mkdir -p "$DIR"

probe() {
  head -1 /proc/stat; sleep 1; head -1 /proc/stat
  grep -E '^(MemTotal|MemAvailable):' /proc/meminfo
  for r in cpu io memory; do printf 'psi_%s ' "$r"; head -1 /proc/pressure/$r 2>/dev/null || echo; done
}

snap() {   # pid utime+stime 이름 (사라진 프로세스는 건너뜀)
  local f line
  for f in /proc/[0-9]*/stat; do
    read -r line < "$f" 2>/dev/null || continue
    echo "$line"
  done | awk '{ if (!match($0, /\(.*\)/)) next; c = substr($0, RSTART + 1, RLENGTH - 2); gsub(/[^A-Za-z0-9_.:+@-]/, "_", c)
                rest = substr($0, RSTART + RLENGTH + 1); split(rest, f, " "); print $1, f[12] + f[13], c }'
}

guest_of() {   # pid -> ct:번호:이름 / vm:번호:이름 / host:-:호스트이름 (cgroup으로 가린다)
  local cg id name
  cg=$(head -1 "/proc/$1/cgroup" 2>/dev/null)
  if [[ $cg =~ /lxc/([0-9]+) ]]; then
    id=${BASH_REMATCH[1]}; name=$(sed -n 's/^hostname: *//p' "/etc/pve/lxc/$id.conf" 2>/dev/null | head -1); echo "ct:$id:${name:-ct$id}"
  elif [[ $cg =~ /qemu.slice/([0-9]+)\.scope ]]; then
    id=${BASH_REMATCH[1]}; name=$(sed -n 's/^name: *//p' "/etc/pve/qemu-server/$id.conf" 2>/dev/null | head -1); echo "vm:$id:${name:-vm$id}"
  else
    echo "host:-:$(hostname -s)"
  fi
}

top_cpu() {
  local a b hz pid pct
  hz=$(getconf CLK_TCK); a=$(snap); sleep 1; b=$(snap)
  echo "cores $(nproc)"
  # 1초 동안 쓴 틱이 많은 순 (상위 60개만 게스트를 가린다)
  awk -v hz="$hz" 'NR == FNR { t[$1] = $2; next } ($1 in t) { d = $2 - t[$1]; if (d > 0) printf "%s %.1f %s\n", $1, d * 100 / hz, $3 }' \
      <(echo "$a") <(echo "$b") | sort -k2 -nr | head -60 | while read -r pid pct name; do
    echo "$(guest_of "$pid") $name $pct"
  done | awk '{ g[$1] += $3; print "proc", $0 } END { for (k in g) printf "guest %s %.1f\n", k, g[k] }' | sort -k1,1 -k4nr
}

quota_for() { if [ "$1" -ge 1 ]; then echo "25% 5"; else echo "60% 10"; fi; }

stage() {
  local n=$1
  echo "$n" > "$DIR/stage.tmp" && mv -f "$DIR/stage.tmp" "$DIR/stage"
  local want; want=$(quota_for "$n")
  if [ "$(cat "$DIR/clam.quota" 2>/dev/null)" != "$want" ]; then
    set -- $want
    for u in $SLOW; do
      systemctl cat "$u" >/dev/null 2>&1 || continue
      systemctl set-property --runtime "$u" CPUQuota="$1" CPUWeight="$2" >/dev/null 2>&1 && echo "quota $u $1"
    done
    echo "$want" > "$DIR/clam.quota"
  fi
  if [ "$n" -ge 2 ]; then
    for u in $SCANS; do
      if systemctl is-active --quiet "$u"; then
        systemctl stop "$u" && echo "stopped $u" && logger -t village "결계 경보 ${n}단계 - $u 중단"
      fi
    done
  fi
}

case "$CMD" in
  probe) probe ;;
  top) top_cpu ;;
  "stage "[0-5]) stage "${CMD#stage }" ;;
  status)
    echo "stage $(cat "$DIR/stage" 2>/dev/null || echo '?') ($(( $(date +%s) - $(stat -c %Y "$DIR/stage" 2>/dev/null || echo 0) ))s ago)"
    for u in $SLOW malware-watch.service; do
      printf '%s %s quota=%s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null)" "$(systemctl show -p CPUQuotaPerSecUSec --value "$u" 2>/dev/null)"
    done ;;
  *) echo "village-ctl: 허용되지 않은 명령: $CMD" >&2; exit 2 ;;
esac
