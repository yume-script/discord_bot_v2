"""
포링푸드의 "지금" - 항상 한국시간(KST)이다.

[변경] 예전엔 datetime.now()(서버 시스템 시간대)를 썼다. 봇 프로세스가 UTC로 돌면 오후 4시 반이
아침 7시 반으로 계산돼서 애순이가 "출근 중"이라고 하는 식으로 스케줄 전체가 9시간 밀렸다.
기존 기록(타임존 없는 ISO 문자열)과 비교할 수 있게 타임존 정보는 뗀 naive 값으로 돌려준다.
"""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST).replace(tzinfo=None)
