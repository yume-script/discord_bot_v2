"""회차 시작/끝 알림은 카톡방에만 - 디스코드로는 안 간다."""
from poring_food import agents, notifier


def test_tick_notice_goes_to_kakao_only(monkeypatch):
    sent, discord = [], []
    monkeypatch.setattr(notifier, "send_to_local_bot", sent.append)
    monkeypatch.setattr(notifier, "send_to_discord", discord.append)
    monkeypatch.setattr(notifier, "TICK_NOTICE", True)
    monkeypatch.setattr(agents, "LAST_TICK", {"decided": 7, "decide_sec": 90})
    notifier.tick_notice("🕒 회차 시작")
    notifier.tick_notice(notifier.tick_summary(185))
    assert sent[0] == "🕒 회차 시작"
    assert "회차 완료" in sent[1] and "3분 5초" in sent[1] and "판단 7명" in sent[1]
    assert not discord
    assert "회차 실패" in notifier.tick_summary(3, failed=True)


def test_tick_notice_can_be_turned_off(monkeypatch):
    sent = []
    monkeypatch.setattr(notifier, "send_to_local_bot", sent.append)
    monkeypatch.setattr(notifier, "TICK_NOTICE", False)
    notifier.tick_notice("x")
    assert not sent
