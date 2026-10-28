#!/bin/bash
# /usr/local/sbin/village-quick-gate.sh check|start|done - ClamAV 매시 빠른 검사(clamav-risk-scan)를 새 파일이 있을 때만 (가이드 7.2)
# 검사보다 서명 DB 로딩(약 1GB, 코어 하나 1분)이 비싸서, 임시 폴더에 지난 검사 뒤 새 파일이 없으면 건너뛴다.
# 검사 스크립트(clamav-watch-scan.sh)는 그대로 두고 유닛 드롭인에서 부른다:
#   check: ExecCondition - 0=검사, 1=건너뜀 (첫 실행 / 하루 한 번 전체 / 새 파일 있음 -> 검사)
#   start: ExecStartPre  - 검사 시작 시각을 남긴다 (검사 중에 생긴 파일은 다음에 다시 본다)
#   done:  ExecStartPost - 성공한 검사만 기준 시각으로 확정 (실패하면 다음에 다시 전부)
D=/var/lib/village
S=$D/quick.stamp
F=$D/quick.full
DIRS="/tmp /var/tmp /dev/shm"
mkdir -p "$D"
case "${1:-check}" in
  check)
    [ -f "$S" ] && [ -f "$F" ] || exit 0
    [ -n "$(find "$F" -mmin +1440 2>/dev/null)" ] && exit 0
    for d in $DIRS; do
      [ -d "$d" ] || continue
      if [ -n "$(find "$d" -xdev -type f -newer "$S" ! -name 'clamav-watch.*' -print -quit 2>/dev/null)" ]; then exit 0; fi
    done
    echo "CLAMAV_SKIP reason=no_new_files mode=quick"
    exit 1 ;;
  start)
    touch "$S.new"
    if [ ! -f "$F" ] || [ -n "$(find "$F" -mmin +1440 2>/dev/null)" ]; then touch "$F.new"; fi ;;
  done)
    [ -f "$S.new" ] && mv -f "$S.new" "$S"
    [ -f "$F.new" ] && mv -f "$F.new" "$F"
    true ;;
esac
