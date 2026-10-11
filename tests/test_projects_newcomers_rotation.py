"""마을 프로젝트(동네 범위는 누구나 참여), 새 이웃(예전 이름 재사용 금지), 하루 교체(소속별 인원 유지)."""
from datetime import datetime

from poring_food import agents, characters, memory, projects, rotation
from poring_food.clock import KST


def test_town_project_open_to_rotated_in_agent():
    p = projects.create("골목 화단 만들기", "꽃길", ["장소", "모종", "심기"], "여화정", ["홍두식"], "동네",
                        ["여화정", "홍두식"], "모임")
    assert p
    res = projects.contribute("골목 화단", "새로온사람", "흙을 날랐다")  # 만들 때 명단에 없던 사람
    assert res and res["gain"] == projects.POINTS["other"]
    assert "새로온사람" in res["project"]["helpers"]


def test_company_project_keeps_members():
    projects.create("사내 텃밭", "옥상 텃밭", ["a", "b"], "김동식", [], "회사", ["김동식", "애순이"], "모임")
    assert projects.contribute("사내 텃밭", "외부인", "구경") is None


def test_project_daily_cap_and_completion():
    projects.create("벤치", "골목 벤치", ["설계", "조립"], "진상필", [], "동네", [], "모임")
    total = sum(projects.contribute("벤치", f"주민{i}", "도움")["gain"] for i in range(10))
    assert total == projects.DAILY_CAP  # 하루 상한 - 며칠에 걸쳐 된다


def test_newcomers_skip_names_used_before(fake_llm):
    memory.remember("박새로이", "옛 단밤 사장의 기억", 5)  # 예전에 있었던 사람
    fake_llm(lambda s, u: {"drama": "이태원 클라쓰", "people": [
        {"name": "박새로이", "rank": "사장"}, {"name": "조이서", "rank": "매니저"},  # 조이서 = 에린 에이전트
        {"name": "길라임", "rank": "트레이너"},  # 정리된 옛 동네 사람
        {"name": "마현이", "rank": "요리사", "outer_persona": "o", "inner_truth": "i", "key_behavior": "k"}]})
    res = agents.move_in_newcomers()
    assert [r["name"] for r in res["added"]] == ["마현이"]
    assert any(c["name"] == "마현이" and c["dept"].startswith("새 이웃") for c in characters.load_roster())


def test_rotation_keeps_quotas_and_fixed(fake_llm, monkeypatch):
    fake_llm(lambda s, u: {"profile": "p", "routine": "r", "speech": "s", "sleep": [1, 7], "hangouts": []})
    monkeypatch.setattr(rotation, "now_kst", lambda: datetime(2026, 10, 12, 6, 5, tzinfo=KST))
    before = agents.load_agents()
    swaps = rotation.tick(agents._base_agents(), agents.MAX_AGENTS)
    after = agents.load_agents()
    assert len(swaps) == 3 and len(after) == len(before)
    count = lambda xs, c: sum(1 for a in xs if a.get("company") == c)  # noqa: E731
    for company in rotation.COMPANIES:
        assert count(before, company) == count(after, company)
    names = {a["name"] for a in after}
    assert {"애순이", "소라", "김동식", "오동백", "여화정"} <= names
    assert rotation.tick(agents._base_agents(), agents.MAX_AGENTS) == []  # 같은 날 두 번은 안 바꾼다
