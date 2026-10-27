#!/bin/bash
# /usr/local/sbin/village-gate.sh <허용 최대 단계>  (종료코드 0=실행, 1=건너뜀) - 가이드 6.3
# 타이머 서비스의 ExecCondition으로 쓴다. 단계 파일이 없거나 10분 넘게 안 바뀌었으면(봇/LLM이 멈춤) 로컬 PSI로 직접 판단한다.
MAXSTAGE=${1:-1}
F=/run/village/stage
if [ -f $F ] && [ $(( $(date +%s) - $(stat -c %Y $F) )) -lt 600 ]; then
  stage=$(cat $F)
else
  cpu=$(awk -F'[= ]' '/^some/{print int($3)}' /proc/pressure/cpu)
  io=$(awk -F'[= ]' '/^some/{print int($3)}' /proc/pressure/io)
  stage=0
  if [ "$cpu" -ge 10 ] || [ "$io" -ge 10 ]; then stage=2; fi
fi
[ "$stage" -le "$MAXSTAGE" ]
