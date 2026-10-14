"""빌런: 악역 연기 원칙, 며칠에 한 번 수작(당한 사람은 사건, 주변은 소문, 진행 중인 일), 숨긴 수작은 들통난다."""
from datetime import datetime, timedelta

from poring_food import agents, characters, chronicle, rotation, villains


def _by(name):
    return next(a for a in agents.load_agents() if a["name"] == name)


def test_villain_flags_and_system_principle():
    for n in ("장대희", "권민우", "부상길"):
        a = _by(n)
        assert villains.is_villain(a) and rotation._fixed(a)   # 교체로 빠지지 않는다
        assert "악역 연기 원칙" in agents._system(a) and a["villain"]["agenda"] in agents._system(a)
    assert "악역" not in agents._system(_by("소라"))


def test_scheme_rumor_thread_and_expose(fake_llm, monkeypatch):
    roster = characters.load_roster()
    ags = agents.load_agents()

    def handler(system, user):
        if "다음 수를 꾸미는" in system and "'장대희'" in system:
            return {"target": "오동백", "title": "보고서 이름 바꿔치기", "action": "오동백의 분기 보고서가 다른 이름으로 윗선에 올라갔다.",
                    "rumor": "이번 주 편성표 작성자가 바뀌었다는 말이 돈다.", "hidden": True,
                    "truth": "장대희가 오동백 보고서를 자기 이름으로 올렸다.", "effects": {"stress": 0.1}, "inner": "내 실적이다."}
        if "다음 수를 꾸미는" in system:
            return {}
        return {}

    fake_llm(handler)
    now = datetime(2026, 10, 12, 11, 3)
    assert villains.tick(ags, roster, now.replace(hour=9)) == []      # SCHEME_HOUR 전엔 안 함
    out = villains.tick(ags, roster, now)
    assert ("오동백", "오동백의 분기 보고서가 다른 이름으로 윗선에 올라갔다.", "사건") in out   # 숨겨서 이름이 안 나온다
    rumors = [w for w, t, k in out if k == "소문"]
    assert rumors and "장대희" not in rumors and "오동백" not in rumors
    assert any("보고서 이름 바꿔치기" in t["title"] for t in chronicle.open_threads())
    assert "보고서 이름 바꿔치기" in villains.prompt_block("장대희", _by("장대희"))
    assert villains.tick(ags, roster, now + timedelta(hours=2)) == []   # 며칠에 한 번
    # 다음 날부터 들통날 수 있다
    monkeypatch.setattr(villains, "EXPOSE_P", 1.0)
    out = villains.tick(ags, roster, now + timedelta(days=1))
    assert any(w == "오동백" and "장대희가" in t for w, t, _ in out)
    rec = villains._load()["장대희"]
    assert rec["schemes"][-1]["exposed"] and rec["reputation"] < 60
    assert "들통남" in villains.prompt_block("장대희", _by("장대희"))
