"""반복 방지(화제 기록/반복 감지/사건 다양성/달력)와 인생 전환점."""
from datetime import datetime, timedelta

from poring_food import agents, life, memory, milestones, novelty, world

T = datetime(2026, 10, 12, 15)


def test_repeat_hint_and_worn_out_topics():
    for d in range(5):
        novelty.note("대화", "이번 주말 등산 같이 갈래? 무등산 단풍이 예쁘대", ["소라", "위벨"], at=T - timedelta(days=d + 1))
    novelty.note("대화", "어제 회의록 정리 부탁해요", ["소라", "슈타르크"], at=T - timedelta(days=1))
    novelty.note("대화", "무등산 등산 갈래? 단풍 예쁘대", ["소라", "위벨"], at=T - timedelta(days=30))  # 오래돼서 지워짐
    h = novelty.hint("이번 주말에 무등산 등산 같이 갈래?", ["소라", "위벨"], now=T)
    assert "처음이 아니다" in h and "5번" in h
    assert novelty.hint("오늘 점심 뭐 먹지", ["소라"], now=T) == ""
    worn = novelty.worn_out("소라", now=T)
    assert "지겹게" in worn and "등산" in worn and "회의록" not in worn
    assert novelty.worn_out("슈타르크", now=T) == ""


def test_kind_balance_and_calendar():
    log = [{"date": "2026-10-10", "kind": "사고/고장"}] * 4 + [{"date": "2026-10-11", "kind": "갈등/다툼"}]
    text = novelty.kind_balance(log, now=T)
    assert "사고/고장(4)" in text and "연애/설렘" in text.split("한 번도 안 나온")[1]
    cal = novelty.calendar_hint(datetime(2026, 10, 29, 9))
    assert "핼러윈(2일 뒤)" in cal and "단풍" in cal
    assert "추석(오늘)" in novelty.calendar_hint(datetime(2026, 9, 25, 9))


def test_world_plan_gets_kinds_and_logs_them(fake_llm, monkeypatch):
    seen = {}

    def handler(system, user):
        if "세계 엔진" in system:
            seen["user"] = user
            return {"events": [{"hour": 10, "kind": "연애/설렘", "title": "카페 고백 소동", "detail": "누가 고백했다",
                                "targets": ["마을"], "visibility": "direct", "effects": {}},
                               {"hour": 11, "kind": "엉뚱한 종류", "title": "정전", "detail": "정전", "targets": ["마을"],
                                "visibility": "direct"}]}
        return {}

    fake_llm(handler)
    monkeypatch.setattr(world, "now_kst", lambda: datetime(2026, 10, 12, 9, 3))
    plan = world._plan_day(datetime(2026, 10, 12, 9, 3), agents.load_agents(), [], "", [])
    assert "사건 종류" in seen["user"] and "[달력]" in seen["user"]
    assert [e["kind"] for e in plan] == ["연애/설렘", ""]


def test_milestone_once_a_day_updates_goals_memory_and_spreads(fake_llm):
    from poring_food import characters
    roster = characters.load_roster()
    ags = agents.load_agents()[:3]

    def handler(system, user):
        if "운명" in system:
            name = user.split("[인물] ")[1].split(" ")[0]
            goal = (life.get(name).get("goals") or [""])[0]
            return {"kind": "목표 달성", "title": "드디어 해냈다", "detail": f"{name}이 오래 바라던 일을 이뤘다.",
                    "public": True, "effects": {"happiness": 0.2}, "goal_done": goal,
                    "new_goals": ["다음 목표 세우기"], "memory": "인생에서 가장 기쁜 날"}
        return {}

    fake_llm(handler)
    now = datetime(2026, 10, 12, 10, 3)
    assert milestones.tick(ags, roster, now.replace(hour=9)) == []            # MILESTONE_HOUR 전엔 안 함
    out = milestones.tick(ags, roster, now)
    who = {x[0] for x in out if x[1].startswith("[내 인생에")}
    assert len(who) == milestones._per_day(len(ags))
    first = next(iter(who))
    assert life.get(first)["goals"][0] == "다음 목표 세우기"
    assert any(m["text"] == "인생에서 가장 기쁜 날" for m in memory.recall(first, k=10))
    company = next(a["company"] for a in ags if a["name"] == first)
    colleagues = {a["name"] for a in ags if a.get("company") == company} - {first}
    assert colleagues <= {x[0] for x in out if not x[1].startswith("[내 인생에")}  # 공개된 일은 같은 회사에 퍼진다
    assert milestones.tick(ags, roster, now + timedelta(hours=1)) == []       # 하루 한 번
    data = milestones._load()
    assert data["people"][first]["history"][-1]["kind"] == "목표 달성"
    # 같은 사람은 MIN_GAP_DAYS 동안 다시 안 뽑힌다
    assert first not in [a["name"] for a in milestones._pick(ags, data["people"], now + timedelta(days=2), 3)]
