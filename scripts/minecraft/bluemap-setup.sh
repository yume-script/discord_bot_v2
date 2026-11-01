#!/bin/bash
# BlueMap(개척지 웹 지도) 처음 설정 - mc-village를 한 번 띄운 뒤 실행한다.
#   cd /mnt/discord_bot_v2/scripts/minecraft && bash bluemap-setup.sh
# 1) 마인크래프트 리소스 내려받기 동의(accept-download) 2) 그리기 스레드 1개(서버 부하) 3) 다시 불러오기
set -e
cd "$(dirname "$0")"
CONF=data/plugins/BlueMap/core.conf
for i in $(seq 60); do [ -f "$CONF" ] && break; sleep 2; done
[ -f "$CONF" ] || { echo "BlueMap 설정이 아직 없음 - docker compose up -d 로 서버를 띄우고 1~2분 뒤 다시"; exit 1; }
sed -i 's/^accept-download:.*/accept-download: true/' "$CONF"
if grep -q '^render-thread-count' "$CONF"; then
  sed -i 's/^render-thread-count:.*/render-thread-count: 1/' "$CONF"
else
  echo 'render-thread-count: 1' >> "$CONF"
fi
docker exec mc-village rcon-cli bluemap reload
echo "BlueMap 설정 끝 - 몇 분 뒤 http://$(hostname -I | awk '{print $1}'):8100 에서 지도가 보인다 (처음엔 그리는 데 시간이 걸림)"
