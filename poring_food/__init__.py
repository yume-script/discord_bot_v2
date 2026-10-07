"""
포링푸드(애순이와 동료들의 회사 일상 시뮬레이션) - 원래 별도 저장소(yume-script/porning_food)의
cron 스크립트였는데 discord_bot_v2로 합쳤다. 봇 안에서 매시 실행되고(cogs/poring_food.py),
LLM/카톡·디스코드 전송/북오아시스 MCP 연결은 봇의 것을 그대로 쓴다.

- 기본 데이터(조직도/페르소나/경쟁사): poring_food/data/ (git으로 관리)
- 실행 중 쌓이는 상태(현재 상태/히스토리/인물 상태/관계/최근 이슈/서고 점검): storage/poring_food/
"""
