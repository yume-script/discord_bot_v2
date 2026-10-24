"""예약함: 대화/통신방/장면을 이야기 속 시각대로 차례대로 올린다."""
import random
from datetime import datetime, timedelta

from poring_food import agents, characters, outbox


def test_schedule_take_due_in_time_order_and_drop_stale(monkeypatch):
    monkeypatch.setattr(outbox, "paced", lambda: True)
    now = datetime(2026, 10, 12, 9, 30)
    outbox.schedule(now + timedelta(minutes=2), "agent", name="B", text="두 번째")
    outbox.schedule(now - timedelta(minutes=1), "agent", name="A", text="첫 번째")
    outbox.schedule(now - timedelta(minutes=200), "kakao", text="너무 늦은 글")
    outbox.schedule(now + timedelta(minutes=1), "discord", text="중간")
    assert [it["text"] for it in outbox.take_due(now)] == ["첫 번째"]
    assert outbox.take_due(now) == []  # 한 번 꺼낸 글은 다시 안 나온다
    later = outbox.take_due(now + timedelta(minutes=3))
    assert [it["text"] for it in later] == ["중간", "두 번째"]
    assert outbox.pending() == 0  # 너무 늦은 글은 버려졌다


def test_not_paced_sends_immediately(monkeypatch):
    sent = []
    monkeypatch.setattr(outbox, "paced", lambda: False)
    monkeypatch.setattr(outbox.runtime, "has_agent_sender", lambda: True)
    monkeypatch.setattr(outbox.runtime, "send_as", lambda acc, name, text, *a, **k: sent.append((name, text)))
    outbox.schedule(None, "agent", name="소라", text="안녕")
    assert sent == [("소라", "안녕")]


def test_slot_and_gap():
    rnd = random.Random(1)
    now = datetime(2026, 10, 12, 9, 3)
    t = outbox.slot(now, 5, 17, rnd)
    assert datetime(2026, 10, 12, 9, 5) <= t <= datetime(2026, 10, 12, 9, 17)
    late = datetime(2026, 10, 12, 9, 40)
    assert late < outbox.slot(late, 5, 17, rnd) <= late + timedelta(seconds=40)  # 이미 지난 칸이면 곧바로
    assert timedelta(seconds=20) <= outbox.gap("짧은 말", rnd) <= timedelta(seconds=90)
    assert timedelta(seconds=40) <= outbox.gap("통신석 답장", rnd, messenger=True) <= timedelta(seconds=180)


def test_tick_paces_conversations_through_the_hour(fake_llm, monkeypatch):
    from tests.test_agents_tick import _handler, _quiet
    _quiet(monkeypatch)
    monkeypatch.setattr(outbox, "paced", lambda: True)
    fake_llm(_handler)
    from tests.test_agents_tick import run_live_jobs
    start = agents.now_kst()
    agents.tick(characters.load_roster())
    assert run_live_jobs() >= 2                                    # 준실시간: 대화는 그 시각에 만들어진다
    items = outbox.take_due(start + timedelta(minutes=80), limit=500)
    talks = [it for it in items if it["name"] not in ("마을 소식",)]
    assert len(talks) >= 4
    dues = [datetime.fromisoformat(it["due"]) for it in talks]
    assert dues == sorted(dues)                                   # 시각 순서대로 나온다
    assert dues[-1] - dues[0] >= timedelta(seconds=20)             # 한꺼번에가 아니라 시간을 두고
    headers = [it for it in talks if "📍" in it["text"].split("\n")[0] or "📱" in it["text"].split("\n")[0]]
    assert len(headers) >= 2                                      # 대화 여러 개가 각자 시간표로
    assert all(it["text"].startswith("-# ") for it in talks)       # 모든 마디에 출처 머리줄 (어디서 누가 누구에게)
    assert any(" → " in it["text"].split("\n")[0] for it in talks[1:])


def test_scene_is_sent_line_by_line(monkeypatch):
    from poring_food import main
    monkeypatch.setattr(outbox, "paced", lambda: True)
    scene = {"discord": "🎬 **[카페 · 10:00] A × B**\n-# 📖 줄거리\n**A**: \"안녕\"\n**B**: \"응\"",
             "kakao": "🎬 [카페 · 10:00] A × B\n📖 줄거리\nA: \"안녕\"\nB: \"응\""}
    now = datetime(2026, 10, 12, 10, 3)
    main._send_scene(scene, now, random.Random(3))
    items = outbox.take_due(now + timedelta(hours=1), limit=50)
    kakao = [it for it in items if it["kind"] == "kakao"]
    disc = [it for it in items if it["kind"] == "discord"]
    assert len(kakao) == 2 and len(disc) == 2                   # 머리줄+첫 대사, 둘째 대사
    assert kakao[0]["text"].startswith("🎬") and "안녕" in kakao[0]["text"]
    assert kakao[1]["text"].startswith("🎬 카페 · 10:") and kakao[1]["text"].endswith('B: "응"')  # 대사마다 출처
    assert disc[1]["text"].startswith("-# 🎬 카페 · 10:")
    assert kakao[0]["due"] < kakao[1]["due"]


def test_scene_also_goes_to_agent_channel_per_speaker(monkeypatch):
    """연재 장면(예: 에린 로지스틱스 사무실)도 에이전트 대화 채널에 화자 이름으로 한 줄씩 올라간다."""
    from poring_food import main
    monkeypatch.setattr(outbox, "paced", lambda: True)
    scene = {"discord": "🎬 **[에린 로지스틱스 사무실 · 10:00] 최택 × 리정혁**\n**최택**: \"보고서\"\n**리정혁**: \"네\"",
             "kakao": "🎬 [에린 로지스틱스 사무실 · 10:00] 최택 × 리정혁\n최택: \"보고서\"\n리정혁: \"네\"",
             "arc_title": "줄거리",
             "scene": {"location": "에린 로지스틱스 사무실", "narration": "조용한 오후.",
                       "lines": [{"speaker": "최택", "line": "보고서"}, {"speaker": "리정혁", "line": "네", "inner": "또?"}]}}
    now = datetime(2026, 10, 12, 10, 3)
    main._send_scene(scene, now, random.Random(3))
    items = outbox.take_due(now + timedelta(hours=1), limit=50)
    agent = [it for it in items if it["kind"] == "agent"]
    assert [it["name"] for it in agent] == ["최택", "리정혁", "마을 소식"]
    assert agent[0]["text"].startswith("-# 🎬 에린 로지스틱스 사무실 · 10:") and "📖 줄거리" in agent[0]["text"]
    assert agent[1]["text"].endswith('"네" *(속마음: 또?)*')
    assert agent[0]["due"] < agent[1]["due"] < agent[2]["due"]
    assert [it for it in items if it["kind"] == "kakao"]
    assert not [it for it in items if it["kind"] == "discord"]  # 방송 채널엔 또 안 올린다 (두 번 안 보이게)
