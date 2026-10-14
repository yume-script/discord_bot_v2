"""정보 칸막이: 회사 안의 일은 그 회사 사람만 안다 (동네 사람이 포링푸드 수치 조작을 들먹이지 않게)."""
from poring_food import agents, chronicle

PF, ERINN, TOWN = "포링푸드 (Poring Food)", "에린 로지스틱스 (Erinn Logistics)", "광주 동네 상가"


def _by(name):
    return next(a for a in agents.load_agents() if a["name"] == name)


def test_thread_scope_filters_prompt_block():
    chronicle.open_thread("권민우의 수작: 실적 수치 조작", "영업 수치가 이상하다는 말", scope="포링푸드")
    chronicle.open_thread("골목 월세 인상", "상가번영회가 월세를 올린다", scope="")
    chronicle.open_thread("에린 배차 꼬임", "배차표가 엉망", scope=ERINN)
    town = chronicle.prompt_block(TOWN)
    assert "골목 월세 인상" in town and "수치 조작" not in town and "배차" not in town
    pf = chronicle.prompt_block(PF)
    assert "수치 조작" in pf and "골목 월세 인상" in pf and "배차" not in pf
    assert "[포링푸드 안의 일]" in pf
    god = chronicle.prompt_block()            # 세계 엔진(전지적 시점)은 다 본다
    assert "수치 조작" in god and "배차" in god


def test_legacy_thread_scope_is_inferred():
    # scope가 없던 예전 기록: 회사 이름이나 한 회사 사람만 나오면 그 회사 일
    chronicle._save_threads([
        {"id": "a", "title": "권민우 보고서 논란", "status": "open", "since": "2026-10-10T09:00", "updates": []},
        {"id": "b", "title": "포링푸드 신메뉴 회의", "status": "open", "since": "2026-10-10T09:00", "updates": []},
        {"id": "c", "title": "동네 이름 짓기", "status": "open", "since": "2026-10-10T09:00", "updates": []},
    ])
    titles = [t["title"] for t in chronicle.open_threads(TOWN)]
    assert titles == ["동네 이름 짓기"]
    assert len(chronicle.open_threads(PF)) == 3


def test_villain_scheme_thread_stays_in_company(fake_llm):
    from poring_food import characters, villains
    from datetime import datetime

    def handler(system, user):
        if "다음 수를 꾸미는" in system and "'장대희'" in system:
            return {"target": "권민우", "title": "보고서 바꿔치기", "action": "권민우의 보고서가 다른 이름으로 올라갔다.",
                    "rumor": "보고서 작성자가 바뀌었다는 말", "hidden": False, "truth": "장대희가 했다", "effects": {}}
        return {}

    fake_llm(handler)
    villains.tick(agents.load_agents(), characters.load_roster(), datetime(2026, 10, 12, 11, 3))
    assert any("보고서 바꿔치기" in t["title"] for t in chronicle.open_threads(PF))
    assert not any("보고서 바꿔치기" in t["title"] for t in chronicle.open_threads(TOWN))


def test_office_gossip_does_not_leak_to_town(monkeypatch):
    for k in ("GOSSIP_P_SAME_PLACE", "GOSSIP_P_COLLEAGUE", "GOSSIP_P_REGULAR", "GOSSIP_P_OTHER"):
        monkeypatch.setattr(agents, k, 1.0)
    monkeypatch.setattr(agents, "GOSSIP_MAX_RECIPIENTS", 100)
    t = [{"speaker": "장대희", "line": "권민우 너 수치 조작한 거 내가 다 안다"}]
    got = agents._gossip(_by("장대희"), _by("권민우"), "직접", "포링푸드 (Poring Food) 사무실", t, {})
    assert got and all(_by(n)["company"] == PF for n, _ in got)
    # 회사 밖(동네)에서 다른 소속끼리 한 얘기는 예전처럼 동네로 돈다
    got = agents._gossip(_by("부상길"), _by("홍두식"), "직접", "골목", t, {})
    assert any(_by(n)["company"] != TOWN for n, _ in got)


def test_system_has_knowledge_boundary():
    s = agents._system(_by("부상길"))
    assert "아는 것의 한계" in s and "포링푸드" in s and "회사 사람이 아니다" in s
    s = agents._system(_by("권민우"))
    assert "나는 포링푸드 사람이다" in s and "에린 로지스틱스" in s


def test_day_summary_keeps_company_section(fake_llm):
    chronicle.open_thread("권민우 보고서", "", scope=PF)

    def handler(system, user):
        assert "회사 안의 일" in user
        return {"summary": "하루.", "public": "골목에 비가 왔다.", "highlights": [],
                "company": {"포링푸드": "권민우 보고서로 회의실이 시끄러웠다.", "에린 로지스틱스": ""},
                "threads": [{"title": "신메뉴 원가 다툼", "status": "open", "note": "부장이 화냄", "scope": "포링푸드"},
                            {"title": "골목 축제", "status": "open", "note": "준비 시작", "scope": "동네"}]}

    fake_llm(handler)
    c = chronicle._summarize_day("2026-10-12")
    assert c["company"] == {PF: "권민우 보고서로 회의실이 시끄러웠다."}
    chronicle._append(chronicle.CHRONICLE_PATH, c)
    town, pf = chronicle.prompt_block(TOWN), chronicle.prompt_block(PF)
    assert "골목에 비가 왔다" in town and "회의실이 시끄러웠다" not in town and "신메뉴 원가" not in town
    assert "골목 축제" in town
    assert "회의실이 시끄러웠다" in pf and "신메뉴 원가" in pf
