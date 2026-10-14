"""골목 청소/우체통: 알림방의 메일 관리 알림을 종류와 개수만 골목 일로 (제목/본문은 안 씀)."""
import json
from datetime import datetime

from poring_food import agents, life, street_mail


def test_parse_kinds_only():
    assert street_mail.parse("알림완료 메일 정리.GAS", "🗑️ 12개의 메일을 휴지통으로 이동하였습니다.") == {"kind": "trash", "count": 12}
    assert street_mail.parse("검토완료 메일 정리.GAS", "🗑️ 3개의 메일을 휴지통으로 이동하였습니다.")["count"] == 3
    a = "📥 알림대기메일관리"
    assert street_mail.parse(a, "**🚨 일일 기본 제공량을 모두 사용했습니다.**")["kind"] == "traffic"
    assert street_mail.parse(a, "**🔴 서버 다운 알림**\n> `dohyun_server` 네트워크 연결이 끊어졌습니다.")["kind"] == "server_down"
    assert street_mail.parse(a, "**📨 이메일 인증 코드 도착**\n> 제목: [은행] 인증번호 123456")["kind"] == "registered"
    # 새 메일의 제목/미리보기에 다른 종류 문구가 있어도 첫 줄만 본다
    ev = street_mail.parse(a, "**📌 새 메일 도착**\n> 제목: 서버 다운 알림 아님\n> 미리보기: Google Takeout ...")
    assert ev == {"kind": "mail", "count": 1}
    assert street_mail.parse("다른 웹훅", "🗑️ 12개의 메일을 휴지통으로") is None


def test_cleanup_pays_cleaner_and_posts(fake_llm, monkeypatch):
    monkeypatch.setattr(agents, "_group_channel", lambda g: 123)
    fake_llm(lambda system, user: {"text": "골목 싹 치웠어요~ 일당은 받아요!"})
    money = life.get("홍두식")["money"]
    now = datetime(2026, 10, 14, 15, 0)
    street_mail.handle({"kind": "trash", "count": 12}, now)
    assert life.get("홍두식")["money"] == money + 2 * street_mail.WAGE_PER_BAG
    with open(agents.GROUP_PATH, encoding="utf-8") as f:
        post = [json.loads(x) for x in f if x.strip()][-1]
    assert post["name"] == "홍두식" and "🧹" in post["text"]
    street_mail.handle({"kind": "mail", "count": 1}, now)
    street_mail.handle({"kind": "registered", "count": 1}, now)
    block = street_mail.prompt_block(now)
    assert "쓰레기 2봉지" in block and "편지 1통" in block and "등기 1통" in block and "123456" not in block


def test_town_event_once_per_kind():
    sent = []
    import poring_food.agents as ag
    orig = ag.inject_event
    ag.inject_event = lambda targets, detail, title="": sent.append(title) or []
    try:
        now = datetime(2026, 10, 14, 9, 0)
        street_mail.handle({"kind": "traffic", "count": 1}, now)
        street_mail.handle({"kind": "traffic", "count": 1}, now.replace(hour=10))    # 3시간 안에 또 - 한 번만
        street_mail.handle({"kind": "traffic", "count": 1}, now.replace(hour=13))
        assert sent == ["건물 인터넷 데이터 바닥", "건물 인터넷 데이터 바닥"]
    finally:
        ag.inject_event = orig
